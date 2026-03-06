(function () {
  if (!window.AvalaTaskDetector) return;

  const detector = window.AvalaTaskDetector;
  let currentUrl = window.location.href;
  let taskPayload = null;
  let startedTaskUid = null;
  let lastFrame = null;

  function send(type, payload) {
    try {
      chrome.runtime.sendMessage({ type, payload });
    } catch (_err) {}
  }

  const emitFrameIfChanged = () => {
    if (!taskPayload) return;
    const frame = detector.extractFrameNumberFromDom();
    if (typeof frame !== "number" || frame === lastFrame) return;
    lastFrame = frame;
    send("FRAME_LOG", {
      task_uid: taskPayload.task_uid,
      frame_number: frame,
      annotations_created: 0,
      annotations_deleted: 0
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
    if (startedTaskUid !== parsed.task_uid) {
      send("TASK_START", parsed);
      startedTaskUid = parsed.task_uid;
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
      return;
    }

    if (taskPayload && startedTaskUid) send("TASK_END", { task_uid: startedTaskUid });
    currentUrl = next;
    startedTaskUid = null;
    if (nextParsed) {
      taskPayload = nextParsed;
      send("TASK_START", nextParsed);
      startedTaskUid = nextParsed.task_uid;
      lastFrame = null;
    } else {
      startTrackingForUrl(next);
    }
  }

  startTrackingForUrl(currentUrl);

  const observer = new MutationObserver(() => {
    handleUrlChange();
    emitFrameIfChanged();
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
})();
