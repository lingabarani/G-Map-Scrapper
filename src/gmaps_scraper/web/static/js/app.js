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

  switchTab(tabId) {
    const tabs = document.querySelectorAll(".drawer-tab");
    tabs.forEach(t => t.classList.toggle("active", t.getAttribute("data-tab") === tabId));
    const contents = document.querySelectorAll(".drawer-tab-content");
    contents.forEach(c => c.classList.toggle("active", c.id === `drawerTab-${tabId}`));
  },

  renderDetails(p) {
    const content = document.getElementById("drawerContent");
    if (!content) return;

    // Helper to safely parse evidence
    let evidenceObj = null;
    if (p.website_evidence) {
      try {
        evidenceObj = typeof p.website_evidence === "string" ? JSON.parse(p.website_evidence) : p.website_evidence;
      } catch (e) {
        evidenceObj = { raw: p.website_evidence };
      }
    }

    const profile = p.profile || null;
    const leaders = p.leaders || [];
    const contacts = p.contacts || [];

    // --- TAB 1: OVERVIEW COMPONENTS ---
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

    // --- TAB 2: WEBSITE TAB COMPONENTS ---
    let webStatusBadge = '';
    const ws = p.website_status || 'unverified';
    if (ws === 'verified') webStatusBadge = '<span class="badge" style="background:rgba(16,185,129,0.2);color:#10b981;border:1px solid #10b981;">Verified Official Website</span>';
    else if (ws === 'candidate') webStatusBadge = '<span class="badge" style="background:rgba(56,189,248,0.2);color:#38bdf8;border:1px solid #38bdf8;">Candidate Discovered</span>';
    else if (ws === 'needs_review') webStatusBadge = '<span class="badge" style="background:rgba(245,158,11,0.2);color:#f59e0b;border:1px solid #f59e0b;">Needs Human Review</span>';
    else if (ws === 'website_missing') webStatusBadge = '<span class="badge" style="background:rgba(100,116,139,0.2);color:#94a3b8;border:1px solid #64748b;">Website Missing</span>';
    else if (ws === 'failed') webStatusBadge = '<span class="badge badge-status-failed">Verification Failed</span>';
    else webStatusBadge = '<span class="badge">Unverified</span>';

    const officialUrl = p.official_website_url || (ws === 'verified' ? p.website : null);

    // --- TAB 3: COMPANY PROFILE TAB COMPONENTS ---
    let servicesHtml = '<span class="empty-dash">—</span>';
    if (profile && profile.services && profile.services.length > 0) {
      servicesHtml = `<div style="display:flex;flex-wrap:wrap;gap:6px;">` +
        profile.services.map(s => `<span class="badge" style="background:var(--accent-purple-glow);color:var(--accent-purple);border:1px solid rgba(139,92,246,0.3);">${escapeHtml(s)}</span>`).join("") +
        `</div>`;
    }

    let locationsHtml = '<span class="empty-dash">—</span>';
    if (profile && profile.locations && profile.locations.length > 0) {
      locationsHtml = `<div style="display:flex;flex-wrap:wrap;gap:6px;">` +
        profile.locations.map(loc => `<span class="badge" style="background:var(--bg-surface-hover);">${escapeHtml(loc)}</span>`).join("") +
        `</div>`;
    }

    // --- TAB 4: LEADERSHIP TAB COMPONENTS ---
    let leadersListHtml = '';
    if (leaders.length > 0) {
      leadersListHtml = leaders.map(ldr => `
        <div class="leader-card">
          <div class="leader-header">
            <div>
              <div style="font-weight:600;font-size:13.5px;color:var(--text-primary);">${escapeHtml(ldr.full_name)}</div>
              <div style="font-size:12px;color:var(--accent-cyan);font-weight:500;">${escapeHtml(ldr.title)}</div>
            </div>
            <div style="display:flex;gap:6px;align-items:center;">
              ${ldr.role_type ? `<span class="badge" style="background:rgba(56,189,248,0.15);color:#38bdf8;">${escapeHtml(ldr.role_type)}</span>` : ''}
              ${ldr.confidence ? `<span class="badge" style="background:rgba(16,185,129,0.15);color:#10b981;">${Math.round(ldr.confidence * 100)}% conf</span>` : ''}
            </div>
          </div>
          ${ldr.biography ? `<div style="font-size:12px;color:var(--text-secondary);margin-top:6px;line-height:1.4;">${escapeHtml(ldr.biography)}</div>` : ''}
          ${ldr.source_url ? `<div style="margin-top:8px;font-size:11px;"><a href="${escapeHtml(ldr.source_url)}" target="_blank" rel="noopener" style="color:var(--text-muted);text-decoration:none;">Source: ${escapeHtml(ldr.source_url)} ↗</a></div>` : ''}
        </div>
      `).join("");
    } else {
      leadersListHtml = `
        <div class="empty-field-notice">
          <strong>No public leadership records published</strong><br>
          This company's official website did not publish executive or management details on its public pages.
        </div>
      `;
    }

    // --- TAB 5: CONTACTS TAB COMPONENTS ---
    let contactsListHtml = '';
    if (contacts.length > 0) {
      contactsListHtml = contacts.map(c => `
        <div class="contact-card">
          <div class="contact-header">
            <div style="display:flex;align-items:center;gap:8px;">
              <span class="badge" style="background:var(--bg-surface-hover);text-transform:uppercase;font-size:10px;">${escapeHtml(c.contact_type)}</span>
              <span style="font-weight:600;font-size:13px;color:var(--text-primary);" class="mono-col">${escapeHtml(c.contact_value)}</span>
            </div>
            <span class="badge" style="background:rgba(16,185,129,0.12);color:#10b981;">${escapeHtml(c.label || 'general')}</span>
          </div>
          ${c.source_url ? `<div style="margin-top:6px;font-size:11px;"><a href="${escapeHtml(c.source_url)}" target="_blank" rel="noopener" style="color:var(--text-muted);text-decoration:none;">Extracted from: ${escapeHtml(c.source_url)} ↗</a></div>` : ''}
        </div>
      `).join("");
    } else {
      contactsListHtml = `
        <div class="empty-field-notice">
          <strong>No public business contacts extracted</strong><br>
          No direct email addresses or specific business department numbers were extracted from the crawled pages.
        </div>
      `;
    }

    // --- TAB 6: SOURCE EVIDENCE TAB COMPONENTS ---
    let sourcePagesHtml = '<span class="empty-dash">—</span>';
    if (profile && profile.source_pages && profile.source_pages.length > 0) {
      sourcePagesHtml = `<ul style="margin:0;padding-left:18px;font-size:12px;color:var(--accent-cyan);display:flex;flex-direction:column;gap:6px;">` +
        profile.source_pages.map(sp => `<li><a href="${escapeHtml(sp)}" target="_blank" rel="noopener" style="color:var(--accent-cyan);text-decoration:none;word-break:break-all;">${escapeHtml(sp)} ↗</a></li>`).join("") +
        `</ul>`;
    }

    content.innerHTML = `
      <div class="drawer-header">
        <div>
          <div class="drawer-title">${escapeHtml(p.name)}</div>
          <div style="font-size:12px;color:var(--text-muted);display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
            <span>${escapeHtml(p.category || 'Business')}</span> &bull;
            <span>${escapeHtml(p.city || '')}${p.region ? `, ${escapeHtml(p.region)}` : ''} (${escapeHtml(p.country_code || p.country || 'USA/MX')})</span> &bull;
            <span class="badge badge-status-${p.status}">${escapeHtml(p.status)}</span>
          </div>
        </div>
        <button class="drawer-close" onclick="Drawer.close()">&times;</button>
      </div>

      <!-- 6 Detail Drawer Tabs Navigation -->
      <nav class="drawer-tabs">
        <button class="drawer-tab active" data-tab="overview" onclick="Drawer.switchTab('overview')">1. Overview</button>
        <button class="drawer-tab" data-tab="website" onclick="Drawer.switchTab('website')">2. Website</button>
        <button class="drawer-tab" data-tab="profile" onclick="Drawer.switchTab('profile')">3. Company Profile</button>
        <button class="drawer-tab" data-tab="leadership" onclick="Drawer.switchTab('leadership')">4. Leadership (${leaders.length})</button>
        <button class="drawer-tab" data-tab="contacts" onclick="Drawer.switchTab('contacts')">5. Contacts (${contacts.length})</button>
        <button class="drawer-tab" data-tab="evidence" onclick="Drawer.switchTab('evidence')">6. Source Evidence</button>
      </nav>

      <div class="drawer-body">
        <!-- TAB 1: OVERVIEW -->
        <div id="drawerTab-overview" class="drawer-tab-content active">
          <div class="drawer-section">
            <div class="drawer-section-title">Core Business Identity</div>
            <div class="detail-kv-grid">
              <div class="detail-k">CID</div><div class="detail-v mono-col">${escapeHtml(p.cid)}</div>
              <div class="detail-k">Place ID</div><div class="detail-v mono-col">${renderValue(p.place_id, true)}</div>
              <div class="detail-k">FTID</div><div class="detail-v mono-col">${renderValue(p.ftid, true)}</div>
              <div class="detail-k">Address</div><div class="detail-v">${renderValue(p.address)}</div>
              <div class="detail-k">State / Region</div><div class="detail-v">${renderValue(p.region)} (${renderValue(p.country_code || p.country)})</div>
              <div class="detail-k">Coordinates</div><div class="detail-v mono-col">${p.latitude != null ? `${p.latitude.toFixed(6)}, ${p.longitude.toFixed(6)}` : '<span class="empty-dash">—</span>'}</div>
              <div class="detail-k">Phone</div><div class="detail-v">${renderValue(p.phone)}</div>
              <div class="detail-k">Google Rating</div><div class="detail-v">${p.rating ? `<span class="rating-badge rating-high">${p.rating}★</span> (${p.review_count || 0} reviews)` : '<span class="empty-dash">—</span>'}</div>
              <div class="detail-k">Claimed Status</div><div class="detail-v">${p.claimed_status ? 'Verified / Claimed' : 'Unclaimed'}</div>
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

        <!-- TAB 2: WEBSITE TAB -->
        <div id="drawerTab-website" class="drawer-tab-content">
          <div class="drawer-section">
            <div class="drawer-section-title">Step 2 Official Website Verification</div>
            <div style="margin-bottom:14px;">
              ${webStatusBadge}
            </div>
            <div class="detail-kv-grid">
              <div class="detail-k">Official Website</div>
              <div class="detail-v">
                ${officialUrl ? `<a href="${escapeHtml(officialUrl)}" target="_blank" rel="noopener" style="color:var(--accent-cyan);font-weight:600;text-decoration:none;">${escapeHtml(officialUrl)} ↗</a>` : '<span class="empty-dash">No official website verified yet</span>'}
              </div>
              <div class="detail-k">Candidate Website</div>
              <div class="detail-v">
                ${p.website_candidate ? `<a href="${escapeHtml(p.website_candidate)}" target="_blank" rel="noopener" style="color:var(--text-secondary);text-decoration:none;">${escapeHtml(p.website_candidate)} ↗</a>` : '<span class="empty-dash">—</span>'}
              </div>
              <div class="detail-k">Original Scraped URL</div>
              <div class="detail-v">
                ${p.website ? `<a href="${escapeHtml(p.website)}" target="_blank" rel="noopener" style="color:var(--text-muted);text-decoration:none;">${escapeHtml(p.website)} ↗</a>` : '<span class="empty-dash">—</span>'}
              </div>
              <div class="detail-k">Enrichment State</div>
              <div class="detail-v">
                <span class="badge badge-status-${p.enrichment_status || 'pending'}">${escapeHtml(p.enrichment_status || 'pending')}</span>
              </div>
              <div class="detail-k">Last Checked</div>
              <div class="detail-v mono-col">${renderValue(p.enrichment_last_checked || p.updated_at)}</div>
            </div>
          </div>

          <div class="drawer-section">
            <div class="drawer-section-title">Status Explanation & Matching Signals</div>
            <div class="notice-box">
              ${ws === 'verified' ? '<strong>Verified Official Domain:</strong> This URL matched the business entity with high confidence across domain ownership, business name matching, and telephone cross-referencing.' :
                ws === 'candidate' ? '<strong>Unverified Candidate:</strong> A candidate website was discovered but full multi-signal verification has not been completed.' :
                ws === 'needs_review' ? '<strong>Requires Manual Review:</strong> Discovery found a candidate URL with partial name or geo mismatch that should be verified by a researcher.' :
                ws === 'website_missing' ? '<strong>No Website Published:</strong> Neither Google Maps nor automated discovery queries identified an active website domain.' :
                '<strong>Verification Check Incomplete:</strong> Run Stage 2 in Pipeline Control to process this business record.'}
            </div>
          </div>
        </div>

        <!-- TAB 3: COMPANY PROFILE TAB -->
        <div id="drawerTab-profile" class="drawer-tab-content">
          <div class="drawer-section">
            <div class="drawer-section-title">Step 3 Extracted Company Overview</div>
            ${profile && profile.overview ? `
              <div style="font-size:13px;line-height:1.6;color:var(--text-primary);background:var(--bg-surface);padding:14px;border-radius:var(--radius-md);border:1px solid var(--border-subtle);margin-bottom:14px;">
                ${escapeHtml(profile.overview)}
              </div>
            ` : `
              <div class="empty-field-notice">
                <strong>Company overview not extracted</strong><br>
                Overview text has not been extracted from the website yet. Execute Stage 3 Company Enrichment to crawl and extract public information.
              </div>
            `}

            <div class="detail-kv-grid" style="margin-top:14px;">
              <div class="detail-k">Industry</div>
              <div class="detail-v">${renderValue(profile ? profile.industry : null)}</div>
              <div class="detail-k">Contact Page</div>
              <div class="detail-v">
                ${profile && profile.contact_page_url ? `<a href="${escapeHtml(profile.contact_page_url)}" target="_blank" rel="noopener" style="color:var(--accent-cyan);text-decoration:none;">${escapeHtml(profile.contact_page_url)} ↗</a>` : '<span class="empty-dash">—</span>'}
              </div>
              <div class="detail-k">Extraction State</div>
              <div class="detail-v">
                <span class="badge badge-status-${profile ? profile.extraction_status : 'pending'}">${profile ? escapeHtml(profile.extraction_status) : 'pending'}</span>
              </div>
              <div class="detail-k">Last Extracted</div>
              <div class="detail-v mono-col">${renderValue(profile ? profile.last_extracted : null)}</div>
            </div>
          </div>

          <div class="drawer-section">
            <div class="drawer-section-title">Services & Capabilities</div>
            ${servicesHtml}
          </div>

          <div class="drawer-section">
            <div class="drawer-section-title">Published Locations / Branches</div>
            ${locationsHtml}
          </div>
        </div>

        <!-- TAB 4: LEADERSHIP TAB -->
        <div id="drawerTab-leadership" class="drawer-tab-content">
          <div class="drawer-section">
            <div class="drawer-section-title">Publicly Published Leadership (${leaders.length})</div>
            <div style="font-size:12px;color:var(--text-muted);margin-bottom:12px;">
              Extracted from official 'About Us', 'Leadership', and 'Team' website pages.
            </div>
            ${leadersListHtml}
          </div>
        </div>

        <!-- TAB 5: CONTACTS TAB -->
        <div id="drawerTab-contacts" class="drawer-tab-content">
          <div class="drawer-section">
            <div class="drawer-section-title">Extracted Business Contacts (${contacts.length})</div>
            <div class="notice-box" style="margin-bottom:14px;">
              ℹ️ <strong>Planned Roadmap:</strong> Step 4 will introduce automated email deliverability and phone number validation checks.
            </div>
            ${contactsListHtml}
          </div>
        </div>

        <!-- TAB 6: SOURCE EVIDENCE TAB -->
        <div id="drawerTab-evidence" class="drawer-tab-content">
          <div class="drawer-section">
            <div class="drawer-section-title">Provenance & Source Evidence</div>
            <div class="detail-kv-grid" style="margin-bottom:14px;">
              <div class="detail-k">Verification Status</div>
              <div class="detail-v">${webStatusBadge}</div>
              <div class="detail-k">Official Domain</div>
              <div class="detail-v mono-col">${renderValue(officialUrl)}</div>
              <div class="detail-k">Discovery Candidate</div>
              <div class="detail-v mono-col">${renderValue(p.website_candidate)}</div>
            </div>

            <div class="drawer-section-title" style="margin-top:16px;">Crawled Website Pages</div>
            ${sourcePagesHtml}

            ${evidenceObj ? `
            <div class="drawer-section-title" style="margin-top:16px;">Verification Evidence Payload</div>
            <pre style="background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:var(--radius-md);padding:12px;font-size:11px;overflow-x:auto;color:var(--accent-cyan);">${escapeHtml(JSON.stringify(evidenceObj, null, 2))}</pre>
            ` : ''}
          </div>
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
