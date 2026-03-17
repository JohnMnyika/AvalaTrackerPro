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
    const map = {
      "FWC_L": "FWC_L (CAM 07)",
      "FWC_R": "FWC_R (CAM 08)",
      "FWC_C": "FWC_C (CAM 06)"
    };
    return map[cam] || cam;
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

      // Highest priority: load=66-100
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

      // Stable fallback UID not affected by volatile query params.
      const stableFallbackKey = [dataset, sequenceId || "", normalizedCamera].filter(Boolean).join("|");
      const fallbackUid = stableFallbackKey
        ? `task-${stableHash(stableFallbackKey)}`
        : `task-${stableHash(u.pathname)}`;
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
      "[class*='annotation']",
      "[class*='cuboid']",
      "svg [data-id]",
      "svg [class*='box']",
      "svg [class*='cuboid']"
    ];

    let count = 0;
    for (const selector of selectors) {
      const nodes = document.querySelectorAll(selector);
      if (nodes && nodes.length) count += nodes.length;
    }

    return count > 0 ? count : null;
  }

  window.AvalaTaskDetector = {
    isTaskUrl,
    parseTaskFromUrl,
    extractFrameNumberFromDom,
    extractAnnotationCountFromDom
  };
})();
