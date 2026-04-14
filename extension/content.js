(function () {
  if (!window.AvalaTaskDetector) return;

  const detector = window.AvalaTaskDetector;
  const DEBUG = true;
  const PENDING_PAYMENTS_STORAGE_KEY = "pendingPayments";
  let currentUrl = window.location.href;
  let taskPayload = null;
  let startedTaskUid = null;
  let lastFrame = null;
  let lastAnnotationCount = null;
  let lastFrameChangeTs = 0;
  let lastContributionSyncHash = null;
  let lastPaymentsSyncHash = null;
  let previousPaymentsSnapshot = null;
  let paymentSyncTimer = null;
  let paymentSyncInFlight = false;
  let pendingPaymentSyncReason = null;
  let paymentObserver = null;
  let lastExtractedExistingBoxes = [];

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

  function logPaymentSync(message, extra) {
    if (!DEBUG) return;
    if (extra !== undefined) {
      console.debug(`[Avala Tracker Pro] ${message}`, extra);
    } else {
      console.debug(`[Avala Tracker Pro] ${message}`);
    }
  }

  function savePendingPayments(payload, reason) {
    try {
      localStorage.setItem(PENDING_PAYMENTS_STORAGE_KEY, JSON.stringify({
        payload,
        reason,
        failed_at: new Date().toISOString()
      }));
    } catch (_err) {}
  }

  function clearPendingPayments() {
    try {
      localStorage.removeItem(PENDING_PAYMENTS_STORAGE_KEY);
    } catch (_err) {}
  }

  function loadPendingPayments() {
    try {
      const raw = localStorage.getItem(PENDING_PAYMENTS_STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_err) {
      return null;
    }
  }

  function getSyncFailureMessage(result) {
    const data = result && result.data ? result.data : {};
    const message = data.message || result.message || result.error || "Unknown error";
    if (/backend_unreachable|health_check_failed/i.test(String(result.error || "")) || /backend not reachable/i.test(String(message))) {
      return "Sync failed: Backend not reachable";
    }
    if (/timeout/i.test(String(result.error || "")) || /timeout/i.test(String(message))) {
      return "Sync failed: Timeout";
    }
    if (/invalid/i.test(String(result.error || "")) || /invalid data format/i.test(String(message))) {
      return "Sync failed: Invalid data format";
    }
    if (/cors/i.test(String(result.error || "")) || /cors/i.test(String(message))) {
      return "Sync failed: CORS blocked request";
    }
    return `Sync failed: ${message}`;
  }

  function ensurePaymentSyncIndicator() {
    let indicator = document.getElementById("avala-tracker-payment-sync-indicator");
    if (indicator) return indicator;
    indicator = document.createElement("div");
    indicator.id = "avala-tracker-payment-sync-indicator";
    indicator.style.position = "fixed";
    indicator.style.right = "16px";
    indicator.style.bottom = "16px";
    indicator.style.zIndex = "2147483647";
    indicator.style.padding = "10px 14px";
    indicator.style.borderRadius = "999px";
    indicator.style.background = "rgba(15, 23, 42, 0.92)";
    indicator.style.color = "#f8fafc";
    indicator.style.fontSize = "13px";
    indicator.style.lineHeight = "1.2";
    indicator.style.fontWeight = "600";
    indicator.style.boxShadow = "0 14px 35px rgba(15, 23, 42, 0.25)";
    indicator.style.border = "1px solid rgba(148, 163, 184, 0.3)";
    indicator.style.transition = "opacity 180ms ease, transform 180ms ease";
    indicator.style.opacity = "0";
    indicator.style.transform = "translateY(8px)";
    indicator.style.pointerEvents = "none";
    document.documentElement.appendChild(indicator);
    return indicator;
  }

  function showPaymentSyncIndicator(message, tone = "neutral") {
    if (!isPaymentPage()) return;
    const indicator = ensurePaymentSyncIndicator();
    const themes = {
      neutral: { background: "rgba(15, 23, 42, 0.92)", border: "rgba(148, 163, 184, 0.3)" },
      success: { background: "rgba(6, 95, 70, 0.95)", border: "rgba(110, 231, 183, 0.42)" },
      warning: { background: "rgba(146, 64, 14, 0.95)", border: "rgba(251, 191, 36, 0.42)" },
      error: { background: "rgba(153, 27, 27, 0.95)", border: "rgba(252, 165, 165, 0.42)" }
    };
    const theme = themes[tone] || themes.neutral;
    indicator.textContent = message;
    indicator.style.background = theme.background;
    indicator.style.borderColor = theme.border;
    indicator.style.opacity = "1";
    indicator.style.transform = "translateY(0)";
    clearTimeout(window.__avalaPaymentIndicatorTimer);
    window.__avalaPaymentIndicatorTimer = window.setTimeout(() => {
      indicator.style.opacity = "0";
      indicator.style.transform = "translateY(8px)";
    }, tone === "neutral" ? 2500 : 3500);
  }

  function buildPaymentSnapshot(recentWork, paymentHistory) {
    return {
      recentWork: (recentWork || []).map((item) => ({
        batch_name: item.batch_name,
        amount_usd: Number(item.amount_usd || 0)
      })).sort((a, b) => a.batch_name.localeCompare(b.batch_name)),
      paymentHistory: (paymentHistory || []).map((item) => ({
        date: item.date,
        amount_usd: Number(item.amount_usd || 0),
        amount_kes: Number(item.amount_kes || 0),
        status: String(item.status || "completed").toLowerCase()
      })).sort((a, b) => a.date.localeCompare(b.date))
    };
  }

  function summarizeSnapshotChanges(previousSnapshot, currentSnapshot) {
    const summary = { added: 0, updated: 0, unchanged: 0 };
    if (!currentSnapshot) return summary;

    const previousBatchMap = new Map((previousSnapshot && previousSnapshot.recentWork || []).map((item) => [item.batch_name, item]));
    for (const item of currentSnapshot.recentWork || []) {
      const previous = previousBatchMap.get(item.batch_name);
      if (!previous) {
        summary.added += 1;
      } else if (previous.amount_usd !== item.amount_usd) {
        summary.updated += 1;
      } else {
        summary.unchanged += 1;
      }
    }

    const previousHistoryMap = new Map((previousSnapshot && previousSnapshot.paymentHistory || []).map((item) => [item.date, item]));
    for (const item of currentSnapshot.paymentHistory || []) {
      const previous = previousHistoryMap.get(item.date);
      if (!previous) {
        summary.added += 1;
        continue;
      }
      const changed = previous.amount_usd !== item.amount_usd
        || previous.amount_kes !== item.amount_kes
        || previous.status !== item.status;
      if (changed) {
        summary.updated += 1;
      } else {
        summary.unchanged += 1;
      }
    }
    return summary;
  }

  function sendPaymentsSyncRequest(payload) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage({ type: "PAYMENTS_SYNC", payload }, (response) => {
          if (chrome.runtime.lastError) {
            resolve({ ok: false, status: 0, error: chrome.runtime.lastError.message || "request_failed" });
            return;
          }
          resolve(response || { ok: false, status: 0, error: "empty_response" });
        });
      } catch (err) {
        resolve({ ok: false, status: 0, error: err && err.message ? err.message : "request_failed" });
      }
    });
  }

  function showErrorNotification(message) {
    showPaymentSyncIndicator(message, "error");
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

  function getExistingBoxesFromDom() {
    const boxes = [];
    const seen = new Set();
    const width = window.innerWidth || document.documentElement.clientWidth || 1;
    const height = window.innerHeight || document.documentElement.clientHeight || 1;

    // Visual selectors: require dimensions > 0
    const visualSelectors = [
      "svg rect",
      "svg polygon",
      "svg path",
      "[class*='annotation']",
      "[class*='box']",
      "[class*='cuboid']"
    ];

    // Data-attribute selectors: don't require dimensions (may be stored/hidden)
    const dataSelectors = [
      "[data-annotation-id]",
      "[data-testid*='annotation']",
      "[data-box-id]",
      "[data-bbox]",
      "[data-bounds]"
    ];

    // Extract from visual elements
    for (const selector of visualSelectors) {
      const elements = document.querySelectorAll(selector);
      for (const el of elements) {
        if (!(el instanceof Element)) continue;
        const rect = el.getBoundingClientRect();
        if (!rect.width || !rect.height) continue;
        const x1 = Math.max(0, rect.left);
        const y1 = Math.max(0, rect.top);
        const x2 = x1 + rect.width;
        const y2 = y1 + rect.height;
        const normalized = [
          Math.min(1, Math.max(0, x1 / width)),
          Math.min(1, Math.max(0, y1 / height)),
          Math.min(1, Math.max(0, x2 / width)),
          Math.min(1, Math.max(0, y2 / height))
        ];
        const key = normalized.map((v) => v.toFixed(4)).join(",");
        if (seen.has(key)) continue;
        seen.add(key);
        const label = el.getAttribute("data-label") || el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("class") || "annotation";
        boxes.push({ label, box: normalized });
      }
    }

    // Extract from data-attribute elements (may be hidden/stored)
    for (const selector of dataSelectors) {
      const elements = document.querySelectorAll(selector);
      for (const el of elements) {
        if (!(el instanceof Element)) continue;
        const rect = el.getBoundingClientRect();
        // For data attributes, extract even with zero dimensions or try to parse from attributes
        let normalized;
        if (rect.width && rect.height) {
          const x1 = Math.max(0, rect.left);
          const y1 = Math.max(0, rect.top);
          const x2 = x1 + rect.width;
          const y2 = y1 + rect.height;
          normalized = [
            Math.min(1, Math.max(0, x1 / width)),
            Math.min(1, Math.max(0, y1 / height)),
            Math.min(1, Math.max(0, x2 / width)),
            Math.min(1, Math.max(0, y2 / height))
          ];
        } else {
          // Try to parse coordinates from data attributes
          const bboxAttr = el.getAttribute("data-bbox") || el.getAttribute("data-bounds");
          if (bboxAttr) {
            try {
              const coords = JSON.parse(bboxAttr);
              if (Array.isArray(coords) && coords.length >= 4) {
                normalized = [coords[0], coords[1], coords[2], coords[3]].map(v => Math.max(0, Math.min(1, v)));
              }
            } catch (e) {
              // Parse failed, skip
              continue;
            }
          } else {
            // No visible dimensions and no bbox attr, skip
            continue;
          }
        }
        const key = normalized.map((v) => v.toFixed(4)).join(",");
        if (seen.has(key)) continue;
        seen.add(key);
        const label = el.getAttribute("data-label") || el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("class") || "annotation";
        boxes.push({ label, box: normalized });
      }
    }

    if (lastExtractedExistingBoxes && Array.isArray(lastExtractedExistingBoxes) && lastExtractedExistingBoxes.length) {
      for (const box of lastExtractedExistingBoxes) {
        if (!box || !Array.isArray(box.box) || box.box.length !== 4) continue;
        const raw = box.box.map((value) => Number(value) || 0);
        let normalized = raw;
        if (raw.some((value) => value > 1)) {
          normalized = [
            Math.min(1, Math.max(0, raw[0] / width)),
            Math.min(1, Math.max(0, raw[1] / height)),
            Math.min(1, Math.max(0, raw[2] / width)),
            Math.min(1, Math.max(0, raw[3] / height)),
          ];
        }
        const key = normalized.map((v) => v.toFixed(4)).join(",");
        if (seen.has(key)) continue;
        seen.add(key);
        boxes.push({ label: box.label || "annotation", box: normalized });
      }
    }
    return boxes;
  }

  function createVisionOverlay() {
    let overlay = document.getElementById("avala-tracker-vision-overlay");
    if (overlay) return overlay;
    overlay = document.createElement("div");
    overlay.id = "avala-tracker-vision-overlay";
    overlay.style.position = "fixed";
    overlay.style.pointerEvents = "none";
    overlay.style.top = "0";
    overlay.style.left = "0";
    overlay.style.width = "100%";
    overlay.style.height = "100%";
    overlay.style.zIndex = "2147483646";
    overlay.style.mixBlendMode = "normal";
    overlay.style.overflow = "visible";
    document.documentElement.appendChild(overlay);
    return overlay;
  }

  function clearVisionOverlay() {
    const overlay = document.getElementById("avala-tracker-vision-overlay");
    if (overlay) overlay.remove();
  }

  function updateVisionOverlay(suggestions, detectedBoxes) {
    const overlay = createVisionOverlay();
    overlay.innerHTML = "";
    const panel = document.createElement("div");
    panel.style.position = "fixed";
    panel.style.top = "12px";
    panel.style.right = "12px";
    panel.style.maxWidth = "320px";
    panel.style.padding = "12px";
    panel.style.background = "rgba(17, 24, 39, 0.92)";
    panel.style.color = "#f8fafc";
    panel.style.border = "1px solid rgba(148, 163, 184, 0.35)";
    panel.style.borderRadius = "14px";
    panel.style.boxShadow = "0 18px 60px rgba(15, 23, 42, 0.35)";
    panel.style.fontSize = "13px";
    panel.style.lineHeight = "1.45";
    panel.style.pointerEvents = "auto";
    panel.style.zIndex = "2147483647";
    panel.innerHTML = `
      <strong style="display:block;margin-bottom:8px;font-size:15px;">Avala Vision Suggestions</strong>
      <div style="margin-bottom:8px;">Detected ${detectedBoxes.length} boxes, suggested ${suggestions.length} improvements.</div>
      <button id="avala-tracker-copy-suggestions" style="width:100%;padding:8px 10px;border:none;border-radius:10px;background:#5b21b6;color:#fff;cursor:pointer;">Copy suggestion payload</button>
      <div id="avala-tracker-suggestion-list" style="margin-top:10px;max-height:260px;overflow:auto;"></div>
    `;
    overlay.appendChild(panel);

    const list = panel.querySelector("#avala-tracker-suggestion-list");
    if (suggestions.length === 0) {
      list.innerHTML = "<div style='color:#94a3b8;'>No high-confidence box updates detected yet.</div>";
    } else {
      suggestions.slice(0, 6).forEach((suggestion, index) => {
        const item = document.createElement("div");
        item.style.padding = "8px";
        item.style.marginBottom = "8px";
        item.style.border = "1px solid rgba(148, 163, 184, 0.15)";
        item.style.borderRadius = "10px";
        item.style.background = "rgba(255, 255, 255, 0.04)";
        item.innerHTML = `
          <div><strong>#${index + 1}</strong> ${suggestion.label || "object"}</div>
          <div style="font-size:12px;color:#cbd5e1;">IOU: ${suggestion.iou} · improvement: ${suggestion.area_improvement}</div>
        `;
        list.appendChild(item);
      });
      if (suggestions.length > 6) {
        const more = document.createElement("div");
        more.style.color = "#94a3b8";
        more.style.fontSize = "12px";
        more.innerText = `and ${suggestions.length - 6} more suggestions...`;
        list.appendChild(more);
      }
    }

    const copyButton = panel.querySelector("#avala-tracker-copy-suggestions");
    copyButton.addEventListener("click", () => {
      const payload = JSON.stringify({ suggestions }, null, 2);
      navigator.clipboard.writeText(payload).then(() => {
        copyButton.innerText = "Copied to clipboard";
        setTimeout(() => { copyButton.innerText = "Copy suggestion payload"; }, 2000);
      }).catch(() => {
        copyButton.innerText = "Copy failed";
      });
    });

    detectedBoxes.forEach((box) => {
      if (!Array.isArray(box.box) || box.box.length !== 4) return;
      const [x1, y1, x2, y2] = box.box;
      const guide = document.createElement("div");
      guide.style.position = "fixed";
      guide.style.left = `${Math.round(x1 * 100)}vw`;
      guide.style.top = `${Math.round(y1 * 100)}vh`;
      guide.style.width = `${Math.round((x2 - x1) * 100)}vw`;
      guide.style.height = `${Math.round((y2 - y1) * 100)}vh`;
      guide.style.border = "2px dashed rgba(56, 189, 248, 0.85)";
      guide.style.background = "rgba(56, 189, 248, 0.12)";
      guide.style.pointerEvents = "none";
      guide.style.zIndex = "2147483646";
      overlay.appendChild(guide);
    });

    suggestions.forEach((box) => {
      if (!Array.isArray(box.detected_box) || box.detected_box.length !== 4) return;
      const [x1, y1, x2, y2] = box.detected_box;
      const guide = document.createElement("div");
      guide.style.position = "fixed";
      guide.style.left = `${Math.round(x1 * 100)}vw`;
      guide.style.top = `${Math.round(y1 * 100)}vh`;
      guide.style.width = `${Math.round((x2 - x1) * 100)}vw`;
      guide.style.height = `${Math.round((y2 - y1) * 100)}vh`;
      guide.style.border = "2px solid rgba(251, 146, 60, 0.95)";
      guide.style.background = "rgba(251, 146, 60, 0.12)";
      guide.style.pointerEvents = "none";
      guide.style.zIndex = "2147483646";
      overlay.appendChild(guide);
    });
  }

  function requestVisionSuggestions() {
    if (!isTaskPage() || !startedTaskUid) return;
    const existing_boxes = lastExtractedExistingBoxes && lastExtractedExistingBoxes.length ? lastExtractedExistingBoxes : getExistingBoxesFromDom();
    const payload = {
      task_uid: startedTaskUid,
      frame_number: typeof lastFrame === "number" ? lastFrame : 0,
      existing_boxes,
      width: window.innerWidth || document.documentElement.clientWidth || 0,
      height: window.innerHeight || document.documentElement.clientHeight || 0,
      capture_screenshot: true,
    };

    const overlay = document.getElementById("avala-tracker-vision-overlay");
    if (overlay) {
      overlay.dataset.previousDisplay = overlay.style.display || "";
      overlay.style.display = "none";
    }

    chrome.runtime.sendMessage({ type: "VISION_ANALYZE", payload }, (response) => {
      if (overlay) {
        overlay.style.display = overlay.dataset.previousDisplay || "";
      }
      if (!response || !response.ok || !response.data) {
        return;
      }
      updateVisionOverlay(response.data.suggestions || [], response.data.detected_boxes || []);
    });
  }

  function scheduleVisionSuggestions(delay = 5000) {
    if (window.__avalaTrackerVisionTimer) {
      clearTimeout(window.__avalaTrackerVisionTimer);
    }
    window.__avalaTrackerVisionTimer = window.setTimeout(() => {
      requestVisionSuggestions();
    }, delay);
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

  async function syncPayments(reason = "manual") {
    if (!isPaymentPage()) return;
    if (paymentSyncInFlight) {
      pendingPaymentSyncReason = reason;
      return;
    }
    let currentSnapshot = null;
    try {
      logPaymentSync("Starting payment scrape");
      const payload = detector.extractPaymentsFromDom();
      if (!payload) return;
      const pendingSync = loadPendingPayments();
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
      const outboundPayload = (recentWork.length || paymentHistory.length)
        ? { recent_work: recentWork, payment_history: paymentHistory, debug }
        : (pendingSync && pendingSync.payload ? pendingSync.payload : { recent_work: recentWork, payment_history: paymentHistory, debug });

      logPaymentSync("Scrape complete", outboundPayload);
      currentSnapshot = buildPaymentSnapshot(outboundPayload.recent_work, outboundPayload.payment_history);
      const nextHash = JSON.stringify(currentSnapshot);
      const shouldForce = reason === "visibility" || reason === "interval" || reason === "initial";
      if (!shouldForce && nextHash === lastPaymentsSyncHash) return;

      const snapshotChanges = summarizeSnapshotChanges(previousPaymentsSnapshot, currentSnapshot);
      if (snapshotChanges.added || snapshotChanges.updated) {
        logPaymentSync("Change detected", { reason, snapshotChanges });
      }

      paymentSyncInFlight = true;
      lastPaymentsSyncHash = nextHash;
      showPaymentSyncIndicator("Syncing...", "neutral");
      logPaymentSync("Sync triggered", { reason, batches: outboundPayload.recent_work.length, history: outboundPayload.payment_history.length });
      logPaymentSync("Before API request");

      const result = await sendPaymentsSyncRequest(outboundPayload);
      logPaymentSync("After API response", result);

      if (result && result.ok && result.data && result.data.status === "success") {
        previousPaymentsSnapshot = currentSnapshot;
        clearPendingPayments();
        const data = result.data || {};
        const inserted = Number(((data.batches && data.batches.inserted) || 0) + ((data.history && data.history.inserted) || 0));
        const updated = Number(((data.batches && data.batches.updated) || 0) + ((data.history && data.history.updated) || 0));
        if (inserted > 0) {
          logPaymentSync("New payment added", { inserted });
        }
        if (updated > 0) {
          logPaymentSync("Payment updated", { updated });
        }
        if (inserted || updated) {
          showPaymentSyncIndicator("Sync successful", "success");
        } else {
          showPaymentSyncIndicator("No changes detected", "warning");
        }
      } else {
        lastPaymentsSyncHash = null;
        const failureMessage = getSyncFailureMessage(result || {});
        savePendingPayments(outboundPayload, failureMessage);
        showErrorNotification(failureMessage);
        logPaymentSync("On error", result);
      }
    } catch (error) {
      lastPaymentsSyncHash = null;
      const failureMessage = getSyncFailureMessage({ error: error && error.message ? error.message : "request_failed", message: error && error.message ? error.message : "request_failed" });
      savePendingPayments({
        recent_work: currentSnapshot ? currentSnapshot.recentWork : [],
        payment_history: currentSnapshot ? currentSnapshot.paymentHistory : [],
        debug: { page_url: window.location.href, page_detected: true }
      }, failureMessage);
      console.error("Sync failed:", error);
      showErrorNotification(failureMessage);
    } finally {
      paymentSyncInFlight = false;
      if (pendingPaymentSyncReason) {
        const nextReason = pendingPaymentSyncReason;
        pendingPaymentSyncReason = null;
        schedulePaymentsSync(300, nextReason);
      }
    }
  }

  function schedulePaymentsSync(delay = 800, reason = "mutation") {
    if (!isPaymentPage()) return;
    if (paymentSyncTimer) {
      clearTimeout(paymentSyncTimer);
    }
    paymentSyncTimer = window.setTimeout(() => {
      paymentSyncTimer = null;
      syncPayments(reason);
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

    if (Array.isArray(data.existing_boxes) && data.existing_boxes.length) {
      lastExtractedExistingBoxes = data.existing_boxes.slice(0, 50);
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

  chrome.runtime.onMessage.addListener((message) => {
    if (!message || message.type !== "PAYMENTS_SYNC_RESULT") return false;
    const result = message.payload || {};
    if (!result.ok) {
      showErrorNotification(getSyncFailureMessage(result));
      return false;
    }
    const data = result.data || {};
    const inserted = Number(((data.batches && data.batches.inserted) || 0) + ((data.history && data.history.inserted) || 0));
    const updated = Number(((data.batches && data.batches.updated) || 0) + ((data.history && data.history.updated) || 0));
    if (inserted || updated) {
      showPaymentSyncIndicator("Sync successful", "success");
    }
    return false;
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

  const observerCallback = (mutations) => {
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
        schedulePaymentsSync(800, "mutation");
      }
    }
  };

  const observer = new MutationObserver(observerCallback);

  function startDomObserver() {
    observer.disconnect();
    const target = document.body || document.documentElement;
    if (!target) return;
    observer.observe(target, {
      childList: true,
      subtree: true,
      characterData: true
    });
    paymentObserver = observer;
  }

  startDomObserver();

  document.addEventListener("mousemove", () => send("ACTIVITY_PING", { active: true }), { passive: true });
  document.addEventListener("keydown", () => send("ACTIVITY_PING", { active: true }), { passive: true });

  window.addEventListener("beforeunload", () => {
    if (startedTaskUid) {
      send("TASK_END", { task_uid: startedTaskUid });
    }
  });

  window.addEventListener("load", () => {
    startDomObserver();
    sendExtensionHeartbeat();
    schedulePaymentsSync(0, "initial");
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      sendExtensionHeartbeat();
      schedulePaymentsSync(0, "visibility");
    }
  });

  setInterval(handleUrlChange, 1000);
  setInterval(sendExtensionHeartbeat, 30000);
  setInterval(() => send("ACTIVITY_PING", { active: true }), 45000);
  setInterval(() => { if (isTaskPage()) emitFrameIfChanged(); }, 3000);
  setInterval(() => { if (isTaskPage()) emitAnnotationDelta(); }, 2000);
  setInterval(() => { if (isTaskPage()) pollGlobalStore(); }, 3000);
  setInterval(() => { if (isTaskPage()) scheduleVisionSuggestions(); }, 7000);
  setInterval(() => { if (isProfilePage()) syncContributionDays(); }, 4000);
  setInterval(() => { if (isPaymentPage()) syncPayments("interval"); }, 30000);
  sendExtensionHeartbeat();
  syncContributionDays();
  startDomObserver();
  schedulePaymentsSync(0, "initial");
  setTimeout(() => schedulePaymentsSync(500, "initial"), 500);
  setTimeout(() => schedulePaymentsSync(1500, "initial"), 1500);
  setTimeout(() => scheduleVisionSuggestions(2000), 2000);
})();
