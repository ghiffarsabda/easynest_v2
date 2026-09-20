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
import threading
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

    # Guillotine cut line detection (horizontal or vertical)
    has_cut = ("guillotine-line" in content or "guillotine-cut-line" in content or "STRAIGHT GUILLOTINE" in content)
    cut_x_match = re.search(r'X\s*=\s*([0-9.]+)\s*mm', content)
    cut_y_match = re.search(r'Y\s*=\s*([0-9.]+)\s*mm', content)
    cut_x_mm = float(cut_x_match.group(1)) if (has_cut and cut_x_match) else None
    cut_y_mm = float(cut_y_match.group(1)) if (has_cut and cut_y_match) else None
    cut_axis = 'y' if cut_y_mm is not None else ('x' if cut_x_mm is not None else None)
    cut_pos_mm = cut_y_mm if cut_axis == 'y' else cut_x_mm

    # Remnant detection
    remnant_match = re.search(r'REUSABLE VIRGIN REMNANT\s*\(([^\)]+)\)', content)
    remnant_dims = remnant_match.group(1).strip() if remnant_match else None

    # Fill mode detection
    is_fill = ("FILL MODE" in content or "EASYNEST FILL MODE" in content or "filler-label" in content)
    fill_info = None
    if is_fill:
        fill_banner_match = re.search(r'PRIMARY:\s*([^\s\(]+)\s*\((\d+)\)\s*\|\s*FILLER:\s*([^\s\(]+)\s*\((\d+)\)', content)
        yield_boost_match = re.search(r'YIELD:\s*([0-9.]+)%\s*→\s*([0-9.]+)%\s*\(\+([0-9.]+)%\s*GAIN\)', content)
        if fill_banner_match and yield_boost_match:
            fill_info = {
                "primary_name": fill_banner_match.group(1),
                "primary_count": int(fill_banner_match.group(2)),
                "filler_name": fill_banner_match.group(3),
                "filler_count": int(fill_banner_match.group(4)),
                "baseline_yield": float(yield_boost_match.group(1)),
                "boosted_yield": float(yield_boost_match.group(2)),
                "gain": float(yield_boost_match.group(3)),
            }

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
    if batch_id in friendly_batch_titles:
        batch_title = friendly_batch_titles[batch_id]
    elif is_fill and fill_info:
        batch_title = f"⚡ Fill: {fill_info['primary_name']} + {fill_info['filler_name']}"
    else:
        batch_title = batch_id.replace("_", " ").title()

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
        "is_fill": is_fill,
        "fill_info": fill_info,
        "cut_axis": cut_axis,
        "cut_pos_mm": cut_pos_mm,
        "cut_x_mm": cut_x_mm,
        "cut_y_mm": cut_y_mm,
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
    margin_mm: float = 5.0,
    packing_strategy: str = "auto"
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
        margin_mm=margin_mm,
        packing_strategy=packing_strategy
    )

    t0 = time.time()
    records = planner.run_batch_order(clean_order, named_parts, output_prefix=safe_slug)
    runtime_s = round(time.time() - t0, 2)

    # Generate preview PNG for partial sheet or first sheet if possible
    if records and records[-1].output_svg_path and os.path.exists(records[-1].output_svg_path):
        png_out = records[-1].output_svg_path.replace(".svg", "_preview.png")
        def _render_bg(svg_p, png_p):
            try:
                render_preview_png(svg_p, png_p, dpi=90)
            except Exception:
                pass
        threading.Thread(target=_render_bg, args=(records[-1].output_svg_path, png_out), daemon=True).start()

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


