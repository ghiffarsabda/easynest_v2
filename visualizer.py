#!/usr/bin/env python3
"""
EasyNest v2 - Interactive Batch Production Studio & Visualizer
=============================================================
A lightweight, zero-dependency local web application to:
1. Browse the CAD Product Catalogue (12 realistic motorcycle components).
2. Configure custom production batches (custom quantities, sheet sizes, kerf, margin).
3. Process the batch in real-time across multiple sequential sheets with directional compaction & guillotine shear cut lines.
4. Interactively inspect and verify all sheets with 60fps GPU-accelerated pan & zoom.

Zero external dependencies. Runs locally on Python standard library http.server.
"""

import os
import sys
import glob
import re
import json
import time
import socket
import socketserver
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, List, Optional, Tuple

PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
PARTS_DIR = os.path.join(BASE_DIR, "parts")

# Cache for loaded CAD parts
_CACHED_NAMED_PARTS = None


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


def get_named_parts():
    """Loads and caches isolated CAD parts from parts/."""
    global _CACHED_NAMED_PARTS
    if _CACHED_NAMED_PARTS is not None:
        return _CACHED_NAMED_PARTS

    from cdr_enhancer import extract_paths_from_svg, build_isolated_objects
    part_files = sorted([f for f in os.listdir(PARTS_DIR) if f.endswith(".svg")])
    named_parts = []
    for pf in part_files:
        full_p = os.path.join(PARTS_DIR, pf)
        pname = os.path.splitext(pf)[0]
        paths = extract_paths_from_svg(full_p)
        objs = build_isolated_objects(paths)
        if objs:
            named_parts.append((pname, objs[0]))
    _CACHED_NAMED_PARTS = named_parts
    return _CACHED_NAMED_PARTS


def get_catalogue() -> List[Dict[str, Any]]:
    """Builds the catalogue of available CAD parts with metadata."""
    named_parts = get_named_parts()
    catalogue = []

    friendly_names = {
        "p01_front_fairing": ("Front Fairing", "Tier A (Large Panels)", "Aerodynamic front cowl with M8 mounts & central headlight cutout"),
        "p02_rear_tail_hugger": ("Rear Tail Hugger", "Tier A (Large Panels)", "Curved rear tire fender with massive concave tire arch void"),
        "p03_radiator_shroud": ("Radiator Shroud", "Tier A (Large Panels)", "Angled airflow radiator duct with 3 cooling louvers"),
        "p04_engine_skid_plate": ("Engine Skid Plate", "Tier A (Large Panels)", "Heavy-duty sump protection plate with drainage port & 8 cooling slots"),
        "p05_tail_tidy_bracket": ("Tail Tidy Bracket", "Tier B (Medium Brackets)", "License plate & turn signal bracket with wiring pass-through"),
        "p06_rearset_footpeg_hanger": ("Footpeg Hanger", "Tier B (Medium Brackets)", "CNC foot control hanger with pivot bore & 4-position adjustment"),
        "p07_exhaust_heat_shield": ("Exhaust Heat Shield", "Tier B (Medium Brackets)", "Curved silencer heat guard with dual airflow baffle relief"),
        "p08_triple_tree_fork_brace": ("Triple Tree Fork Brace", "Tier B (Medium Brackets)", "Front fork stabilizer brace with dual 50mm clamp bores & stem port"),
        "p09_radiator_grill_bracket": ("Radiator Grill Bracket", "Tier C (Small Hardware)", "Slim mounting bracket with 6 slotted fastener holes"),
        "p10_brake_caliper_bracket": ("Brake Caliper Bracket", "Tier C (Small Hardware)", "Radial brake caliper adapter plate with 4 heavy M10 mounting holes"),
        "p11_handlebar_clamp": ("Handlebar Clamp", "Tier C (Small Hardware)", "Top handlebar riser clamp with 5 fastener bores"),
        "p12_frame_gusset_tag": ("Frame Gusset Tag", "Tier C (Small Hardware)", "Triangular chassis reinforcement gusset with lightening aperture")
    }

    for name, obj in named_parts:
        title, tier, desc = friendly_names.get(name, (name, "General Parts", ""))
        w_mm = round(obj.width / 100.0, 1)
        h_mm = round(obj.height / 100.0, 1)
        area_cm2 = round(obj.outer_path.polygon.area / 10000.0 / 100.0, 1)
        holes_count = len(obj.holes)

        catalogue.append({
            "id": name,
            "title": title,
            "tier": tier,
            "description": desc,
            "width_mm": w_mm,
            "height_mm": h_mm,
            "area_cm2": area_cm2,
            "holes": holes_count,
            "svg_url": f"/parts/{name}.svg"
        })

    return catalogue


def parse_sheet_metadata(svg_path: str) -> Dict[str, Any]:
    """Extracts nesting metrics, part counts, and remnant info from an SVG file."""
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

    # Batch grouping logic
    # Look for batch pattern: <batch_prefix>_sheet_<N>.svg OR condX_...
    sheet_num = 1
    sheet_match = re.search(r'_sheet_(\d+)', filename)
    if sheet_match:
        sheet_num = int(sheet_match.group(1))
        batch_id = filename[:sheet_match.start()]
    elif "cond1" in filename:
        batch_id = "cond1_single_maxfit"
    elif "cond2" in filename:
        batch_id = "cond2_ideal_mixed_maxfit"
    else:
        batch_id = base_name

    friendly_batch_titles = {
        "cond1_single_maxfit": "Condition 1: Single-Part Max Fit (p01)",
        "cond2_ideal_mixed_maxfit": "Condition 2: Max Mixed Fit (Ideal Pair p02+p08)",
        "cond3_fixed_order": "Condition 3: Fixed Order (45 Fairings)",
        "cond4_assembly_bom": "Condition 4: Mixed Assembly BOM (225 Parts)",
        "cond5_rush_kanban": "Condition 5: Rush Kanban (35 Parts)"
    }
    batch_title = friendly_batch_titles.get(batch_id, batch_id.replace("_", " ").title())

    is_partial = (remnant_dims is not None) or ("PARTIAL" in content)

    return {
        "filename": filename,
        "batch_id": batch_id,
        "batch_title": batch_title,
        "sheet_num": sheet_num,
        "svg_url": f"/output/{filename}",
        "png_url": f"/output/{preview_png}" if has_png else None,
        "has_png": has_png,
        "total_parts": len(parts),
        "part_counts": part_counts,
        "is_partial": is_partial,
        "cut_x_mm": cut_x_mm,
        "remnant_dims": remnant_dims,
        "viewbox": viewbox,
        "mtime": os.path.getmtime(svg_path)
    }


