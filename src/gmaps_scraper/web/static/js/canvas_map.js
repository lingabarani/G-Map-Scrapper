/**
 * 100% Local Canvas Coordinate Map
 * Renders spatial scatter, pan, zoom, grid clustering, and marker tooltips without external tile servers.
 */

class CanvasMap {
  constructor(canvasId, options = {}) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext("2d");
    this.points = []; // [{cid, lat, lng, name, rating, category}]
    this.onMarkerClick = options.onMarkerClick || null;

    // View state
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this.isDragging = false;
    this.dragStart = { x: 0, y: 0 };
    this.hoveredMarker = null;

    // Bounding box computed from points
    this.bounds = { minLat: 40.0, maxLat: 41.0, minLng: -74.5, maxLng: -73.5 };

    this.initEvents();
    this.resize();
    window.addEventListener("resize", () => this.resize());
  }

  setPoints(points) {
    this.points = points.filter(p => p.lat != null && p.lng != null);
    if (this.points.length > 0) {
      let minLat = 90, maxLat = -90, minLng = 180, maxLng = -180;
      this.points.forEach(p => {
        if (p.lat < minLat) minLat = p.lat;
        if (p.lat > maxLat) maxLat = p.lat;
        if (p.lng < minLng) minLng = p.lng;
        if (p.lng > maxLng) maxLng = p.lng;
      });
      // Add margin
      const latMargin = Math.max((maxLat - minLat) * 0.1, 0.005);
      const lngMargin = Math.max((maxLng - minLng) * 0.1, 0.005);
      this.bounds = {
        minLat: minLat - latMargin,
        maxLat: maxLat + latMargin,
        minLng: minLng - lngMargin,
        maxLng: maxLng + lngMargin
      };
      this.resetView();
    }
    this.render();
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = rect.width * dpr;
    this.canvas.height = rect.height * dpr;
    this.ctx.scale(dpr, dpr);
    this.width = rect.width;
    this.height = rect.height;
    this.render();
  }

  resetView() {
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this.render();
  }

  // Lat/Lng to Canvas pixels (Mercator approximation)
  latLngToScreen(lat, lng) {
    const normX = (lng - this.bounds.minLng) / (this.bounds.maxLng - this.bounds.minLng || 1);
    const normY = 1.0 - ((lat - this.bounds.minLat) / (this.bounds.maxLat - this.bounds.minLat || 1));

    const cx = this.width / 2;
    const cy = this.height / 2;

    const x = (normX * this.width - cx) * this.zoom + cx + this.panX;
    const y = (normY * this.height - cy) * this.zoom + cy + this.panY;
    return { x, y };
  }

  screenToLatLng(x, y) {
    const cx = this.width / 2;
    const cy = this.height / 2;

    const normX = ((x - this.panX - cx) / this.zoom + cx) / this.width;
    const normY = ((y - this.panY - cy) / this.zoom + cy) / this.height;

    const lng = this.bounds.minLng + normX * (this.bounds.maxLng - this.bounds.minLng);
    const lat = this.bounds.minLat + (1.0 - normY) * (this.bounds.maxLat - this.bounds.minLat);
    return { lat, lng };
  }

  render() {
    const ctx = this.ctx;
    const width = this.width;
    const height = this.height;
    ctx.clearRect(0, 0, width, height);

    // Background grid
    ctx.fillStyle = "#0c101c";
    ctx.fillRect(0, 0, width, height);

    // Draw coordinate radar / grid lines
    ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
    ctx.lineWidth = 1;
    const gridSize = 40 * this.zoom;
    const startX = (this.panX % gridSize + gridSize) % gridSize;
    const startY = (this.panY % gridSize + gridSize) % gridSize;

    for (let x = startX; x < width; x += gridSize) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    }
    for (let y = startY; y < height; y += gridSize) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }

    if (this.points.length === 0) {
      ctx.fillStyle = "#64748b";
      ctx.font = "14px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No geolocated places found for current filter", width / 2, height / 2);
      return;
    }

    // Grid clustering: group points falling into ~30px cells
    const cellSize = 36;
    const clusters = {};

    this.points.forEach(p => {
      const pos = this.latLngToScreen(p.lat, p.lng);
      // Skip if off-canvas
      if (pos.x < -20 || pos.x > width + 20 || pos.y < -20 || pos.y > height + 20) return;

      const cellKey = `${Math.floor(pos.x / cellSize)},${Math.floor(pos.y / cellSize)}`;
      if (!clusters[cellKey]) {
        clusters[cellKey] = { x: pos.x, y: pos.y, count: 0, items: [] };
      }
      clusters[cellKey].count++;
      clusters[cellKey].items.push(p);
    });

    // Render markers / clusters
    Object.values(clusters).forEach(cl => {
      if (cl.count > 1 && this.zoom < 3.5) {
        // Draw cluster bubble
        const radius = Math.min(22, 12 + Math.log2(cl.count) * 3);
        ctx.fillStyle = "rgba(56, 189, 248, 0.25)";
        ctx.beginPath();
        ctx.arc(cl.x, cl.y, radius + 4, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = "#38bdf8";
        ctx.beginPath();
        ctx.arc(cl.x, cl.y, radius, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = "#0b0f19";
        ctx.font = "bold 11px ui-monospace, monospace";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(cl.count.toString(), cl.x, cl.y);
      } else {
        // Single marker
        const item = cl.items[0];
        const isHovered = this.hoveredMarker && this.hoveredMarker.cid === item.cid;
        const radius = isHovered ? 8 : 5.5;

        // Pulse glow
        ctx.fillStyle = item.rating >= 4.5 ? "rgba(16, 185, 129, 0.4)" : "rgba(56, 189, 248, 0.4)";
        ctx.beginPath();
        ctx.arc(cl.x, cl.y, radius + 4, 0, Math.PI * 2);
        ctx.fill();

        // Pin center
        ctx.fillStyle = item.rating >= 4.5 ? "#10b981" : "#38bdf8";
        ctx.beginPath();
        ctx.arc(cl.x, cl.y, radius, 0, Math.PI * 2);
        ctx.fill();

        // Draw place label if zoomed in or hovered
        if (this.zoom >= 2.0 || isHovered) {
          ctx.fillStyle = isHovered ? "#ffffff" : "#94a3b8";
          ctx.font = isHovered ? "bold 12px sans-serif" : "10px sans-serif";
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";
          ctx.fillText(item.name, cl.x, cl.y - radius - 3);
        }
      }
    });

    // Tooltip for hovered item
    if (this.hoveredMarker) {
      const pos = this.latLngToScreen(this.hoveredMarker.lat, this.hoveredMarker.lng);
      this.drawTooltip(pos.x, pos.y, this.hoveredMarker);
    }

    // Coordinates overlay HUD in bottom corner
    ctx.fillStyle = "rgba(16, 21, 34, 0.85)";
    ctx.strokeStyle = "rgba(255, 255, 255, 0.1)";
    ctx.beginPath();
    ctx.roundRect(12, height - 34, 210, 24, 4);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = "#94a3b8";
    ctx.font = "10.5px ui-monospace, monospace";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(`Zoom: ${this.zoom.toFixed(1)}x | Places: ${this.points.length}`, 20, height - 22);
  }

  drawTooltip(x, y, item) {
    const ctx = this.ctx;
    const title = item.name;
    const sub = `${item.rating ? item.rating + "★ " : ""}${item.category || ""}`;

    ctx.font = "bold 12px sans-serif";
    const titleW = ctx.measureText(title).width;
    ctx.font = "11px sans-serif";
    const subW = ctx.measureText(sub).width;
    const boxW = Math.max(titleW, subW) + 24;
    const boxH = 46;

    const boxX = Math.min(Math.max(10, x - boxW / 2), this.width - boxW - 10);
    const boxY = Math.max(10, y - boxH - 12);

    ctx.fillStyle = "#161c2d";
    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.roundRect(boxX, boxY, boxW, boxH, 6);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = "#f1f5f9";
    ctx.font = "bold 12px sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(title, boxX + 12, boxY + 18);

    ctx.fillStyle = "#94a3b8";
    ctx.font = "11px sans-serif";
    ctx.fillText(sub, boxX + 12, boxY + 36);
  }

  initEvents() {
    this.canvas.addEventListener("mousedown", e => {
      this.isDragging = true;
      this.dragStart = { x: e.clientX - this.panX, y: e.clientY - this.panY };
    });

    window.addEventListener("mousemove", e => {
      const rect = this.canvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;

      if (this.isDragging) {
        this.panX = e.clientX - this.dragStart.x;
        this.panY = e.clientY - this.dragStart.y;
        this.render();
      } else {
        // Check hover
        let hit = null;
        for (const p of this.points) {
          const pos = this.latLngToScreen(p.lat, p.lng);
          const dist = Math.hypot(pos.x - mouseX, pos.y - mouseY);
          if (dist < 12) {
            hit = p;
            break;
          }
        }
        if (hit !== this.hoveredMarker) {
          this.hoveredMarker = hit;
          this.canvas.style.cursor = hit ? "pointer" : "default";
          this.render();
        }
      }
    });

    window.addEventListener("mouseup", () => {
      this.isDragging = false;
    });

    this.canvas.addEventListener("wheel", e => {
      e.preventDefault();
      const zoomFactor = e.deltaY < 0 ? 1.15 : 0.85;
      const newZoom = Math.min(Math.max(this.zoom * zoomFactor, 0.5), 25.0);

      // Zoom towards mouse position
      const rect = this.canvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;

      this.panX = mouseX - (mouseX - this.panX) * (newZoom / this.zoom);
      this.panY = mouseY - (mouseY - this.panY) * (newZoom / this.zoom);
      this.zoom = newZoom;
      this.render();
    });

    this.canvas.addEventListener("click", e => {
      if (this.hoveredMarker && this.onMarkerClick) {
        this.onMarkerClick(this.hoveredMarker);
      }
    });
  }
}

window.CanvasMap = CanvasMap;
