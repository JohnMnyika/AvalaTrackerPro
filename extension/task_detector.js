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
      return /(datasets|work_batches|work-batches|tasks|jobs)/i.test(u.pathname);
    } catch (_err) {
      return false;
    }
  }

  function parseTaskFromUrl(url) {
    try {
      const u = new URL(url);
      const parts = u.pathname.split("/").filter(Boolean);

      const idFromQuery =
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
      const idAnchor = ["tasks", "task", "work_batches", "work-batches", "jobs", "job"];
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

      const cameraGuess = (u.searchParams.get("camera") || "unknown").trim();
      const frameStart = Number(u.searchParams.get("frame_start") || 0);
      const frameEnd = Number(u.searchParams.get("frame_end") || 0);
      const totalFrames = Number(
        u.searchParams.get("total_frames") || Math.max(frameEnd - frameStart, 0)
      );
      const expectedHours = Number(u.searchParams.get("expected_hours") || 0);

      // Build a stable fallback UID that is not affected by volatile query params.
      // This avoids creating a new task on each in-app URL state change.
      const stableFallbackKey = [dataset, u.searchParams.get("sequence_id") || "", cameraGuess]
        .filter(Boolean)
        .join("|");
      const fallbackUid = stableFallbackKey
        ? `task-${stableHash(stableFallbackKey)}`
        : `task-${stableHash(u.pathname)}`;
      const resolvedTaskUid = idFromQuery || idFromPath || fallbackUid;

      return {
        task_uid: resolvedTaskUid,
        dataset,
        sequence_id: u.searchParams.get("sequence_id") || null,
        camera: cameraGuess,
        frame_start: Number.isFinite(frameStart) ? frameStart : 0,
        frame_end: Number.isFinite(frameEnd) ? frameEnd : 0,
        total_frames: Number.isFinite(totalFrames) ? totalFrames : 0,
        expected_hours: Number.isFinite(expectedHours) && expectedHours > 0 ? expectedHours : null
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

  window.AvalaTaskDetector = {
    isTaskUrl,
    parseTaskFromUrl,
    extractFrameNumberFromDom
  };
})();
