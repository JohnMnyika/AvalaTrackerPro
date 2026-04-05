const API_BASE = "http://localhost:8000";
const tabTaskMap = new Map();

async function post(path, payload) {
  try {
    const response = await fetch(API_BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return { ok: response.ok, status: response.status };
  } catch (err) {
    return { ok: false, status: 0, error: err && err.message ? err.message : "request_failed" };
  }
}

async function postPaymentDebug(payload) {
  await post("/payments/debug", payload);
}

async function postExtensionHeartbeat(payload) {
  await post("/extension/heartbeat", payload);
}

async function handlePaymentSync(payload) {
  const debug = payload && payload.debug ? { ...payload.debug } : {};
  const syncPayload = {
    recent_work: Array.isArray(payload && payload.recent_work) ? payload.recent_work : [],
    payment_history: Array.isArray(payload && payload.payment_history) ? payload.payment_history : []
  };
  const result = await post("/payments/sync", syncPayload);
  await postPaymentDebug({
    sync_key: "payments_dashboard",
    page_url: debug.page_url || null,
    page_detected: !!debug.page_detected,
    recent_work_section_found: !!debug.recent_work_section_found,
    payment_history_section_found: !!debug.payment_history_section_found,
    recent_work_rows: Number(debug.recent_work_rows || syncPayload.recent_work.length || 0),
    payment_history_rows: Number(debug.payment_history_rows || syncPayload.payment_history.length || 0),
    page_fingerprint: debug.page_fingerprint || null,
    last_status: result.ok
      ? ((syncPayload.recent_work.length || syncPayload.payment_history.length) ? "synced" : (debug.last_status || "waiting_for_sync"))
      : "backend_error",
    last_error: result.ok ? null : (result.error || `http_${result.status}`),
    backend_status_code: result.status || null
  });
}

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!msg || !msg.type) return false;

  if (msg.type === "TASK_START" && msg.payload) {
    if (sender.tab && sender.tab.id !== undefined) {
      tabTaskMap.set(sender.tab.id, msg.payload.task_uid);
    }
    post("/task/start", msg.payload);
    return true;
  }

  if (msg.type === "TASK_UPDATE" && msg.payload) {
    post("/task/update", msg.payload);
    return true;
  }

  if (msg.type === "FRAME_LOG" && msg.payload) {
    post("/frame/log", msg.payload);
    return true;
  }

  if (msg.type === "ACTIVITY_PING" && msg.payload) {
    post("/activity/ping", { source: "extension", active: !!msg.payload.active });
    return true;
  }

  if (msg.type === "EXTENSION_HEARTBEAT" && msg.payload) {
    postExtensionHeartbeat(msg.payload);
    return true;
  }

  if (msg.type === "CONTRIBUTIONS_SYNC" && msg.payload) {
    post("/contributions/sync", msg.payload);
    return true;
  }

  if (msg.type === "PAYMENTS_SYNC" && msg.payload) {
    handlePaymentSync(msg.payload);
    return true;
  }

  if (msg.type === "PAYMENT_BATCH_ADD" && msg.payload) {
    post("/payments/add-batch", msg.payload);
    return true;
  }

  if (msg.type === "PAYMENT_HISTORY_ADD" && msg.payload) {
    post("/payments/add-history", msg.payload);
    return true;
  }

  if (msg.type === "TASK_END" && msg.payload) {
    post("/task/end", msg.payload);
    return true;
  }

  return false;
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
