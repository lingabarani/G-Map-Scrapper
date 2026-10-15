/**
 * Google Maps Scraper — Live Search Client
 * Zero CDNs, vanilla JavaScript, real-time Server-Sent Events (SSE) streaming.
 */

const LiveSearch = (() => {
  let activeJobId = null;
  let eventSource = null;
  let resolvedData = null;
  let timerInterval = null;
  let startTime = null;
  let placesList = [];
  let mapPoints = [];
  let coordsMode = false;

  // Initialize on DOM load
  document.addEventListener("DOMContentLoaded", () => {
    initUrlParams();
    refreshRecentJobs();
    initMapEvents();
  });

  function initUrlParams() {
    const params = new URLSearchParams(window.location.search);
    const q = params.get("q") || params.get("query");
    const loc = params.get("location") || params.get("where");
    const jid = params.get("job") || params.get("job_id");

    if (q) document.getElementById("queryInput").value = q;
    if (loc) document.getElementById("locationInput").value = loc;

    if (jid) {
      loadExistingJob(jid);
    } else if (q && loc) {
      resolveLocation();
    }
  }

  function toggleCoordsMode() {
    coordsMode = !coordsMode;
    const textContainer = document.getElementById("textLocationContainer");
    const coordsContainer = document.getElementById("coordsContainer");
    const label = document.getElementById("coordsModeLabel");

    if (coordsMode) {
      textContainer.style.display = "none";
      coordsContainer.style.display = "grid";
      label.textContent = "Use Free Text Location";
    } else {
      textContainer.style.display = "block";
      coordsContainer.style.display = "none";
      label.textContent = "Use Coordinates Mode";
    }
  }

  async function parseOneBox() {
    const input = document.getElementById("nlInput");
    const text = input.value.trim();
    if (!text) return;

    try {
      const resp = await fetch("/api/live/parse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text })
      });
      const data = await resp.json();
      if (data.query) {
        document.getElementById("queryInput").value = data.query;
      }
      if (data.location) {
        if (coordsMode) toggleCoordsMode();
        document.getElementById("locationInput").value = data.location;
      }
      // Never auto-run from a parse as specified in prompt; user confirms!
    } catch (e) {
      console.error("NL parse error:", e);
    }
  }

  async function resolveLocation() {
    const btn = document.getElementById("btnResolve");
    const statusText = document.getElementById("resolveStatusText");
    let loc = "";

    if (coordsMode) {
      const lat = document.getElementById("coordLat").value.trim();
      const lng = document.getElementById("coordLng").value.trim();
      const span = document.getElementById("coordSpan").value.trim();
      if (!lat || !lng) {
        statusText.textContent = "Please enter lat and lng.";
        statusText.style.color = "#ef4444";
        return;
      }
      loc = span ? `${lat},${lng},${span}` : `${lat},${lng}`;
    } else {
      loc = document.getElementById("locationInput").value.trim();
      if (!loc) {
        statusText.textContent = "Please enter a location name.";
        statusText.style.color = "#ef4444";
        return;
      }
    }

    btn.disabled = true;
    btn.innerHTML = `<span>⏳</span> Resolving...`;
    statusText.textContent = "Querying Google Maps RPC (zero API keys)...";
    statusText.style.color = "#38bdf8";

    try {
      const resp = await fetch("/api/live/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ location: loc })
      });

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "Location resolution failed");
      }

      resolvedData = await resp.json();
      resolvedData._locInput = loc;
      displayConfirmation(resolvedData);
      statusText.textContent = "Resolved successfully!";
      statusText.style.color = "#10b981";
    } catch (e) {
      resolvedData = null;
      statusText.textContent = e.message;
      statusText.style.color = "#ef4444";
      document.getElementById("confirmationCard").style.display = "none";
    } finally {
      btn.disabled = false;
      btn.innerHTML = `<span>📍</span> Resolve Location`;
    }
  }

  function displayConfirmation(data) {
    const card = document.getElementById("confirmationCard");
    document.getElementById("resolvedTitle").textContent = `Resolved: ${data.label}`;
    document.getElementById("resolvedBadge").textContent = data.feature_class || "area";
    document.getElementById("resolvedCoords").textContent = `(${data.lat.toFixed(4)}, ${data.lng.toFixed(4)})`;
    document.getElementById("resolvedSpan").textContent = `${data.span_deg}° (~${Math.round(data.span_deg * 111)} km)`;
    document.getElementById("resolvedCountry").textContent = data.country ? `${data.country} (${data.country_code || '--'})` : (data.country_code || 'Unknown');
    document.getElementById("resolvedEstimate").textContent = `~${data.estimated_requests} reqs (~${Math.ceil(data.estimated_seconds / 60)} min)`;

    card.style.display = "block";
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });

    // Render preview canvas
    renderPreviewMap(data.lat, data.lng, data.span_deg);
  }

  function dismissConfirmation() {
    document.getElementById("confirmationCard").style.display = "none";
  }

  function renderPreviewMap(lat, lng, span) {
    const canvas = document.getElementById("previewMapCanvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;

    ctx.fillStyle = "#0f172a";
    ctx.fillRect(0, 0, w, h);

    // Draw coordinate grid
    ctx.strokeStyle = "rgba(51, 65, 85, 0.4)";
    ctx.lineWidth = 1;
    for (let x = 0; x < w; x += 30) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }
    for (let y = 0; y < h; y += 30) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }

    // Draw bounding box
    const cx = w / 2;
    const cy = h / 2;
    const boxW = Math.min(w * 0.7, Math.max(40, w * 0.5));
    const boxH = Math.min(h * 0.7, Math.max(30, h * 0.5));

    ctx.fillStyle = "rgba(56, 189, 248, 0.12)";
    ctx.fillRect(cx - boxW / 2, cy - boxH / 2, boxW, boxH);

    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 2;
    ctx.strokeRect(cx - boxW / 2, cy - boxH / 2, boxW, boxH);

    // Center pin
    ctx.beginPath();
    ctx.arc(cx, cy, 5, 0, Math.PI * 2);
    ctx.fillStyle = "#ef4444";
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  async function quickSearchAndScrape() {
    await parseOneBox();
    await startScrape();
  }

  async function startScrape() {
    const query = document.getElementById("queryInput").value.trim();
    if (!query) {
      alert("Please enter a business keyword (e.g. 'hospital' or 'bakery').");
      return;
    }

    const currentLoc = coordsMode
      ? `${document.getElementById("coordLat").value.trim()},${document.getElementById("coordLng").value.trim()}`
      : document.getElementById("locationInput").value.trim();

    if (!currentLoc) {
      alert("Please enter a location name (e.g. 'Coimbatore' or 'Chicago').");
      return;
    }

    if (!resolvedData || resolvedData._locInput !== currentLoc) {
      await resolveLocation();
      if (!resolvedData) return;
    }

    const options = {
      max_places: parseInt(document.getElementById("optMaxPlaces").value, 10) || 200,
      max_reviews_per_place: parseInt(document.getElementById("optMaxReviews").value, 10) || 50,
      max_depth: parseInt(document.getElementById("optMaxDepth").value, 10) || 3,
      enrich_details: document.getElementById("optEnrich").checked,
      fetch_reviews: document.getElementById("optReviews").checked,
      download_photos: document.getElementById("optPhotos").checked,
    };

    const spanOverride = parseFloat(document.getElementById("optSpanOverride").value);
    if (!isNaN(spanOverride) && spanOverride > 0) {
      options.span_override_deg = spanOverride;
    }

    const payload = {
      query: query,
      location: resolvedData.label,
      lat: resolvedData.lat,
      lng: resolvedData.lng,
      span_deg: options.span_override_deg || resolvedData.span_deg,
      options: options
    };

    try {
      const resp = await fetch("/api/live/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      const res = await resp.json();
      if (!resp.ok) {
        if (res.error === "large_job_confirmation_required") {
          if (confirm(`${res.detail}\nDo you want to proceed anyway?`)) {
            payload.confirmed = true;
            const retryResp = await fetch("/api/live/start", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(payload)
            });
            const retryRes = await retryResp.json();
            initExecution(retryRes.job_id, query, resolvedData.label);
            return;
          }
        }
        throw new Error(res.detail || res.message || "Failed to start job");
      }

      initExecution(res.job_id, query, resolvedData.label);
    } catch (e) {
      alert("Error starting scrape: " + e.message);
    }
  }

  function initExecution(jobId, query, label) {
    activeJobId = jobId;
    placesList = [];
    mapPoints = [];

    // Hide confirmation, show execution dashboard
    document.getElementById("confirmationCard").style.display = "none";
    const execDash = document.getElementById("executionDashboard");
    execDash.style.display = "block";
    execDash.scrollIntoView({ behavior: "smooth" });

    // Reset UI counters
    document.getElementById("jobCurrentQuery").textContent = `Query: "${query}" in ${label}`;
    document.getElementById("counterPlaces").textContent = "0";
    document.getElementById("counterNewBadge").textContent = "(0 new)";
    document.getElementById("counterReviews").textContent = "0";
    document.getElementById("counterPhotos").textContent = "0";
    document.getElementById("counterElapsed").textContent = "0s";
    document.getElementById("counterEta").textContent = "~Estimating...";
    document.getElementById("placesTableCountBadge").textContent = "0";
    document.getElementById("livePlacesTbody").innerHTML = "";
    document.getElementById("liveLogTail").innerHTML = "";

    document.getElementById("btnCancelJob").style.display = "inline-block";
    document.getElementById("finishedActions").style.display = "none";

    setStepperStage("queued");

    // Start timer
    startTime = Date.now();
    if (timerInterval) clearInterval(timerInterval);
    timerInterval = setInterval(updateTimer, 1000);

    // Open Server-Sent Events stream
    connectEventSource(jobId);
  }

  function updateTimer() {
    if (!startTime) return;
    const elapsedSec = Math.floor((Date.now() - startTime) / 1000);
    const m = Math.floor(elapsedSec / 60);
    const s = elapsedSec % 60;
    document.getElementById("counterElapsed").textContent = m > 0 ? `${m}m ${s}s` : `${s}s`;
  }

  function connectEventSource(jobId) {
    if (eventSource) {
      eventSource.close();
    }

    eventSource = new EventSource(`/api/live/stream?job_id=${encodeURIComponent(jobId)}`);

    eventSource.addEventListener("stage", (e) => {
      try {
        const data = JSON.parse(e.data);
        setStepperStage(data.stage);
        logMessage(`[Stage] ${data.stage.toUpperCase()}: ${data.message || ''}`);
      } catch (err) {}
    });

    eventSource.addEventListener("progress", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.stage) setStepperStage(data.stage);
        if (data.places_found !== undefined) document.getElementById("counterPlaces").textContent = data.places_found;
        if (data.new_places !== undefined) document.getElementById("counterNewBadge").textContent = `(${data.new_places} new)`;
        if (data.reviews_found !== undefined) document.getElementById("counterReviews").textContent = data.reviews_found;
        if (data.photos_found !== undefined) document.getElementById("counterPhotos").textContent = data.photos_found;
        if (data.eta_s !== undefined) document.getElementById("counterEta").textContent = data.eta_s > 0 ? `~${data.eta_s}s` : "Wrapping up...";
        if (data.message) logMessage(`[Progress] ${data.message}`);
      } catch (err) {}
    });

    eventSource.addEventListener("place", (e) => {
      try {
        const p = JSON.parse(e.data);
        addLivePlace(p);
      } catch (err) {}
    });

    eventSource.addEventListener("error", (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.status === "failed" || data.message) {
          handleJobFailed(data);
          return;
        }
      } catch (err) {}
      logMessage(`[Stream notice] Reconnecting stream...`);
    });

    eventSource.addEventListener("failed", (e) => {
      try {
        const data = JSON.parse(e.data);
        handleJobFailed(data);
      } catch (err) {}
    });

    eventSource.addEventListener("done", (e) => {
      try {
        const data = JSON.parse(e.data);
        handleJobFinished(data);
      } catch (err) {}
    });

    eventSource.onerror = (err) => {
      console.warn("EventSource error, closing connection:", err);
    };
  }

  function addLivePlace(p) {
    placesList.unshift(p);
    document.getElementById("placesTableCountBadge").textContent = placesList.length;

    const tbody = document.getElementById("livePlacesTbody");
    const emptyRow = document.getElementById("emptyTableRow");
    if (emptyRow) emptyRow.remove();

    const tr = document.createElement("tr");
    tr.className = "row-highlight";
    tr.style.borderBottom = "1px solid #334155";

    const ratingBadge = p.rating ? `<span style="color:#f59e0b;font-weight:700;">★ ${p.rating}</span> <span style="color:#64748b;font-size:11px;">(${p.review_count || 0})</span>` : '<span style="color:#64748b;">--</span>';
    const verticalBadge = p.vertical ? `<span class="badge badge-sm" style="background:#1e3a8a;color:#93c5fd;padding:1px 6px;border-radius:4px;font-size:10px;">${p.vertical}</span>` : '';

    tr.innerHTML = `
      <td style="padding:8px 10px;font-weight:600;color:#f1f5f9;">
        <a href="/places/${p.cid}" target="_blank" style="color:#38bdf8;text-decoration:none;">${escapeHtml(p.name || 'Unnamed')}</a>
        ${p.is_new ? '<span style="background:rgba(16,185,129,0.2);color:#10b981;font-size:9px;padding:1px 4px;border-radius:3px;margin-left:4px;">NEW</span>' : ''}
      </td>
      <td style="padding:8px 10px;">${verticalBadge}</td>
      <td style="padding:8px 10px;">${ratingBadge}</td>
      <td style="padding:8px 10px;color:#cbd5e1;max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(p.address || '--')}</td>
      <td style="padding:8px 10px;color:#94a3b8;font-family:monospace;">${escapeHtml(p.phone || '--')}</td>
    `;

    tbody.insertBefore(tr, tbody.firstChild);

    // Add point to canvas map
    if (p.latitude && p.longitude) {
      mapPoints.push({
        lat: p.latitude,
        lng: p.longitude,
        name: p.name,
        vertical: p.vertical,
        cid: p.cid
      });
      renderLiveCanvasMap();
    }
  }

  function handleJobFinished(data) {
    if (timerInterval) clearInterval(timerInterval);
    if (eventSource) eventSource.close();

    const badge = document.getElementById("jobStatusBadge");
    const pulse = document.getElementById("jobPulseDot");
    const status = data.status || "done";

    if (status === "failed") {
      handleJobFailed(data);
      return;
    }

    if (status === "cancelled") {
      setStepperStage("cancelled");
      badge.textContent = "CANCELLED";
      badge.style.background = "#7f1d1d";
      badge.style.color = "#fca5a5";
      pulse.style.background = "#ef4444";
      pulse.style.boxShadow = "0 0 10px #ef4444";
      logMessage(`[Finished] Job cancelled. Partial places stored in database.`);
    } else {
      setStepperStage("done");
      badge.textContent = "COMPLETED";
      badge.style.background = "#065f46";
      badge.style.color = "#6ee7b7";
      pulse.style.background = "#10b981";
      logMessage(`[Finished] Completed! ${placesList.length} places captured and indexed.`);
    }

    pulse.style.animation = "none";
    document.getElementById("btnCancelJob").style.display = "none";
    const finishedActions = document.getElementById("finishedActions");
    finishedActions.style.display = "inline-flex";

    const viewBtn = document.getElementById("btnViewPlaces");
    viewBtn.href = `/places?job=${encodeURIComponent(activeJobId)}`;

    refreshRecentJobs();
  }

  function handleJobFailed(data) {
    if (timerInterval) clearInterval(timerInterval);
    if (eventSource) eventSource.close();

    setStepperStage("failed");

    const badge = document.getElementById("jobStatusBadge");
    const pulse = document.getElementById("jobPulseDot");
    badge.textContent = "FAILED";
    badge.style.background = "#7f1d1d";
    badge.style.color = "#fca5a5";
    pulse.style.background = "#ef4444";
    pulse.style.boxShadow = "0 0 10px #ef4444";
    pulse.style.animation = "none";

    const errMsg = (data && (data.message || data.detail || data.error)) || "Job encountered an error";
    logMessage(`[Failed] ${errMsg}`);

    document.getElementById("btnCancelJob").style.display = "none";
    const finishedActions = document.getElementById("finishedActions");
    finishedActions.style.display = "inline-flex";

    const viewBtn = document.getElementById("btnViewPlaces");
    viewBtn.href = `/places?job=${encodeURIComponent(activeJobId)}`;

    refreshRecentJobs();
  }

  async function cancelJob() {
    if (!activeJobId) return;
    if (!confirm("Are you sure you want to stop the live scrape? Partial places will be saved in your database.")) return;

    try {
      const resp = await fetch(`/api/live/cancel/${encodeURIComponent(activeJobId)}`, { method: "POST" });
      if (resp.ok) {
        logMessage("[Action] Cancel request sent to worker. Stopping gracefully...");
      }
    } catch (e) {
      console.error("Cancel failed:", e);
    }
  }

  function setStepperStage(stage) {
    const stages = ["queued", "resolving", "discovering", "enriching", "reviews", "photos", "done"];
    const currIdx = stages.indexOf(stage);

    stages.forEach((s, idx) => {
      const el = document.getElementById(`step-${s}`);
      if (!el) return;
      el.classList.remove("active", "completed");
      if (idx < currIdx) {
        el.classList.add("completed");
      } else if (idx === currIdx) {
        el.classList.add("active");
      }
    });

    const badge = document.getElementById("jobStatusBadge");
    badge.textContent = stage.toUpperCase();
  }

  function logMessage(text) {
    const tail = document.getElementById("liveLogTail");
    if (!tail) return;
    const timeStr = new Date().toLocaleTimeString();
    const line = document.createElement("div");
    line.textContent = `[${timeStr}] ${text}`;
    tail.appendChild(line);
    tail.scrollTop = tail.scrollHeight;
  }

  // -------------------------------------------------------------
  // Live Canvas Map Plotter
  // -------------------------------------------------------------
  let mapBounds = null;

  function renderLiveCanvasMap() {
    const canvas = document.getElementById("liveMapCanvas");
    if (!canvas || mapPoints.length === 0) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;

    ctx.fillStyle = "#0a0d14";
    ctx.fillRect(0, 0, w, h);

    // Grid lines
    ctx.strokeStyle = "rgba(255, 255, 255, 0.04)";
    ctx.lineWidth = 1;
    for (let x = 0; x < w; x += 40) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }
    for (let y = 0; y < h; y += 40) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }

    // Compute bounding box of points
    let minLat = Infinity, maxLat = -Infinity, minLng = Infinity, maxLng = -Infinity;
    mapPoints.forEach(p => {
      if (p.lat < minLat) minLat = p.lat;
      if (p.lat > maxLat) maxLat = p.lat;
      if (p.lng < minLng) minLng = p.lng;
      if (p.lng > maxLng) maxLng = p.lng;
    });

    const padLat = Math.max((maxLat - minLat) * 0.15, 0.005);
    const padLng = Math.max((maxLng - minLng) * 0.15, 0.005);
    mapBounds = {
      minLat: minLat - padLat,
      maxLat: maxLat + padLat,
      minLng: minLng - padLng,
      maxLng: maxLng + padLng
    };

    // Plot points
    mapPoints.forEach(p => {
      const x = ((p.lng - mapBounds.minLng) / (mapBounds.maxLng - mapBounds.minLng)) * w;
      const y = h - (((p.lat - mapBounds.minLat) / (mapBounds.maxLat - mapBounds.minLat)) * h);

      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fillStyle = "#38bdf8";
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1;
      ctx.stroke();
    });

    // Count badge on map
    ctx.fillStyle = "rgba(15, 23, 42, 0.85)";
    ctx.fillRect(10, 10, 130, 26);
    ctx.fillStyle = "#94a3b8";
    ctx.font = "11px sans-serif";
    ctx.fillText(`Mapped: ${mapPoints.length} points`, 18, 27);
  }

  function resetLiveMap() {
    renderLiveCanvasMap();
  }

  function initMapEvents() {
    const canvas = document.getElementById("liveMapCanvas");
    const tooltip = document.getElementById("liveMapTooltip");
    if (!canvas || !tooltip) return;

    canvas.addEventListener("mousemove", (e) => {
      if (!mapBounds || mapPoints.length === 0) return;
      const rect = canvas.getBoundingClientRect();
      const mx = (e.clientX - rect.left) * (canvas.width / rect.width);
      const my = (e.clientY - rect.top) * (canvas.height / rect.height);

      let closest = null;
      let minDist = 15;

      mapPoints.forEach(p => {
        const px = ((p.lng - mapBounds.minLng) / (mapBounds.maxLng - mapBounds.minLng)) * canvas.width;
        const py = canvas.height - (((p.lat - mapBounds.minLat) / (mapBounds.maxLat - mapBounds.minLat)) * canvas.height);
        const dist = Math.hypot(mx - px, my - py);
        if (dist < minDist) {
          minDist = dist;
          closest = p;
        }
      });

      if (closest) {
        tooltip.style.display = "block";
        tooltip.style.left = `${e.clientX - rect.left + 10}px`;
        tooltip.style.top = `${e.clientY - rect.top + 10}px`;
        tooltip.innerHTML = `<strong>${escapeHtml(closest.name)}</strong><br><span style="color:#94a3b8;">${closest.vertical || 'place'}</span>`;
      } else {
        tooltip.style.display = "none";
      }
    });

    canvas.addEventListener("mouseleave", () => {
      tooltip.style.display = "none";
    });
  }

  // -------------------------------------------------------------
  // Exports & Replay
  // -------------------------------------------------------------
  function exportCsv() {
    if (!activeJobId) return;
    window.location.href = `/api/export?format=csv&job=${encodeURIComponent(activeJobId)}`;
  }

  function exportJson() {
    if (!activeJobId) return;
    window.location.href = `/api/export?format=json&full=true&job=${encodeURIComponent(activeJobId)}`;
  }

  function copyJson() {
    if (!placesList || placesList.length === 0) {
      alert("No places discovered to copy.");
      return;
    }
    navigator.clipboard.writeText(JSON.stringify(placesList, null, 2))
      .then(() => alert(`Copied ${placesList.length} places to clipboard!`))
      .catch(err => alert("Copy failed: " + err));
  }

  async function refreshRecentJobs() {
    try {
      const resp = await fetch("/api/live/jobs?limit=20");
      if (!resp.ok) return;
      const jobs = await resp.json();
      const select = document.getElementById("recentJobsSelect");
      select.innerHTML = '<option value="">-- Select Past Job --</option>';

      jobs.forEach(j => {
        const opt = document.createElement("option");
        opt.value = j.id;
        opt.textContent = `[${j.status.toUpperCase()}] "${j.query}" in ${j.location_input} (${j.places_found || 0} places)`;
        select.appendChild(opt);
      });
    } catch (e) {}
  }

  async function loadExistingJob(jobId) {
    if (!jobId) return;
    try {
      const resp = await fetch(`/api/live/job/${encodeURIComponent(jobId)}`);
      if (!resp.ok) return;
      const data = await resp.json();
      const j = data.job;

      activeJobId = j.id;
      placesList = [];
      mapPoints = [];

      document.getElementById("executionDashboard").style.display = "block";
      document.getElementById("jobCurrentQuery").textContent = `Job ${j.id}: "${j.query}" in ${j.location_input}`;
      document.getElementById("counterPlaces").textContent = j.places_found || 0;
      document.getElementById("counterReviews").textContent = j.reviews_found || 0;
      document.getElementById("counterPhotos").textContent = j.photos_found || 0;
      document.getElementById("counterElapsed").textContent = "--";
      document.getElementById("counterEta").textContent = j.status === "done" ? "Finished" : "--";
      document.getElementById("livePlacesTbody").innerHTML = "";
      document.getElementById("liveLogTail").innerHTML = "";

      // Replay events
      if (data.events) {
        data.events.forEach(ev => {
          logMessage(`[${(ev.stage || '').toUpperCase()}] ${ev.message || ''}`);
          if (ev.payload && ev.payload.cid) {
            addLivePlace(ev.payload);
          }
        });
      }

      // If no places replayed from events, fetch saved places for this job
      if (placesList.length === 0) {
        try {
          const pResp = await fetch(`/api/places?job=${encodeURIComponent(j.id)}&page_size=200`);
          if (pResp.ok) {
            const pData = await pResp.json();
            if (pData.items && pData.items.length > 0) {
              pData.items.forEach(p => addLivePlace(p));
            }
          }
        } catch (pe) {}
      }

      setStepperStage(j.status === "done" ? "done" : j.stage || "queued");

      const badge = document.getElementById("jobStatusBadge");
      badge.textContent = j.status.toUpperCase();
      if (j.status === "failed") {
        badge.style.background = "#7f1d1d";
        badge.style.color = "#fca5a5";
      } else if (j.status === "cancelled") {
        badge.style.background = "#7f1d1d";
        badge.style.color = "#fca5a5";
      } else if (j.status === "done") {
        badge.style.background = "#065f46";
        badge.style.color = "#6ee7b7";
      }
      document.getElementById("btnCancelJob").style.display = j.status in {done:1, cancelled:1, failed:1} ? "none" : "inline-block";
      document.getElementById("finishedActions").style.display = "inline-flex";
      document.getElementById("btnViewPlaces").href = `/places?job=${encodeURIComponent(j.id)}`;

      // If job is still running, subscribe to SSE
      if (!["done", "cancelled", "failed"].includes(j.status)) {
        connectEventSource(j.id);
      }
    } catch (e) {
      console.error("Error loading job:", e);
    }
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  return {
    parseOneBox,
    quickSearchAndScrape,
    toggleCoordsMode,
    resolveLocation,
    displayConfirmation,
    dismissConfirmation,
    startScrape,
    cancelJob,
    resetLiveMap,
    exportCsv,
    exportJson,
    copyJson,
    refreshRecentJobs,
    loadExistingJob
  };
})();
