(function () {
  if (window.__avalaTrackerBridgeInstalled) return;
  window.__avalaTrackerBridgeInstalled = true;

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
})();
