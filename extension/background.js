const API_BASE = "http://localhost:8000";
const tabTaskMap = new Map();
const DEBUG = true;
const REQUEST_TIMEOUT_MS = 5000;

function debugLog(message, extra) {
  if (!DEBUG) return;
  if (extra !== undefined) {
    console.log(`[Avala Tracker Pro] ${message}`, extra);
  } else {
    console.log(`[Avala Tracker Pro] ${message}`);
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function createTimeout(controller, timeoutMs = REQUEST_TIMEOUT_MS) {
  return setTimeout(() => controller.abort(), timeoutMs);
}

function classifyFetchError(err) {
  const message = err && err.message ? String(err.message) : "request_failed";
  if (err && err.name === "AbortError") {
    return { code: "timeout", message: "Timeout" };
  }
  if (/cors/i.test(message)) {
    return { code: "cors_error", message: "CORS blocked request" };
  }
  if (/failed to fetch|networkerror|load failed/i.test(message)) {
    return { code: "backend_unreachable", message: "Backend not reachable" };
  }
  return { code: "request_failed", message };
}

async function fetchJson(path, options = {}) {
  const controller = new AbortController();
  const timeoutId = createTimeout(controller, options.timeoutMs || REQUEST_TIMEOUT_MS);
  try {
    debugLog(`Sending request to ${path}`);
    const response = await fetch(API_BASE + path, {
      method: options.method || "GET",
      headers: options.headers || {},
      body: options.body,
      signal: controller.signal
    });
    const data = await response.json().catch(() => null);
    clearTimeout(timeoutId);
    return {
      ok: response.ok,
      status: response.status,
      data,
      snapshot_hash: data && data.snapshot_hash ? data.snapshot_hash : null
    };
  } catch (err) {
    clearTimeout(timeoutId);
    const classified = classifyFetchError(err);
    return {
      ok: false,
      status: 0,
      error: classified.code,
      message: classified.message
    };
  }
}

async function post(path, payload) {
  return fetchJson(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

async function postPaymentDebug(payload) {
  await post("/payments/debug", payload);
}

async function postExtensionHeartbeat(payload) {
  await post("/extension/heartbeat", payload);
}

function validatePaymentPayload(payload) {
  const warnings = [];
  const batches = [];
  const history = [];

  for (const batch of Array.isArray(payload && payload.batches) ? payload.batches : []) {
    const batchName = batch && typeof batch.batch_name === "string" ? batch.batch_name.trim() : "";
    const amountUsd = Number(batch && batch.amount_usd);
    if (!batchName) {
      warnings.push("Skipped batch with missing batch_name");
      continue;
    }
    if (!Number.isFinite(amountUsd)) {
      warnings.push(`Skipped batch ${batchName}: invalid data format`);
      continue;
    }
    batches.push({
      batch_name: batchName,
      amount_usd: amountUsd,
      source: batch.source || "recent_work",
      timestamp: batch.timestamp || null
    });
  }

  for (const item of Array.isArray(payload && payload.history) ? payload.history : []) {
    const paymentDate = item && item.date ? String(item.date) : "";
    const amountUsd = Number(item && item.amount_usd);
    const amountKes = Number(item && item.amount_kes);
    if (!paymentDate) {
      warnings.push("Skipped payment history entry with missing date");
      continue;
    }
    if (!Number.isFinite(amountUsd) || !Number.isFinite(amountKes)) {
      warnings.push(`Skipped payment history ${paymentDate}: invalid data format`);
      continue;
    }
    history.push({
      date: paymentDate,
      amount_usd: amountUsd,
      amount_kes: amountKes,
      status: item.status || "completed"
    });
  }

  return { batches, history, warnings };
}

async function checkBackendHealth() {
  debugLog("Checking backend health");
  const result = await fetchJson("/health");
  if (!result.ok) return result;
  if (!result.data || result.data.status !== "ok") {
    return { ok: false, status: result.status, error: "backend_unreachable", message: "Backend not reachable" };
  }
  return result;
}

async function postWithRetry(path, payload, maxAttempts = 3) {
  let lastResult = { ok: false, status: 0, error: "request_failed", message: "Request failed" };
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    debugLog(`Payment sync attempt ${attempt}/${maxAttempts}`);
    lastResult = await post(path, payload);
    debugLog("Response received", lastResult);
    if (lastResult.ok && lastResult.data && lastResult.data.status === "success") {
      return { ...lastResult, attempts: attempt };
    }
    if (attempt < maxAttempts) {
      await sleep(1000 * (2 ** (attempt - 1)));
    }
  }
  return { ...lastResult, attempts: maxAttempts };
}

function normalizeBase64Data(dataUrl) {
  if (!dataUrl || typeof dataUrl !== "string") return null;
  if (dataUrl.startsWith("data:")) {
    return dataUrl.split(",", 2)[1];
  }
  return dataUrl;
}

async function handleVisionAnalyze(payload, sender, sendResponse) {
  let imageBase64 = payload.image_base64 || null;
  if (!imageBase64 && payload.capture_screenshot && sender && sender.tab) {
    chrome.tabs.captureVisibleTab(sender.tab.windowId, { format: "png" }, async (dataUrl) => {
      if (chrome.runtime.lastError || !dataUrl) {
        sendResponse({ ok: false, error: "capture_visible_tab_failed" });
        return;
      }
      payload.image_base64 = normalizeBase64Data(dataUrl);
      try {
        const response = await fetch(API_BASE + "/vision/analyze", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => null);
        sendResponse({ ok: response.ok, status: response.status, data });
      } catch (err) {
        sendResponse({ ok: false, error: err && err.message ? err.message : "request_failed" });
      }
    });
    return;
  }

  try {
    const response = await fetch(API_BASE + "/vision/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => null);
    sendResponse({ ok: response.ok, status: response.status, data });
  } catch (err) {
    sendResponse({ ok: false, error: err && err.message ? err.message : "request_failed" });
  }
}

async function notifyTab(tabId, message) {
  if (tabId === undefined || tabId === null) return;
  try {
    await chrome.tabs.sendMessage(tabId, message);
  } catch (_err) {}
}

async function handlePaymentSync(payload, sender) {
  const debug = payload && payload.debug ? { ...payload.debug } : {};
  debugLog("Starting payment scrape");
  const syncPayload = validatePaymentPayload({
    batches: Array.isArray(payload && payload.recent_work) ? payload.recent_work : [],
    history: Array.isArray(payload && payload.payment_history) ? payload.payment_history : []
  });
  debugLog("Scrape complete", syncPayload);

  const health = await checkBackendHealth();
  if (!health.ok) {
    const failure = {
      ok: false,
      status: health.status || 0,
      error: health.error || "backend_unreachable",
      message: health.message || "Backend not reachable",
      data: {
        status: "error",
        detail: "Payment sync failed",
        message: health.message || "Backend not reachable",
        batches: { inserted: 0, updated: 0, unchanged: 0, total: syncPayload.batches.length },
        history: { inserted: 0, updated: 0, unchanged: 0, total: syncPayload.history.length },
        warnings: syncPayload.warnings
      }
    };
    await postPaymentDebug({
      sync_key: "payments_dashboard",
      page_url: debug.page_url || null,
      page_detected: !!debug.page_detected,
      recent_work_section_found: !!debug.recent_work_section_found,
      payment_history_section_found: !!debug.payment_history_section_found,
      recent_work_rows: Number(debug.recent_work_rows || syncPayload.batches.length || 0),
      payment_history_rows: Number(debug.payment_history_rows || syncPayload.history.length || 0),
      page_fingerprint: debug.page_fingerprint || null,
      last_status: "backend_error",
      last_error: failure.message,
      backend_status_code: health.status || null
    });
    await notifyTab(sender && sender.tab ? sender.tab.id : null, {
      type: "PAYMENTS_SYNC_RESULT",
      payload: failure,
    });
    return failure;
  }

  if (!syncPayload.batches.length && !syncPayload.history.length) {
    const emptyResult = {
      ok: true,
      status: 200,
      data: {
        status: "success",
        detail: "No valid payment rows to sync",
        message: "No valid payment rows to sync",
        batches: { inserted: 0, updated: 0, unchanged: 0, total: 0 },
        history: { inserted: 0, updated: 0, unchanged: 0, total: 0 },
        warnings: syncPayload.warnings.length ? syncPayload.warnings : ["Empty scrape result"]
      }
    };
    await notifyTab(sender && sender.tab ? sender.tab.id : null, {
      type: "PAYMENTS_SYNC_RESULT",
      payload: emptyResult,
    });
    return emptyResult;
  }

  debugLog("Sending to backend");
  const result = await postWithRetry("/payments/sync-full", syncPayload, 3);
  if (result.ok && (!result.data || result.data.status !== "success")) {
    result.ok = false;
    result.error = "invalid_backend_response";
    result.message = (result.data && (result.data.message || result.data.detail)) || "Invalid data format";
  }
  if (syncPayload.warnings.length) {
    result.data = result.data || {};
    result.data.warnings = [...syncPayload.warnings, ...(Array.isArray(result.data.warnings) ? result.data.warnings : [])];
  }
  await postPaymentDebug({
    sync_key: "payments_dashboard",
    page_url: debug.page_url || null,
    page_detected: !!debug.page_detected,
    recent_work_section_found: !!debug.recent_work_section_found,
    payment_history_section_found: !!debug.payment_history_section_found,
    recent_work_rows: Number(debug.recent_work_rows || syncPayload.batches.length || 0),
    payment_history_rows: Number(debug.payment_history_rows || syncPayload.history.length || 0),
    page_fingerprint: debug.page_fingerprint || result.snapshot_hash || null,
    last_status: result.ok
      ? ((syncPayload.batches.length || syncPayload.history.length) ? "synced" : (debug.last_status || "waiting_for_sync"))
      : "backend_error",
    last_error: result.ok ? null : (result.message || result.error || `http_${result.status}`),
    backend_status_code: result.status || null
  });
  await notifyTab(sender && sender.tab ? sender.tab.id : null, {
    type: "PAYMENTS_SYNC_RESULT",
    payload: result,
  });
  return result;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
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
    handlePaymentSync(msg.payload, sender).then((result) => {
      sendResponse(result);
    }).catch((err) => {
      sendResponse({ ok: false, status: 0, error: err && err.message ? err.message : "request_failed" });
    });
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

  if (msg.type === "VISION_ANALYZE" && msg.payload) {
    handleVisionAnalyze(msg.payload, sender, sendResponse);
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
