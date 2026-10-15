/**
 * Global App Logic: Keyboard Shortcuts, Detail Drawer, Live Runs Polling
 */

function escapeHtml(str) {
  if (str === null || str === undefined) return '<span class="empty-dash">—</span>';
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderValue(val, isMono = false) {
  if (val === null || val === undefined || val === "") {
    return '<span class="empty-dash">—</span>';
  }
  const escaped = escapeHtml(val);
  return isMono ? `<span class="mono-col">${escaped}</span>` : escaped;
}

// Global Drawer Controller
window.Drawer = {
  open(cid) {
    const backdrop = document.getElementById("drawerBackdrop");
    const drawer = document.getElementById("detailDrawer");
    const content = document.getElementById("drawerContent");
    if (!drawer) return;

    drawer.classList.add("active");
    if (backdrop) backdrop.classList.add("active");
    if (content) content.innerHTML = '<div class="state-box"><div class="state-icon">⏳</div><div>Loading details...</div></div>';

    fetch(`/api/places/${cid}`)
      .then(res => res.json())
      .then(data => {
        this.renderDetails(data);
      })
      .catch(err => {
        if (content) content.innerHTML = `<div class="state-box"><div class="state-icon">⚠️</div><div>Failed to load: ${escapeHtml(err.message)}</div></div>`;
      });
  },

  close() {
    const backdrop = document.getElementById("drawerBackdrop");
    const drawer = document.getElementById("detailDrawer");
    if (drawer) drawer.classList.remove("active");
    if (backdrop) backdrop.classList.remove("active");
  },

  renderDetails(p) {
    const content = document.getElementById("drawerContent");
    if (!content) return;

    let hoursHtml = '<span class="empty-dash">—</span>';
    if (p.hours && p.hours.length > 0) {
      hoursHtml = `<div style="display:flex;flex-direction:column;gap:4px;font-size:12px;">` +
        p.hours.map(h => `<div style="display:flex;justify-content:space-between;"><span style="color:var(--text-muted);">${escapeHtml(h.day_name)}</span><span>${escapeHtml(h.formatted_text)}</span></div>`).join("") +
        `</div>`;
    }

    let attrsHtml = '<span class="empty-dash">—</span>';
    if (p.attributes && p.attributes.length > 0) {
      attrsHtml = `<div style="display:flex;flex-wrap:wrap;gap:6px;">` +
        p.attributes.map(a => `<span class="badge" style="background:var(--bg-surface-hover);color:var(--text-primary);border:1px solid var(--border-subtle);">${escapeHtml(a.attribute_name)}</span>`).join("") +
        `</div>`;
    }

    let photosHtml = '<span class="empty-dash">—</span>';
    if (p.photos && p.photos.length > 0) {
      photosHtml = `<div class="photo-gallery">` +
        p.photos.map(ph => {
          const src = ph.local_path ? `/media/photos/${p.cid}/${ph.local_path.split(/[\\/]/).pop()}` : ph.photo_url;
          return `<div class="photo-card"><img src="${escapeHtml(src)}" alt="Place photo" loading="lazy" onerror="this.src='/static/img/placeholder.svg';"></div>`;
        }).join("") +
        `</div>`;
    }

    let reviewsHtml = '<span class="empty-dash">—</span>';
    if (p.reviews && p.reviews.length > 0) {
      reviewsHtml = p.reviews.map(r => `
        <div class="review-item">
          <div class="review-header">
            <span class="review-author">${escapeHtml(r.author_name)}</span>
            <span class="rating-badge rating-high">${r.rating}★</span>
          </div>
          <div style="font-size:11px;color:var(--text-muted);margin-bottom:6px;">${escapeHtml(r.relative_date)}</div>
          <div class="review-text">${escapeHtml(r.text)}</div>
          ${r.owner_response_text ? `<div class="owner-reply"><strong style="color:var(--accent-cyan);">Owner Reply:</strong> ${escapeHtml(r.owner_response_text)}</div>` : ''}
        </div>
      `).join("");
    }

    content.innerHTML = `
      <div class="drawer-header">
        <div>
          <div class="drawer-title">${escapeHtml(p.name)}</div>
          <div style="font-size:12px;color:var(--text-muted);">${escapeHtml(p.category)} &bull; ${escapeHtml(p.status)}</div>
        </div>
        <button class="drawer-close" onclick="Drawer.close()">&times;</button>
      </div>
      <div class="drawer-body">
        <div class="drawer-section">
          <div class="drawer-section-title">Overview</div>
          <div class="detail-kv-grid">
            <div class="detail-k">CID</div><div class="detail-v mono-col">${escapeHtml(p.cid)}</div>
            <div class="detail-k">Place ID</div><div class="detail-v mono-col">${renderValue(p.place_id, true)}</div>
            <div class="detail-k">FTID</div><div class="detail-v mono-col">${renderValue(p.ftid, true)}</div>
            <div class="detail-k">Address</div><div class="detail-v">${renderValue(p.address)}</div>
            <div class="detail-k">Coordinates</div><div class="detail-v mono-col">${p.latitude != null ? `${p.latitude.toFixed(6)}, ${p.longitude.toFixed(6)}` : '<span class="empty-dash">—</span>'}</div>
            <div class="detail-k">Phone</div><div class="detail-v">${renderValue(p.phone)}</div>
            <div class="detail-k">Website</div><div class="detail-v">${p.website ? `<a href="${escapeHtml(p.website)}" target="_blank" rel="noopener" style="color:var(--accent-cyan);text-decoration:none;">${escapeHtml(p.website)}</a>` : '<span class="empty-dash">—</span>'}</div>
            <div class="detail-k">Rating</div><div class="detail-v rating-badge rating-high">${p.rating ? `${p.rating}★ (${p.review_count} reviews)` : '<span class="empty-dash">—</span>'}</div>
            <div class="detail-k">Claimed</div><div class="detail-v">${p.claimed_status ? 'Yes' : 'No'}</div>
          </div>
        </div>

        <div class="drawer-section">
          <div class="drawer-section-title">Hours of Operation</div>
          ${hoursHtml}
        </div>

        <div class="drawer-section">
          <div class="drawer-section-title">Attributes & Amenities</div>
          ${attrsHtml}
        </div>

        ${p.popular_times && p.popular_times.length > 0 ? `
        <div class="drawer-section">
          <div class="drawer-section-title">Popular Times (Hourly)</div>
          <canvas id="drawerPopularTimes" height="90" style="width:100%;"></canvas>
        </div>` : ''}

        <div class="drawer-section">
          <div class="drawer-section-title">Local Photos (${p.photos ? p.photos.length : 0})</div>
          ${photosHtml}
        </div>

        <div class="drawer-section">
          <div class="drawer-section-title">Customer Reviews (${p.reviews ? p.reviews.length : 0})</div>
          ${reviewsHtml}
        </div>
      </div>
    `;

    if (p.popular_times && p.popular_times.length > 0 && window.CanvasCharts) {
      setTimeout(() => {
        window.CanvasCharts.drawPopularTimes("drawerPopularTimes", p.popular_times);
      }, 50);
    }
  }
};

// Global Keyboard Navigation
document.addEventListener("keydown", e => {
  // Esc closes drawer
  if (e.key === "Escape") {
    window.Drawer.close();
    return;
  }

  // If typing in input, ignore navigation shortcuts
  if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) {
    return;
  }

  // '/' focuses search
  if (e.key === "/") {
    e.preventDefault();
    const searchInput = document.getElementById("searchInput");
    if (searchInput) searchInput.focus();
    return;
  }

  // j / k table row selection
  const table = document.getElementById("placesTable");
  if (table) {
    const rows = Array.from(table.querySelectorAll("tbody tr"));
    if (rows.length === 0) return;

    let selectedIdx = rows.findIndex(r => r.classList.contains("selected"));

    if (e.key === "j" || e.key === "ArrowDown") {
      e.preventDefault();
      if (selectedIdx < rows.length - 1) {
        if (selectedIdx >= 0) rows[selectedIdx].classList.remove("selected");
        selectedIdx++;
        rows[selectedIdx].classList.add("selected");
        rows[selectedIdx].scrollIntoView({ block: "nearest" });
      }
    } else if (e.key === "k" || e.key === "ArrowUp") {
      e.preventDefault();
      if (selectedIdx > 0) {
        rows[selectedIdx].classList.remove("selected");
        selectedIdx--;
        rows[selectedIdx].classList.add("selected");
        rows[selectedIdx].scrollIntoView({ block: "nearest" });
      }
    } else if (e.key === "Enter" && selectedIdx >= 0) {
      e.preventDefault();
      const cid = rows[selectedIdx].getAttribute("data-cid");
      if (cid) window.Drawer.open(cid);
    }
  }
});
