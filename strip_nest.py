#!/usr/bin/env python3
"""
===============================================================================
       EASYNEST V3 - 2D IRREGULAR STRIP PACKING ENGINE (SPARROW SOTA)
===============================================================================
Specialized nesting engine designed for open-ended / continuous strip packing:
- Fixed Strip / Coil / Plate Width W (e.g. 1220 mm, 1000 mm, etc.)
- Open-Ended Unbounded Length L -> strictly minimized: min(L_used)
- Powered by Sparrow / jagua-rs (Rust SOTA collision & rubber-band compaction)
- Outputs exact Guillotine Cutoff Line, material consumption, and density metrics
===============================================================================
"""

import os
import sys
import time
import math
import argparse
from typing import List, Tuple, Dict, Any, Optional
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Polygon, MultiPolygon, Point
from shapely import affinity

# EasyNest CAD & Parsing
from cdr_enhancer import (
    extract_paths_from_svg,
    build_isolated_objects,
    IsolatedObject,
    PathCommand,
    CurvePath
)

try:
    import spyrrow
    SPARROW_AVAILABLE = True
except ImportError:
    SPARROW_AVAILABLE = False


@dataclass
class StripPlacedPart:
    """Represents a part placed on the continuous strip."""
    part_id: str
    part_index: int
    x_mm: float
    y_mm: float
    angle_deg: float
    width_mm: float
    height_mm: float
    area_cm2: float
    transformed_poly: Polygon
    raw_obj: IsolatedObject
    minx_raw: float = 0.0
    miny_raw: float = 0.0
    tx_mm: float = 0.0
    ty_mm: float = 0.0


@dataclass
class StripPackingResult:
    """Encapsulates the complete result of a strip packing optimization."""
    job_name: str
    strip_width_mm: float
    min_strip_length_mm: float
    margin_mm: float
    kerf_mm: float
    parts_placed: List[StripPlacedPart]
    total_parts_placed: int
    total_parts_requested: int
    density_pct: float
    net_parts_area_m2: float
    total_material_area_m2: float
    scrap_area_m2: float
    computation_time_sec: float
    guillotine_shear_x_mm: float
    output_svg_path: Optional[str] = None


