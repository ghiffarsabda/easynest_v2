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
from shapely.strtree import STRtree

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

    def _left_gravity_compaction(
        self,
        placed_parts: List[StripPlacedPart],
        margin_mm: float,
        kerf_mm: float
    ) -> List[StripPlacedPart]:
        """
        Post-Solve Magnet Compaction:
        Scans all placed parts from left to right along the strip.
        Slides each part leftward along X using STRtree collision checking,
        eliminating trailing slack, bubbles, and gaps between parts.
        """
        if not placed_parts:
            return placed_parts

        sorted_parts = sorted(placed_parts, key=lambda p: p.transformed_poly.bounds[0])
        compacted: List[StripPlacedPart] = []
        compacted_polys = []
        half_kerf = kerf_mm / 2.0

        for part in sorted_parts:
            curr_poly = part.transformed_poly
            curr_minx = curr_poly.bounds[0]

            if not compacted:
                shift_x = margin_mm - curr_minx
                if abs(shift_x) > 0.01:
                    new_poly = affinity.translate(curr_poly, shift_x, 0)
                    part.tx_mm += shift_x
                    part.transformed_poly = new_poly
                    part.x_mm = new_poly.bounds[0]
                compacted.append(part)
                compacted_polys.append(part.transformed_poly.buffer(half_kerf))
                continue

            tree = STRtree(compacted_polys)

            best_shift = 0.0
            step = 5.0
            test_shift = -step
            while curr_minx + test_shift >= margin_mm:
                cand = affinity.translate(curr_poly, test_shift, 0)
                cand_buf = cand.buffer(half_kerf)
                if len(tree.query(cand_buf, predicate='intersects')) > 0:
                    break
                best_shift = test_shift
                test_shift -= step

            fine_step = 1.0
            test_shift = best_shift - fine_step
            while curr_minx + test_shift >= margin_mm:
                cand = affinity.translate(curr_poly, test_shift, 0)
                cand_buf = cand.buffer(half_kerf)
                if len(tree.query(cand_buf, predicate='intersects')) > 0:
                    break
                best_shift = test_shift
                test_shift -= fine_step

            micro_step = 0.2
            test_shift = best_shift - micro_step
            while curr_minx + test_shift >= margin_mm:
                cand = affinity.translate(curr_poly, test_shift, 0)
                cand_buf = cand.buffer(half_kerf)
                if len(tree.query(cand_buf, predicate='intersects')) > 0:
                    break
                best_shift = test_shift
                test_shift -= micro_step

            if abs(best_shift) > 0.01:
                new_poly = affinity.translate(curr_poly, best_shift, 0)
                part.tx_mm += best_shift
                part.transformed_poly = new_poly
                part.x_mm = new_poly.bounds[0]

            compacted.append(part)
            compacted_polys.append(part.transformed_poly.buffer(half_kerf))

        return compacted

    def _solve_single_part_lattice(
        self,
        pname: str,
        qty: int,
        obj: IsolatedObject,
        job_name: str
    ) -> Optional[StripPackingResult]:
        """
        High-Density Staggered Interlocking Lattice Generator for Single-Part Orders.
        Evaluates alternating angle columns (0°, 180°, 90°, 270°) with optimal contact dx
        and vertical pitch dy. Delivers tight, zero-waste honeycomb/tessellation layouts.
        """
        t_start = time.time()
        usable_h = self.strip_width_mm - 2.0 * self.margin_mm

        raw_poly = affinity.scale(obj.outer_path.polygon, xfact=0.01, yfact=0.01, origin=(0, 0))
        minx_raw, miny_raw, maxx_raw, maxy_raw = raw_poly.bounds
        norm_p = affinity.translate(raw_poly, -minx_raw, -miny_raw)
        p_area_cm2 = norm_p.area / 100.0

        combos = [
            (0.0, 0.0), (0.0, 180.0), (180.0, 0.0), (180.0, 180.0),
            (90.0, 90.0), (90.0, 270.0), (270.0, 90.0), (270.0, 270.0)
        ]

        best_cand = None
        best_strip_len = float('inf')

        for a1, a2 in combos:
            p1_rot = affinity.rotate(norm_p, a1, origin=(0, 0))
            b1 = p1_rot.bounds
            p1 = affinity.translate(p1_rot, -b1[0], -b1[1])
            w1, h1 = b1[2] - b1[0], b1[3] - b1[1]

            p2_rot = affinity.rotate(norm_p, a2, origin=(0, 0))
            b2 = p2_rot.bounds
            p2 = affinity.translate(p2_rot, -b2[0], -b2[1])
            w2, h2 = b2[2] - b2[0], b2[3] - b2[1]

            if h1 > usable_h or h2 > usable_h:
                continue

            dy1 = h1 + self.kerf_mm
            for dy_test in np.arange(h1 + self.kerf_mm, h1 * 0.4, -0.5):
                cand = affinity.translate(p1, 0, dy_test)
                if p1.distance(cand) < self.kerf_mm:
                    break
                dy1 = dy_test

            dy2 = h2 + self.kerf_mm
            for dy_test in np.arange(h2 + self.kerf_mm, h2 * 0.4, -0.5):
                cand = affinity.translate(p2, 0, dy_test)
                if p2.distance(cand) < self.kerf_mm:
                    break
                dy2 = dy_test

            n_rows1 = int((usable_h - h1) / dy1) + 1
            n_rows2 = int((usable_h - h2) / dy2) + 1
            if n_rows1 <= 0 or n_rows2 <= 0:
                continue

            col1_polys = [affinity.translate(p1, 0, i * dy1) for i in range(n_rows1)]
            max_w1 = max(p.bounds[2] for p in col1_polys)

            for dy_offset in np.linspace(-dy1 * 0.6, dy1 * 0.6, 25):
                col2_polys = [affinity.translate(p2, 0, dy_offset + j * dy2) for j in range(n_rows2)
                              if 0 <= dy_offset + j * dy2 and dy_offset + j * dy2 + h2 <= usable_h]
                if not col2_polys:
                    continue

                test_dx = max_w1 + self.kerf_mm
                step = 1.0
                while test_dx > max_w1 * 0.2:
                    coll = False
                    for pb in col2_polys:
                        test_pb = affinity.translate(pb, test_dx, 0)
                        for pa in col1_polys:
                            if pa.distance(test_pb) < self.kerf_mm:
                                coll = True
                                break
                        if coll:
                            break
                    if coll:
                        test_dx += step
                        break
                    test_dx -= step
                dx1_2 = test_dx

                col2_shifted = [affinity.translate(pb, dx1_2, 0) for pb in col2_polys]
                test_dx2 = (dx1_2 + w2) + self.kerf_mm
                while test_dx2 > dx1_2 + w2 * 0.2:
                    coll = False
                    for pa in col1_polys:
                        test_pa = affinity.translate(pa, test_dx2, 0)
                        for pb in col2_shifted:
                            if pb.distance(test_pa) < self.kerf_mm:
                                coll = True
                                break
                        if coll:
                            break
                    if coll:
                        test_dx2 += step
                        break
                    test_dx2 -= step
                dx2_1 = test_dx2 - dx1_2

                placed_info = []
                curr_x = 0.0
                is_col1 = True
                count = 0
                while count < qty:
                    if is_col1:
                        for i in range(min(n_rows1, qty - count)):
                            py = i * dy1
                            placed_info.append((a1, curr_x - b1[0], py - b1[1], affinity.translate(p1, curr_x, py)))
                            count += 1
                        curr_x += dx1_2
                        is_col1 = False
                    else:
                        for j in range(min(len(col2_polys), qty - count)):
                            py = col2_polys[j].bounds[1]
                            placed_info.append((a2, curr_x - b2[0], py - b2[1], affinity.translate(p2, curr_x, py)))
                            count += 1
                        curr_x += dx2_1
                        is_col1 = True

                final_maxx = max(p[3].bounds[2] for p in placed_info)
                tot_len = final_maxx + 2.0 * self.margin_mm
                if tot_len < best_strip_len:
                    best_strip_len = tot_len
                    best_cand = (a1, a2, tot_len, placed_info)

        if not best_cand:
            return None

        placed_parts: List[StripPlacedPart] = []
        for idx, (ang, tx, ty, poly) in enumerate(best_cand[3], start=1):
            placed_poly = affinity.translate(poly, self.margin_mm, self.margin_mm)
            b = placed_poly.bounds
            placed_parts.append(
                StripPlacedPart(
                    part_id=pname,
                    part_index=0,
                    x_mm=b[0],
                    y_mm=b[1],
                    angle_deg=ang,
                    width_mm=b[2] - b[0],
                    height_mm=b[3] - b[1],
                    area_cm2=p_area_cm2,
                    transformed_poly=placed_poly,
                    raw_obj=obj,
                    minx_raw=minx_raw,
                    miny_raw=miny_raw,
                    tx_mm=tx,
                    ty_mm=ty
                )
            )

        placed_parts = self._left_gravity_compaction(placed_parts, self.margin_mm, self.kerf_mm)
        min_strip_len = max(p.transformed_poly.bounds[2] for p in placed_parts) + self.margin_mm

        comp_time = time.time() - t_start
        total_mat_m2 = (self.strip_width_mm * min_strip_len) / 1_000_000.0
        net_m2 = (p_area_cm2 * qty) / 10_000.0
        density_pct = (net_m2 / total_mat_m2) * 100.0 if total_mat_m2 > 0 else 0.0
        scrap_m2 = max(0.0, total_mat_m2 - net_m2)

        return StripPackingResult(
            job_name=job_name,
            strip_width_mm=self.strip_width_mm,
            min_strip_length_mm=min_strip_len,
            margin_mm=self.margin_mm,
            kerf_mm=self.kerf_mm,
            parts_placed=placed_parts,
            total_parts_placed=len(placed_parts),
            total_parts_requested=qty,
            density_pct=density_pct,
            net_parts_area_m2=net_m2,
            total_material_area_m2=total_mat_m2,
            scrap_area_m2=scrap_m2,
            computation_time_sec=comp_time,
            guillotine_shear_x_mm=min_strip_len,
            output_svg_path=None
        )

    def _solve_sparrow(
        self,
        order: Dict[str, int],
        named_parts_dict: Dict[str, IsolatedObject],
        job_name: str
    ) -> StripPackingResult:
        """
        Deep-Compaction Sparrow SOTA Solver with 24-Angle Freedom and Left-Gravity Compaction.
        """
        t_start = time.time()
        effective_strip_height = self.strip_width_mm - 2.0 * self.margin_mm

        sparrow_items: List[spyrrow.Item] = []
        part_lookup: Dict[str, Tuple[int, IsolatedObject, Polygon, float, float]] = {}
        total_parts_requested = 0
        net_parts_area_cm2 = 0.0

        # Enable 24-angle rotational freedom (15 deg step) if standard angles passed
        angles_to_use = self.allowed_angles
        if len(angles_to_use) <= 4:
            angles_to_use = [float(i * 15.0) for i in range(24)]

        for idx, (pname, qty) in enumerate(order.items()):
            if qty <= 0 or pname not in named_parts_dict:
                continue

            obj = named_parts_dict[pname]
            total_parts_requested += qty

            raw_poly = affinity.scale(obj.outer_path.polygon, xfact=0.01, yfact=0.01, origin=(0, 0))
            minx, miny, maxx, maxy = raw_poly.bounds
            norm_poly = affinity.translate(raw_poly, -minx, -miny)
            pw_mm = maxx - minx
            ph_mm = maxy - miny
            p_area_cm2 = norm_poly.area / 100.0
            net_parts_area_cm2 += p_area_cm2 * qty

            part_lookup[pname] = (idx, obj, norm_poly, pw_mm, ph_mm, minx, miny)

            simp_poly = norm_poly.simplify(0.4, preserve_topology=True)
            boundary_coords = list(simp_poly.exterior.coords)

            sparrow_items.append(
                spyrrow.Item(
                    id=pname,
                    shape=boundary_coords,
                    demand=qty,
                    allowed_orientations=angles_to_use
                )
            )

        instance = spyrrow.StripPackingInstance(
            name=job_name,
            strip_height=effective_strip_height,
            items=sparrow_items
        )

        exp_time = max(1, int(self.time_budget_sec * 0.40))
        cmp_time = max(2, int(self.time_budget_sec * 0.60))

        config = spyrrow.StripPackingConfig(
            total_computation_time=None,
            exploration_time=exp_time,
            compression_time=cmp_time,
            min_items_separation=self.kerf_mm,
            quadtree_depth=5,
            early_termination=False
        )

        solution = instance.solve(config)

        placed_parts: List[StripPlacedPart] = []
        for p in solution.placed_items:
            pname = p.id
            if pname not in part_lookup:
                continue

            idx, obj, norm_poly, pw, ph, minx_raw, miny_raw = part_lookup[pname]
            rot_deg = float(p.rotation)
            tx, ty = p.translation

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

        # Apply Left-Gravity Compaction
        placed_parts = self._left_gravity_compaction(placed_parts, self.margin_mm, self.kerf_mm)
        min_strip_len = max(p.transformed_poly.bounds[2] for p in placed_parts) + self.margin_mm

        comp_time = time.time() - t_start
        total_mat_m2 = (self.strip_width_mm * min_strip_len) / 1_000_000.0
        net_m2 = net_parts_area_cm2 / 10_000.0
        density_pct = (net_m2 / total_mat_m2) * 100.0 if total_mat_m2 > 0 else 0.0
        scrap_m2 = max(0.0, total_mat_m2 - net_m2)

        return StripPackingResult(
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
            output_svg_path=None
        )

    def optimize_strip(
        self,
        order: Dict[str, int],
        named_parts_dict: Dict[str, IsolatedObject],
        job_name: str = "strip_nesting",
        output_svg_path: Optional[str] = None
    ) -> StripPackingResult:
        """
        Packs the ordered parts into the tightest possible strip of fixed width self.strip_width_mm.
        Uses Tournament Selection between Staggered Interlocking Lattice and Enhanced Sparrow SOTA.
        """
        if not SPARROW_AVAILABLE:
            raise RuntimeError(
                "Spyrrow (Sparrow nesting engine) is not installed. "
                "Install with `pip install spyrrow` to use Strip Mode."
            )

        clean_order = {k: v for k, v in order.items() if v > 0 and k in named_parts_dict}
        if not clean_order:
            raise ValueError("No valid parts with quantity > 0 in order.")

        total_parts_requested = sum(clean_order.values())
        print("=" * 80)
        print(f"[*] EASYNEST STRIP PACKING (DUAL-ENGINE SOTA)")
        print(f"    Strip Width (Fixed) : {self.strip_width_mm:.1f} mm")
        print(f"    Border Margin       : {self.margin_mm:.1f} mm | Kerf Spacing: {self.kerf_mm:.1f} mm")
        print(f"    Total Parts to Pack : {total_parts_requested} units across {len(clean_order)} part types")
        print(f"    Optimization Budget : {self.time_budget_sec} seconds")
        print("=" * 80)

        # Single Part Order: Run Lattice Generator & Sparrow in Tournament
        lattice_result = None
        if len(clean_order) == 1:
            pname, qty = list(clean_order.items())[0]
            try:
                print("[*] Generating Staggered Interlocking Lattice candidate...")
                lattice_result = self._solve_single_part_lattice(pname, qty, named_parts_dict[pname], job_name)
                if lattice_result:
                    print(f"    -> Lattice candidate: Length = {lattice_result.min_strip_length_mm:.1f} mm | Density = {lattice_result.density_pct:.1f}%")
            except Exception as e:
                print(f"[!] Lattice generator warning: {e}")

        # Run Sparrow Deep Compaction
        print("[*] Running Deep Compaction Physics Solver (Sparrow SOTA)...")
        sparrow_result = self._solve_sparrow(clean_order, named_parts_dict, job_name)
        print(f"    -> Sparrow candidate: Length = {sparrow_result.min_strip_length_mm:.1f} mm | Density = {sparrow_result.density_pct:.1f}%")

        # Pick Tournament Winner
        if lattice_result and lattice_result.min_strip_length_mm < sparrow_result.min_strip_length_mm:
            winner = lattice_result
            engine_name = "Staggered Interlocking Lattice (Champion)"
        else:
            winner = sparrow_result
            engine_name = "Deep Sparrow SOTA (Champion)"

        print("\n" + "=" * 80)
        print(f"       STRIP PACKING PRODUCTION SCOREBOARD [{engine_name}]")
        print("=" * 80)
        print(f"  Fixed Strip Width   : {self.strip_width_mm:.1f} mm")
        print(f"  MINIMUM STRIP LENGTH: {winner.min_strip_length_mm:.1f} mm ({winner.min_strip_length_mm/1000.0:.3f} meters)")
        print(f"  Guillotine Cut Line : X = {winner.min_strip_length_mm:.1f} mm")
        print(f"  Total Parts Placed  : {winner.total_parts_placed} / {total_parts_requested} units")
        print(f"  Strip Packing Density: {winner.density_pct:.2f}%")
        print(f"  Total Material Area : {winner.total_material_area_m2:.3f} m²")
        print(f"  Net Finished Parts  : {winner.net_parts_area_m2:.3f} m²")
        print(f"  Scrap Area          : {winner.scrap_area_m2:.3f} m²")
        print(f"  Optimization Runtime: {winner.computation_time_sec:.2f}s")
        print("=" * 80)

        if output_svg_path:
            self.generate_strip_svg(winner, output_svg_path)
            winner.output_svg_path = output_svg_path

        return winner

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
            '      .strip-plate { fill: #f8fafc; stroke: #64748b; stroke-width: 40; }',
            '      .usable-margin { fill: none; stroke: #cbd5e1; stroke-dasharray: 120,120; stroke-width: 20; }',
            '      .shear-line { stroke: #ef4444; stroke-dasharray: 200,100; stroke-width: 50; }',
            '      .shear-badge { font-family: sans-serif; font-size: 260px; font-weight: bold; fill: #dc2626; }',
            '      .part-path { stroke-linejoin: round; stroke-linecap: round; }',
            '      .part-label { font-family: sans-serif; font-size: 130px; font-weight: bold; fill: #0f172a; pointer-events: none; }',
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

            lines.append(f'  <g id="part_{idx}_{part.part_id}" class="nested-part" data-part="{part.part_id}">')
            lines.append(
                f'    <path class="part-path" d="{compound_d}" '
                f'fill="{fill_c}" fill-opacity="0.4" stroke="{stroke_c}" stroke-width="25" fill-rule="evenodd" />'
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
