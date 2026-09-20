#!/usr/bin/env python3
"""
EasyNest v2 - Industrial Batch Nesting Local Visualizer
======================================================
A lightweight, zero-dependency visualizer to inspect nesting outputs and batches.
Runs locally using Python's standard library http.server.

Features:
- Responsive desktop & mobile UI (works directly from your phone/tablet/laptop)
- Interactive vector SVG pan & zoom (scroll wheel, touch pinch, drag)
- Instant part inspection: hover to view part name, ID, and position
- Side-by-side / toggle view for Rendered PNG previews
- Automatic detection of Reusable Remnants and Straight Guillotine Cut Lines
- Live auto-refresh when new batch nests are generated
"""

import os
import sys
import glob
import re
import json
import socket
import socketserver
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, List, Optional

PORT = 8080
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def get_local_ip() -> str:
    """Returns the primary local network IP for accessing from other devices."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def parse_sheet_metadata(svg_path: str) -> Dict[str, Any]:
    """Extracts nesting metrics, part counts, and remnant information from an SVG file."""
    filename = os.path.basename(svg_path)
    base_name = os.path.splitext(filename)[0]
    preview_png = f"{base_name}_preview.png"
    has_png = os.path.exists(os.path.join(OUTPUT_DIR, preview_png))

    with open(svg_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Parse part names and counts
    parts = re.findall(r'data-part(?:-name)?="([^"]+)"', content)
    part_counts: Dict[str, int] = {}
    for p in parts:
        part_counts[p] = part_counts.get(p, 0) + 1

    # Extract viewBox
    vb_match = re.search(r'viewBox="([^"]+)"', content)
    viewbox = vb_match.group(1) if vb_match else "0 0 122000 244000"

    # Guillotine cut line detection
    has_cut = ("guillotine-cut-line" in content or "STRAIGHT GUILLOTINE" in content)
    cut_x_match = re.search(r'X\s*=\s*([0-9.]+)\s*mm', content)
    cut_x_mm = float(cut_x_match.group(1)) if (has_cut and cut_x_match) else None

    # Remnant detection
    remnant_match = re.search(r'REUSABLE VIRGIN REMNANT\s*\(([^\)]+)\)', content)
    remnant_dims = remnant_match.group(1).strip() if remnant_match else None

    # Condition / Batch category classification
    condition_id = "other"
    condition_title = "Other Outputs"
    sheet_num = 1

    if "cond1" in filename:
        condition_id = "cond1"
        condition_title = "Condition 1: Single-Part Max Fit"
    elif "cond2" in filename:
        condition_id = "cond2"
        condition_title = "Condition 2: Max Mixed Fit (Ideal Pair)"
    elif "cond3" in filename:
        condition_id = "cond3"
        condition_title = "Condition 3: Custom Fixed Order (45 Fairings)"
    elif "cond4" in filename:
        condition_id = "cond4"
        condition_title = "Condition 4: Mixed Custom Assembly BOM"
    elif "cond5" in filename:
        condition_id = "cond5"
        condition_title = "Condition 5: Rush Kanban + Remnant Salvage"

    sheet_match = re.search(r'sheet_(\d+)', filename)
    if sheet_match:
        sheet_num = int(sheet_match.group(1))

    is_partial = (remnant_dims is not None) or ("PARTIAL" in content)

    return {
        "filename": filename,
        "svg_url": f"/output/{filename}",
        "png_url": f"/output/{preview_png}" if has_png else None,
        "has_png": has_png,
        "condition_id": condition_id,
        "condition_title": condition_title,
        "sheet_num": sheet_num,
        "total_parts": len(parts),
        "part_counts": part_counts,
        "is_partial": is_partial,
        "cut_x_mm": cut_x_mm,
        "remnant_dims": remnant_dims,
        "viewbox": viewbox,
        "mtime": os.path.getmtime(svg_path)
    }


def get_all_batches() -> List[Dict[str, Any]]:
    """Gathers and groups all available output sheets."""
    svg_files = glob.glob(os.path.join(OUTPUT_DIR, "*.svg"))
    sheets = [parse_sheet_metadata(p) for p in svg_files]

    # Sort logically by condition and sheet number
    def sort_key(s: Dict[str, Any]):
        cond_order = {"cond1": 1, "cond2": 2, "cond3": 3, "cond4": 4, "cond5": 5, "other": 99}
        return (cond_order.get(s["condition_id"], 99), s["sheet_num"], s["filename"])

    sheets.sort(key=sort_key)
    return sheets


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>EasyNest v2 — Production Visualizer</title>
  <style>
    :root {
      --bg: #090d16;
      --card-bg: #111827;
      --card-border: #1f2937;
      --text: #f3f4f6;
      --text-dim: #9ca3af;
      --accent: #38bdf8;
      --accent-glow: rgba(56, 189, 248, 0.15);
      --emerald: #10b981;
      --amber: #f59e0b;
      --rose: #f43f5e;
      --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: var(--font-sans);
      height: 100dvh;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }

    /* --- TOP NAVBAR --- */
    header {
      background: #0d1322;
      border-bottom: 1px solid var(--card-border);
      padding: 0.6rem 1.2rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      z-index: 20;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .brand-badge {
      background: linear-gradient(135deg, #0284c7, #2563eb);
      color: white;
      font-weight: 800;
      font-size: 0.8rem;
      padding: 0.25rem 0.6rem;
      border-radius: 6px;
      letter-spacing: 0.05em;
    }
    .brand-title {
      font-weight: 700;
      font-size: 1.05rem;
      letter-spacing: -0.01em;
    }
    .status-pill {
      font-size: 0.75rem;
      background: rgba(16, 185, 129, 0.15);
      color: var(--emerald);
      border: 1px solid rgba(16, 185, 129, 0.3);
      padding: 0.2rem 0.5rem;
      border-radius: 9999px;
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }
    .status-dot {
      width: 6px;
      height: 6px;
      background: var(--emerald);
      border-radius: 50%;
      box-shadow: 0 0 8px var(--emerald);
    }

    .top-actions {
      display: flex;
      align-items: center;
      gap: 0.6rem;
    }
    .btn {
      background: #1e293b;
      color: var(--text);
      border: 1px solid #334155;
      padding: 0.45rem 0.85rem;
      border-radius: 6px;
      font-size: 0.85rem;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      transition: all 0.15s ease;
      user-select: none;
    }
    .btn:hover { background: #334155; border-color: #475569; }
    .btn:active { transform: scale(0.98); }
    .btn-accent {
      background: #0284c7;
      border-color: #0369a1;
      color: white;
    }
    .btn-accent:hover { background: #0369a1; }
    .btn-group {
      display: flex;
      background: #1e293b;
      border-radius: 6px;
      padding: 2px;
      border: 1px solid #334155;
    }
    .btn-group .btn {
      border: none;
      background: transparent;
      padding: 0.35rem 0.75rem;
      border-radius: 4px;
    }
    .btn-group .btn.active {
      background: #0284c7;
      color: white;
      box-shadow: 0 1px 3px rgba(0,0,0,0.3);
    }

    /* --- APP LAYOUT --- */
    .app-container {
      display: flex;
      flex: 1;
      height: calc(100dvh - 54px);
      overflow: hidden;
    }

    /* --- SIDEBAR --- */
    aside {
      width: 320px;
      min-width: 280px;
      background: #0c111e;
      border-right: 1px solid var(--card-border);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .sidebar-header {
      padding: 0.9rem 1rem;
      border-bottom: 1px solid var(--card-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .sidebar-title {
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-dim);
      font-weight: 700;
    }
    .sheet-list {
      flex: 1;
      overflow-y: auto;
      padding: 0.75rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .condition-group-title {
      font-size: 0.75rem;
      font-weight: 700;
      color: #38bdf8;
      padding: 0.4rem 0.5rem;
      letter-spacing: 0.04em;
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }
    .sheet-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 0.75rem;
      cursor: pointer;
      transition: all 0.15s ease;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .sheet-card:hover {
      border-color: #38bdf8;
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    }
    .sheet-card.active {
      border-color: #38bdf8;
      background: #14213d;
      box-shadow: 0 0 0 1px #38bdf8, 0 4px 12px rgba(56, 189, 248, 0.15);
    }
    .card-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .card-title {
      font-size: 0.88rem;
      font-weight: 600;
      color: #f9fafb;
    }
    .badge {
      font-size: 0.7rem;
      padding: 0.15rem 0.45rem;
      border-radius: 4px;
      font-weight: 600;
      letter-spacing: 0.02em;
    }
    .badge-full {
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.3);
    }
    .badge-remnant {
      background: rgba(245, 158, 11, 0.15);
      color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.3);
    }
    .card-metrics {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      font-size: 0.78rem;
      color: var(--text-dim);
    }
    .metric-item {
      display: flex;
      align-items: center;
      gap: 0.3rem;
    }

    /* --- MAIN VIEWPORT --- */
    main {
      flex: 1;
      display: flex;
      flex-direction: column;
      background: #070a12;
      position: relative;
      overflow: hidden;
    }
    .viewport-toolbar {
      position: absolute;
      top: 1rem;
      right: 1rem;
      z-index: 10;
      display: flex;
      align-items: center;
      gap: 0.4rem;
      background: rgba(17, 24, 39, 0.85);
      backdrop-filter: blur(8px);
      padding: 0.3rem 0.5rem;
      border-radius: 8px;
      border: 1px solid var(--card-border);
      box-shadow: 0 4px 16px rgba(0,0,0,0.4);
    }
    .toolbar-btn {
      background: transparent;
      border: none;
      color: var(--text);
      width: 32px;
      height: 32px;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 0.9rem;
      transition: background 0.15s;
    }
    .toolbar-btn:hover { background: #334155; }
    .zoom-level-label {
      font-size: 0.75rem;
      font-family: var(--font-mono);
      min-width: 52px;
      text-align: center;
      color: var(--text-dim);
    }

    .canvas-container {
      flex: 1;
      position: relative;
      overflow: hidden;
      cursor: grab;
      display: flex;
      align-items: center;
      justify-content: center;
      background-image: radial-gradient(rgba(255, 255, 255, 0.05) 1px, transparent 0);
      background-size: 24px 24px;
    }
    .canvas-container:active { cursor: grabbing; }

    .sheet-wrapper {
      position: absolute;
      transform-origin: 0 0;
      will-change: transform;
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(255, 255, 255, 0.1);
      background: #ffffff;
      transition: box-shadow 0.2s ease;
    }

    #svg-host, #img-host {
      width: 100%;
      height: 100%;
      display: block;
    }
    #svg-host svg {
      width: 100%;
      height: 100%;
      display: block;
    }

    /* Part hover highlight */
    .nested-part {
      transition: opacity 0.15s, stroke-width 0.15s;
      cursor: pointer;
    }
    .nested-part:hover path {
      stroke: #38bdf8 !important;
      stroke-width: 80 !important;
      fill-opacity: 0.75 !important;
    }

    /* --- BOTTOM INSPECTION DRAWER --- */
    .inspector-drawer {
      background: #0d1322;
      border-top: 1px solid var(--card-border);
      padding: 0.75rem 1.2rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1.5rem;
      z-index: 10;
    }
    .stat-pill-group {
      display: flex;
      align-items: center;
      gap: 1rem;
      flex-wrap: wrap;
    }
    .stat-pill {
      display: flex;
      flex-direction: column;
    }
    .stat-label {
      font-size: 0.65rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-dim);
      font-weight: 600;
    }
    .stat-val {
      font-size: 0.95rem;
      font-weight: 700;
      color: #f3f4f6;
      font-family: var(--font-mono);
    }
    .stat-val.accent { color: #38bdf8; }
    .stat-val.emerald { color: #34d399; }
    .stat-val.amber { color: #fbbf24; }

    .part-pills {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      flex-wrap: wrap;
      max-width: 550px;
    }
    .part-tag {
      background: #1e293b;
      border: 1px solid #334155;
      color: #cbd5e1;
      padding: 0.2rem 0.55rem;
      border-radius: 4px;
      font-size: 0.75rem;
      font-family: var(--font-mono);
      display: flex;
      align-items: center;
      gap: 0.4rem;
    }
    .part-tag span {
      color: #38bdf8;
      font-weight: 700;
    }

    /* Tooltip */
    #hover-tooltip {
      position: fixed;
      pointer-events: none;
      background: rgba(15, 23, 42, 0.95);
      border: 1px solid #38bdf8;
      box-shadow: 0 8px 24px rgba(0,0,0,0.5);
      color: white;
      padding: 0.4rem 0.75rem;
      border-radius: 6px;
      font-size: 0.8rem;
      font-family: var(--font-mono);
      z-index: 1000;
      display: none;
    }

    /* Mobile responsive */
    @media (max-width: 800px) {
      aside { width: 100%; height: 200px; }
      .app-container { flex-direction: column; }
      .inspector-drawer { flex-direction: column; align-items: flex-start; }
    }
  </style>
</head>
<body>

  <!-- Top Bar -->
  <header>
    <div class="brand">
      <span class="brand-badge">EASYNEST v2</span>
      <span class="brand-title">Production Batch Visualizer</span>
      <div class="status-pill">
        <span class="status-dot"></span>
        <span id="host-label">Local Host</span>
      </div>
    </div>

    <div class="top-actions">
      <!-- Vector vs PNG Preview Toggle -->
      <div class="btn-group">
        <button id="view-mode-svg" class="btn active" onclick="setViewMode('svg')">Vector SVG</button>
        <button id="view-mode-png" class="btn" onclick="setViewMode('png')">Rendered PNG</button>
      </div>

      <button class="btn" onclick="prevSheet()" title="Previous Sheet (←)">◀ Prev</button>
      <button class="btn" onclick="nextSheet()" title="Next Sheet (→)">Next ▶</button>
      <button class="btn btn-accent" onclick="loadBatches()" title="Rescan Output Folder">↻ Refresh</button>
    </div>
  </header>

  <!-- Main App Layout -->
  <div class="app-container">

    <!-- Sidebar: Batch & Sheet Selector -->
    <aside>
      <div class="sidebar-header">
        <span class="sidebar-title">Production Sheets</span>
        <span id="sheet-count-badge" class="badge badge-full">0 Sheets</span>
      </div>
      <div id="sheet-list" class="sheet-list">
        <!-- Rendered dynamically -->
      </div>
    </aside>

    <!-- Main Canvas Viewport -->
    <main>
      <!-- Floating Viewport Controls -->
      <div class="viewport-toolbar">
        <button class="toolbar-btn" onclick="zoomIn()" title="Zoom In (+)">＋</button>
        <span id="zoom-text" class="zoom-level-label">100%</span>
        <button class="toolbar-btn" onclick="zoomOut()" title="Zoom Out (-)">－</button>
        <button class="toolbar-btn" onclick="fitToScreen()" title="Fit Sheet to Window (F)">⛶</button>
        <button class="toolbar-btn" onclick="resetZoom()" title="1:1 Pixel Scale (1)">1:1</button>
      </div>

      <!-- Canvas Pan / Zoom Area -->
      <div id="canvas-container" class="canvas-container">
        <div id="sheet-wrapper" class="sheet-wrapper">
          <div id="svg-host"></div>
          <img id="img-host" style="display: none;" alt="Preview" />
        </div>
      </div>

      <!-- Bottom Information Drawer -->
      <div class="inspector-drawer">
        <div class="stat-pill-group">
          <div class="stat-pill">
            <span class="stat-label">Sheet Dimensions</span>
            <span class="stat-val">1220 × 2440 mm</span>
          </div>
          <div class="stat-pill">
            <span class="stat-label">Total Parts Nested</span>
            <span id="stat-total-parts" class="stat-val emerald">-</span>
          </div>
          <div class="stat-pill" id="stat-cut-box" style="display: none;">
            <span class="stat-label">✂ Guillotine Cut</span>
            <span id="stat-cut-x" class="stat-val amber">-</span>
          </div>
          <div class="stat-pill" id="stat-remnant-box" style="display: none;">
            <span class="stat-label">📦 Virgin Remnant</span>
            <span id="stat-remnant" class="stat-val emerald">-</span>
          </div>
        </div>

        <!-- Part counts tags -->
        <div class="stat-pill">
          <span class="stat-label">Part Manifest</span>
          <div id="part-pills" class="part-pills">
            <!-- Rendered dynamically -->
          </div>
        </div>
      </div>

    </main>

  </div>

  <div id="hover-tooltip"></div>

  <script>
    let allSheets = [];
    let currentSheetIndex = 0;
    let viewMode = 'svg'; // 'svg' or 'png'

    // Pan & Zoom State
    let scale = 1.0;
    let panX = 0;
    let panY = 0;
    let isDragging = false;
    let startX = 0;
    let startY = 0;

    const canvas = document.getElementById('canvas-container');
    const wrapper = document.getElementById('sheet-wrapper');
    const svgHost = document.getElementById('svg-host');
    const imgHost = document.getElementById('img-host');
    const zoomText = document.getElementById('zoom-text');
    const tooltip = document.getElementById('hover-tooltip');

    function updateTransform() {
      wrapper.style.transform = `translate(${panX}px, ${panY}px) scale(${scale})`;
      zoomText.textContent = `${Math.round(scale * 100)}%`;
    }

    function zoomIn() {
      scale = Math.min(scale * 1.25, 20.0);
      updateTransform();
    }

    function zoomOut() {
      scale = Math.max(scale / 1.25, 0.05);
      updateTransform();
    }

    function resetZoom() {
      scale = 1.0;
      panX = (canvas.clientWidth - wrapper.offsetWidth * scale) / 2;
      panY = (canvas.clientHeight - wrapper.offsetHeight * scale) / 2;
      updateTransform();
    }

    function fitToScreen() {
      if (!wrapper.offsetWidth || !wrapper.offsetHeight) return;
      const pad = 40;
      const availW = canvas.clientWidth - pad * 2;
      const availH = canvas.clientHeight - pad * 2;
      const scaleX = availW / wrapper.offsetWidth;
      const scaleY = availH / wrapper.offsetHeight;
      scale = Math.min(scaleX, scaleY, 1.5);
      panX = (canvas.clientWidth - wrapper.offsetWidth * scale) / 2;
      panY = (canvas.clientHeight - wrapper.offsetHeight * scale) / 2;
      updateTransform();
    }

    // Mouse drag pan
    canvas.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      isDragging = true;
      startX = e.clientX - panX;
      startY = e.clientY - panY;
    });

    window.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      panX = e.clientX - startX;
      panY = e.clientY - startY;
      updateTransform();
    });

    window.addEventListener('mouseup', () => { isDragging = false; });

    // Scroll wheel zoom
    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;

      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      const newScale = Math.min(Math.max(scale * factor, 0.05), 20.0);

      panX = mouseX - (mouseX - panX) * (newScale / scale);
      panY = mouseY - (mouseY - panY) * (newScale / scale);
      scale = newScale;
      updateTransform();
    }, { passive: false });

    // Keyboard Shortcuts
    window.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight' || e.key === 'j') nextSheet();
      else if (e.key === 'ArrowLeft' || e.key === 'k') prevSheet();
      else if (e.key === 'f') fitToScreen();
      else if (e.key === '1') resetZoom();
      else if (e.key === '+' || e.key === '=') zoomIn();
      else if (e.key === '-') zoomOut();
      else if (e.key === 'v') setViewMode(viewMode === 'svg' ? 'png' : 'svg');
    });

    function setViewMode(mode) {
      viewMode = mode;
      document.getElementById('view-mode-svg').classList.toggle('active', mode === 'svg');
      document.getElementById('view-mode-png').classList.toggle('active', mode === 'png');

      const sheet = allSheets[currentSheetIndex];
      if (!sheet) return;

      if (mode === 'png' && sheet.has_png) {
        svgHost.style.display = 'none';
        imgHost.style.display = 'block';
        imgHost.src = sheet.png_url;
      } else {
        svgHost.style.display = 'block';
        imgHost.style.display = 'none';
      }
    }

    function renderSheetList() {
      const list = document.getElementById('sheet-list');
      list.innerHTML = '';

      let currentGroup = '';
      allSheets.forEach((sheet, idx) => {
        if (sheet.condition_title !== currentGroup) {
          currentGroup = sheet.condition_title;
          const grp = document.createElement('div');
          grp.className = 'condition-group-title';
          grp.textContent = currentGroup;
          list.appendChild(grp);
        }

        const card = document.createElement('div');
        card.className = `sheet-card ${idx === currentSheetIndex ? 'active' : ''}`;
        card.onclick = () => selectSheet(idx);

        const badgeHtml = sheet.is_partial
          ? `<span class="badge badge-remnant">✂ Remnant (${sheet.remnant_dims || 'Trimmed'})</span>`
          : `<span class="badge badge-full">Full Capacity</span>`;

        card.innerHTML = `
          <div class="card-top">
            <span class="card-title">Sheet #${sheet.sheet_num}</span>
            ${badgeHtml}
          </div>
          <div class="card-metrics">
            <span class="metric-item"><strong>${sheet.total_parts}</strong> parts</span>
            <span>•</span>
            <span class="metric-item" style="font-family: var(--font-mono); font-size: 0.72rem; color: #64748b;">${sheet.filename}</span>
          </div>
        `;
        list.appendChild(card);
      });

      document.getElementById('sheet-count-badge').textContent = `${allSheets.length} Sheets`;
    }

    function selectSheet(index) {
      if (index < 0 || index >= allSheets.length) return;
      currentSheetIndex = index;
      renderSheetList();
      loadActiveSheet();
    }

    function prevSheet() {
      if (currentSheetIndex > 0) selectSheet(currentSheetIndex - 1);
    }

    function nextSheet() {
      if (currentSheetIndex < allSheets.length - 1) selectSheet(currentSheetIndex + 1);
    }

    async function loadActiveSheet() {
      const sheet = allSheets[currentSheetIndex];
      if (!sheet) return;

      // Update Inspector Stats
      document.getElementById('stat-total-parts').textContent = `${sheet.total_parts} units`;

      const cutBox = document.getElementById('stat-cut-box');
      const remBox = document.getElementById('stat-remnant-box');
      if (sheet.cut_x_mm) {
        cutBox.style.display = 'flex';
        document.getElementById('stat-cut-x').textContent = `X = ${sheet.cut_x_mm.toFixed(1)} mm`;
      } else {
        cutBox.style.display = 'none';
      }

      if (sheet.remnant_dims) {
        remBox.style.display = 'flex';
        document.getElementById('stat-remnant').textContent = sheet.remnant_dims;
      } else {
        remBox.style.display = 'none';
      }

      // Update Manifest Pills
      const pillsContainer = document.getElementById('part-pills');
      pillsContainer.innerHTML = '';
      for (const [pname, count] of Object.entries(sheet.part_counts)) {
        const pill = document.createElement('div');
        pill.className = 'part-tag';
        pill.innerHTML = `${pname}: <span>${count}</span>`;
        pillsContainer.appendChild(pill);
      }

      // Load SVG directly
      try {
        const res = await fetch(sheet.svg_url);
        const svgText = await res.text();
        svgHost.innerHTML = svgText;

        const svgEl = svgHost.querySelector('svg');
        if (svgEl) {
          // Standardize display aspect
          wrapper.style.width = '600px';
          wrapper.style.height = '1200px';

          // Attach hover handlers on nested parts
          svgEl.querySelectorAll('.nested-part').forEach(part => {
            part.addEventListener('mouseenter', (e) => {
              const name = part.getAttribute('data-part') || part.getAttribute('data-part-name') || 'Part';
              const id = part.id || '';
              tooltip.style.display = 'block';
              tooltip.innerHTML = `<strong>${name}</strong> (${id})`;
            });
            part.addEventListener('mousemove', (e) => {
              tooltip.style.left = `${e.clientX + 14}px`;
              tooltip.style.top = `${e.clientY + 14}px`;
            });
            part.addEventListener('mouseleave', () => {
              tooltip.style.display = 'none';
            });
          });
        }
      } catch (err) {
        console.error('Failed to load SVG:', err);
      }

      if (viewMode === 'png' && sheet.has_png) {
        imgHost.src = sheet.png_url;
        imgHost.style.display = 'block';
        svgHost.style.display = 'none';
      } else {
        imgHost.style.display = 'none';
        svgHost.style.display = 'block';
      }

      // Fit to screen on initial load
      setTimeout(fitToScreen, 50);
    }

    async function loadBatches() {
      try {
        const res = await fetch('/api/batches');
        allSheets = await res.json();
        if (allSheets.length > 0) {
          selectSheet(0);
        }
      } catch (err) {
        console.error('Failed to load batches:', err);
      }
    }

    // Auto-detect host IP
    document.getElementById('host-label').textContent = window.location.hostname;

    // Initial load
    loadBatches();
  </script>
</body>
</html>
"""


