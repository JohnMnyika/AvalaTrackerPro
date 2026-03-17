(function () {
  if (!window.AvalaTaskDetector) return;

  const detector = window.AvalaTaskDetector;
  let currentUrl = window.location.href;
  let taskPayload = null;
  let startedTaskUid = null;
  let lastFrame = null;
  let lastAnnotationCount = null;
  let lastFrameChangeTs = 0;

  function send(type, payload) {
    try {
      chrome.runtime.sendMessage({ type, payload });
    } catch (_err) {}
  }

  function mergeTaskPayload(update) {
    if (!taskPayload) taskPayload = update;
    let changed = false;
    const fields = [
      "task_uid",
      "dataset",
      "sequence_id",
      "camera",
      "frame_start",
      "frame_end",
      "total_frames",
      "expected_hours"
    ];
    for (const field of fields) {
      if (update[field] === undefined || update[field] === null) continue;
      if (taskPayload[field] !== update[field]) {
        taskPayload[field] = update[field];
        changed = true;
      }
    }
    return changed;
  }

  function sendTaskUpdate(update) {
    if (!update || !update.task_uid) return;
    send("TASK_UPDATE", update);
  }

  const emitFrameIfChanged = () => {
    if (!taskPayload) return;
    const frame = detector.extractFrameNumberFromDom();
    if (typeof frame !== "number" || frame === lastFrame) return;
    lastFrame = frame;
    lastFrameChangeTs = Date.now();
    send("FRAME_LOG", {
      task_uid: taskPayload.task_uid,
      frame_number: frame,
      annotations_created: 0,
      annotations_deleted: 0
    });
  };

  const emitFrameFromPreview = (previewFrame) => {
    if (!taskPayload) return;
    if (!Number.isFinite(previewFrame)) return;
    if (previewFrame === lastFrame) return;
    lastFrame = previewFrame;
    lastFrameChangeTs = Date.now();
    send("FRAME_LOG", {
      task_uid: taskPayload.task_uid,
      frame_number: previewFrame,
      annotations_created: 0,
      annotations_deleted: 0
    });
  };

  const emitAnnotationDelta = () => {
    if (!taskPayload) return;
    const count = detector.extractAnnotationCountFromDom();
    if (count === null) return;

    if (lastAnnotationCount === null) {
      lastAnnotationCount = count;
      return;
    }

    // Ignore quick churn right after frame changes to avoid false deltas.
    if (Date.now() - lastFrameChangeTs < 2000) {
      lastAnnotationCount = count;
      return;
    }

    const delta = count - lastAnnotationCount;
    if (delta === 0) return;

    lastAnnotationCount = count;
    const frame = typeof lastFrame === "number" ? lastFrame : detector.extractFrameNumberFromDom();
    send("FRAME_LOG", {
      task_uid: taskPayload.task_uid,
      frame_number: Number.isFinite(frame) ? frame : 0,
      annotations_created: delta > 0 ? delta : 0,
      annotations_deleted: delta < 0 ? Math.abs(delta) : 0
    });
  };

  function startTrackingForUrl(url) {
    if (!detector.isTaskUrl(url)) {
      taskPayload = null;
      return;
    }
    const parsed = detector.parseTaskFromUrl(url);
    if (!parsed) return;
    taskPayload = parsed;
    lastFrame = null;
    lastAnnotationCount = null;
    if (startedTaskUid !== parsed.task_uid) {
      send("TASK_START", parsed);
      startedTaskUid = parsed.task_uid;
    }
    if (Number.isFinite(parsed.preview_frame)) {
      emitFrameFromPreview(parsed.preview_frame);
    }
  }

  function handleUrlChange() {
    const next = window.location.href;
    if (next === currentUrl) return;

    const nextParsed = detector.isTaskUrl(next) ? detector.parseTaskFromUrl(next) : null;
    const nextUid = nextParsed ? nextParsed.task_uid : null;

    // Ignore SPA URL churn that keeps the same logical task.
    if (startedTaskUid && nextUid && startedTaskUid === nextUid) {
      currentUrl = next;
      taskPayload = nextParsed;
      if (nextParsed && Number.isFinite(nextParsed.preview_frame)) {
        emitFrameFromPreview(nextParsed.preview_frame);
      }
      return;
    }

    if (taskPayload && startedTaskUid) send("TASK_END", { task_uid: startedTaskUid });
    currentUrl = next;
    startedTaskUid = null;
    lastAnnotationCount = null;
    if (nextParsed) {
      taskPayload = nextParsed;
      send("TASK_START", nextParsed);
      startedTaskUid = nextParsed.task_uid;
      lastFrame = null;
      if (Number.isFinite(nextParsed.preview_frame)) {
        emitFrameFromPreview(nextParsed.preview_frame);
      }
    } else {
      startTrackingForUrl(next);
    }
  }

  function extractFromGlobalStore() {
    const candidates = [
      window.__APOLLO_STATE__,
      window.__INITIAL_STATE__,
      window.__STATE__,
      window.__AVALA_STATE__,
      window.__AVALA_STORE__
    ];

    const MAX_DEPTH = 4;
    const MAX_ARRAY = 50;
    const out = {};

    function extractSignals(obj, depth) {
      if (!obj || depth > MAX_DEPTH) return;
      if (Array.isArray(obj)) {
        if (obj.length && typeof obj[0] === "object") {
          for (let i = 0; i < Math.min(obj.length, MAX_ARRAY); i += 1) {
            extractSignals(obj[i], depth + 1);
          }
        }
        return;
      }
      if (typeof obj !== "object") return;

      for (const key of Object.keys(obj)) {
        const val = obj[key];
        const lower = key.toLowerCase();

        if (val && typeof val === "string") {
          if (["work_unit_uid", "task_uid", "taskid", "task_id"].includes(lower)) out.task_uid = val;
          if (["sequence_id", "sequenceid"].includes(lower)) out.sequence_id = val;
          if (["dataset"].includes(lower)) out.dataset = val;
          if (["camera", "camera_name", "cameraname"].includes(lower)) out.camera = val;
        }

        if (typeof val === "number") {
          if (["frame", "frame_number", "framenumber", "frame_index", "frameindex"].includes(lower)) {
            out.frame_number = val;
          }
        }

        if (Array.isArray(val)) {
          if (["annotations", "cuboids", "boxes", "objects", "labels", "annotations3d", "cuboidannotations"].includes(lower)) {
            out.boxes_count = val.length;
          }
        }

        if (val && typeof val === "object") {
          extractSignals(val, depth + 1);
        }
      }
    }

    for (const candidate of candidates) {
      if (!candidate) continue;
      extractSignals(candidate, 0);
      if (Object.keys(out).length) break;
    }

    return Object.keys(out).length ? out : null;
  }

  function injectNetworkHook() {
    const hook = function () {
      const MAX_DEPTH = 4;
      const MAX_ARRAY = 50;

      function extractSignals(obj, depth, out) {
        if (!obj || depth > MAX_DEPTH) return;
        if (Array.isArray(obj)) {
          if (obj.length && typeof obj[0] === "object") {
            for (let i = 0; i < Math.min(obj.length, MAX_ARRAY); i += 1) {
              extractSignals(obj[i], depth + 1, out);
            }
          }
          return;
        }
        if (typeof obj !== "object") return;

        for (const key of Object.keys(obj)) {
          const val = obj[key];
          const lower = key.toLowerCase();

          if (val && typeof val === "string") {
            if (["work_unit_uid", "task_uid", "taskid", "task_id"].includes(lower)) out.task_uid = val;
            if (["sequence_id", "sequenceid"].includes(lower)) out.sequence_id = val;
            if (["dataset"].includes(lower)) out.dataset = val;
            if (["camera", "camera_name", "cameraname"].includes(lower)) out.camera = val;
          }

          if (typeof val === "number") {
            if (["frame", "frame_number", "framenumber", "frame_index", "frameindex"].includes(lower)) {
              out.frame_number = val;
            }
          }

          if (Array.isArray(val)) {
            if (["annotations", "cuboids", "boxes", "objects", "labels", "annotations3d", "cuboidannotations"].includes(lower)) {
              out.boxes_count = val.length;
            }
          }

          if (val && typeof val === "object") {
            extractSignals(val, depth + 1, out);
          }
        }
      }

      function shouldMarkComplete(url) {
        if (!url) return false;
        return /save|submit|complete|annotation|label/i.test(url);
      }

      const originalFetch = window.fetch;
      window.fetch = async function (...args) {
        const res = await originalFetch.apply(this, args);
        try {
          const url = typeof args[0] === "string" ? args[0] : (args[0] && args[0].url) || res.url || "";
          const ct = (res.headers.get("content-type") || "").toLowerCase();
          if (ct.includes("application/json")) {
            const clone = res.clone();
            const data = await clone.json();
            const out = {};
            extractSignals(data, 0, out);
            if (shouldMarkComplete(url)) out.frame_completed = true;
            if (Object.keys(out).length) {
              window.postMessage({ source: "avala-tracker-pro", type: "network", url, data: out }, "*");
            }
          }
        } catch (_err) {}
        return res;
      };

      const OriginalXHR = window.XMLHttpRequest;
      function XHRProxy() {
        const xhr = new OriginalXHR();
        let url = "";
        xhr.open = function (method, openUrl, ...rest) {
          url = openUrl || "";
          return OriginalXHR.prototype.open.call(this, method, openUrl, ...rest);
        };
        xhr.addEventListener("load", function () {
          try {
            const text = xhr.responseText || "";
            if (!text || (text[0] !== "{" && text[0] !== "[")) return;
            const data = JSON.parse(text);
            const out = {};
            extractSignals(data, 0, out);
            if (shouldMarkComplete(url)) out.frame_completed = true;
            if (Object.keys(out).length) {
              window.postMessage({ source: "avala-tracker-pro", type: "network", url, data: out }, "*");
            }
          } catch (_err) {}
        });
        return xhr;
      }
      XHRProxy.prototype = OriginalXHR.prototype;
      window.XMLHttpRequest = XHRProxy;
    };

    const script = document.createElement("script");
    script.textContent = `(${hook.toString()})();`;
    document.documentElement.appendChild(script);
    script.remove();
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const msg = event.data || {};
    if (msg.source !== "avala-tracker-pro" || msg.type !== "network") return;

    const data = msg.data || {};
    if (!data) return;

    if (data.task_uid && startedTaskUid && data.task_uid !== startedTaskUid) {
      send("TASK_END", { task_uid: startedTaskUid });
      startedTaskUid = data.task_uid;
      taskPayload = taskPayload || {};
      taskPayload.task_uid = data.task_uid;
      send("TASK_START", { ...taskPayload, task_uid: data.task_uid });
    }

    const update = {
      task_uid: startedTaskUid || (taskPayload && taskPayload.task_uid) || data.task_uid,
      dataset: data.dataset,
      sequence_id: data.sequence_id,
      camera: data.camera,
      frame_start: data.frame_start,
      frame_end: data.frame_end,
      total_frames: data.total_frames,
      expected_hours: data.expected_hours
    };

    if (update.task_uid && mergeTaskPayload(update)) {
      sendTaskUpdate(update);
    }

    if (typeof data.frame_number === "number") {
      if (data.frame_number !== lastFrame) {
        lastFrame = data.frame_number;
        lastFrameChangeTs = Date.now();
      }
    }

    if (data.frame_completed && update.task_uid) {
      let createdDelta = 0;
      if (typeof data.boxes_count === "number") {
        if (lastAnnotationCount === null) lastAnnotationCount = data.boxes_count;
        createdDelta = Math.max(0, data.boxes_count - lastAnnotationCount);
        lastAnnotationCount = data.boxes_count;
      }
      send("FRAME_LOG", {
        task_uid: update.task_uid,
        frame_number: typeof lastFrame === "number" ? lastFrame : 0,
        annotations_created: createdDelta,
        annotations_deleted: 0
      });
    }
  });

  function pollGlobalStore() {
    const data = extractFromGlobalStore();
    if (!data) return;

    if (data.task_uid && startedTaskUid && data.task_uid !== startedTaskUid) {
      send("TASK_END", { task_uid: startedTaskUid });
      startedTaskUid = data.task_uid;
      taskPayload = taskPayload || {};
      taskPayload.task_uid = data.task_uid;
      send("TASK_START", { ...taskPayload, task_uid: data.task_uid });
    }

    const update = {
      task_uid: startedTaskUid || (taskPayload && taskPayload.task_uid) || data.task_uid,
      dataset: data.dataset,
      sequence_id: data.sequence_id,
      camera: data.camera
    };

    if (update.task_uid && mergeTaskPayload(update)) {
      sendTaskUpdate(update);
    }

    if (typeof data.frame_number === "number") {
      emitFrameFromPreview(data.frame_number);
    }

    if (typeof data.boxes_count === "number") {
      if (lastAnnotationCount === null) lastAnnotationCount = data.boxes_count;
      const delta = data.boxes_count - lastAnnotationCount;
      if (delta !== 0) {
        lastAnnotationCount = data.boxes_count;
        send("FRAME_LOG", {
          task_uid: update.task_uid,
          frame_number: typeof lastFrame === "number" ? lastFrame : 0,
          annotations_created: delta > 0 ? delta : 0,
          annotations_deleted: delta < 0 ? Math.abs(delta) : 0
        });
      }
    }
  }

  startTrackingForUrl(currentUrl);
  injectNetworkHook();

  const observer = new MutationObserver(() => {
    handleUrlChange();
    emitFrameIfChanged();
    emitAnnotationDelta();
  });
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true
  });

  document.addEventListener("mousemove", () => send("ACTIVITY_PING", { active: true }), { passive: true });
  document.addEventListener("keydown", () => send("ACTIVITY_PING", { active: true }), { passive: true });

  window.addEventListener("beforeunload", () => {
    if (startedTaskUid) {
      send("TASK_END", { task_uid: startedTaskUid });
    }
  });

  setInterval(handleUrlChange, 1000);
  setInterval(() => send("ACTIVITY_PING", { active: true }), 45000);
  setInterval(emitFrameIfChanged, 3000);
  setInterval(emitAnnotationDelta, 2000);
  setInterval(pollGlobalStore, 3000);
})();
