const API_BASE = "http://localhost:8000";
const tabTaskMap = new Map();

async function post(path, payload) {
  try {
    await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
  } catch (_err) {
    // Local backend may be offline temporarily.
  }
}

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!msg || !msg.type) return;

  if (msg.type === "TASK_START" && msg.payload) {
    if (sender.tab && sender.tab.id !== undefined) {
      tabTaskMap.set(sender.tab.id, msg.payload.task_uid);
    }
    post("/task/start", msg.payload);
  }

  if (msg.type === "FRAME_LOG" && msg.payload) {
    post("/frame/log", msg.payload);
  }

  if (msg.type === "ACTIVITY_PING" && msg.payload) {
    post("/activity/ping", { source: "extension", active: !!msg.payload.active });
  }

  if (msg.type === "TASK_END" && msg.payload) {
    post("/task/end", msg.payload);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  const taskUid = tabTaskMap.get(tabId);
  if (!taskUid) return;
  post("/task/end", { task_uid: taskUid });
  tabTaskMap.delete(tabId);
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== "loading") return;
  const url = tab.url || "";
  const onAvala = url.includes("avala.ai/datasets/") || url.includes("avala.ai/work_batches/");
  if (!onAvala) {
    const taskUid = tabTaskMap.get(tabId);
    if (taskUid) {
      post("/task/end", { task_uid: taskUid });
      tabTaskMap.delete(tabId);
    }
  }
});