class VisualizerRequestHandler(SimpleHTTPRequestHandler):
    """Serves the single-page visualizer and live nesting batch API."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
            return

        elif path == "/api/batches":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            batches = get_all_batches()
            self.wfile.write(json.dumps(batches).encode("utf-8"))
            return

        elif path.startswith("/output/"):
            # Serve files directly from the output directory
            rel_path = path[len("/output/"):]
            local_path = os.path.join(OUTPUT_DIR, rel_path)

            if not os.path.exists(local_path):
                self.send_error(404, f"File Not Found: {rel_path}")
                return

            ext = os.path.splitext(local_path)[1].lower()
            mime_types = {
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".json": "application/json"
            }
            content_type = mime_types.get(ext, "application/octet-stream")

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            with open(local_path, "rb") as f:
                self.wfile.write(f.read())
            return

        else:
            self.send_error(404, "Not Found")


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run_visualizer(port: int = PORT):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    local_ip = get_local_ip()

    # Find free port if 8080 is busy
    actual_port = port
    for test_port in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("0.0.0.0", test_port), VisualizerRequestHandler)
            actual_port = test_port
            break
        except OSError:
            continue
    else:
        print(f"Error: Could not bind to any port from {port} to {port+20}")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("      EASYNEST V2 - INDUSTRIAL BATCH & OUTPUT VISUALIZER")
    print("=" * 70)
    print(f"[*] Local Access (this machine) : http://localhost:{actual_port}")
    print(f"[*] Network Access (your phone) : http://{local_ip}:{actual_port}")
    print(f"[*] Monitoring outputs folder   : {OUTPUT_DIR}")
    print("=" * 70)
    print("[*] Press Ctrl+C to stop the server.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Shutting down visualizer server.")
        server.server_close()


if __name__ == "__main__":
    port_arg = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else PORT
    run_visualizer(port_arg)