def get_all_batches_grouped() -> List[Dict[str, Any]]:
    """Gathers all output sheets and groups them into logical batches."""
    svg_files = glob.glob(os.path.join(OUTPUT_DIR, "*.svg"))
    sheets = [parse_sheet_metadata(p) for p in svg_files]

    # Group by batch_id
    batch_dict: Dict[str, Dict[str, Any]] = {}
    for s in sheets:
        bid = s["batch_id"]
        if bid not in batch_dict:
            batch_dict[bid] = {
                "batch_id": bid,
                "batch_title": s["batch_title"],
                "total_parts": 0,
                "sheets": [],
                "latest_mtime": s["mtime"]
            }
        batch_dict[bid]["sheets"].append(s)
        batch_dict[bid]["total_parts"] += s["total_parts"]
        batch_dict[bid]["latest_mtime"] = max(batch_dict[bid]["latest_mtime"], s["mtime"])

    # Sort sheets within each batch by sheet_num
    for bid, binfo in batch_dict.items():
        binfo["sheets"].sort(key=lambda s: s["sheet_num"])
        binfo["sheets_count"] = len(binfo["sheets"])

    # Sort batches (newest custom batches first, standard test conditions at bottom or top)
    sorted_batches = list(batch_dict.values())
    cond_priority = {"cond1_single_maxfit": 1, "cond2_ideal_mixed_maxfit": 2, "cond3_fixed_order": 3, "cond4_assembly_bom": 4, "cond5_rush_kanban": 5}

    def batch_sort_key(b):
        bid = b["batch_id"]
        if bid in cond_priority:
            return (0, cond_priority[bid])
        return (1, -b["latest_mtime"])

    sorted_batches.sort(key=batch_sort_key)
    return sorted_batches


def execute_production_batch(
    batch_name: str,
    order: Dict[str, int],
    sheet_w_mm: float = 1220.0,
    sheet_h_mm: float = 2440.0,
    kerf_mm: float = 2.0,
    margin_mm: float = 5.0
) -> Dict[str, Any]:
    """Executes MultiSheetBatchPlanner for a user-configured production batch."""
    from batch_nest import MultiSheetBatchPlanner
    from cdr_enhancer import render_preview_png

    named_parts = get_named_parts()
    valid_part_names = {name for name, _ in named_parts}
    # Filter order to only valid catalogue parts with qty > 0
    clean_order = {k: int(v) for k, v in order.items() if k in valid_part_names and int(v) > 0}
    if not clean_order:
        raise ValueError(f"Order must contain at least one valid part with quantity > 0. Valid parts: {sorted(list(valid_part_names))}")

    # Sanitize batch slug
    safe_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', batch_name.strip())
    if not safe_slug:
        safe_slug = f"batch_{int(time.time())}"

    planner = MultiSheetBatchPlanner(
        sheet_w_mm=sheet_w_mm,
        sheet_h_mm=sheet_h_mm,
        kerf_mm=kerf_mm,
        margin_mm=margin_mm
    )

    t0 = time.time()
    records = planner.run_batch_order(clean_order, named_parts, output_prefix=safe_slug)
    runtime_s = round(time.time() - t0, 2)

    # Generate preview PNG for partial sheet or first sheet if possible
    if records and records[-1].output_svg_path and os.path.exists(records[-1].output_svg_path):
        png_out = records[-1].output_svg_path.replace(".svg", "_preview.png")
        try:
            render_preview_png(records[-1].output_svg_path, png_out, dpi=90)
        except Exception:
            pass

    # Build response summary
    total_parts = sum(r.total_parts for r in records)
    total_salvage_m2 = sum((r.remnant_area_m2 or 0.0) for r in records)

    return {
        "success": True,
        "batch_id": safe_slug,
        "batch_title": safe_slug.replace("_", " ").title(),
        "total_parts_ordered": sum(clean_order.values()),
        "total_parts_produced": total_parts,
        "sheets_count": len(records),
        "salvaged_remnant_m2": round(total_salvage_m2, 3),
        "runtime_seconds": runtime_s
    }


