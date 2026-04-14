(function () {
  function hostLooksLikeAvala(hostname) {
    return hostname === "avala.ai" || hostname.endsWith(".avala.ai");
  }

  function stableHash(input) {
    let hash = 0;
    for (let i = 0; i < input.length; i += 1) {
      hash = (hash << 5) - hash + input.charCodeAt(i);
      hash |= 0;
    }
    return Math.abs(hash).toString(16);
  }

  function isTaskUrl(url) {
    try {
      const u = new URL(url);
      if (!hostLooksLikeAvala(u.hostname)) return false;
      return /(datasets|work_batches|work-batches|tasks|jobs|sequences)/i.test(u.pathname);
    } catch (_err) {
      return false;
    }
  }

  function isProfileUrl(url) {
    try {
      const u = new URL(url);
      if (!hostLooksLikeAvala(u.hostname)) return false;
      return /^\/@/i.test(u.pathname);
    } catch (_err) {
      return false;
    }
  }

  function isPaymentDashboardUrl(url) {
    try {
      const u = new URL(url);
      const hostname = (u.hostname || "").toLowerCase();
      const pathname = (u.pathname || "").toLowerCase();
      if (hostname === "pay.avala.ai" || hostname.endsWith(".pay.avala.ai")) {
        return pathname === "/dashboard" || pathname === "/dashboard/" || pathname.startsWith("/dashboard/");
      }
      return false;
    } catch (_err) {
      return false;
    }
  }

  function parseFrameRangeFromLoad(loadValue) {
    if (!loadValue) return null;
    const match = loadValue.match(/(\d+)\s*-\s*(\d+)/);
    if (!match) return null;
    const start = Number(match[1]);
    const end = Number(match[2]);
    if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
    return [start, end];
  }

  function parseCameraFromViewport(viewportValue) {
    if (!viewportValue) return null;
    const parts = viewportValue.split(":");
    return parts.length > 0 ? parts[0] : null;
  }

  function parseSequenceFromPath(parts) {
    const idx = parts.indexOf("sequences");
    if (idx >= 0 && parts[idx + 1]) return parts[idx + 1];
    return null;
  }

  function normalizeCameraName(cameraRaw) {
    if (!cameraRaw) return null;
    const cam = cameraRaw.trim();
    return cam.replace(/\s*\(CAM\s*\d+\)\s*$/i, "");
  }

  function parseTaskFromUrl(url) {
    try {
      const u = new URL(url);
      const parts = u.pathname.split("/").filter(Boolean);

      const idFromQuery =
        u.searchParams.get("work_unit_uid") ||
        u.searchParams.get("task_uid") ||
        u.searchParams.get("taskId") ||
        u.searchParams.get("task_id") ||
        u.searchParams.get("batch_id") ||
        u.searchParams.get("work_batch_id") ||
        u.searchParams.get("job_id") ||
        u.searchParams.get("jobId") ||
        u.searchParams.get("item_id") ||
        u.searchParams.get("itemId") ||
        u.searchParams.get("sample_id") ||
        u.searchParams.get("sampleId") ||
        u.searchParams.get("sequence_id");

      let idFromPath = null;
      const idAnchor = ["tasks", "task", "work_batches", "work-batches", "jobs", "job", "sequences"];
      for (const anchor of idAnchor) {
        const idx = parts.indexOf(anchor);
        if (idx >= 0 && parts[idx + 1]) {
          idFromPath = parts[idx + 1];
          break;
        }
      }

      let dataset = "unknown-dataset";
      const datasetIdx = parts.indexOf("datasets");
      if (datasetIdx >= 0 && parts[datasetIdx + 1]) {
        dataset = parts[datasetIdx + 1];
      } else {
        dataset = u.searchParams.get("dataset") || dataset;
      }

      const sequenceFromPath = parseSequenceFromPath(parts);
      const sequenceId = u.searchParams.get("sequence_id") || sequenceFromPath || null;
      const viewportCamera = parseCameraFromViewport(u.searchParams.get("canvas_viewport"));
      const cameraGuess = (u.searchParams.get("camera") || viewportCamera || "unknown").trim();
      const normalizedCamera = normalizeCameraName(cameraGuess) || cameraGuess;

      const loadRange = parseFrameRangeFromLoad(u.searchParams.get("load"));
      const preview = Number(u.searchParams.get("preview"));
      const explicitStart = Number(u.searchParams.get("frame_start") || 0);
      const explicitEnd = Number(u.searchParams.get("frame_end") || 0);

      let frameStart = explicitStart;
      let frameEnd = explicitEnd;
      if (loadRange) {
        frameStart = loadRange[0];
        frameEnd = loadRange[1];
      } else if (Number.isFinite(preview)) {
        frameStart = preview;
        if (!Number.isFinite(frameEnd) || frameEnd === 0) frameEnd = preview;
      }

      const totalFrames = Number(
        u.searchParams.get("total_frames") || (frameEnd >= frameStart ? frameEnd - frameStart + 1 : 0)
      );
      const expectedHours = Number(u.searchParams.get("expected_hours") || 0);

      const stableFallbackKey = [dataset, sequenceId || "", normalizedCamera].filter(Boolean).join("|");
      const fallbackUid = stableFallbackKey ? `task-${stableHash(stableFallbackKey)}` : `task-${stableHash(u.pathname)}`;
      const resolvedTaskUid = idFromQuery || idFromPath || fallbackUid;

      return {
        task_uid: resolvedTaskUid,
        dataset,
        sequence_id: sequenceId,
        camera: normalizedCamera,
        frame_start: Number.isFinite(frameStart) ? frameStart : 0,
        frame_end: Number.isFinite(frameEnd) ? frameEnd : 0,
        total_frames: Number.isFinite(totalFrames) ? totalFrames : 0,
        expected_hours: Number.isFinite(expectedHours) && expectedHours > 0 ? expectedHours : null,
        preview_frame: Number.isFinite(preview) ? preview : null
      };
    } catch (_err) {
      return null;
    }
  }

  function extractFrameNumberFromDom() {
    const candidates = [
      "[data-frame-number]",
      "[data-testid='frame-number']",
      "[class*='frame']"
    ];
    for (const selector of candidates) {
      const el = document.querySelector(selector);
      if (!el) continue;
      const attr = el.getAttribute("data-frame-number");
      if (attr && /^\d+$/.test(attr)) return Number(attr);
      const text = (el.textContent || "").match(/(\d+)/);
      if (text) return Number(text[1]);
    }
    return null;
  }

  function extractAnnotationCountFromDom() {
    const selectors = [
      "[data-annotation-id]",
      "[data-testid*='annotation']",
      "[data-testid*='cuboid']",
      "[data-box-id]",
      "[data-bounds]",
      "[data-bbox]",
      "[class*='annotation']",
      "[class*='cuboid']",
      "[class*='box']",
      "[aria-label*='annotation']",
      "[aria-label*='box']",
      "svg [data-id]",
      "svg [data-label]",
      "svg [class*='box']",
      "svg [class*='cuboid']",
      "svg rect[data-id]",
      "svg polygon[data-id]",
      "svg path[data-id]"
    ];

    let count = 0;
    for (const selector of selectors) {
      const nodes = document.querySelectorAll(selector);
      if (nodes && nodes.length) count += nodes.length;
    }
    return count > 0 ? count : null;
  }

  function parseContributionLabel(label) {
    if (!label) return null;
    const text = String(label).replace(/\s+/g, " ").trim();
    const match = text.match(/(\d+)\s+(?:contributions?|boxes?|annotations?).*?on\s+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})/i);
    if (!match) return null;
    const count = Number(match[1]);
    const parsedDate = new Date(match[2].replace(/,/g, ""));
    if (!Number.isFinite(count) || Number.isNaN(parsedDate.getTime())) return null;
    return {
      contribution_date: parsedDate.toISOString(),
      boxes_count: count,
      source: "profile"
    };
  }

  function extractContributionDaysFromDom() {
    const elements = document.querySelectorAll("[title], [aria-label], [data-tooltip], rect");
    const days = [];
    const seen = new Set();
    for (const el of elements) {
      const candidates = [
        el.getAttribute && el.getAttribute("title"),
        el.getAttribute && el.getAttribute("aria-label"),
        el.getAttribute && el.getAttribute("data-tooltip"),
        el.dataset && el.dataset.tooltip,
        el.textContent
      ];
      for (const candidate of candidates) {
        const parsed = parseContributionLabel(candidate);
        if (!parsed) continue;
        const key = `${parsed.contribution_date}|${parsed.boxes_count}`;
        if (seen.has(key)) continue;
        seen.add(key);
        days.push(parsed);
      }
    }
    return days;
  }

  function parseUsd(text) {
    if (!text) return null;
    const match = String(text).replace(/,/g, "").match(/\$\s*(\d+(?:\.\d+)?)/);
    return match ? Number(match[1]) : null;
  }

  function parseKes(text) {
    if (!text) return null;
    const match = String(text).replace(/,/g, "").match(/KES\s*(\d+(?:\.\d+)?)/i);
    return match ? Number(match[1]) : null;
  }

  function normalizeHistoryDate(text) {
    if (!text) return null;
    const match = String(text).trim().match(/^([A-Z][a-z]{2})\s+(\d{1,2}),\s*(\d{4})$/);
    if (!match) return null;
    const months = { Jan: '01', Feb: '02', Mar: '03', Apr: '04', May: '05', Jun: '06', Jul: '07', Aug: '08', Sep: '09', Oct: '10', Nov: '11', Dec: '12' };
    const month = months[match[1]];
    const day = String(match[2]).padStart(2, '0');
    const year = match[3];
    return month ? `${year}-${month}-${day}` : null;
  }

  const paymentSectionCache = {
    recentWork: null,
    paymentHistory: null
  };

  function normalizeText(value) {
    return String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
  }

  function getPaymentDashboardRoot() {
    return document.querySelector("main, [role='main'], [data-testid='dashboard-root']") || document.body;
  }

  function isConnectedElement(node) {
    return Boolean(node && node.isConnected);
  }

  function resolveSectionContainer(node) {
    if (!node) return null;
    return (
      node.closest("section, article, [role='region'], [class*='card'], [class*='panel'], [class*='section']") ||
      node.parentElement ||
      null
    );
  }

  function findSectionRootByHeading(headingText) {
    const cacheKey = headingText === "Recent Work Added" ? "recentWork" : "paymentHistory";
    const cached = paymentSectionCache[cacheKey];
    if (isConnectedElement(cached) && normalizeText(cached.textContent).includes(normalizeText(headingText))) {
      return cached;
    }

    const root = getPaymentDashboardRoot();
    const target = normalizeText(headingText);
    const headingSelectors = [
      "h1", "h2", "h3", "h4", "h5", "h6",
      "[role='heading']",
      "header *",
      "[class*='title']",
      "[class*='heading']",
      "strong",
      "span"
    ].join(", ");

    const candidates = Array.from(root.querySelectorAll(headingSelectors));
    for (const node of candidates) {
      const text = normalizeText(node.textContent);
      if (!text || (!text.includes(target) && text !== target)) continue;
      const container = resolveSectionContainer(node);
      if (container) {
        paymentSectionCache[cacheKey] = container;
        return container;
      }
    }
    return null;
  }

  function collectSectionItems(section) {
    if (!section) return [];
    const selectors = [
      ":scope li",
      ":scope tr",
      ":scope [role='row']",
      ":scope [data-testid*='row']",
      ":scope [class*='row']",
      ":scope [class*='item']",
      ":scope article",
      ":scope section > div"
    ];
    const seen = new Set();
    const items = [];
    for (const selector of selectors) {
      for (const node of section.querySelectorAll(selector)) {
        if (!isConnectedElement(node) || seen.has(node)) continue;
        seen.add(node);
        items.push(node);
      }
      if (items.length) break;
    }
    return items.length ? items : Array.from(section.children || []);
  }

  function extractPaymentSectionText(rawText, startHeading, endHeadings) {
    const normalized = String(rawText || '').replace(/\s+/g, ' ').trim();
    if (!normalized) return '';
    const lower = normalized.toLowerCase();
    const startIndex = lower.lastIndexOf(String(startHeading || '').toLowerCase());
    let scoped = startIndex >= 0 ? normalized.slice(startIndex) : normalized;
    const scopedLower = scoped.toLowerCase();
    let endIndex = -1;
    for (const heading of endHeadings || []) {
      const idx = scopedLower.indexOf(String(heading || '').toLowerCase());
      if (idx > 0 && (endIndex === -1 || idx < endIndex)) {
        endIndex = idx;
      }
    }
    if (endIndex > 0) {
      scoped = scoped.slice(0, endIndex);
    }
    return scoped.trim();
  }

  function getPaymentTextCandidates() {
    const root = getPaymentDashboardRoot();
    const candidates = [
      document.body && document.body.innerText,
      root && root.innerText,
      document.body && document.body.textContent,
      root && root.textContent,
    ];
    const out = [];
    const seen = new Set();
    for (const value of candidates) {
      const normalized = String(value || '').replace(/\s+/g, ' ').trim();
      if (!normalized || seen.has(normalized)) continue;
      seen.add(normalized);
      out.push(normalized);
    }
    return out;
  }

  function dedupeBy(rows, makeKey) {
    const seen = new Set();
    const deduped = [];
    for (const row of rows || []) {
      const key = makeKey(row);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      deduped.push(row);
    }
    return deduped;
  }

  function extractBatchName(text) {
    if (!text) return null;
    const match = String(text).toLowerCase().match(/\bbatch[-_]\d+(?:[-_][a-z]+)+(?=about|\b|[^a-z0-9])/);
    return match ? sanitizeBatchName(match[0]) : null;
  }

  function sanitizeBatchName(value) {
    if (!value) return null;
    let normalized = String(value).toLowerCase().replace(/_/g, "-").trim();
    normalized = normalized.replace(/(?:about|ago|hours?|days?|minutes?|months?)$/i, "");
    normalized = normalized.replace(/\d{1,2}$/i, "");
    normalized = normalized.replace(/-+/g, "-").replace(/-$/, "");
    return /\bbatch-\d+(?:-[a-z]+)+\b/.test(normalized) ? normalized : null;
  }

  function escapeRegex(value) {
    return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function extractBatchNumber(batchName) {
    if (!batchName) return null;
    const match = String(batchName).match(/\bbatch-(\d+)\b/i);
    return match ? Number(match[1]) : null;
  }

  function isLikelyBatchAmount(value, batchName) {
    if (!Number.isFinite(value) || value < 0) return false;
    if (value > 100) return false;
    const batchNumber = extractBatchNumber(batchName);
    if (Number.isFinite(batchNumber) && value === batchNumber) return false;
    return true;
  }

  function collectNodeTextCandidates(node) {
    if (!node) return [];
    const candidates = [];
    const seen = new Set();

    function push(value) {
      const normalized = String(value || "").replace(/\s+/g, " ").trim();
      if (!normalized || seen.has(normalized)) return;
      seen.add(normalized);
      candidates.push(normalized);
    }

    push(node.textContent || "");
    for (const child of node.querySelectorAll("*")) {
      push(child.textContent || "");
      push(child.getAttribute && child.getAttribute("aria-label"));
      push(child.getAttribute && child.getAttribute("title"));
      push(child.getAttribute && child.getAttribute("data-tooltip"));
    }
    return candidates;
  }

  function parseLooseUsd(text) {
    if (!text) return null;
    const normalized = String(text).replace(/,/g, "");
    const matches = Array.from(normalized.matchAll(/(\d+(?:\.\d+)?)(?!\s*h\b)/gi));
    if (!matches.length) return null;
    const filtered = matches
      .map((match) => Number(match[1]))
      .filter((value) => Number.isFinite(value));
    if (!filtered.length) return null;
    return filtered[filtered.length - 1];
  }

  function sanitizeAmountText(text, batchName) {
    return String(text || "")
      .replace(new RegExp(escapeRegex(batchName || ""), "ig"), " ")
      .replace(/(?:about\s+\d+\s+(?:minutes?|hours?|days?|months?)|\d+\s+(?:minutes?|hours?|days?|months?))(?:\s+ago)?/ig, " ")
      .replace(/\b\d+(?:\.\d+)?h\b/ig, " ")
      .replace(/\bitems?\b/ig, " ")
      .replace(/\bhours?\b/ig, " ");
  }

  function extractRecentWorkRowFromItem(item) {
    const textCandidates = collectNodeTextCandidates(item);
    const mergedText = textCandidates.join(" | ");
    const batchName = extractBatchName(mergedText);
    if (!batchName) return null;

    let amount = null;
    for (const text of textCandidates) {
      amount = parseUsd(text);
      if (isLikelyBatchAmount(amount, batchName)) break;
    }
    if (!isLikelyBatchAmount(amount, batchName)) {
      const sanitizedText = sanitizeAmountText(mergedText, batchName);
      amount = parseLooseUsd(sanitizedText);
    }
    if (!isLikelyBatchAmount(amount, batchName)) {
      for (const text of textCandidates) {
        const sanitizedText = sanitizeAmountText(text, batchName);
        amount = parseLooseUsd(sanitizedText);
        if (isLikelyBatchAmount(amount, batchName)) break;
      }
    }
    if (!isLikelyBatchAmount(amount, batchName)) return null;

    const timestampMatch = mergedText.match(/(?:about\s+\d+\s+(?:minutes?|hours?|days?|months?)|\d+\s+(?:minutes?|hours?|days?|months?))(?:\s+ago)?/i);
    return {
      batch_name: batchName,
      amount_usd: amount,
      timestamp: timestampMatch ? timestampMatch[0] : null,
      source: "recent_work"
    };
  }

  function extractHistoryRowFromItem(item) {
    const textCandidates = collectNodeTextCandidates(item);
    const mergedText = textCandidates.join(" | ");
    const dateMatch = mergedText.match(/([A-Z][a-z]{2}\s+\d{1,2},\s*\d{4})/);
    const normalizedDate = normalizeHistoryDate(dateMatch && dateMatch[1]);
    if (!normalizedDate) return null;

    let amountUsd = null;
    let amountKes = null;
    for (const text of textCandidates) {
      if (!Number.isFinite(amountUsd)) amountUsd = parseUsd(text);
      if (!Number.isFinite(amountKes)) amountKes = parseKes(text);
      if (Number.isFinite(amountUsd) && Number.isFinite(amountKes)) break;
    }
    if (!Number.isFinite(amountUsd)) {
      amountUsd = parseLooseUsd(mergedText);
    }
    if (!Number.isFinite(amountUsd)) return null;

    const statusMatch = mergedText.match(/\b(completed|pending|failed|processing)\b/i);
    return {
      date: normalizedDate,
      amount_usd: amountUsd,
      amount_kes: Number.isFinite(amountKes) ? amountKes : 0,
      status: statusMatch ? statusMatch[1].toLowerCase() : "completed"
    };
  }

  function parseRecentWorkText(text) {
    const scoped = extractPaymentSectionText(text, 'Recent Work Added', ['Payment History', 'Bonuses & Deductions']);
    if (!scoped) return [];
    const rows = [];
    const pattern = /(batch[-_]\d+(?:[-_][a-z]+)+)(?:[A-Za-z]+)?[\s\S]{0,50}((?:about\s+\d+\s+(?:minutes?|hours?|days?))|(?:\d+\s+(?:minutes?|hours?|days?))|(?:about\s+1\s+month)|(?:\d+\s+months?)|(?:1\s+day))(?:\s+ago)?[\s\S]{0,50}?(\d+(?:\.\d+)?)/gi;
    let match;
    while ((match = pattern.exec(scoped)) !== null) {
      const batchName = sanitizeBatchName(match[1]);
      const timestamp = match[2];
      const amount = Number(match[3]);
      if (!batchName || !isLikelyBatchAmount(amount, batchName)) continue;
      rows.push({
        batch_name: batchName,
        amount_usd: amount,
        timestamp,
        source: 'recent_work'
      });
    }
    return dedupeBy(rows, (row) => `${row.batch_name}|${row.amount_usd}`);
  }

  function parseRecentWorkItems(items) {
    const rows = [];
    for (const item of items || []) {
      const parsed = extractRecentWorkRowFromItem(item);
      if (parsed) rows.push(parsed);
    }
    if (rows.length) {
      return dedupeBy(rows, (row) => `${row.batch_name}|${row.amount_usd}`);
    }
    const combinedText = items
      .map((item) => (item.textContent || '').replace(/\s+/g, ' ').trim())
      .filter(Boolean)
      .join('\n');
    return parseRecentWorkText(combinedText);
  }

  function parsePaymentHistoryText(text) {
    const scoped = extractPaymentSectionText(text, 'Payment History', ['Bonuses & Deductions']);
    if (!scoped) return [];
    const rows = [];
    const pattern = /([A-Z][a-z]{2}\s+\d{1,2},\s*\d{4})\s*(completed|pending|failed|processing)\s*\$(\d+(?:\.\d+)?)\s*(?:→\s*KES\s*([\d,]+(?:\.\d+)?))?/gi;
    let match;
    while ((match = pattern.exec(scoped)) !== null) {
      const normalizedDate = normalizeHistoryDate(match[1]);
      const amountUsd = Number(match[3]);
      const amountKes = match[4] ? Number(String(match[4]).replace(/,/g, '')) : 0;
      const status = (match[2] || 'completed').toLowerCase();
      if (!normalizedDate || !Number.isFinite(amountUsd)) continue;
      rows.push({
        date: normalizedDate,
        amount_usd: amountUsd,
        amount_kes: Number.isFinite(amountKes) ? amountKes : 0,
        status
      });
    }
    return dedupeBy(rows, (row) => `${row.date}|${row.amount_usd}|${row.amount_kes}`);
  }

  function parsePaymentHistoryItems(items) {
    const rows = [];
    for (const item of items || []) {
      const parsed = extractHistoryRowFromItem(item);
      if (parsed) rows.push(parsed);
    }
    if (rows.length) {
      return dedupeBy(rows, (row) => `${row.date}|${row.amount_usd}|${row.amount_kes}|${row.status}`);
    }
    const combinedText = items
      .map((item) => (item.textContent || '').replace(/\s+/g, ' ').trim())
      .filter(Boolean)
      .join('\n');
    return parsePaymentHistoryText(combinedText);
  }

  function collectPaymentFallbackItems() {
    const root = getPaymentDashboardRoot();
    return Array.from(root.querySelectorAll('li, tr, [role="row"], article, section, div'));
  }

  function extractRecentWorkFromDom() {
    const rows = [];
    const section = findSectionRootByHeading('Recent Work Added');
    if (section) {
      const rowsFromItems = parseRecentWorkItems(collectSectionItems(section));
      rows.push(...rowsFromItems);
      const sectionText = ((section.innerText || section.textContent) || '').replace(/\s+/g, ' ').trim();
      const rowsFromText = parseRecentWorkText(sectionText);
      rows.push(...rowsFromText);
    }
    for (const candidate of getPaymentTextCandidates()) {
      rows.push(...parseRecentWorkText(candidate));
    }
    return dedupeBy(rows, (row) => `${row.batch_name}|${row.amount_usd}`);
  }

  function extractPaymentHistoryFromDom() {
    const rows = [];
    const section = findSectionRootByHeading('Payment History');
    if (section) {
      const rowsFromItems = parsePaymentHistoryItems(collectSectionItems(section));
      rows.push(...rowsFromItems);
      const sectionText = ((section.innerText || section.textContent) || '').replace(/\s+/g, ' ').trim();
      const rowsFromText = parsePaymentHistoryText(sectionText);
      rows.push(...rowsFromText);
    }
    for (const candidate of getPaymentTextCandidates()) {
      rows.push(...parsePaymentHistoryText(candidate));
    }
    return dedupeBy(rows, (row) => `${row.date}|${row.amount_usd}|${row.amount_kes}|${row.status}`);
  }

  function isPaymentMutationRelevant(target) {
    if (!target || !(target instanceof Element)) return false;
    const recentWorkSection = findSectionRootByHeading('Recent Work Added');
    const paymentHistorySection = findSectionRootByHeading('Payment History');
    if (
      (recentWorkSection && (target === recentWorkSection || recentWorkSection.contains(target))) ||
      (paymentHistorySection && (target === paymentHistorySection || paymentHistorySection.contains(target)))
    ) {
      return true;
    }
    const root = getPaymentDashboardRoot();
    return Boolean(root && (target === root || root.contains(target)));
  }

  function buildPaymentFingerprint() {
    const samples = getPaymentTextCandidates().slice(0, 3).map((value) => value.slice(0, 500));
    return samples.join('\n---\n');
  }

  function inspectPaymentSections() {
    const recentWorkSection = findSectionRootByHeading('Recent Work Added');
    const paymentHistorySection = findSectionRootByHeading('Payment History');
    const recentWork = extractRecentWorkFromDom();
    const paymentHistory = extractPaymentHistoryFromDom();
    return {
      page_detected: isPaymentDashboardUrl(window.location.href),
      page_url: window.location.href,
      recent_work_section_found: Boolean(recentWorkSection) || recentWork.length > 0,
      payment_history_section_found: Boolean(paymentHistorySection) || paymentHistory.length > 0,
      recent_work_rows: recentWork.length,
      payment_history_rows: paymentHistory.length,
      page_fingerprint: buildPaymentFingerprint(),
      recent_work: recentWork,
      payment_history: paymentHistory
    };
  }

  function extractPaymentsFromDom() {
    const diagnostics = inspectPaymentSections();
    return {
      recent_work: diagnostics.recent_work,
      payment_history: diagnostics.payment_history,
      debug: {
        page_detected: diagnostics.page_detected,
        page_url: diagnostics.page_url,
        recent_work_section_found: diagnostics.recent_work_section_found,
        payment_history_section_found: diagnostics.payment_history_section_found,
        recent_work_rows: diagnostics.recent_work_rows,
        payment_history_rows: diagnostics.payment_history_rows,
        page_fingerprint: diagnostics.page_fingerprint,
        last_status:
          diagnostics.recent_work_rows || diagnostics.payment_history_rows
            ? "scraped_rows"
            : (diagnostics.recent_work_section_found || diagnostics.payment_history_section_found)
              ? "sections_found"
              : "waiting_for_sync"
      }
    };
  }

  window.AvalaTaskDetector = {
    isTaskUrl,
    isProfileUrl,
    isPaymentDashboardUrl,
    parseTaskFromUrl,
    extractFrameNumberFromDom,
    extractAnnotationCountFromDom,
    extractContributionDaysFromDom,
    extractPaymentsFromDom,
    inspectPaymentSections,
    isPaymentMutationRelevant
  };
})();
