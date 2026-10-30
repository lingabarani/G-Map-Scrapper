/**
 * 100% Local Vanilla Canvas Charts (Zero CDN, Zero Dependencies)
 */

window.CanvasCharts = {
  /**
   * Draws rating distribution histogram (1 to 5 stars)
   */
  drawRatingHistogram(canvasId, distribution) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);

    const stars = ["1", "2", "3", "4", "5"];
    const counts = stars.map(s => distribution[s] || 0);
    const maxVal = Math.max(1, ...counts);

    const padding = { top: 20, right: 20, bottom: 30, left: 40 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;
    const barWidth = Math.min(48, (chartW / stars.length) * 0.65);
    const gap = (chartW - (barWidth * stars.length)) / (stars.length + 1);

    // Gridlines
    ctx.strokeStyle = "rgba(255, 255, 255, 0.07)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padding.left, padding.top + chartH);
    ctx.lineTo(width - padding.right, padding.top + chartH);
    ctx.stroke();

    stars.forEach((star, i) => {
      const val = counts[i];
      const barH = (val / maxVal) * (chartH - 10);
      const x = padding.left + gap + i * (barWidth + gap);
      const y = padding.top + chartH - barH;

      // Color scheme gradient based on star
      const grad = ctx.createLinearGradient(0, y, 0, padding.top + chartH);
      if (star >= 4) {
        grad.addColorStop(0, "#10b981");
        grad.addColorStop(1, "rgba(16, 185, 129, 0.2)");
      } else if (star == 3) {
        grad.addColorStop(0, "#f59e0b");
        grad.addColorStop(1, "rgba(245, 158, 11, 0.2)");
      } else {
        grad.addColorStop(0, "#f43f5e");
        grad.addColorStop(1, "rgba(244, 63, 94, 0.2)");
      }

      // Rounded bar top
      ctx.fillStyle = grad;
      ctx.beginPath();
      const r = Math.min(4, barH);
      ctx.roundRect(x, y, barWidth, barH, [r, r, 0, 0]);
      ctx.fill();

      // Count label above bar
      ctx.fillStyle = "#94a3b8";
      ctx.font = "11px ui-monospace, monospace";
      ctx.textAlign = "center";
      ctx.fillText(val.toString(), x + barWidth / 2, y - 5);

      // Star label below
      ctx.fillStyle = "#cbd5e1";
      ctx.font = "11px -apple-system, sans-serif";
      ctx.fillText(star + "★", x + barWidth / 2, padding.top + chartH + 18);
    });
  },

  /**
   * Draws top-20 categories horizontal bar chart
   */
  drawCategoriesChart(canvasId, categories) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);

    if (!categories || categories.length === 0) {
      ctx.fillStyle = "#64748b";
      ctx.font = "12px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No category data available", width / 2, height / 2);
      return;
    }

    const items = categories.slice(0, 10); // Top 10 for clean display
    const maxVal = Math.max(1, ...items.map(it => it.count));
    const padding = { top: 10, right: 45, bottom: 10, left: 130 };
    const rowH = (height - padding.top - padding.bottom) / items.length;
    const barMaxW = width - padding.left - padding.right;

    items.forEach((item, i) => {
      const y = padding.top + i * rowH;
      const barW = Math.max(4, (item.count / maxVal) * barMaxW);
      const barH = rowH * 0.55;

      // Category label (truncated)
      ctx.fillStyle = "#cbd5e1";
      ctx.font = "11px -apple-system, sans-serif";
      ctx.textAlign = "right";
      let name = item.category || "Uncategorized";
      if (name.length > 17) name = name.substring(0, 15) + "…";
      ctx.fillText(name, padding.left - 10, y + barH * 0.8);

      // Bar
      const grad = ctx.createLinearGradient(padding.left, 0, padding.left + barW, 0);
      grad.addColorStop(0, "#38bdf8");
      grad.addColorStop(1, "rgba(56, 189, 248, 0.4)");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.roundRect(padding.left, y, barW, barH, [0, 3, 3, 0]);
      ctx.fill();

      // Count label
      ctx.fillStyle = "#94a3b8";
      ctx.font = "10.5px ui-monospace, monospace";
      ctx.textAlign = "left";
      ctx.fillText(item.count.toString(), padding.left + barW + 8, y + barH * 0.8);
    });
  },

  /**
   * Draws a mini sparkline
   */
  drawSparkline(canvasId, points) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);

    if (!points || points.length < 2) return;
    const minVal = Math.min(...points);
    const maxVal = Math.max(...points) || 1;
    const range = (maxVal - minVal) || 1;

    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 2;
    ctx.beginPath();

    points.forEach((p, i) => {
      const x = (i / (points.length - 1)) * (width - 8) + 4;
      const y = height - 4 - ((p - minVal) / range) * (height - 8);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  },

  /**
   * Draws popular times hourly histogram
   */
  drawPopularTimes(canvasId, hourlyData) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);

    if (!hourlyData || hourlyData.length === 0) {
      ctx.fillStyle = "#64748b";
      ctx.font = "11px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No popular times recorded", width / 2, height / 2);
      return;
    }

    const padding = { top: 15, right: 10, bottom: 20, left: 10 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;
    const slotW = chartW / 24;

    // Fill map for all 24 hours
    const hoursMap = {};
    hourlyData.forEach(h => { hoursMap[h.hour_idx] = h.popularity_pct; });

    for (let h = 0; h < 24; h++) {
      const pop = hoursMap[h] || 0;
      const barH = (pop / 100) * chartH;
      const x = padding.left + h * slotW + 1;
      const y = padding.top + chartH - barH;

      ctx.fillStyle = pop > 70 ? "#f43f5e" : pop > 40 ? "#38bdf8" : "#64748b";
      ctx.fillRect(x, y, Math.max(2, slotW - 2), barH);

      if (h % 4 === 0) {
        ctx.fillStyle = "#64748b";
        ctx.font = "9px ui-monospace, monospace";
        ctx.textAlign = "center";
        const label = h === 0 ? "12a" : h === 12 ? "12p" : h > 12 ? (h - 12) + "p" : h + "a";
        ctx.fillText(label, x + slotW / 2, padding.top + chartH + 14);
      }
    }
  },

  /**
   * Draws generic horizontal bars (e.g. for Countries/Regions or Extraction Coverage)
   */
  drawHorizontalBarChart(canvasId, items, defaultColor = "#38bdf8") {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);

    if (!items || items.length === 0) {
      ctx.fillStyle = "#64748b";
      ctx.font = "12px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No data available", width / 2, height / 2);
      return;
    }

    const maxVal = Math.max(1, ...items.map(it => it.count || it.value || 0));
    const padding = { top: 12, right: 55, bottom: 12, left: 110 };
    const rowH = (height - padding.top - padding.bottom) / items.length;
    const barMaxW = width - padding.left - padding.right;

    items.forEach((item, i) => {
      const val = item.count !== undefined ? item.count : (item.value || 0);
      const y = padding.top + i * rowH;
      const barW = Math.max(4, (val / maxVal) * barMaxW);
      const barH = Math.min(22, rowH * 0.65);
      const color = item.color || defaultColor;

      // Label
      ctx.fillStyle = "#cbd5e1";
      ctx.font = "11px -apple-system, BlinkMacSystemFont, sans-serif";
      ctx.textAlign = "right";
      let name = item.label || item.name || item.category || "";
      if (name.length > 15) name = name.substring(0, 13) + "…";
      ctx.fillText(name, padding.left - 10, y + barH * 0.75);

      // Bar
      const grad = ctx.createLinearGradient(padding.left, 0, padding.left + barW, 0);
      grad.addColorStop(0, color);
      grad.addColorStop(1, color + "aa");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.roundRect(padding.left, y, barW, barH, [0, 3, 3, 0]);
      ctx.fill();

      // Value label
      ctx.fillStyle = "#94a3b8";
      ctx.font = "10.5px ui-monospace, monospace";
      ctx.textAlign = "left";
      const valText = item.suffix ? `${val}${item.suffix}` : val.toString();
      ctx.fillText(valText, padding.left + barW + 8, y + barH * 0.75);
    });
  },

  /**
   * Draws a donut chart with legend
   */
  drawDonutChart(canvasId, segments) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);

    const validSegs = (segments || []).filter(s => (s.value || 0) > 0);
    const total = validSegs.reduce((sum, s) => sum + s.value, 0);

    if (total === 0) {
      ctx.fillStyle = "#64748b";
      ctx.font = "12px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No data available", width / 2, height / 2);
      return;
    }

    const centerX = width * 0.35;
    const centerY = height * 0.5;
    const radius = Math.min(centerX - 20, centerY - 15);
    const innerRadius = radius * 0.55;

    let startAngle = -Math.PI / 2;

    validSegs.forEach(seg => {
      const sliceAngle = (seg.value / total) * 2 * Math.PI;
      const endAngle = startAngle + sliceAngle;

      ctx.beginPath();
      ctx.arc(centerX, centerY, radius, startAngle, endAngle);
      ctx.arc(centerX, centerY, innerRadius, endAngle, startAngle, true);
      ctx.closePath();

      ctx.fillStyle = seg.color || "#38bdf8";
      ctx.fill();

      startAngle = endAngle;
    });

    // Center total text
    ctx.fillStyle = "#f8fafc";
    ctx.font = "bold 14px ui-monospace, monospace";
    ctx.textAlign = "center";
    ctx.fillText(total.toString(), centerX, centerY + 5);

    // Legend on the right
    const legendX = width * 0.62;
    const legendY = Math.max(15, (height - validSegs.length * 20) / 2);

    validSegs.forEach((seg, i) => {
      const ly = legendY + i * 22;
      // Color dot
      ctx.fillStyle = seg.color || "#38bdf8";
      ctx.beginPath();
      ctx.arc(legendX, ly + 4, 4, 0, 2 * Math.PI);
      ctx.fill();

      // Text
      ctx.fillStyle = "#cbd5e1";
      ctx.font = "11px -apple-system, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(`${seg.label}: ${seg.value}`, legendX + 12, ly + 8);
    });
  }
};

