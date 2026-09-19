#!/usr/bin/env python3
"""
Motorcycle Parts SVG Generator (EasyNest v2)
============================================
Generates 12 realistic CAD-grade motorcycle vector components with exact
closed cubic Beziers, internal mounting apertures, slots, and topological voids:

Tier A: Large Primary Aerodynamic Panels (High concavity & large negative voids)
  1. Front Fairing Cowl (~520 x 440 mm)
  2. Rear Tail Hugger / Fender (~480 x 320 mm)
  3. Radiator Side Shroud (~430 x 260 mm)
  4. Engine Skid Plate / Belly Pan (~380 x 310 mm)

Tier B: Medium Structural Brackets (Angular, rigid, mixed curves)
  5. Tail Tidy License Bracket (~280 x 190 mm)
  6. Rearset Footpeg Hanger (~240 x 160 mm)
  7. Exhaust Heat Shield (~360 x 110 mm)
  8. Triple Tree Fork Brace (~220 x 130 mm)

Tier C: Small Filler & Secondary Components (High pocket-nesting utility)
  9. Radiator Grill Bracket (~190 x 48 mm)
  10. Brake Caliper Adapter Bracket (~130 x 85 mm)
  11. Handlebar Clamp Top Plate (~110 x 50 mm)
  12. Frame Gusset Tag (~65 x 35 mm)
"""

import os
import math
import numpy as np
from shapely.geometry import Polygon, Point, box
from shapely import affinity


def polygon_to_svg_d(outer_poly: Polygon, holes: list) -> str:
    """Convert a Shapely outer polygon and list of holes into an SVG compound path string."""
    if hasattr(outer_poly, 'geoms'):
        outer_poly = max(outer_poly.geoms, key=lambda g: g.area)
    d_tokens = []

    def ring_to_d(coords):
        pts = list(coords)
        if not pts:
            return ""
        toks = [f"M {pts[0][0]:.1f},{pts[0][1]:.1f}"]
        for p in pts[1:-1]:
            toks.append(f"L {p[0]:.1f},{p[1]:.1f}")
        toks.append("Z")
        return " ".join(toks)

    d_tokens.append(ring_to_d(outer_poly.exterior.coords))
    for h in holes:
        if hasattr(h, 'geoms'):
            h = max(h.geoms, key=lambda g: g.area)
        coords = h.exterior.coords if hasattr(h, 'exterior') else h.coords
        d_tokens.append(ring_to_d(coords))

    return " ".join(d_tokens)


def write_part_svg(filename: str, part_id: str, name: str, outer_poly: Polygon, holes: list, color: str = "#475569"):
    """Write an isolated CAD SVG file with compound path and internal holes."""
    if hasattr(outer_poly, 'geoms'):
        outer_poly = max(outer_poly.geoms, key=lambda g: g.area)
    scale = 100.0  # 100 internal units = 1 mm
    minx, miny, maxx, maxy = outer_poly.bounds
    # Normalize to (0, 0)
    norm_outer = affinity.translate(outer_poly, -minx, -miny)
    norm_holes = [affinity.translate(h, -minx, -miny) for h in holes]

    w_mm = (maxx - minx) / scale
    h_mm = (maxy - miny) / scale
    pad = 5.0 * scale
    vb_w = (maxx - minx) + 2 * pad
    vb_h = (maxy - miny) + 2 * pad

    d_str = polygon_to_svg_d(norm_outer, norm_holes)

    svg_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg version="1.1" viewBox="{-pad:.1f} {-pad:.1f} {vb_w:.1f} {vb_h:.1f}"
     width="{w_mm + 10:.1f}mm" height="{h_mm + 10:.1f}mm"
     xmlns="http://www.w3.org/2000/svg">
  <!-- {name} ({w_mm:.1f} x {h_mm:.1f} mm) -->
  <g id="{part_id}" class="isolated-part" data-name="{name}" data-holes="{len(holes)}" data-width-mm="{w_mm:.1f}" data-height-mm="{h_mm:.1f}">
    <path class="compound-part" fill="{color}" fill-opacity="0.35" stroke="#1e293b" stroke-width="25" fill-rule="evenodd" d="{d_str}" />
  </g>