def execute_fill_batch(
    primary_part_name: str,
    filler_part_name: str,
    primary_mode: str = "max_fit",
    primary_qty: Optional[int] = None,
    batch_name: str = "",
    sheet_w_mm: float = 1220.0,
    sheet_h_mm: float = 2440.0,
    kerf_mm: float = 2.0,
    margin_mm: float = 5.0,
    packing_strategy: str = "auto"
) -> Dict[str, Any]:
    """Executes MultiSheetBatchPlanner.run_fill_order() for primary + void filler nesting."""
    from batch_nest import MultiSheetBatchPlanner
    from cdr_enhancer import render_preview_png

    named_parts = get_named_parts()
    valid_part_names = {name for name, _ in named_parts}
    if primary_part_name not in valid_part_names:
        raise ValueError(f"Primary part '{primary_part_name}' not found in catalogue.")
    if filler_part_name not in valid_part_names:
        raise ValueError(f"Filler part '{filler_part_name}' not found in catalogue.")

    safe_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', batch_name.strip())
    if not safe_slug:
        safe_slug = f"fill_{primary_part_name}_{filler_part_name}_{int(time.time())}"

    planner = MultiSheetBatchPlanner(
        sheet_w_mm=sheet_w_mm,
        sheet_h_mm=sheet_h_mm,
        kerf_mm=kerf_mm,
        margin_mm=margin_mm,
        packing_strategy=packing_strategy
    )

    t0 = time.time()
    record = planner.run_fill_order(
        primary_part_name=primary_part_name,
        filler_part_name=filler_part_name,
        named_parts=named_parts,
        primary_qty_mode=primary_mode,
        primary_qty=(primary_qty or 10),
        output_prefix=safe_slug
    )
    runtime_s = round(time.time() - t0, 2)

    if record and record.output_svg_path and os.path.exists(record.output_svg_path):
        png_out = record.output_svg_path.replace(".svg", "_preview.png")
        def _render_fill_bg(svg_p, png_p):
            try:
                render_preview_png(svg_p, png_p, dpi=90)
            except Exception:
                pass
        threading.Thread(target=_render_fill_bg, args=(record.output_svg_path, png_out), daemon=True).start()

    return {
        "success": True,
        "batch_id": safe_slug,
        "batch_title": f"⚡ Fill: {primary_part_name} + {filler_part_name}",
        "total_parts": record.total_parts,
        "parts_breakdown": record.part_counts,
        "yield_pct": record.utilization_pct,
        "scrap_pct": record.scrap_pct,
        "sheet_svg_path": record.output_svg_path,
        "svg_filename": os.path.basename(record.output_svg_path) if record.output_svg_path else None,
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

    /* Studio Mode Switcher */
    .studio-mode-switcher {
      display: flex;
      background: #111827;
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 4px;
      gap: 6px;
      max-width: 580px;
    }
    .mode-tab-btn {
      flex: 1;
      background: transparent;
      border: none;
      color: var(--text-dim);
      font-size: 0.88rem;
      font-weight: 600;
      padding: 0.55rem 1.1rem;
      border-radius: 6px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
      transition: all 0.15s ease;
    }
    .mode-tab-btn:hover { color: var(--text); background: #1e293b; }
    .mode-tab-btn.active {
      background: #0284c7;
      color: white;
      box-shadow: 0 2px 8px rgba(2, 132, 199, 0.4);
    }
    .mode-tab-btn.mode-fill.active {
      background: linear-gradient(135deg, #059669, #0d9488);
      box-shadow: 0 2px 10px rgba(5, 150, 105, 0.4);
    }

    /* Fill Card Styles */
    .fill-preview-comparison {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1.25rem;
      margin-top: 0.5rem;
    }
    .fill-compare-card {
      background: #0d1322;
      border: 1px solid #1f2937;
      border-radius: 8px;
      padding: 1rem;
      display: flex;
      align-items: center;
      gap: 1rem;
    }
    .fill-compare-thumb {
      width: 80px;
      height: 80px;
      background: #ffffff;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 0.35rem;
      flex-shrink: 0;
    }
    .fill-compare-thumb img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
    }
    .fill-compare-info {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
    }
    .fill-compare-badge {
      font-size: 0.7rem;
      font-weight: 700;
      text-transform: uppercase;
      padding: 0.15rem 0.45rem;
      border-radius: 4px;
      display: inline-block;
      width: fit-content;
    }
    .fill-badge-primary { background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); }
    .fill-badge-filler { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }

    .badge-fill-stats {
      background: rgba(16, 185, 129, 0.16);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.35);
      font-size: 0.75rem;
      font-weight: 700;
      padding: 0.25rem 0.6rem;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      white-space: nowrap;
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
       NORMAL SHEET VIEWER
       ========================================================================== */
    .viewer-layout {
      position: fixed;
      inset: 0;
      width: 100vw;
      height: 100dvh;
      display: none;
      flex-direction: column;
      background: #070a12;
      z-index: 20;
    }
    .viewer-layout.active {
      display: flex !important;
    }

    /* Top Navigation Bar */
    .viewer-navbar {
      background: #0d1322;
      border-bottom: 1px solid var(--card-border);
      padding: 0.6rem 1.2rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      flex-wrap: wrap;
      z-index: 10;
    }
    .viewer-nav-left, .viewer-nav-center, .viewer-nav-right {
      display: flex;
      align-items: center;
      gap: 0.6rem;
    }
    .nav-divider {
      width: 1px;
      height: 20px;
      background: var(--card-border);
      margin: 0 0.2rem;
    }
    .nav-label {
      font-size: 0.8rem;
      color: var(--text-dim);
      font-weight: 600;
    }
    .nav-select {
      max-width: 320px;
      padding: 0.35rem 0.6rem;
      font-size: 0.85rem;
    }
    .sheet-counter-badge {
      background: #1e293b;
      border: 1px solid #334155;
      color: #f3f4f6;
      font-family: var(--font-mono);
      font-size: 0.85rem;
      font-weight: 700;
      padding: 0.3rem 0.8rem;
      border-radius: 6px;
      white-space: nowrap;
    }
    .badge-remnant {
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
    .nav-meta-tag {
      font-size: 0.8rem;
      font-family: var(--font-mono);
      background: #111827;
      border: 1px solid var(--card-border);
      color: var(--text-dim);
      padding: 0.3rem 0.6rem;
      border-radius: 6px;
      white-space: nowrap;
    }
    .nav-meta-tag.emerald {
      color: #34d399;
      border-color: rgba(16, 185, 129, 0.3);
      background: rgba(16, 185, 129, 0.1);
    }

    /* Main Area: Centered Sheet (Zero Dragging, Normal Clean View) */
    .viewer-canvas-area {
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 1rem 1.5rem;
      overflow: auto;
      background: #090d16;
    }
    .sheet-paper {
      background: #ffffff;
      border-radius: 6px;
      box-shadow: 0 10px 40px rgba(0, 0, 0, 0.7);
      display: flex;
      align-items: center;
      justify-content: center;
      max-height: calc(100vh - 145px);
      max-width: 95vw;
      padding: 8px;
    }
    #svg-host {
      display: flex;
      align-items: center;
      justify-content: center;
      max-width: 100%;
      max-height: calc(100vh - 160px);
    }
    #svg-host svg {
      max-height: calc(100vh - 160px);
      max-width: min(94vw, 1300px);
      width: auto;
      height: auto;
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

    /* Bottom Manifest Bar */
    .viewer-footer {
      background: #0d1322;
      border-top: 1px solid var(--card-border);
      padding: 0.5rem 1.2rem;
      display: flex;
      align-items: center;
      gap: 1rem;
      flex-wrap: wrap;
      min-height: 48px;
      z-index: 10;
    }
    .footer-label {
      font-size: 0.75rem;
      color: var(--text-dim);
      font-weight: 600;
      white-space: nowrap;
    }
    .part-pills {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      flex-wrap: wrap;
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

    <!-- Studio Mode Switcher: Standard Batch vs Void Fill Mode -->
    <div class="studio-mode-switcher">
      <button id="mode-tab-batch" class="mode-tab-btn active" onclick="switchStudioMode('batch')">
        <span>📦</span> Standard Multi-Sheet Batch
      </button>
      <button id="mode-tab-fill" class="mode-tab-btn mode-fill" onclick="switchStudioMode('fill')">
        <span>⚡</span> Void Fill Mode (Primary + Filler)
      </button>
    </div>

    <!-- Mode 1: Standard Batch Container -->
    <div id="studio-mode-batch-container" style="display: flex; flex-direction: column; gap: 1.25rem;">

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

        <!-- Rational Compaction Strategy -->
        <div class="input-group">
          <label class="input-label" for="select-packing-strategy">Compaction Strategy</label>
          <select id="select-packing-strategy" class="form-control">
            <option value="auto" selected>Auto (Smart Operator — Best Remnant)</option>
            <option value="horizontal">Horizontal Strip (Top Row across plate)</option>
            <option value="vertical">Vertical Strip (Left Column down plate)</option>
            <option value="compact">Compact Block (Corner Envelope)</option>
          </select>
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
    </div> <!-- /studio-mode-batch-container -->

    <!-- Mode 2: Fill Nesting Container -->
    <div id="studio-mode-fill-container" style="display: none; flex-direction: column; gap: 1.25rem;">
      <!-- Fill Configuration Card -->
      <div class="config-card">
        <div class="config-header">
          <div class="config-title">
            <span>⚡</span> Primary + Void Filler Nesting Engine
          </div>
          <div class="preset-pills">
            <span style="font-size: 0.75rem; color: var(--text-dim); margin-right: 4px;">Fill Presets:</span>
            <button class="preset-btn" onclick="applyFillPreset('cavity_demo')">🏍️ Tail Hugger + Fork Brace (Cavity)</button>
            <button class="preset-btn" onclick="applyFillPreset('dense_void')">🛡️ Skid Plate + Gusset Tag (Dense)</button>
            <button class="preset-btn" onclick="applyFillPreset('corridor_pack')">🏁 Tail Hugger + Sprocket Cover</button>
          </div>
        </div>

        <div class="config-grid">
          <!-- Fill Batch Identifier -->
          <div class="input-group">
            <label class="input-label" for="fill-input-batch-name">Fill Batch Identifier</label>
            <input id="fill-input-batch-name" class="form-control" type="text" placeholder="e.g. fill_hugger_brace_01" />
          </div>

          <!-- Primary Component SKU -->
          <div class="input-group">
            <label class="input-label" for="fill-primary-sku">Primary Product (Base Host Part)</label>
            <select id="fill-primary-sku" class="form-control" onchange="onFillPartChange()">
              <!-- Populated via JS -->
            </select>
          </div>

          <!-- Primary Quantity Mode -->
          <div class="input-group">
            <label class="input-label" for="fill-primary-mode">Primary Product Allocation</label>
            <select id="fill-primary-mode" class="form-control" onchange="onFillModeChange()">
              <option value="max_fit" selected>Max Fit (Exhaust Full Sheet First)</option>
              <option value="custom">Custom Fixed Quantity</option>
            </select>
          </div>

          <!-- Custom Primary Quantity (only when custom) -->
          <div id="fill-custom-qty-box" class="input-group" style="display: none;">
            <label class="input-label" for="fill-primary-qty">Primary Target Quantity</label>
            <input id="fill-primary-qty" class="form-control" type="number" value="12" min="1" max="500" />
          </div>

          <!-- Secondary Filler Component SKU -->
          <div class="input-group">
            <label class="input-label" for="fill-filler-sku">Secondary Filler (Void & Corridors)</label>
            <select id="fill-filler-sku" class="form-control" onchange="onFillPartChange()">
              <!-- Populated via JS -->
            </select>
          </div>

          <!-- Sheet Size Preset -->
          <div class="input-group">
            <label class="input-label" for="fill-select-sheet-size">Sheet Size Preset</label>
            <select id="fill-select-sheet-size" class="form-control" onchange="onFillSheetSizeChange()">
              <option value="1220x2440" selected>Standard Industrial: 1220 × 2440 mm (4×8 ft)</option>
              <option value="1000x2000">Standard Metric: 1000 × 2000 mm (1×2 m)</option>
              <option value="1220x1220">Square Half-Sheet: 1220 × 1220 mm (4×4 ft)</option>
              <option value="600x1200">Compact Router Bed: 600 × 1200 mm</option>
            </select>
          </div>

          <!-- Tool Kerf & Margin -->
          <div class="input-group">
            <label class="input-label" for="fill-input-kerf">Tool Cutting Kerf (mm)</label>
            <input id="fill-input-kerf" class="form-control" type="number" value="2.0" step="0.5" min="0.5" max="20.0" />
          </div>

          <div class="input-group">
            <label class="input-label" for="fill-input-margin">Sheet Border Margin (mm)</label>
            <input id="fill-input-margin" class="form-control" type="number" value="5.0" step="1.0" min="0.0" max="50.0" />
          </div>
        </div>

        <!-- Part Previews Comparison -->
        <div class="fill-preview-comparison">
          <div class="fill-compare-card">
            <div class="fill-compare-thumb">
              <img id="fill-preview-primary-img" src="" alt="Primary Part" />
            </div>
            <div class="fill-compare-info">
              <span class="fill-compare-badge fill-badge-primary">PRIMARY BASE PART</span>
              <div id="fill-preview-primary-title" style="font-weight: 700; font-size: 0.95rem; color: #f9fafb;">-</div>
              <div id="fill-preview-primary-dims" style="font-size: 0.8rem; color: var(--text-dim); font-family: var(--font-mono);">-</div>
              <div style="font-size: 0.75rem; color: #93c5fd; margin-top: 4px;">Rendered in Blue (#3b82f6), labeled P1..Pn</div>
            </div>
          </div>

          <div class="fill-compare-card">
            <div class="fill-compare-thumb">
              <img id="fill-preview-filler-img" src="" alt="Filler Part" />
            </div>
            <div class="fill-compare-info">
              <span class="fill-compare-badge fill-badge-filler">SECONDARY VOID FILLER</span>
              <div id="fill-preview-filler-title" style="font-weight: 700; font-size: 0.95rem; color: #f9fafb;">-</div>
              <div id="fill-preview-filler-dims" style="font-size: 0.8rem; color: var(--text-dim); font-family: var(--font-mono);">-</div>
              <div style="font-size: 0.75rem; color: #6ee7b7; margin-top: 4px;">Rendered in Emerald (#10b981), labeled F1..Fn</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Fill Action Banner -->
      <div class="action-banner" style="border-color: #059669; background: linear-gradient(90deg, #092019, #0c2d24);">
        <div class="banner-stats">
          <div class="banner-stat-item">
            <span class="banner-stat-num" style="color: #34d399;">3-Phase</span>
            <span class="banner-stat-label">Nesting Pipeline</span>
          </div>
          <div class="banner-stat-item">
            <span class="banner-stat-num" style="font-size: 0.95rem; color: #a7f3d0;">1. Primary Layout → 2. Concave Cavity Injection → 3. Open Remnant Corridors</span>
            <span class="banner-stat-label">Guaranteed 0 Collisions & Maximum Sheet Saturation</span>
          </div>
        </div>

        <button id="btn-process-fill" class="btn btn-emerald" style="padding: 0.75rem 2rem; font-size: 1rem; background: #059669;" onclick="processFill()">
          <span>⚡</span> RUN FILL NESTING
        </button>
      </div>
    </div>

  </div>

  <!-- =======================================================================
       FULL-PAGE SHEET INSPECTOR
       ======================================================================= -->
  <!-- =======================================================================
       NORMAL SHEET VIEWER (CLEAN & CENTERED)
       ======================================================================= -->
  <div id="view-viewer" class="view-container viewer-layout">

    <!-- Top Navigation Bar -->
    <header class="viewer-navbar">
      <div class="viewer-nav-left">
        <button class="btn" onclick="switchView('studio')">
          ← Back to Studio
        </button>
        <div class="nav-divider"></div>
        <label class="nav-label" for="select-batch">Batch:</label>
        <select id="select-batch" class="form-control nav-select" onchange="onBatchSelectChange()">
          <!-- Populated dynamically -->
        </select>
      </div>

      <div class="viewer-nav-center">
        <button class="btn btn-sm" onclick="prevSheet()" title="Previous Sheet (←)">◀ Prev</button>
        <span id="sheet-nav-label" class="sheet-counter-badge">Sheet 1 of 1</span>
        <button class="btn btn-sm" onclick="nextSheet()" title="Next Sheet (→)">Next ▶</button>
        <div id="top-remnant-pill" class="badge-remnant" style="display: none;"></div>
        <div id="top-fill-pill" class="badge-fill-stats" style="display: none;"></div>
      </div>

      <div class="viewer-nav-right">
        <span id="stat-sheet-dims" class="nav-meta-tag">1220 × 2440 mm</span>
        <span id="stat-total-parts" class="nav-meta-tag emerald">-</span>
        <button class="btn btn-sm" onclick="refreshAll()" title="Refresh All Data">↻</button>
      </div>
    </header>

    <!-- Centered Sheet Frame (No dragging, normal centered preview) -->
    <main class="viewer-canvas-area">
      <div class="sheet-paper">
        <div id="svg-host"></div>
      </div>
    </main>

    <!-- Bottom Manifest Bar -->
    <footer class="viewer-footer">
      <span class="footer-label">Parts on this sheet (hover to highlight):</span>
      <div id="part-pills" class="part-pills"></div>
    </footer>

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

    const svgHost = document.getElementById('svg-host');
    const tooltip = document.getElementById('hover-tooltip');

    // View Switching
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
    }

    window.addEventListener('popstate', () => {
      const path = window.location.pathname.toLowerCase();
      const isViewer = path.includes('inspector') || path.includes('viewer');
      switchView(isViewer ? 'viewer' : 'studio', false);
    });

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
        populateFillSelects();
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
      const packingStrategy = document.getElementById('select-packing-strategy').value || 'auto';

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
            margin_mm: margin,
            packing_strategy: packingStrategy
          })
        });

        const result = await res.json();
        overlay.style.display = 'none';

        if (result.success) {
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
    // VOID FILL MODE JAVASCRIPT LOGIC
    // =========================================================================

    function switchStudioMode(mode) {
      const isFill = (mode === 'fill');
      document.getElementById('mode-tab-batch').classList.toggle('active', !isFill);
      document.getElementById('mode-tab-fill').classList.toggle('active', isFill);
      document.getElementById('studio-mode-batch-container').style.display = isFill ? 'none' : 'flex';
      document.getElementById('studio-mode-fill-container').style.display = isFill ? 'flex' : 'none';
    }

    function populateFillSelects() {
      const primSel = document.getElementById('fill-primary-sku');
      const fillSel = document.getElementById('fill-filler-sku');
      if (!primSel || !fillSel) return;

      primSel.innerHTML = '';
      fillSel.innerHTML = '';

      catalogueData.forEach(item => {
        const optP = document.createElement('option');
        optP.value = item.id;
        optP.textContent = `${item.title} (${item.width_mm} × ${item.height_mm} mm)`;
        primSel.appendChild(optP);

        const optF = document.createElement('option');
        optF.value = item.id;
        optF.textContent = `${item.title} (${item.width_mm} × ${item.height_mm} mm)`;
        fillSel.appendChild(optF);
      });

      if (catalogueData.some(c => c.id === 'p02_rear_tail_hugger')) {
        primSel.value = 'p02_rear_tail_hugger';
      }
      if (catalogueData.some(c => c.id === 'p08_triple_tree_fork_brace')) {
        fillSel.value = 'p08_triple_tree_fork_brace';
      }

      onFillPartChange();
    }

    function onFillPartChange() {
      const primId = document.getElementById('fill-primary-sku').value;
      const fillId = document.getElementById('fill-filler-sku').value;

      const pItem = catalogueData.find(c => c.id === primId);
      const fItem = catalogueData.find(c => c.id === fillId);

      if (pItem) {
        document.getElementById('fill-preview-primary-img').src = pItem.svg_url;
        document.getElementById('fill-preview-primary-title').textContent = pItem.title;
        document.getElementById('fill-preview-primary-dims').textContent = `${pItem.width_mm} × ${pItem.height_mm} mm • ${pItem.tier}`;
      }
      if (fItem) {
        document.getElementById('fill-preview-filler-img').src = fItem.svg_url;
        document.getElementById('fill-preview-filler-title').textContent = fItem.title;
        document.getElementById('fill-preview-filler-dims').textContent = `${fItem.width_mm} × ${fItem.height_mm} mm • ${fItem.tier}`;
      }
    }

    function onFillModeChange() {
      const mode = document.getElementById('fill-primary-mode').value;
      const customBox = document.getElementById('fill-custom-qty-box');
      customBox.style.display = (mode === 'custom') ? 'flex' : 'none';
    }

    function onFillSheetSizeChange() {
      // Preset selection handles size
    }

    function applyFillPreset(key) {
      if (key === 'cavity_demo') {
        document.getElementById('fill-primary-sku').value = 'p02_rear_tail_hugger';
        document.getElementById('fill-primary-mode').value = 'max_fit';
        document.getElementById('fill-filler-sku').value = 'p08_triple_tree_fork_brace';
        document.getElementById('fill-input-batch-name').value = 'fill_hugger_cavity_demo';
      } else if (key === 'dense_void') {
        document.getElementById('fill-primary-sku').value = 'p04_engine_skid_plate';
        document.getElementById('fill-primary-mode').value = 'custom';
        document.getElementById('fill-primary-qty').value = '12';
        document.getElementById('fill-filler-sku').value = 'p12_frame_gusset_tag';
        document.getElementById('fill-input-batch-name').value = 'fill_skid_dense_void';
      } else if (key === 'corridor_pack') {
        document.getElementById('fill-primary-sku').value = 'p02_rear_tail_hugger';
        document.getElementById('fill-primary-mode').value = 'custom';
        document.getElementById('fill-primary-qty').value = '16';
        document.getElementById('fill-filler-sku').value = 'p07_sprocket_cover';
        document.getElementById('fill-input-batch-name').value = 'fill_hugger_corridor_pack';
      }
      onFillModeChange();
      onFillPartChange();
    }

    async function processFill() {
      const primaryPart = document.getElementById('fill-primary-sku').value;
      const fillerPart = document.getElementById('fill-filler-sku').value;
      const primaryMode = document.getElementById('fill-primary-mode').value;
      const primaryQty = (primaryMode === 'custom') ? (parseInt(document.getElementById('fill-primary-qty').value) || 12) : null;
      const sheetSizeVal = document.getElementById('fill-select-sheet-size').value;
      const parts = sheetSizeVal.split('x');
      const sheetW = parseFloat(parts[0]) || 1220.0;
      const sheetH = parseFloat(parts[1]) || 2440.0;
      const kerf = parseFloat(document.getElementById('fill-input-kerf').value) || 2.0;
      const margin = parseFloat(document.getElementById('fill-input-margin').value) || 5.0;

      let batchName = document.getElementById('fill-input-batch-name').value.trim();
      if (!batchName) {
        batchName = `fill_${primaryPart}_${fillerPart}_${Date.now()}`;
      }

      const overlay = document.getElementById('progress-overlay');
      overlay.style.display = 'flex';
      document.getElementById('progress-text').textContent = `Running Void Fill Nesting: ${batchName}...`;
      document.getElementById('progress-subtext').textContent = 'Phase 1: Primary Layout → Phase 2: Cavity Injection → Phase 3: Void Corridors...';

      try {
        const res = await fetch('/api/run_fill', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            primary_part_name: primaryPart,
            filler_part_name: fillerPart,
            primary_mode: primaryMode,
            primary_qty: primaryQty,
            batch_name: batchName,
            sheet_w_mm: sheetW,
            sheet_h_mm: sheetH,
            kerf_mm: kerf,
            margin_mm: margin,
            packing_strategy: 'auto'
          })
        });

        const result = await res.json();
        overlay.style.display = 'none';

        if (result.success) {
          await loadBatches(result.batch_id);
          switchView('viewer');
        } else {
          alert(`Fill nesting failed: ${result.error || 'Unknown error'}`);
        }
      } catch (err) {
        overlay.style.display = 'none';
        alert(`Error executing fill nesting: ${err.message}`);
      }
    }

    // =========================================================================
    // NORMAL SHEET VIEWER LOGIC (NO DRAGGING)
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
          activeSheetIndex = 0;
          loadCurrentSheet();
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
      loadCurrentSheet();
    }

    function prevSheet() {
      const batch = allBatches.find(b => b.batch_id === activeBatchId);
      if (batch && activeSheetIndex > 0) {
        activeSheetIndex -= 1;
        loadCurrentSheet();
      }
    }

    function nextSheet() {
      const batch = allBatches.find(b => b.batch_id === activeBatchId);
      if (batch && activeSheetIndex < batch.sheets.length - 1) {
        activeSheetIndex += 1;
        loadCurrentSheet();
      }
    }

    async function loadCurrentSheet() {
      const batch = allBatches.find(b => b.batch_id === activeBatchId);
      if (!batch || !batch.sheets[activeSheetIndex]) return;
      const sheet = batch.sheets[activeSheetIndex];

      const totalSheets = batch.sheets.length;
      document.getElementById('sheet-nav-label').textContent = `Sheet ${sheet.sheet_num} of ${totalSheets}`;
      document.getElementById('stat-total-parts').textContent = `${sheet.total_parts} parts`;

      const remnantPill = document.getElementById('top-remnant-pill');
      const fillPill = document.getElementById('top-fill-pill');

      if (sheet.is_fill && sheet.fill_info) {
        const fi = sheet.fill_info;
        if (fillPill) {
          fillPill.style.display = 'inline-flex';
          fillPill.innerHTML = `⚡ Yield: ${fi.baseline_yield.toFixed(1)}% → ${fi.boosted_yield.toFixed(1)}% (+${fi.gain.toFixed(1)}% Gain) • Prim: ${fi.primary_count} | Fill: ${fi.filler_count}`;
        }
      } else if (fillPill) {
        fillPill.style.display = 'none';
      }

      const axis = sheet.cut_axis ? sheet.cut_axis.toUpperCase() : (sheet.cut_y_mm ? 'Y' : 'X');
      const cutPos = sheet.cut_pos_mm || sheet.cut_y_mm || sheet.cut_x_mm;
      if (!sheet.is_fill && cutPos && sheet.remnant_dims) {
        remnantPill.style.display = 'inline-flex';
        remnantPill.innerHTML = `✂ Shear Cut @ ${axis} = ${cutPos.toFixed(1)} mm • Remnant: ${sheet.remnant_dims}`;
      } else if (!sheet.is_fill && cutPos) {
        remnantPill.style.display = 'inline-flex';
        remnantPill.innerHTML = `✂ Shear Cut @ ${axis} = ${cutPos.toFixed(1)} mm`;
      } else {
        remnantPill.style.display = 'none';
      }

      // Manifest Pills
      const pillsContainer = document.getElementById('part-pills');
      pillsContainer.innerHTML = '';
      for (const [pname, count] of Object.entries(sheet.part_counts)) {
        const pill = document.createElement('div');
        pill.className = 'part-tag';
        pill.setAttribute('data-name', pname);
        if (sheet.is_fill && sheet.fill_info) {
          const isPrim = (pname === sheet.fill_info.primary_name);
          const isFill = (pname === sheet.fill_info.filler_name);
          const prefix = isPrim ? '<span style="color:#60a5fa;font-weight:800;margin-right:2px;">[P]</span> ' : (isFill ? '<span style="color:#34d399;font-weight:800;margin-right:2px;">[F]</span> ' : '');
          pill.innerHTML = `${prefix}${pname}: <span>${count}</span>`;
          if (isPrim) pill.style.borderColor = 'rgba(59, 130, 246, 0.5)';
          if (isFill) pill.style.borderColor = 'rgba(16, 185, 129, 0.5)';
        } else {
          pill.innerHTML = `${pname}: <span>${count}</span>`;
        }
        pill.onclick = () => highlightPartsByName(pname);
        pillsContainer.appendChild(pill);
      }

      // Fetch SVG
      try {
        const res = await fetch(sheet.svg_url);
        const svgText = await res.text();
        svgHost.innerHTML = svgText;

        const svgEl = svgHost.querySelector('svg');
        if (svgEl) {
          const vb = svgEl.getAttribute('viewBox');
          if (vb) {
            const parts = vb.trim().split(/[\s,]+/).map(Number);
            if (parts.length === 4 && parts[2] > 0 && parts[3] > 0) {
              const displayW = Math.round(parts[2] / 100);
              const displayH = Math.round(parts[3] / 100);
              document.getElementById('stat-sheet-dims').textContent = `${displayW} × ${displayH} mm`;
            }
          }

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

    // Keyboard Shortcuts
    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
      if (e.key === 'ArrowRight' || e.key === 'j') nextSheet();
      else if (e.key === 'ArrowLeft' || e.key === 'k') prevSheet();
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
                packing_strategy = data.get("packing_strategy", "auto")

                result = execute_production_batch(
                    batch_name=batch_name,
                    order=order,
                    sheet_w_mm=sheet_w,
                    sheet_h_mm=sheet_h,
                    kerf_mm=kerf,
                    margin_mm=margin,
                    packing_strategy=packing_strategy
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

        elif path == "/api/run_fill":
            content_length = int(self.headers.get("Content-Length", 0))
            body_bytes = self.rfile.read(content_length)
            try:
                data = json.loads(body_bytes.decode("utf-8"))
                primary_part = data.get("primary_part_name", "")
                filler_part = data.get("filler_part_name", "")
                primary_mode = data.get("primary_mode", "max_fit")
                primary_qty = data.get("primary_qty")
                if primary_qty is not None and str(primary_qty).isdigit():
                    primary_qty = int(primary_qty)
                else:
                    primary_qty = None
                batch_name = data.get("batch_name", "")
                sheet_w = float(data.get("sheet_w_mm", 1220.0))
                sheet_h = float(data.get("sheet_h_mm", 2440.0))
                kerf = float(data.get("kerf_mm", 2.0))
                margin = float(data.get("margin_mm", 5.0))
                packing_strategy = data.get("packing_strategy", "auto")

                result = execute_fill_batch(
                    primary_part_name=primary_part,
                    filler_part_name=filler_part,
                    primary_mode=primary_mode,
                    primary_qty=primary_qty,
                    batch_name=batch_name,
                    sheet_w_mm=sheet_w,
                    sheet_h_mm=sheet_h,
                    kerf_mm=kerf,
                    margin_mm=margin,
                    packing_strategy=packing_strategy
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
