(function () {
  if (!window.AvalaTaskDetector) return;

  const detector = window.AvalaTaskDetector;
  let currentUrl = window.location.href;
  let taskPayload = null;
  let startedTaskUid = null;
  let lastFrame = null;
  let lastAnnotationCount = null;
  let lastFrameChangeTs = 0;
  let lastContributionSyncHash = null;
  let lastPaymentsSyncHash = null;
  let paymentSyncTimer = null;

  const isTaskPage = () => detector.isTaskUrl(window.location.href);
  const isProfilePage = () => detector.isProfileUrl(window.location.href);
  const isPaymentPage = () => detector.isPaymentDashboardUrl(window.location.href);

  async function postDirect(path, payload) {
    try {
      await fetch(`http://localhost:8000${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    } catch (_err) {}
  }

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

  function currentPageType() {
    if (isPaymentPage()) return "payments";
    if (isTaskPage()) return "task";
    if (isProfilePage()) return "profile";
    return "other";
  }

  function sendExtensionHeartbeat() {
    const payload = {
      client_key: "primary",
      page_url: window.location.href,
      page_type: currentPageType(),
      source: "content_script"
    };
    send("EXTENSION_HEARTBEAT", payload);
    postDirect("/extension/heartbeat", payload);
  }

  function syncContributionDays() {
    if (!isProfilePage()) return;
    const days = detector.extractContributionDaysFromDom();
    if (!days || !days.length) return;
    const nextHash = JSON.stringify(days);
    if (nextHash === lastContributionSyncHash) return;
    lastContributionSyncHash = nextHash;
    send("CONTRIBUTIONS_SYNC", { days });
  }

  function syncPayments() {
    if (!isPaymentPage()) return;
    const payload = detector.extractPaymentsFromDom();
    if (!payload) return;
    const recentWork = Array.isArray(payload.recent_work) ? payload.recent_work : [];
    const paymentHistory = Array.isArray(payload.payment_history) ? payload.payment_history : [];
    const debug = payload.debug || {
      page_detected: true,
      page_url: window.location.href,
      recent_work_section_found: false,
      payment_history_section_found: false,
      recent_work_rows: recentWork.length,
      payment_history_rows: paymentHistory.length,
      last_status: "waiting_for_sync"
    };
    const nextHash = JSON.stringify({ recentWork, paymentHistory, debug });
    if (nextHash === lastPaymentsSyncHash) return;
    lastPaymentsSyncHash = nextHash;
    send("PAYMENTS_SYNC", { recent_work: recentWork, payment_history: paymentHistory, debug });
  }

  function schedulePaymentsSync(delay = 250) {
    if (!isPaymentPage()) return;
    if (paymentSyncTimer) {
      clearTimeout(paymentSyncTimer);
    }
    paymentSyncTimer = window.setTimeout(() => {
      paymentSyncTimer = null;
      syncPayments();
    }, delay);
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
    // Skip tracking unknown-dataset or unknown camera tasks
    if (parsed.dataset === "unknown-dataset" || parsed.camera === "unknown") return;
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

    // Skip tracking unknown-dataset or unknown camera tasks
    const isUnknown = nextParsed && (nextParsed.dataset === "unknown-dataset" || nextParsed.camera === "unknown");

    // Ignore SPA URL churn that keeps the same logical task.
    if (startedTaskUid && nextUid && startedTaskUid === nextUid) {
      currentUrl = next;
      if (!isUnknown) {
        taskPayload = nextParsed;
        if (nextParsed && Number.isFinite(nextParsed.preview_frame)) {
          emitFrameFromPreview(nextParsed.preview_frame);
        }
      }
      return;
    }

    if (taskPayload && startedTaskUid) send("TASK_END", { task_uid: startedTaskUid });
    currentUrl = next;
    startedTaskUid = null;
    lastAnnotationCount = null;
    if (nextParsed && !isUnknown) {
      taskPayload = nextParsed;
      send("TASK_START", nextParsed);
      startedTaskUid = nextParsed.task_uid;
      lastFrame = null;
      if (Number.isFinite(nextParsed.preview_frame)) {
        emitFrameFromPreview(nextParsed.preview_frame);
      }
    } else if (!isUnknown) {
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
    if (document.getElementById("avala-tracker-bridge")) return;
    const script = document.createElement("script");
    script.id = "avala-tracker-bridge";
    script.src = chrome.runtime.getURL("page_bridge.js");
    script.onload = () => script.remove();
    (document.head || document.documentElement).appendChild(script);
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

  const observer = new MutationObserver((mutations) => {
    handleUrlChange();
    if (isTaskPage()) {
      emitFrameIfChanged();
      emitAnnotationDelta();
    }
    if (isProfilePage()) {
      syncContributionDays();
    }
    if (isPaymentPage()) {
      const shouldSyncPayments = mutations.some((mutation) => {
        if (detector.isPaymentMutationRelevant(mutation.target)) return true;
        return Array.from(mutation.addedNodes || []).some(
          (node) => node instanceof Element && detector.isPaymentMutationRelevant(node)
        );
      });
      if (shouldSyncPayments) {
        schedulePaymentsSync(100);
      }
    }
  });
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true
  });

  document.addEventListener("mousemove", () => send("ACTIVITY_PING", { active: true }), { passive: true });
  document.addEventListener("keydown", () => send("ACTIVITY_PING", { active: true }), { passive: true });

  window.addEventListener("beforeunload", () => {
    if (startedTaskUid) {
      send("TASK_END", { task_uid: startedTaskUid });
    }
  });

  window.addEventListener("load", () => {
    sendExtensionHeartbeat();
    schedulePaymentsSync(0);
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      sendExtensionHeartbeat();
      schedulePaymentsSync(0);
    }
  });

  setInterval(handleUrlChange, 1000);
  setInterval(sendExtensionHeartbeat, 30000);
  setInterval(() => send("ACTIVITY_PING", { active: true }), 45000);
  setInterval(() => { if (isTaskPage()) emitFrameIfChanged(); }, 3000);
  setInterval(() => { if (isTaskPage()) emitAnnotationDelta(); }, 2000);
  setInterval(() => { if (isTaskPage()) pollGlobalStore(); }, 3000);
  setInterval(() => { if (isProfilePage()) syncContributionDays(); }, 4000);
  setInterval(() => { if (isPaymentPage()) syncPayments(); }, 2000);
  sendExtensionHeartbeat();
  syncContributionDays();
  schedulePaymentsSync(0);
  setTimeout(() => schedulePaymentsSync(500), 500);
  setTimeout(() => schedulePaymentsSync(1500), 1500);
})();