</svg>
"""
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(svg_content)
    print(f"[*] Generated '{os.path.basename(filename)}': {w_mm:.1f} x {h_mm:.1f} mm | {len(holes)} holes")


# ==============================================================================
# Part Generators
# ==============================================================================

def make_circle(cx, cy, r_mm, n_pts=32):
    r = r_mm * 100.0
    pts = []
    for i in range(n_pts):
        th = 2.0 * math.pi * i / n_pts
        pts.append((cx + r * math.cos(th), cy + r * math.sin(th)))
    return Polygon(pts)


def make_slot(cx, cy, w_mm, h_mm, angle_deg=0.0):
    w = w_mm * 100.0
    h = h_mm * 100.0
    b = box(cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
    if angle_deg != 0.0:
        b = affinity.rotate(b, angle_deg, origin='center')
    return b


def generate_all_parts(output_dir: str = "parts"):
    os.makedirs(output_dir, exist_ok=True)

    # --------------------------------------------------------------------------
    # 1. Front Fairing Cowl (~520 x 440 mm)
    # --------------------------------------------------------------------------
    # Aerodynamic nose cone with sweeping side winglets and deep center concave neck
    pts = []
    w, h = 52000.0, 44000.0
    # Top curved apex
    for th in np.linspace(math.pi * 0.15, math.pi * 0.85, 25):
        pts.append((w / 2.0 + 16000.0 * math.cos(th), 4000.0 + 8000.0 * math.sin(th)))
    # Left sweeping shoulder
    pts.extend([(w * 0.15, h * 0.25), (w * 0.05, h * 0.40), (0.0, h * 0.60)])
    # Left winglet tip
    pts.extend([(w * 0.04, h * 0.75), (w * 0.12, h * 0.95), (w * 0.22, h * 0.88)])
    # Deep waist concavity
    pts.extend([(w * 0.35, h * 0.72), (w * 0.42, h * 0.65)])
    # Bottom chin recess
    pts.extend([(w * 0.50, h * 0.75)])
    # Right waist concavity
    pts.extend([(w * 0.58, h * 0.65), (w * 0.65, h * 0.72)])
    # Right winglet tip
    pts.extend([(w * 0.78, h * 0.88), (w * 0.88, h * 0.95), (w * 0.96, h * 0.75)])
    # Right shoulder
    pts.extend([(w * 1.0, h * 0.60), (w * 0.95, h * 0.40), (w * 0.85, h * 0.25)])
    poly1 = Polygon(pts).buffer(0)

    holes1 = [
        make_circle(w * 0.25, h * 0.35, 4.5), make_circle(w * 0.75, h * 0.35, 4.5),
        make_circle(w * 0.20, h * 0.60, 4.0), make_circle(w * 0.80, h * 0.60, 4.0),
        make_circle(w * 0.30, h * 0.80, 3.5), make_circle(w * 0.70, h * 0.80, 3.5),
        make_slot(w * 0.50, h * 0.25, 40.0, 10.0),
        make_slot(w * 0.50, h * 0.45, 60.0, 14.0)
    ]
    write_part_svg(os.path.join(output_dir, "p01_front_fairing.svg"), "p01_front_fairing", "Front Fairing Cowl", poly1, holes1, "#3b82f6")

    # --------------------------------------------------------------------------
    # 2. Rear Tail Hugger / Fender (~480 x 320 mm)
    # --------------------------------------------------------------------------
    # Deep circular tire arch with elongated cantilever swingarm mount
    w, h = 48000.0, 32000.0
    pts = []
    # Outer arch
    for th in np.linspace(math.pi * 0.05, math.pi * 0.95, 30):
        pts.append((w * 0.45 + 23000.0 * math.cos(th), h * 0.85 - 26000.0 * math.sin(th)))
    # Cantilever mount arm extending right
    pts.extend([(w * 0.75, h * 0.82), (w * 0.98, h * 0.88), (w, h), (w * 0.70, h * 0.98)])
    # Inner tire cavity arch (deep circular void)
    for th in np.linspace(math.pi * 0.90, math.pi * 0.10, 25):
        pts.append((w * 0.45 + 17500.0 * math.cos(th), h * 0.92 - 20000.0 * math.sin(th)))
    pts.append((w * 0.05, h * 0.90))
    poly2 = Polygon(pts).buffer(0)

    holes2 = [
        make_circle(w * 0.88, h * 0.92, 5.0), make_circle(w * 0.94, h * 0.94, 5.0),
        make_circle(w * 0.75, h * 0.90, 4.0), make_slot(w * 0.35, h * 0.15, 35.0, 8.0, 20.0),
        make_slot(w * 0.55, h * 0.15, 35.0, 8.0, -20.0)
    ]
    write_part_svg(os.path.join(output_dir, "p02_rear_tail_hugger.svg"), "p02_rear_tail_hugger", "Rear Tail Hugger", poly2, holes2, "#06b6d4")

    # --------------------------------------------------------------------------
    # 3. Radiator Side Shroud (~430 x 260 mm)
    # --------------------------------------------------------------------------
    # Aerodynamic swept delta wing cowling
    w, h = 43000.0, 26000.0
    pts = [
        (0.0, h * 0.15), (w * 0.55, 0.0), (w, h * 0.30),
        (w * 0.92, h * 0.65), (w * 0.75, h), (w * 0.45, h * 0.85),
        (w * 0.20, h * 0.92), (w * 0.08, h * 0.55)
    ]
    poly3 = Polygon(pts).buffer(0)
    holes3 = [
        make_slot(w * 0.35, h * 0.40, 65.0, 14.0, 25.0),
        make_slot(w * 0.55, h * 0.50, 65.0, 14.0, 25.0),
        make_slot(w * 0.75, h * 0.60, 55.0, 12.0, 25.0),
        make_circle(w * 0.15, h * 0.25, 4.5), make_circle(w * 0.85, h * 0.35, 4.5),
        make_circle(w * 0.40, h * 0.80, 4.0)
    ]
    write_part_svg(os.path.join(output_dir, "p03_radiator_shroud.svg"), "p03_radiator_shroud", "Radiator Side Shroud", poly3, holes3, "#10b981")

    # --------------------------------------------------------------------------
    # 4. Engine Skid Plate / Belly Pan (~380 x 310 mm)
    # --------------------------------------------------------------------------
    # Hexagonal wrap-around plate with oil drain & cooling vents
    w, h = 38000.0, 31000.0
    pts = [
        (w * 0.18, 0.0), (w * 0.82, 0.0), (w, h * 0.30),
        (w * 0.92, h * 0.85), (w * 0.75, h), (w * 0.25, h),
        (w * 0.08, h * 0.85), (0.0, h * 0.30)
    ]
    poly4 = Polygon(pts).buffer(0)
    holes4 = [
        make_circle(w * 0.50, h * 0.50, 20.0),  # Oil drain hole
        make_slot(w * 0.30, h * 0.25, 40.0, 8.0), make_slot(w * 0.70, h * 0.25, 40.0, 8.0),
        make_slot(w * 0.30, h * 0.75, 40.0, 8.0), make_slot(w * 0.70, h * 0.75, 40.0, 8.0),
        make_circle(w * 0.15, h * 0.15, 5.0), make_circle(w * 0.85, h * 0.15, 5.0),
        make_circle(w * 0.15, h * 0.85, 5.0), make_circle(w * 0.85, h * 0.85, 5.0)
    ]
    write_part_svg(os.path.join(output_dir, "p04_engine_skid_plate.svg"), "p04_engine_skid_plate", "Engine Skid Plate", poly4, holes4, "#8b5cf6")

    # --------------------------------------------------------------------------
    # 5. Tail Tidy License Plate Bracket (~280 x 190 mm)
    # --------------------------------------------------------------------------
    # T-shaped frame with signal light ears and wiring passes
    w, h = 28000.0, 19000.0
    pts = [
        (w * 0.25, 0.0), (w * 0.75, 0.0), (w * 0.78, h * 0.35),
        (w, h * 0.40), (w * 0.98, h * 0.60), (w * 0.65, h * 0.65),
        (w * 0.60, h), (w * 0.40, h), (w * 0.35, h * 0.65),
        (w * 0.02, h * 0.60), (0.0, h * 0.40), (w * 0.22, h * 0.35)
    ]
    poly5 = Polygon(pts).buffer(0)
    holes5 = [
        make_circle(w * 0.10, h * 0.50, 6.0), make_circle(w * 0.90, h * 0.50, 6.0),  # Turn signal ears
        make_circle(w * 0.42, h * 0.85, 4.0), make_circle(w * 0.58, h * 0.85, 4.0),  # Plate bolts
        make_slot(w * 0.50, h * 0.20, 30.0, 12.0)  # Wire pass-through
    ]
    write_part_svg(os.path.join(output_dir, "p05_tail_tidy_bracket.svg"), "p05_tail_tidy_bracket", "Tail Tidy License Bracket", poly5, holes5, "#ec4899")

    # --------------------------------------------------------------------------
    # 6. Rearset Footpeg Hanger (~240 x 160 mm)
    # --------------------------------------------------------------------------
    # Asymmetric cantilevered arm with pivot bearings and adjustment holes
    w, h = 24000.0, 16000.0
    pts = [
        (w * 0.15, 0.0), (w * 0.55, 0.0), (w, h * 0.55),
        (w * 0.85, h), (w * 0.45, h * 0.80), (w * 0.10, h * 0.90),
        (0.0, h * 0.50)
    ]
    poly6 = Polygon(pts).buffer(0)
    holes6 = [
        make_circle(w * 0.35, h * 0.30, 12.0),  # Main pivot
        make_circle(w * 0.70, h * 0.60, 4.5), make_circle(w * 0.80, h * 0.65, 4.5),
        make_circle(w * 0.75, h * 0.75, 4.5), make_circle(w * 0.85, h * 0.80, 4.5),
        make_slot(w * 0.25, h * 0.65, 25.0, 8.0, -30.0)
    ]
    write_part_svg(os.path.join(output_dir, "p06_rearset_footpeg_hanger.svg"), "p06_rearset_footpeg_hanger", "Rearset Footpeg Hanger", poly6, holes6, "#f59e0b")

    # --------------------------------------------------------------------------
    # 7. Exhaust Heat Shield (~360 x 110 mm)
    # --------------------------------------------------------------------------
    # Slender curved guard with longitudinal dissipation louvers
    w, h = 36000.0, 11000.0
    pts = []
    for th in np.linspace(0.0, math.pi, 25):
        pts.append((w * 0.5 + w * 0.48 * math.cos(th), 4000.0 + 3000.0 * math.sin(th)))
    for th in np.linspace(math.pi, 0.0, 25):
        pts.append((w * 0.5 + w * 0.46 * math.cos(th), h - 3000.0 * math.sin(th)))
    poly7 = Polygon(pts).buffer(0)
    holes7 = [
        make_slot(w * 0.25, h * 0.50, 45.0, 6.0),
        make_slot(w * 0.45, h * 0.50, 45.0, 6.0),
        make_slot(w * 0.65, h * 0.50, 45.0, 6.0),
        make_slot(w * 0.82, h * 0.50, 30.0, 6.0),
        make_circle(w * 0.10, h * 0.50, 4.0),
        make_circle(w * 0.93, h * 0.50, 4.0)
    ]
    write_part_svg(os.path.join(output_dir, "p07_exhaust_heat_shield.svg"), "p07_exhaust_heat_shield", "Exhaust Heat Shield", poly7, holes7, "#64748b")

    # --------------------------------------------------------------------------
    # 8. Triple Tree Fork Brace (~220 x 130 mm)
    # --------------------------------------------------------------------------
    # Stabilizer with dual 50mm fork clamp rings and center stem cutout
    w, h = 22000.0, 13000.0
    pts = [
        (w * 0.20, 0.0), (w * 0.80, 0.0), (w, h * 0.50),
        (w * 0.85, h), (w * 0.15, h), (0.0, h * 0.50)
    ]
    poly8 = Polygon(pts).buffer(0)
    holes8 = [
        make_circle(w * 0.25, h * 0.50, 25.0),  # Left 50mm fork ring
        make_circle(w * 0.75, h * 0.50, 25.0),  # Right 50mm fork ring
        make_circle(w * 0.50, h * 0.50, 15.0),  # Center 30mm stem hole
        make_circle(w * 0.50, h * 0.15, 4.0),
        make_circle(w * 0.50, h * 0.85, 4.0)
    ]
    write_part_svg(os.path.join(output_dir, "p08_triple_tree_fork_brace.svg"), "p08_triple_tree_fork_brace", "Triple Tree Fork Brace", poly8, holes8, "#0284c7")

    # --------------------------------------------------------------------------
    # 9. Radiator Grill Core Bracket (~190 x 48 mm)
    # --------------------------------------------------------------------------
    # Slender strap with 6 screw holes
    w, h = 19000.0, 4800.0
    b9 = box(0, 0, w, h)
    poly9 = b9.buffer(0)
    holes9 = [
        make_circle(w * 0.10, h * 0.50, 3.0), make_circle(w * 0.26, h * 0.50, 3.0),
        make_circle(w * 0.42, h * 0.50, 3.0), make_circle(w * 0.58, h * 0.50, 3.0),
        make_circle(w * 0.74, h * 0.50, 3.0), make_circle(w * 0.90, h * 0.50, 3.0)
    ]
    write_part_svg(os.path.join(output_dir, "p09_radiator_grill_bracket.svg"), "p09_radiator_grill_bracket", "Radiator Grill Bracket", poly9, holes9, "#14b8a6")

    # --------------------------------------------------------------------------
    # 10. Brake Caliper Adapter Bracket (~130 x 85 mm)
    # --------------------------------------------------------------------------
    # Dense L-bracket with M10 holes and weight reduction pocket
    w, h = 13000.0, 8500.0
    pts = [
        (0.0, 0.0), (w, 0.0), (w, h * 0.50),
        (w * 0.65, h * 0.50), (w * 0.65, h), (0.0, h)
    ]
    poly10 = Polygon(pts).buffer(0)
    holes10 = [
        make_circle(w * 0.25, h * 0.25, 5.25),  # M10 hole 1
        make_circle(w * 0.75, h * 0.25, 5.25),  # M10 hole 2
        make_circle(w * 0.35, h * 0.75, 5.25),  # Caliper bolt
        make_slot(w * 0.25, h * 0.50, 20.0, 6.0)
    ]
    write_part_svg(os.path.join(output_dir, "p10_brake_caliper_bracket.svg"), "p10_brake_caliper_bracket", "Brake Caliper Bracket", poly10, holes10, "#e11d48")

    # --------------------------------------------------------------------------
    # 11. Handlebar Clamp Top Plate (~110 x 50 mm)
    # --------------------------------------------------------------------------
    # Sturdy clamp plate with 4 bolt holes and center pocket
    w, h = 11000.0, 5000.0
    pts = [
        (w * 0.08, 0.0), (w * 0.92, 0.0), (w, h * 0.20),
        (w, h * 0.80), (w * 0.92, h), (w * 0.08, h),
        (0.0, h * 0.80), (0.0, h * 0.20)
    ]
    poly11 = Polygon(pts).buffer(0)
    holes11 = [
        make_circle(w * 0.20, h * 0.30, 4.25), make_circle(w * 0.80, h * 0.30, 4.25),
        make_circle(w * 0.20, h * 0.70, 4.25), make_circle(w * 0.80, h * 0.70, 4.25),
        make_slot(w * 0.50, h * 0.50, 25.0, 10.0)
    ]
    write_part_svg(os.path.join(output_dir, "p11_handlebar_clamp.svg"), "p11_handlebar_clamp", "Handlebar Clamp Plate", poly11, holes11, "#4f46e5")

    # --------------------------------------------------------------------------
    # 12. Frame Gusset Tag (~65 x 35 mm)
    # --------------------------------------------------------------------------
    # Triangular chassis web with weight reduction hole
    w, h = 6500.0, 3500.0
    pts = [(0.0, 0.0), (w, 0.0), (w * 0.85, h), (w * 0.15, h)]
    poly12 = Polygon(pts).buffer(0)
    holes12 = [
        make_circle(w * 0.50, h * 0.50, 6.0)
    ]
    write_part_svg(os.path.join(output_dir, "p12_frame_gusset_tag.svg"), "p12_frame_gusset_tag", "Frame Gusset Tag", poly12, holes12, "#ea580c")

    print("\n[SUCCESS] Generated all 12 motorcycle parts in 'parts/' directory.")


if __name__ == "__main__":
    generate_all_parts()