# ==============================================================================
# HTML, CSS & JAVASCRIPT FRONTEND APPLICATION
# ==============================================================================

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>EasyNest v2 — Production Batch Studio & Visualizer</title>
  <style>
    :root {
      --bg: #090d16;
      --card-bg: #111827;
      --card-border: #1f2937;
      --card-hover: #1e293b;
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

    /* --- TOP HEADER --- */
    header {
      background: #0d1322;
      border-bottom: 1px solid var(--card-border);
      padding: 0.6rem 1.2rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      z-index: 30;
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

    /* Top Nav Tabs */
    .tab-switcher {
      display: flex;
      background: #111827;
      padding: 3px;
      border-radius: 8px;
      border: 1px solid var(--card-border);
      gap: 4px;
    }
    .tab-btn {
      background: transparent;
      border: none;
      color: var(--text-dim);
      font-size: 0.85rem;
      font-weight: 600;
      padding: 0.45rem 1rem;
      border-radius: 6px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 0.45rem;
      transition: all 0.15s;
    }
    .tab-btn:hover { color: var(--text); }
    .tab-btn.active {
      background: #0284c7;
      color: white;
      box-shadow: 0 2px 8px rgba(2, 132, 199, 0.35);
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
      font-weight: 600;
    }
    .btn-accent:hover { background: #0369a1; }
    .btn-emerald {
      background: #059669;
      border-color: #047857;
      color: white;
      font-weight: 700;
      box-shadow: 0 2px 10px rgba(5, 150, 105, 0.3);
    }
    .btn-emerald:hover { background: #047857; }
    .btn-emerald:disabled {
      background: #1e293b;
      border-color: #334155;
      color: #64748b;
      cursor: not-allowed;
      box-shadow: none;
    }

    /* --- VIEW CONTAINERS & FULL-PAGE SWITCHER --- */
    body.mode-viewer {
      overflow: hidden !important;
    }
    body.mode-viewer header.studio-header {
      display: none !important;
    }
    body.mode-viewer #view-studio {
      display: none !important;
    }
    body.mode-viewer #view-viewer {
      display: flex !important;
    }

    .view-container {
      display: none;
      flex: 1;
      height: calc(100dvh - 54px);
      overflow: hidden;
    }
    .view-container.active {
      display: flex;
    }

    /* ==========================================================================
       TAB 1: BATCH STUDIO (CATALOGUE & CONFIG)
       ========================================================================== */
    .studio-layout {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow-y: auto;
      padding: 1.25rem 2rem;
      gap: 1.25rem;
      background: #070a12;
    }

    /* Config Bar */
    .config-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 1.2rem 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1rem;
      box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    }
    .config-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid #1f2937;
      padding-bottom: 0.75rem;
    }
    .config-title {
      font-size: 1rem;
      font-weight: 700;
      color: #f3f4f6;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .config-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1.25rem;
    }
    .input-group {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }
    .input-label {
      font-size: 0.75rem;
      font-weight: 600;
      color: var(--text-dim);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .form-control {
      background: #090d16;
      border: 1px solid #374151;
      color: #f3f4f6;
      padding: 0.6rem 0.8rem;
      border-radius: 6px;
      font-size: 0.9rem;
      font-family: inherit;
      outline: none;
      transition: border-color 0.15s;
    }
    .form-control:focus {
      border-color: #38bdf8;
      box-shadow: 0 0 0 1px #38bdf8;
    }
    .custom-sheet-row {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    /* Batch Action Banner */
    .action-banner {
      background: linear-gradient(90deg, #111e38, #0f2744);
      border: 1px solid #0284c7;
      border-radius: 10px;
      padding: 1rem 1.5rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      flex-wrap: wrap;
    }
    .banner-stats {
      display: flex;
      align-items: center;
      gap: 1.5rem;
      flex-wrap: wrap;
    }
    .banner-stat-item {
      display: flex;
      flex-direction: column;
    }
    .banner-stat-num {
      font-size: 1.3rem;
      font-weight: 800;
      font-family: var(--font-mono);
      color: #38bdf8;
    }
    .banner-stat-label {
      font-size: 0.7rem;
      text-transform: uppercase;
      color: var(--text-dim);
      letter-spacing: 0.05em;
    }

    .preset-pills {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
    }
    .preset-btn {
      background: #1e293b;
      border: 1px solid #334155;
      color: #cbd5e1;
      padding: 0.35rem 0.7rem;
      border-radius: 6px;
      font-size: 0.75rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
    }
    .preset-btn:hover { background: #334155; color: white; border-color: #475569; }

    /* Catalogue Grid */
    .catalogue-section {
      display: flex;
      flex-direction: column;
      gap: 0.9rem;
    }
    .section-title {
      font-size: 1.1rem;
      font-weight: 700;
      color: #f3f4f6;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .catalogue-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 1rem;
    }
    .catalogue-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      transition: all 0.15s ease;
      position: relative;
    }
    .catalogue-card:hover {
      border-color: #38bdf8;
      transform: translateY(-2px);
      box-shadow: 0 8px 24px rgba(0,0,0,0.4);
    }
    .catalogue-card.has-qty {
      border-color: #0284c7;
      background: #0f1c33;
      box-shadow: 0 0 0 1px #0284c7;
    }
    .card-preview-box {
      height: 140px;
      background: #ffffff;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 0.5rem;
      overflow: hidden;
    }
    .card-preview-box img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
    }
    .card-meta-title {
      font-size: 0.95rem;
      font-weight: 700;
      color: #f9fafb;
    }
    .card-meta-tier {
      font-size: 0.7rem;
      color: #38bdf8;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .card-dims {
      font-size: 0.78rem;
      color: var(--text-dim);
      font-family: var(--font-mono);
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .card-desc {
      font-size: 0.75rem;
      color: #94a3b8;
      line-height: 1.3;
      min-height: 32px;
    }

    /* Stepper */
    .stepper-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
      margin-top: auto;
      padding-top: 0.5rem;
      border-top: 1px solid #1f2937;
    }
    .stepper-controls {
      display: flex;
      align-items: center;
      background: #090d16;
      border: 1px solid #374151;
      border-radius: 6px;
      overflow: hidden;
    }
    .step-btn {
      background: transparent;
      border: none;
      color: #f3f4f6;
      width: 32px;
      height: 32px;
      font-size: 1.1rem;
      font-weight: bold;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.1s;
    }
    .step-btn:hover { background: #374151; }
    .step-input {
      width: 48px;
      height: 32px;
      background: transparent;
      border: none;
      color: #38bdf8;
      font-family: var(--font-mono);
      font-size: 0.95rem;
      font-weight: 700;
      text-align: center;
      outline: none;
    }
    .quick-adds {
      display: flex;
      gap: 0.3rem;
    }
    .quick-btn {
      background: #1e293b;
      border: 1px solid #334155;
      color: #cbd5e1;
      font-size: 0.7rem;
      font-weight: 600;
      padding: 0.25rem 0.45rem;
      border-radius: 4px;
      cursor: pointer;
    }
    .quick-btn:hover { background: #334155; color: white; }

    /* ==========================================================================
       FULL-PAGE SHEET INSPECTOR (OUTPUT VIEW)
       ========================================================================== */
    #view-viewer {
      position: fixed;
      inset: 0;
      width: 100vw;
      height: 100dvh;
      overflow: hidden;
      background: #05070d;
      display: none;
      z-index: 20;
    }
    #view-viewer.active {
      display: flex;
    }

    .canvas-container {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      cursor: grab;
      display: flex;
      align-items: center;
      justify-content: center;
      background-color: #060911;
      background-image: radial-gradient(rgba(255, 255, 255, 0.06) 1px, transparent 0);
      background-size: 26px 26px;
    }
    .canvas-container:active { cursor: grabbing; }

    .sheet-wrapper {
      position: absolute;
      transform-origin: 0 0;
      will-change: transform;
      box-shadow: 0 25px 65px rgba(0, 0, 0, 0.75), 0 0 0 1px rgba(255, 255, 255, 0.12);
      background: #ffffff;
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

    /* Nested part interactive highlight */
    .nested-part {
      transition: opacity 0.15s, stroke-width 0.15s;
      cursor: pointer;
    }
    .nested-part:hover path {
      stroke: #38bdf8 !important;
      stroke-width: 80 !important;
      fill-opacity: 0.75 !important;
    }
    .nested-part.highlighted path {
      stroke: #f59e0b !important;
      stroke-width: 95 !important;
      fill: #f59e0b !important;
      fill-opacity: 0.85 !important;
    }

    /* Floating Top HUD */
    .inspector-top-hud {
      position: absolute;
      top: 12px;
      left: 14px;
      right: 14px;
      z-index: 40;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      padding: 0.45rem 0.85rem;
      background: rgba(13, 19, 34, 0.90);
      backdrop-filter: blur(14px);
      -webkit-backdrop-filter: blur(14px);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 10px;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.55);
      pointer-events: auto;
    }
    .hud-group {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: nowrap;
    }
    .hud-title-box {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      background: #111827;
      padding: 0.3rem 0.7rem;
      border-radius: 6px;
      border: 1px solid #1f2937;
    }
    .sheet-title-text {
      font-size: 0.85rem;
      font-weight: 700;
      color: #f9fafb;
      white-space: nowrap;
    }
    .top-remnant-pill {
      background: rgba(245, 158, 11, 0.16);
      color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.35);
      font-size: 0.75rem;
      font-weight: 700;
      padding: 0.25rem 0.6rem;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      white-space: nowrap;
    }
    .pill-mini {
      background: rgba(56, 189, 248, 0.15);
      color: #38bdf8;
      border: 1px solid rgba(56, 189, 248, 0.3);
      font-size: 0.72rem;
      font-weight: 700;
      padding: 0.15rem 0.45rem;
      border-radius: 9999px;
      font-family: var(--font-mono);
    }
    .btn-icon {
      background: #1e293b;
      border: 1px solid #334155;
      color: var(--text);
      width: 32px;
      height: 32px;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 0.85rem;
      transition: all 0.15s ease;
    }
    .btn-icon:hover { background: #334155; border-color: #475569; }

    /* Slide-over Drawer for Batches & Sheets */
    .drawer-backdrop {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.65);
      backdrop-filter: blur(4px);
      -webkit-backdrop-filter: blur(4px);
      z-index: 90;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.2s ease;
    }
    .drawer-backdrop.open {
      opacity: 1;
      pointer-events: auto;
    }
    .sheet-drawer {
      position: fixed;
      top: 0;
      left: 0;
      bottom: 0;
      width: 380px;
      max-width: 88vw;
      background: #0d1322;
      border-right: 1px solid var(--card-border);
      z-index: 100;
      display: flex;
      flex-direction: column;
      transform: translateX(-100%);
      transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      box-shadow: 12px 0 40px rgba(0, 0, 0, 0.7);
    }
    .sheet-drawer.open {
      transform: translateX(0);
    }
    .drawer-header {
      padding: 1rem 1.2rem;
      border-bottom: 1px solid var(--card-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .drawer-title {
      font-size: 1rem;
      font-weight: 700;
      color: #f9fafb;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .drawer-close {
      background: transparent;
      border: none;
      color: var(--text-dim);
      font-size: 1.25rem;
      cursor: pointer;
      padding: 0.2rem 0.5rem;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .drawer-close:hover { color: white; background: #1e293b; }
    .drawer-content {
      flex: 1;
      overflow-y: auto;
      padding: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.9rem;
    }
    .drawer-label {
      font-size: 0.7rem;
      color: var(--text-dim);
      text-transform: uppercase;
      font-weight: 700;
      letter-spacing: 0.05em;
    }

    .sheet-list {
      display: flex;
      flex-direction: column;
      gap: 0.6rem;
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
      gap: 0.4rem;
    }
    .sheet-card:hover {
      border-color: #38bdf8;
      transform: translateY(-1px);
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
      gap: 0.6rem;
      font-size: 0.75rem;
      color: var(--text-dim);
    }

    .toolbar-btn {
      background: transparent;
      border: none;
      color: var(--text);
      width: 28px;
      height: 28px;
      border-radius: 4px;
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
      min-width: 48px;
      text-align: center;
      color: var(--text-dim);
    }

    /* Floating Bottom Dock */
    .inspector-bottom-dock {
      position: absolute;
      bottom: 12px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 40;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0.4rem;
      max-width: min(94vw, 1050px);
      pointer-events: none;
      transition: all 0.2s ease;
    }
    .dock-bar {
      pointer-events: auto;
      background: rgba(13, 19, 34, 0.92);
      backdrop-filter: blur(14px);
      -webkit-backdrop-filter: blur(14px);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 10px;
      padding: 0.45rem 0.9rem;
      display: flex;
      align-items: center;
      gap: 1rem;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.55);
      flex-wrap: wrap;
      justify-content: center;
    }
    .inspector-bottom-dock.minimized .dock-details {
      display: none !important;
    }
    .dock-details {
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
      max-width: 580px;
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
      cursor: pointer;
      user-select: none;
      transition: all 0.15s;
    }
    .part-tag:hover { background: #334155; border-color: #38bdf8; }
    .part-tag.active {
      background: #0284c7;
      border-color: #38bdf8;
      color: white;
    }
    .part-tag span { color: #38bdf8; font-weight: 700; }
    .part-tag.active span { color: #ffffff; }

    /* Modal / Progress Overlay */
    #progress-overlay {
      position: fixed;
      inset: 0;
      background: rgba(9, 13, 22, 0.85);
      backdrop-filter: blur(12px);
      z-index: 999;
      display: none;
      align-items: center;
      justify-content: center;
      flex-direction: column;
      gap: 1rem;
    }
    .spinner {
      width: 48px;
      height: 48px;
      border: 4px solid #1e293b;
      border-top-color: #38bdf8;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .progress-text {
      font-size: 1.1rem;
      font-weight: 700;
      color: #f3f4f6;
    }
    .progress-subtext {
      font-size: 0.85rem;
      color: var(--text-dim);
      font-family: var(--font-mono);
    }

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
  </style>
</head>
<body>

  <!-- Top App Bar (Only for Batch Studio) -->
  <header id="studio-header" class="studio-header">
    <div class="brand">
      <span class="brand-badge">EASYNEST v2</span>
      <span class="brand-title">Batch Production Studio</span>
      <div class="status-pill">
        <span class="status-dot"></span>
        <span id="host-label">Local Host</span>
      </div>
    </div>

    <div class="top-actions">
      <button class="btn btn-accent" onclick="switchView('viewer')" title="Open Full-Page Sheet Inspector">
        <span>👁️</span> Full-Page Sheet Inspector
      </button>
      <button class="btn" onclick="refreshAll()" title="Refresh All Data">↻ Refresh</button>
    </div>
  </header>

  <!-- =======================================================================
       TAB 1: BATCH PRODUCTION STUDIO
       ======================================================================= -->
  <div id="view-studio" class="view-container studio-layout active">

    <!-- Batch Configuration Form -->
    <div class="config-card">
      <div class="config-header">
        <div class="config-title">
          <span>⚙️</span> Batch Production Parameters
        </div>
        <div class="preset-pills">
          <span style="font-size: 0.75rem; color: var(--text-dim); margin-right: 4px;">Quick Kits:</span>
          <button class="preset-btn" onclick="applyPreset('bom')">🏍️ Complete 25-Bike BOM</button>
          <button class="preset-btn" onclick="applyPreset('armor')">🛡️ Fairing & Skid Kit</button>
          <button class="preset-btn" onclick="applyPreset('ideal')">⚡ Ideal Pair (p02 + p08)</button>
          <button class="preset-btn" onclick="applyPreset('clear')">✕ Clear All (0)</button>
        </div>
      </div>

      <div class="config-grid">
        <!-- Batch Name -->
        <div class="input-group">
          <label class="input-label" for="input-batch-name">Batch Name / Identifier</label>
          <input id="input-batch-name" class="form-control" type="text" placeholder="e.g. rush_motorcycle_run_01" />
        </div>

        <!-- Sheet Size Preset -->
        <div class="input-group">
          <label class="input-label" for="select-sheet-size">Sheet Size Preset</label>
          <select id="select-sheet-size" class="form-control" onchange="onSheetSizeChange()">
            <option value="1220x2440" selected>Standard Industrial: 1220 × 2440 mm (4×8 ft)</option>
            <option value="1000x2000">Standard Metric: 1000 × 2000 mm (1×2 m)</option>
            <option value="1220x1220">Square Half-Sheet: 1220 × 1220 mm (4×4 ft)</option>
            <option value="600x1200">Compact Router Bed: 600 × 1200 mm</option>
            <option value="custom">Custom Size (W × H mm)...</option>
          </select>
        </div>

        <!-- Custom Dimensions (if selected) -->
        <div id="custom-sheet-box" class="input-group" style="display: none;">
          <label class="input-label">Custom Sheet (W × H mm)</label>
          <div class="custom-sheet-row">
            <input id="input-sheet-w" class="form-control" type="number" value="1220" min="200" max="10000" style="width: 100px;" />
            <span>×</span>
            <input id="input-sheet-h" class="form-control" type="number" value="2440" min="200" max="10000" style="width: 100px;" />
            <span style="font-size: 0.8rem; color: var(--text-dim);">mm</span>
          </div>
        </div>

        <!-- Tool Kerf Spacing -->
        <div class="input-group">
          <label class="input-label" for="input-kerf">Tool Cutting Kerf (mm)</label>
          <input id="input-kerf" class="form-control" type="number" value="2.0" step="0.5" min="0.5" max="20.0" />
        </div>

        <!-- Margin Spacing -->
        <div class="input-group">
          <label class="input-label" for="input-margin">Sheet Border Margin (mm)</label>
          <input id="input-margin" class="form-control" type="number" value="5.0" step="1.0" min="0.0" max="50.0" />
        </div>
      </div>
    </div>

    <!-- Live Order Action Banner -->
    <div class="action-banner">
      <div class="banner-stats">
        <div class="banner-stat-item">
          <span id="banner-parts-count" class="banner-stat-num">0</span>
          <span class="banner-stat-label">Total Parts in Batch</span>
        </div>
        <div class="banner-stat-item">
          <span id="banner-unique-parts" class="banner-stat-num">0</span>
          <span class="banner-stat-label">Unique Part SKUs</span>
        </div>
        <div class="banner-stat-item">
          <span id="banner-est-area" class="banner-stat-num">0.0 m²</span>
          <span class="banner-stat-label">Est. Part Net Area</span>
        </div>
      </div>

      <!-- Action Button -->
      <button id="btn-process-batch" class="btn btn-emerald" style="padding: 0.75rem 1.75rem; font-size: 1rem;" onclick="processBatch()" disabled>
        <span>🚀</span> PROCESS BATCH
      </button>
    </div>

    <!-- Product Catalogue Section -->
    <div class="catalogue-section">
      <div class="section-title">
        <span>🏍️ CAD Product Catalogue (Set Quantities)</span>
      </div>

      <div id="catalogue-grid" class="catalogue-grid">
        <!-- Rendered dynamically -->
      </div>
    </div>

  </div>

  <!-- =======================================================================
       FULL-PAGE SHEET INSPECTOR
       ======================================================================= -->
  <div id="view-viewer" class="view-container">

    <!-- Slide-over Drawer Backdrop -->
    <div id="drawer-backdrop" class="drawer-backdrop" onclick="closeDrawer()"></div>

    <!-- Slide-over Batches & Sheet Explorer Drawer -->
    <div id="sheet-drawer" class="sheet-drawer">
      <div class="drawer-header">
        <div class="drawer-title">
          <span>📦</span> Batches & Sheet Explorer
        </div>
        <button class="drawer-close" onclick="closeDrawer()" title="Close Drawer (Esc)">✕</button>
      </div>
      <div class="drawer-content">
        <div>
          <label class="drawer-label" for="select-batch">Select Production Batch</label>
          <select id="select-batch" class="form-control" onchange="onBatchSelectChange()" style="margin-top: 4px;">
            <!-- Populated dynamically -->
          </select>
        </div>

        <div>
          <label class="drawer-label">Sheets in Selected Batch</label>
          <div id="sheet-list" class="sheet-list" style="margin-top: 6px;">
            <!-- Rendered dynamically -->
          </div>
        </div>
      </div>
    </div>

    <!-- Edge-to-Edge Pan & Zoom Canvas -->
    <div id="canvas-container" class="canvas-container">
      <div id="sheet-wrapper" class="sheet-wrapper">
        <div id="svg-host"></div>
        <img id="img-host" style="display: none;" alt="Preview" />
      </div>
    </div>

    <!-- Floating Top HUD -->
    <div class="inspector-top-hud">
      <div class="hud-group">
        <button class="btn" onclick="switchView('studio')" title="Return to Batch Production Studio">
          <span>← 🏭</span> Studio
        </button>
        <button class="btn btn-accent" onclick="toggleDrawer()" title="Browse Batches & Sheets (B)">
          <span>☰</span> Batches & Sheets <span id="batch-sheet-pill" class="pill-mini">Sheet 1/1</span>
        </button>
      </div>

      <div class="hud-group">
        <button class="btn-icon" onclick="prevSheet()" title="Previous Sheet (←)">◀</button>
        <div class="hud-title-box">
          <span id="sheet-header-title" class="sheet-title-text">Loading Sheet...</span>
        </div>
        <button class="btn-icon" onclick="nextSheet()" title="Next Sheet (→)">▶</button>
        <div id="top-remnant-pill" class="top-remnant-pill" style="display: none;"></div>
      </div>

      <div class="hud-group">
        <div class="hud-group" style="background: #111827; padding: 2px 6px; border-radius: 6px; border: 1px solid #1f2937;">
          <button class="toolbar-btn" onclick="zoomOut()" title="Zoom Out (-)">－</button>
          <span id="zoom-text" class="zoom-level-label">100%</span>
          <button class="toolbar-btn" onclick="zoomIn()" title="Zoom In (+)">＋</button>
        </div>
        <button class="btn" onclick="fitToScreen()" title="Fit to Screen (F)"><span>⛶</span> Fit</button>
        <button class="btn" onclick="resetZoom()" title="1:1 Pixel Scale (1)">1:1</button>
        <button id="btn-fullscreen" class="btn" onclick="toggleFullscreen()" title="Native Fullscreen Mode"><span>🖵</span> Fullscreen</button>
      </div>
    </div>

    <!-- Floating Bottom Dock -->
    <div id="inspector-bottom-dock" class="inspector-bottom-dock">
      <div class="dock-bar">
        <div class="dock-details">
          <div class="stat-pill">
            <span class="stat-label">Sheet Dimensions</span>
            <span id="stat-sheet-dims" class="stat-val">1220 × 2440 mm</span>
          </div>
          <div class="stat-pill">
            <span class="stat-label">Parts Placed</span>
            <span id="stat-total-parts" class="stat-val emerald">-</span>
          </div>
          <div class="stat-pill" id="stat-cut-box" style="display: none;">
            <span class="stat-label">✂ Guillotine Cut</span>
            <span id="stat-cut-x" class="stat-val amber">-</span>
          </div>
          <div class="stat-pill" id="stat-remnant-box" style="display: none;">
            <span class="stat-label">📦 Salvaged Remnant</span>
            <span id="stat-remnant" class="stat-val emerald">-</span>
          </div>
          <div class="stat-pill">
            <span class="stat-label">Sheet Part Breakdown (Hover / Click to Highlight)</span>
            <div id="part-pills" class="part-pills"></div>
          </div>
        </div>

        <button id="btn-toggle-dock" class="btn" onclick="toggleBottomDock()" style="font-size: 0.75rem; padding: 0.25rem 0.6rem;">
          ▼ Minimize
        </button>
      </div>
    </div>

  </div>

  <!-- Fullscreen Loading Overlay -->
  <div id="progress-overlay">
    <div class="spinner"></div>
    <div id="progress-text" class="progress-text">Processing Batch Nesting...</div>
    <div id="progress-subtext" class="progress-subtext">Compacting layout across multiple sheets...</div>
  </div>

  <div id="hover-tooltip"></div>

  <script>
    // Global State
    let catalogueData = [];
    let orderQuantities = {};
    let allBatches = [];
    let activeBatchId = null;
    let activeSheetIndex = 0;

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

    // View Switching (Full Page Studio vs Full Page Inspector)
    function switchView(viewName, pushState = true) {
      const isViewer = (viewName === 'viewer' || viewName === 'inspector');
      document.body.classList.toggle('mode-viewer', isViewer);
      document.getElementById('view-studio').classList.toggle('active', !isViewer);
      document.getElementById('view-viewer').classList.toggle('active', isViewer);

      if (pushState) {
        const targetPath = isViewer ? '/inspector' : '/';
        if (window.location.pathname !== targetPath) {
          window.history.pushState({ view: viewName }, '', targetPath);
        }
      }

      if (isViewer) {
        setTimeout(fitToScreen, 60);
      }
    }

    window.addEventListener('popstate', (e) => {
      const path = window.location.pathname.toLowerCase();
      const isViewer = path.includes('inspector') || path.includes('viewer');
      switchView(isViewer ? 'viewer' : 'studio', false);
    });

    // Drawer Controls for Full-Page Inspector
    function openDrawer() {
      document.getElementById('sheet-drawer').classList.add('open');
      document.getElementById('drawer-backdrop').classList.add('open');
    }
    function closeDrawer() {
      document.getElementById('sheet-drawer').classList.remove('open');
      document.getElementById('drawer-backdrop').classList.remove('open');
    }
    function toggleDrawer() {
      const drawer = document.getElementById('sheet-drawer');
      if (drawer.classList.contains('open')) {
        closeDrawer();
      } else {
        openDrawer();
      }
    }

    // Fullscreen Toggle
    function toggleFullscreen() {
      if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(() => {});
      } else {
        if (document.exitFullscreen) {
          document.exitFullscreen();
        }
      }
    }

    document.addEventListener('fullscreenchange', () => {
      const btn = document.getElementById('btn-fullscreen');
      if (btn) {
        btn.innerHTML = document.fullscreenElement ? '<span>🗗</span> Exit' : '<span>🖵</span> Fullscreen';
      }
      setTimeout(fitToScreen, 100);
    });

    // Bottom Dock Toggle
    function toggleBottomDock() {
      const dock = document.getElementById('inspector-bottom-dock');
      dock.classList.toggle('minimized');
      const toggleBtn = document.getElementById('btn-toggle-dock');
      if (toggleBtn) {
        toggleBtn.textContent = dock.classList.contains('minimized') ? '▲ Details' : '▼ Minimize';
      }
      setTimeout(fitToScreen, 60);
    }

    // Default Batch Name Generator
    function generateDefaultBatchName() {
      const now = new Date();
      const pad = (n) => String(n).padStart(2, '0');
      const stamp = `${now.getFullYear()}${pad(now.getMonth()+1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}`;
      return `batch_${stamp}`;
    }

    // Sheet Size Preset Toggle
    function onSheetSizeChange() {
      const val = document.getElementById('select-sheet-size').value;
      const customBox = document.getElementById('custom-sheet-box');
      if (val === 'custom') {
        customBox.style.display = 'flex';
      } else {
        customBox.style.display = 'none';
        const parts = val.split('x');
        document.getElementById('input-sheet-w').value = parts[0];
        document.getElementById('input-sheet-h').value = parts[1];
      }
    }

    // Load Catalogue from Server
    async function loadCatalogue() {
      try {
        const res = await fetch('/api/catalogue');
        catalogueData = await res.json();
        renderCatalogue();
      } catch (err) {
        console.error('Failed to load catalogue:', err);
      }
    }

    function renderCatalogue() {
      const grid = document.getElementById('catalogue-grid');
      grid.innerHTML = '';

      catalogueData.forEach(item => {
        const qty = orderQuantities[item.id] || 0;
        const card = document.createElement('div');
        card.className = `catalogue-card ${qty > 0 ? 'has-qty' : ''}`;
        card.id = `card-${item.id}`;

        card.innerHTML = `
          <div class="card-preview-box">
            <img src="${item.svg_url}" alt="${item.title}" loading="lazy" />
          </div>
          <div>
            <div class="card-meta-tier">${item.tier}</div>
            <div class="card-meta-title">${item.title}</div>
            <div class="card-dims">${item.width_mm} × ${item.height_mm} mm • ${item.holes} holes</div>
          </div>
          <div class="card-desc">${item.description}</div>
          <div class="stepper-row">
            <div class="stepper-controls">
              <button class="step-btn" onclick="modifyQty('${item.id}', -1)">−</button>
              <input id="input-qty-${item.id}" class="step-input" type="number" min="0" max="1000" value="${qty}" onchange="setQty('${item.id}', this.value)" />
              <button class="step-btn" onclick="modifyQty('${item.id}', 1)">＋</button>
            </div>
            <div class="quick-adds">
              <button class="quick-btn" onclick="modifyQty('${item.id}', 5)">+5</button>
              <button class="quick-btn" onclick="modifyQty('${item.id}', 10)">+10</button>
            </div>
          </div>
        `;
        grid.appendChild(card);
      });

      updateOrderSummary();
    }

    function modifyQty(partId, delta) {
      const cur = orderQuantities[partId] || 0;
      const next = Math.max(0, cur + delta);
      setQty(partId, next);
    }

    function setQty(partId, val) {
      const num = Math.max(0, parseInt(val) || 0);
      orderQuantities[partId] = num;
      const inp = document.getElementById(`input-qty-${partId}`);
      if (inp) inp.value = num;

      const card = document.getElementById(`card-${partId}`);
      if (card) card.classList.toggle('has-qty', num > 0);

      updateOrderSummary();
    }

    function updateOrderSummary() {
      let totalParts = 0;
      let uniqueCount = 0;
      let totalAreaCm2 = 0;

      for (const [id, qty] of Object.entries(orderQuantities)) {
        if (qty > 0) {
          totalParts += qty;
          uniqueCount += 1;
          const item = catalogueData.find(c => c.id === id);
          if (item) totalAreaCm2 += item.area_cm2 * qty;
        }
      }

      document.getElementById('banner-parts-count').textContent = totalParts;
      document.getElementById('banner-unique-parts').textContent = uniqueCount;
      document.getElementById('banner-est-area').textContent = `${(totalAreaCm2 / 10000).toFixed(2)} m²`;

      const btn = document.getElementById('btn-process-batch');
      btn.disabled = (totalParts === 0);
      btn.innerHTML = `<span>🚀</span> PROCESS BATCH (${totalParts} Parts)`;
    }

    function applyPreset(type) {
      orderQuantities = {};
      if (type === 'bom') {
        orderQuantities['p01_front_fairing'] = 25;
        orderQuantities['p04_engine_skid_plate'] = 25;
        orderQuantities['p05_tail_tidy_bracket'] = 25;
        orderQuantities['p09_radiator_grill_bracket'] = 50;
        orderQuantities['p12_frame_gusset_tag'] = 100;
      } else if (type === 'armor') {
        orderQuantities['p01_front_fairing'] = 12;
        orderQuantities['p02_rear_tail_hugger'] = 12;
        orderQuantities['p04_engine_skid_plate'] = 12;
      } else if (type === 'ideal') {
        orderQuantities['p02_rear_tail_hugger'] = 25;
        orderQuantities['p08_triple_tree_fork_brace'] = 26;
      }
      renderCatalogue();
    }

    // Process Batch Run (POST /api/run_batch)
    async function processBatch() {
      const batchName = document.getElementById('input-batch-name').value.trim() || generateDefaultBatchName();
      const sheetW = parseFloat(document.getElementById('input-sheet-w').value) || 1220.0;
      const sheetH = parseFloat(document.getElementById('input-sheet-h').value) || 2440.0;
      const kerf = parseFloat(document.getElementById('input-kerf').value) || 2.0;
      const margin = parseFloat(document.getElementById('input-margin').value) || 5.0;

      const overlay = document.getElementById('progress-overlay');
      overlay.style.display = 'flex';
      document.getElementById('progress-text').textContent = `Processing Batch: ${batchName}...`;
      document.getElementById('progress-subtext').textContent = 'Allocating orders & compacting partial sheets with guillotine cut line...';

      try {
        const res = await fetch('/api/run_batch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            batch_name: batchName,
            order: orderQuantities,
            sheet_w_mm: sheetW,
            sheet_h_mm: sheetH,
            kerf_mm: kerf,
            margin_mm: margin
          })
        });

        const result = await res.json();
        overlay.style.display = 'none';

        if (result.success) {
          // Refresh batch list, select new batch, and switch to Full-Page Inspector!
          await loadBatches(result.batch_id);
          switchView('viewer');
        } else {
          alert(`Batch nesting failed: ${result.error || 'Unknown error'}`);
        }
      } catch (err) {
        overlay.style.display = 'none';
        alert(`Error executing batch: ${err.message}`);
      }
    }

    // =========================================================================
    // FULL-PAGE SHEET INSPECTOR LOGIC
    // =========================================================================

    async function loadBatches(selectBatchId = null) {
      try {
        const res = await fetch('/api/batches');
        allBatches = await res.json();
        renderBatchSelector();

        if (allBatches.length > 0) {
          if (selectBatchId) {
            activeBatchId = selectBatchId;
          } else if (!activeBatchId) {
            activeBatchId = allBatches[0].batch_id;
          }
          document.getElementById('select-batch').value = activeBatchId;
          renderActiveBatchSheets();
        }
      } catch (err) {
        console.error('Failed to load batches:', err);
      }
    }

    function renderBatchSelector() {
      const sel = document.getElementById('select-batch');
      sel.innerHTML = '';
      allBatches.forEach(b => {
        const opt = document.createElement('option');
        opt.value = b.batch_id;
        opt.textContent = `${b.batch_title} (${b.sheets_count} Sheets, ${b.total_parts} Parts)`;
        sel.appendChild(opt);
      });
    }

    function onBatchSelectChange() {
      activeBatchId = document.getElementById('select-batch').value;
      activeSheetIndex = 0;
      renderActiveBatchSheets();
    }

    function renderActiveBatchSheets() {
      const batch = allBatches.find(b => b.batch_id === activeBatchId);
      const list = document.getElementById('sheet-list');
      list.innerHTML = '';
      if (!batch) return;

      batch.sheets.forEach((sheet, idx) => {
        const card = document.createElement('div');
        card.className = `sheet-card ${idx === activeSheetIndex ? 'active' : ''}`;
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
            <span><strong>${sheet.total_parts}</strong> parts</span>
            <span>•</span>
            <span style="font-family: var(--font-mono); font-size: 0.72rem; color: #64748b;">${sheet.filename}</span>
          </div>
        `;
        list.appendChild(card);
      });

      loadCurrentSheet();
    }

    function selectSheet(idx) {
      activeSheetIndex = idx;
      renderActiveBatchSheets();
      closeDrawer(); // Automatically close drawer so user inspects full-page sheet
    }

    function prevSheet() {
      const batch = allBatches.find(b => b.batch_id === activeBatchId);
      if (batch && activeSheetIndex > 0) {
        selectSheet(activeSheetIndex - 1);
      }
    }

    function nextSheet() {
      const batch = allBatches.find(b => b.batch_id === activeBatchId);
      if (batch && activeSheetIndex < batch.sheets.length - 1) {
        selectSheet(activeSheetIndex + 1);
      }
    }

    async function loadCurrentSheet() {
      const batch = allBatches.find(b => b.batch_id === activeBatchId);
      if (!batch || !batch.sheets[activeSheetIndex]) return;
      const sheet = batch.sheets[activeSheetIndex];

      // Update Top Floating HUD
      const totalSheets = batch.sheets.length;
      document.getElementById('sheet-header-title').textContent = `${batch.batch_title} — Sheet ${sheet.sheet_num} of ${totalSheets}`;
      document.getElementById('batch-sheet-pill').textContent = `Sheet ${sheet.sheet_num}/${totalSheets}`;

      const remnantPill = document.getElementById('top-remnant-pill');
      if (sheet.cut_x_mm && sheet.remnant_dims) {
        remnantPill.style.display = 'inline-flex';
        remnantPill.innerHTML = `✂ Cut @ X = ${sheet.cut_x_mm.toFixed(1)} mm • Remnant: ${sheet.remnant_dims}`;
      } else if (sheet.cut_x_mm) {
        remnantPill.style.display = 'inline-flex';
        remnantPill.innerHTML = `✂ Cut @ X = ${sheet.cut_x_mm.toFixed(1)} mm`;
      } else {
        remnantPill.style.display = 'none';
      }

      // Update Bottom Dock Metrics
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

      // Manifest Pills
      const pillsContainer = document.getElementById('part-pills');
      pillsContainer.innerHTML = '';
      for (const [pname, count] of Object.entries(sheet.part_counts)) {
        const pill = document.createElement('div');
        pill.className = 'part-tag';
        pill.setAttribute('data-name', pname);
        pill.innerHTML = `${pname}: <span>${count}</span>`;
        pill.onclick = () => highlightPartsByName(pname);
        pillsContainer.appendChild(pill);
      }

      // Fetch SVG & Dynamically Calculate Native Aspect Ratio
      try {
        const res = await fetch(sheet.svg_url);
        const svgText = await res.text();
        svgHost.innerHTML = svgText;

        const svgEl = svgHost.querySelector('svg');
        if (svgEl) {
          const vb = svgEl.getAttribute('viewBox');
          let sheetW = 1220;
          let sheetH = 2440;
          if (vb) {
            const parts = vb.trim().split(/[\s,]+/).map(Number);
            if (parts.length === 4 && parts[2] > 0 && parts[3] > 0) {
              sheetW = parts[2];
              sheetH = parts[3];
            }
          }
          // Set wrapper dimensions proportional to real sheet aspect ratio
          const baseW = 1000;
          const baseH = Math.round((sheetH / sheetW) * baseW);
          wrapper.style.width = `${baseW}px`;
          wrapper.style.height = `${baseH}px`;

          // Format dimensions for display
          const displayW = Math.round(sheetW / 100);
          const displayH = Math.round(sheetH / 100);
          document.getElementById('stat-sheet-dims').textContent = `${displayW} × ${displayH} mm`;

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

      setTimeout(fitToScreen, 60);
    }

    function highlightPartsByName(partName) {
      const svgEl = svgHost.querySelector('svg');
      if (!svgEl) return;
      const alreadyActive = document.querySelector(`.part-tag.active[data-name="${partName}"]`);

      document.querySelectorAll('.part-tag').forEach(tag => {
        tag.classList.toggle('active', !alreadyActive && tag.getAttribute('data-name') === partName);
      });

      svgEl.querySelectorAll('.nested-part').forEach(p => {
        const name = p.getAttribute('data-part') || p.getAttribute('data-part-name') || '';
        p.classList.toggle('highlighted', !alreadyActive && name === partName);
      });
    }

    // Pan & Zoom Engine
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
      const isMobile = window.innerWidth < 768;
      const padX = isMobile ? 16 : 48;
      const padY = isMobile ? 65 : 75; // accounts for floating top HUD and bottom dock
      const availW = canvas.clientWidth - padX * 2;
      const availH = canvas.clientHeight - padY * 2;
      const scaleX = availW / wrapper.offsetWidth;
      const scaleY = availH / wrapper.offsetHeight;
      scale = Math.min(scaleX, scaleY, 4.0);
      panX = (canvas.clientWidth - wrapper.offsetWidth * scale) / 2;
      panY = (canvas.clientHeight - wrapper.offsetHeight * scale) / 2;
      updateTransform();
    }

    window.addEventListener('resize', () => {
      if (document.body.classList.contains('mode-viewer')) {
        fitToScreen();
      }
    });

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
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
      if (e.key === 'Escape') closeDrawer();
      else if (e.key === 'b' || e.key === 'B') toggleDrawer();
      else if (e.key === 'ArrowRight' || e.key === 'j') nextSheet();
      else if (e.key === 'ArrowLeft' || e.key === 'k') prevSheet();
      else if (e.key === 'f' || e.key === 'F') fitToScreen();
      else if (e.key === '1') resetZoom();
      else if (e.key === '+' || e.key === '=') zoomIn();
      else if (e.key === '-') zoomOut();
    });

    async function refreshAll() {
      await loadCatalogue();
      await loadBatches();
    }

    // Initialize Page & Route
    const currentPath = window.location.pathname.toLowerCase();
    const urlParams = new URLSearchParams(window.location.search);
    const startInViewer = urlParams.get('view') === 'viewer' || 
      urlParams.get('view') === 'inspector' || 
      currentPath.includes('inspector') || 
      currentPath.includes('viewer');

    document.getElementById('input-batch-name').value = generateDefaultBatchName();
    document.getElementById('host-label').textContent = window.location.hostname;

    async function init() {
      await loadCatalogue();
      await loadBatches();
      switchView(startInViewer ? 'viewer' : 'studio', false);
    }
    init();
  </script>
</body>
</html>
"""


# ==============================================================================
# HTTP REQUEST HANDLER
# ==============================================================================

class VisualizerRequestHandler(SimpleHTTPRequestHandler):
    """Serves the batch production studio, catalogue API, and live nesting execution."""

    def do_HEAD(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html", "/inspector", "/viewer", "/studio"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return
        super().do_HEAD()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html", "/inspector", "/viewer", "/studio"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
            return

        elif path == "/api/catalogue":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            catalogue = get_catalogue()
            self.wfile.write(json.dumps(catalogue).encode("utf-8"))
            return

        elif path == "/api/batches":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            batches = get_all_batches_grouped()
            self.wfile.write(json.dumps(batches).encode("utf-8"))
            return

        elif path.startswith("/parts/"):
            # Serve individual part CAD SVGs for the catalogue preview
            rel_path = path[len("/parts/"):]
            local_path = os.path.join(PARTS_DIR, rel_path)
            if not os.path.exists(local_path):
                self.send_error(404, f"Part Not Found: {rel_path}")
                return

            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            with open(local_path, "rb") as f:
                self.wfile.write(f.read())
            return

        elif path.startswith("/output/"):
            # Serve sheet SVGs and preview PNGs
            rel_path = path[len("/output/"):]
            local_path = os.path.join(OUTPUT_DIR, rel_path)
            if not os.path.exists(local_path):
                self.send_error(404, f"Output File Not Found: {rel_path}")
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

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/run_batch":
            content_length = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(content_length)
            try:
                data = json.loads(body_bytes.decode("utf-8"))
                batch_name = data.get("batch_name", "")
                order = data.get("order", {})
                sheet_w = float(data.get("sheet_w_mm", 1220.0))
                sheet_h = float(data.get("sheet_h_mm", 2440.0))
                kerf = float(data.get("kerf_mm", 2.0))
                margin = float(data.get("margin_mm", 5.0))

                result = execute_production_batch(
                    batch_name=batch_name,
                    order=order,
                    sheet_w_mm=sheet_w,
                    sheet_h_mm=sheet_h,
                    kerf_mm=kerf,
                    margin_mm=margin
                )

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode("utf-8"))

            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
            return

        else:
            self.send_error(404, "Not Found")


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run_visualizer(port: int = PORT):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(PARTS_DIR, exist_ok=True)
    local_ip = get_local_ip()

    # Pre-cache parts
    try:
        get_named_parts()
    except Exception as e:
        print(f"[*] Warning: Could not pre-cache parts: {e}")

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
    print("   EASYNEST V2 - BATCH PRODUCTION STUDIO & VISUALIZER")
    print("=" * 70)
    print(f"[*] Local Access (this machine) : http://localhost:{actual_port}")
    print(f"[*] Network Access (your phone) : http://{local_ip}:{actual_port}")
    print(f"[*] CAD Parts Directory         : {PARTS_DIR}")
    print(f"[*] Batch Outputs Directory     : {OUTPUT_DIR}")
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