class StripNestingEngine:
    """
    State-of-the-Art 2D Irregular Strip Packing Engine.
    Uses Sparrow (jagua-rs) for physics-based continuous strip compaction.
    """

    def __init__(
        self,
        strip_width_mm: float = 1220.0,
        margin_mm: float = 10.0,
        kerf_mm: float = 2.0,
        time_budget_sec: int = 15,
        allowed_angles: Optional[List[float]] = None
    ):
        self.strip_width_mm = float(strip_width_mm)
        self.margin_mm = float(margin_mm)
        self.kerf_mm = float(kerf_mm)
        self.time_budget_sec = max(2, int(time_budget_sec))
        self.allowed_angles = allowed_angles or [0.0, 90.0, 180.0, 270.0]

    def optimize_strip(
        self,
        order: Dict[str, int],
        named_parts_dict: Dict[str, IsolatedObject],
        job_name: str = "strip_nesting",
        output_svg_path: Optional[str] = None
    ) -> StripPackingResult:
        """
        Packs the ordered parts into the tightest possible strip of fixed width self.strip_width_mm.
        """
        if not SPARROW_AVAILABLE:
            raise RuntimeError(
                "Spyrrow (Sparrow nesting engine) is not installed. "
                "Install with `pip install spyrrow` to use Strip Mode."
            )

        t_start = time.time()
        effective_strip_height = self.strip_width_mm - 2.0 * self.margin_mm
        if effective_strip_height <= 10.0:
            raise ValueError(f"Strip width {self.strip_width_mm}mm too small for margin {self.margin_mm}mm")

        sparrow_items: List[spyrrow.Item] = []
        part_lookup: Dict[str, Tuple[int, IsolatedObject, Polygon, float, float]] = {}

        total_parts_requested = 0
        net_parts_area_cm2 = 0.0

        for idx, (pname, qty) in enumerate(order.items()):
            if qty <= 0 or pname not in named_parts_dict:
                continue

            obj = named_parts_dict[pname]
            total_parts_requested += qty

            # In EasyNest, internal coordinates are scaled: 100 units = 1 mm
            raw_poly = affinity.scale(obj.outer_path.polygon, xfact=0.01, yfact=0.01, origin=(0, 0))
            minx, miny, maxx, maxy = raw_poly.bounds
            norm_poly = affinity.translate(raw_poly, -minx, -miny)
            pw_mm = maxx - minx
            ph_mm = maxy - miny
            p_area_cm2 = norm_poly.area / 100.0
            net_parts_area_cm2 += p_area_cm2 * qty

            part_lookup[pname] = (idx, obj, norm_poly, pw_mm, ph_mm, minx, miny)

            # Simplify slightly for robust and fast boundary evaluation in Sparrow
            simp_poly = norm_poly.simplify(0.4, preserve_topology=True)
            boundary_coords = list(simp_poly.exterior.coords)

            sparrow_items.append(
                spyrrow.Item(
                    id=pname,
                    shape=boundary_coords,
                    demand=qty,
                    allowed_orientations=self.allowed_angles
                )
            )

        if not sparrow_items:
            raise ValueError("No valid parts with quantity > 0 in order.")

        print("=" * 80)
        print(f"[*] EASYNEST STRIP PACKING (SPARROW SOTA)")
        print(f"    Strip Width (Fixed) : {self.strip_width_mm:.1f} mm (Usable: {effective_strip_height:.1f} mm)")
        print(f"    Border Margin       : {self.margin_mm:.1f} mm | Kerf Spacing: {self.kerf_mm:.1f} mm")
        print(f"    Total Parts to Pack : {total_parts_requested} units across {len(sparrow_items)} part types")
        print(f"    Optimization Budget : {self.time_budget_sec} seconds (Exploration + Compaction)")
        print("=" * 80)

        # Create Sparrow Instance & Config
        instance = spyrrow.StripPackingInstance(
            name=job_name,
            strip_height=effective_strip_height,
            items=sparrow_items
        )

        config = spyrrow.StripPackingConfig(
            total_computation_time=self.time_budget_sec,
            min_items_separation=self.kerf_mm,
            quadtree_depth=4
        )

        # Solve via Rust jagua-rs
        print("[*] Sparrow solving continuous strip compression...")
        solution = instance.solve(config)
        comp_time = time.time() - t_start

        min_strip_len = solution.width + 2.0 * self.margin_mm
        total_mat_m2 = (self.strip_width_mm * min_strip_len) / 1_000_000.0
        net_m2 = net_parts_area_cm2 / 10_000.0
        density_pct = (net_m2 / total_mat_m2) * 100.0 if total_mat_m2 > 0 else 0.0
        scrap_m2 = max(0.0, total_mat_m2 - net_m2)

        # Reconstruct placed parts
        placed_parts: List[StripPlacedPart] = []
        for p in solution.placed_items:
            pname = p.id
            if pname not in part_lookup:
                continue

            idx, obj, norm_poly, pw, ph, minx_raw, miny_raw = part_lookup[pname]
            rot_deg = float(p.rotation)
            tx, ty = p.translation

            # In Sparrow, rotation is applied around origin (0, 0), then translated by (tx, ty)
            # Add sheet margin offset
            rot_poly = affinity.rotate(norm_poly, rot_deg, origin=(0, 0))
            placed_poly = affinity.translate(
                rot_poly,
                tx + self.margin_mm,
                ty + self.margin_mm
            )

            p_minx, p_miny, p_maxx, p_maxy = placed_poly.bounds

            placed_parts.append(
                StripPlacedPart(
                    part_id=pname,
                    part_index=idx,
                    x_mm=p_minx,
                    y_mm=p_miny,
                    angle_deg=rot_deg,
                    width_mm=p_maxx - p_minx,
                    height_mm=p_maxy - p_miny,
                    area_cm2=placed_poly.area / 100.0,
                    transformed_poly=placed_poly,
                    raw_obj=obj,
                    minx_raw=minx_raw,
                    miny_raw=miny_raw,
                    tx_mm=tx,
                    ty_mm=ty
                )
            )

        print("\n" + "=" * 80)
        print("       STRIP PACKING PRODUCTION SCOREBOARD (SPARROW SOTA)")
        print("=" * 80)
        print(f"  Fixed Strip Width   : {self.strip_width_mm:.1f} mm")
        print(f"  MINIMUM STRIP LENGTH: {min_strip_len:.1f} mm ({min_strip_len/1000.0:.3f} meters)")
        print(f"  Guillotine Cut Line : X = {min_strip_len:.1f} mm")
        print(f"  Total Parts Placed  : {len(placed_parts)} / {total_parts_requested} units")
        print(f"  Strip Packing Density: {density_pct:.2f}% (Sparrow reported: {solution.density*100:.2f}%)")
        print(f"  Total Material Area : {total_mat_m2:.3f} m²")
        print(f"  Net Finished Parts  : {net_m2:.3f} m²")
        print(f"  Scrap Area          : {scrap_m2:.3f} m²")
        print(f"  Optimization Runtime: {comp_time:.2f}s")
        print("=" * 80)

        result = StripPackingResult(
            job_name=job_name,
            strip_width_mm=self.strip_width_mm,
            min_strip_length_mm=min_strip_len,
            margin_mm=self.margin_mm,
            kerf_mm=self.kerf_mm,
            parts_placed=placed_parts,
            total_parts_placed=len(placed_parts),
            total_parts_requested=total_parts_requested,
            density_pct=density_pct,
            net_parts_area_m2=net_m2,
            total_material_area_m2=total_mat_m2,
            scrap_area_m2=scrap_m2,
            computation_time_sec=comp_time,
            guillotine_shear_x_mm=min_strip_len,
            output_svg_path=output_svg_path
        )

        if output_svg_path:
            self.generate_strip_svg(result, output_svg_path)
            result.output_svg_path = output_svg_path

        return result

    def generate_strip_svg(
        self,
        res: StripPackingResult,
        output_svg_path: str
    ) -> str:
        """
        Generates an industrial SVG layout of the continuous strip:
        - Continuous strip envelope [0, min_strip_length_mm] x [0, strip_width_mm]
        - Clearly demarcated Guillotine Cutoff Line
        - High-contrast nested parts with clean internal hole cutouts
        - Industrial scoreboard banner
        """
        scale = 100.0  # 100 SVG units = 1 mm
        strip_len_mm = res.min_strip_length_mm
        strip_w_mm = res.strip_width_mm
        margin_mm = res.margin_mm

        svg_w = strip_len_mm * scale
        svg_h = strip_w_mm * scale
        margin_svg = margin_mm * scale
        usable_w_svg = (strip_len_mm - 2 * margin_mm) * scale
        usable_h_svg = (strip_w_mm - 2 * margin_mm) * scale

        # Palette
        palettes = [
            ("#3b82f6", "#1d4ed8", 0.45),  # Blue
            ("#10b981", "#047857", 0.45),  # Emerald
            ("#f59e0b", "#b45309", 0.45),  # Amber
            ("#8b5cf6", "#6d28d9", 0.45),  # Purple
            ("#ec4899", "#be185d", 0.45),  # Pink
            ("#06b6d4", "#0e7490", 0.45),  # Cyan
            ("#f97316", "#c2410c", 0.45),  # Orange
            ("#14b8a6", "#0f766e", 0.45),  # Teal
            ("#6366f1", "#4338ca", 0.45),  # Indigo
            ("#eab308", "#a16207", 0.45),  # Yellow
            ("#84cc16", "#4d7c0f", 0.45),  # Lime
            ("#a855f7", "#7e22ce", 0.45),  # Violet
        ]

        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg version="1.1" viewBox="0 0 {svg_w:.1f} {svg_h:.1f}" '
            f'width="{strip_len_mm:.1f}mm" height="{strip_w_mm:.1f}mm" '
            'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">',
            '  <defs>',
            '    <style>',
            '      .strip-plate { fill: #0f172a; stroke: #334155; stroke-width: 40; }',
            '      .usable-margin { fill: none; stroke: #38bdf8; stroke-dasharray: 160,160; stroke-width: 25; }',
            '      .shear-line { stroke: #ef4444; stroke-dasharray: 250,150; stroke-width: 80; }',
            '      .shear-badge { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; font-size: 320px; font-weight: bold; fill: #ef4444; }',
            '      .part-path { vector-effect: non-scaling-stroke; stroke-linejoin: round; stroke-linecap: round; }',
            '      .part-label { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; font-size: 150px; font-weight: 800; fill: #ffffff; pointer-events: none; }',
            '    </style>',
            '  </defs>',
            f'  <!-- Continuous Strip Stock ({strip_len_mm:.1f} x {strip_w_mm:.1f} mm) -->',
            f'  <rect class="strip-plate" x="0" y="0" width="{svg_w:.1f}" height="{svg_h:.1f}" />',
            f'  <rect class="usable-margin" x="{margin_svg:.1f}" y="{margin_svg:.1f}" width="{usable_w_svg:.1f}" height="{usable_h_svg:.1f}" />',
            '  <!-- Placed Parts Layer -->'
        ]

        # Draw each placed part with clean polygon and interior holes
        for idx, part in enumerate(res.parts_placed, start=1):
            fill_c, stroke_c, opacity = palettes[part.part_index % len(palettes)]

            rad = math.radians(part.angle_deg)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)

            def xform_coord(p: np.ndarray) -> np.ndarray:
                x_mm = (p[0] / 100.0) - part.minx_raw
                y_mm = (p[1] / 100.0) - part.miny_raw
                rx = x_mm * cos_a - y_mm * sin_a + part.tx_mm + margin_mm
                ry = x_mm * sin_a + y_mm * cos_a + part.ty_mm + margin_mm
                return np.array([rx * scale, ry * scale])

            def transform_curve(cp: CurvePath) -> CurvePath:
                new_cmds = []
                for c in cp.commands:
                    if c.cmd == 'Z':
                        new_cmds.append(PathCommand('Z', []))
                    else:
                        new_pts = [xform_coord(pt) for pt in c.points]
                        new_cmds.append(PathCommand(c.cmd, new_pts))
                return CurvePath.from_commands(new_cmds)

            t_outer = transform_curve(part.raw_obj.outer_path)
            t_holes = [transform_curve(h) for h in part.raw_obj.holes]
            compound_d = t_outer.to_svg_d()
            if t_holes:
                compound_d += ' ' + ' '.join(h.to_svg_d() for h in t_holes)

            lines.append(f'  <g id="part_{idx}_{part.part_id}" class="nested-part" data-part="{part.part_id}" data-part-name="{part.part_id}">')
            lines.append(
                f'    <path class="part-path" d="{compound_d}" '
                f'fill="{fill_c}" fill-opacity="{opacity}" stroke="{stroke_c}" stroke-width="35" fill-rule="evenodd" />'
            )

            # Center label
            cx, cy = part.transformed_poly.centroid.x * scale, part.transformed_poly.centroid.y * scale
            short_name = part.part_id.split('_')[0]
            lines.append(
                f'    <text class="part-label" x="{cx:.1f}" y="{cy:.1f}" text-anchor="middle" dominant-baseline="central">'
                f'#{idx} {short_name}</text>'
            )
            lines.append('  </g>')

        # Draw Guillotine Shear Line at the end of the strip
        cut_x_svg = (strip_len_mm - margin_mm / 2.0) * scale
        lines.append('  <!-- Guillotine Cutoff Line -->')
        lines.append(f'  <line class="shear-line" x1="{cut_x_svg:.1f}" y1="0" x2="{cut_x_svg:.1f}" y2="{svg_h:.1f}" />')
        lines.append(
            f'  <text class="shear-badge" x="{cut_x_svg - 250:.1f}" y="{svg_h / 2.0:.1f}" '
            f'text-anchor="middle" transform="rotate(-90, {cut_x_svg - 250:.1f}, {svg_h / 2.0:.1f})">'
            f'✂ GUILLOTINE SHEAR LINE: L = {strip_len_mm:.1f} mm | DENSITY: {res.density_pct:.1f}%</text>'
        )

        lines.append('</svg>')

        os.makedirs(os.path.dirname(output_svg_path) or ".", exist_ok=True)
        with open(output_svg_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        return output_svg_path

    def _poly_to_svg_path_d(self, poly: Polygon, scale: float) -> str:
        """Converts a Shapely Polygon (with exterior and holes) into SVG path d string."""
        cmds = []

        # Exterior
        ext_coords = list(poly.exterior.coords)
        if ext_coords:
            cmds.append(f"M {ext_coords[0][0]*scale:.1f} {ext_coords[0][1]*scale:.1f}")
            for pt in ext_coords[1:]:
                cmds.append(f"L {pt[0]*scale:.1f} {pt[1]*scale:.1f}")
            cmds.append("Z")

        # Interiors (Holes)
        for interior in poly.interiors:
            hole_coords = list(interior.coords)
            if hole_coords:
                cmds.append(f"M {hole_coords[0][0]*scale:.1f} {hole_coords[0][1]*scale:.1f}")
                for pt in hole_coords[1:]:
                    cmds.append(f"L {pt[0]*scale:.1f} {pt[1]*scale:.1f}")
                cmds.append("Z")

        return " ".join(cmds)


def run_strip_benchmark(
    condition_id: str = "4",
    strip_width_mm: float = 1220.0,
    time_budget_sec: int = 15
):
    """Runs strip packing benchmark using standard test conditions."""
    parts_dir = "parts"
    part_files = sorted([f for f in os.listdir(parts_dir) if f.startswith("p") and f.endswith(".svg")])
    named_parts_dict: Dict[str, IsolatedObject] = {}

    for pf in part_files:
        full_p = os.path.join(parts_dir, pf)
        pname = os.path.splitext(pf)[0]
        paths = extract_paths_from_svg(full_p)
        objs = build_isolated_objects(paths)
        if objs:
            named_parts_dict[pname] = objs[0]

    # Pre-set orders from standard conditions
    if condition_id == "4":
        order = {
            "p01_front_fairing": 25,
            "p04_engine_skid_plate": 25,
            "p05_tail_tidy_bracket": 25,
            "p09_radiator_grill_bracket": 50,
            "p12_frame_gusset_tag": 100
        }
        job_name = "cond4_assembly_bom_strip"
    elif condition_id == "5":
        order = {
            "p04_engine_skid_plate": 15,
            "p08_triple_tree_fork_brace": 20
        }
        job_name = "cond5_rush_kanban_strip"
    else:
        order = {
            "p01_front_fairing": 5,
            "p04_engine_skid_plate": 10,
            "p05_tail_tidy_bracket": 15,
            "p12_frame_gusset_tag": 30
        }
        job_name = f"cond{condition_id}_custom_strip"

    engine = StripNestingEngine(
        strip_width_mm=strip_width_mm,
        margin_mm=10.0,
        kerf_mm=2.0,
        time_budget_sec=time_budget_sec
    )

    out_svg = f"output/{job_name}.svg"
    return engine.optimize_strip(order, named_parts_dict, job_name=job_name, output_svg_path=out_svg)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EasyNest v3 Strip Packing Optimizer (Sparrow SOTA)")
    parser.add_argument("--width", type=float, default=1220.0, help="Continuous Strip/Coil Width (mm)")
    parser.add_argument("--time", type=int, default=15, help="Sparrow optimization budget (seconds)")
    parser.add_argument("--condition", choices=["1", "2", "3", "4", "5"], default="4",
                        help="Pre-configured test condition (default: 4 = 25-bike assembly BOM)")
    args = parser.parse_args()

    run_strip_benchmark(
        condition_id=args.condition,
        strip_width_mm=args.width,
        time_budget_sec=args.time
    )
