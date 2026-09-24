#!/usr/bin/env python3
"""
CDR to Enhanced SVG Converter & Edge Repair Tool
================================================
Converts CorelDRAW (.cdr) files into clean, enhanced SVG files.
Repairs disconnected lines / edge gaps to form perfectly closed, isolated
objects with internal holes (cutouts, screw holes) properly preserved and isolated.

Features:
- Headless conversion of .cdr to .svg using LibreOffice/soffice.
- Detection and repair of open contours and edge gaps (chaining & auto-closing).
- Geometric polygon containment tree via Shapely (identifies outer shells vs interior holes).
- Generation of clean compound SVG paths with `fill-rule="evenodd"`.
- ViewBox fitting, optional origin normalization, and customizable styling.
- High-resolution visual preview generation (PNG).
"""

import os
import sys
import re
import math
import shutil
import argparse
import tempfile
import subprocess
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any

import numpy as np
from shapely.geometry import Polygon, MultiPolygon
from shapely.validation import make_valid


# ==============================================================================
# Vector Math & Curve Helpers
# ==============================================================================

def dist2d(p1: np.ndarray, p2: np.ndarray) -> float:
    return float(np.linalg.norm(p1 - p2))


def sample_cubic_bezier(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray, samples: int = 24) -> np.ndarray:
    """Sample points along a cubic Bezier curve."""
    t = np.linspace(0.0, 1.0, samples + 1)[:, None]
    pts = (1.0 - t)**3 * p0 + 3.0 * (1.0 - t)**2 * t * p1 + 3.0 * (1.0 - t) * t**2 * p2 + t**3 * p3
    return pts


# ==============================================================================
# SVG Path Command Representation
# ==============================================================================

@dataclass
class PathCommand:
    cmd: str  # 'M', 'L', 'C', 'Z'
    points: List[np.ndarray] = field(default_factory=list)

    def translated(self, dx: float, dy: float) -> 'PathCommand':
        offset = np.array([dx, dy])
        new_pts = [p + offset for p in self.points]
        return PathCommand(self.cmd, new_pts)

    def to_svg(self, precision: int = 2) -> str:
        def fmt(p):
            # Format number cleanly
            x = f"{p[0]:.{precision}f}".rstrip('0').rstrip('.')
            y = f"{p[1]:.{precision}f}".rstrip('0').rstrip('.')
            return f"{x},{y}"

        if self.cmd == 'Z':
            return 'Z'
        elif self.cmd == 'M':
            return f"M {fmt(self.points[0])}"
        elif self.cmd == 'L':
            return f"L {fmt(self.points[0])}"
        elif self.cmd == 'C':
            return f"C {fmt(self.points[0])} {fmt(self.points[1])} {fmt(self.points[2])}"
        return ''


@dataclass
class CurvePath:
    commands: List[PathCommand]
    is_closed: bool = False
    start_pt: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0]))
    end_pt: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0]))
    sampled_points: List[np.ndarray] = field(default_factory=list)
    polygon: Optional[Polygon] = None
    area: float = 0.0

    @classmethod
    def from_commands(cls, commands: List[PathCommand], samples_per_curve: int = 20) -> 'CurvePath':
        sampled = []
        curr_pt = np.array([0.0, 0.0])
        start_pt = np.array([0.0, 0.0])
        is_closed = False

        for cmd in commands:
            if cmd.cmd == 'M':
                curr_pt = cmd.points[0].copy()
                start_pt = curr_pt.copy()
                sampled.append(curr_pt.copy())
            elif cmd.cmd == 'L':
                curr_pt = cmd.points[0].copy()
                sampled.append(curr_pt.copy())
            elif cmd.cmd == 'C':
                p0 = curr_pt.copy()
                p1, p2, p3 = cmd.points[0], cmd.points[1], cmd.points[2]
                curve_pts = sample_cubic_bezier(p0, p1, p2, p3, samples=samples_per_curve)
                for p in curve_pts[1:]:
                    sampled.append(p)
                curr_pt = p3.copy()
            elif cmd.cmd == 'Z':
                is_closed = True
                curr_pt = start_pt.copy()
                sampled.append(curr_pt.copy())

        end_pt = curr_pt.copy()

        # Build Polygon if closed or nearly closed
        poly = None
        area = 0.0
        if len(sampled) >= 3:
            pts_arr = np.array(sampled)
            # Ensure closed for Shapely polygon
            if not np.allclose(pts_arr[0], pts_arr[-1]):
                pts_arr = np.vstack([pts_arr, pts_arr[0]])
            try:
                p = Polygon(pts_arr)
                if not p.is_valid:
                    p = make_valid(p)
                    if isinstance(p, MultiPolygon):
                        p = max(p.geoms, key=lambda g: g.area)
                poly = p
                area = float(poly.area)
            except Exception:
                pass

        return cls(
            commands=commands,
            is_closed=is_closed,
            start_pt=start_pt,
            end_pt=end_pt,
            sampled_points=sampled,
            polygon=poly,
            area=area
        )

    def to_svg_d(self, precision: int = 2) -> str:
        return ' '.join(c.to_svg(precision) for c in self.commands if c.to_svg(precision))

    def reversed(self) -> 'CurvePath':
        """Reverse the curve path direction while maintaining exact Bezier curvature."""
        if not self.commands:
            return self

        # Extract all waypoints
        rev_cmds: List[PathCommand] = []
        # Find endpoint
        new_start = self.end_pt.copy()
        rev_cmds.append(PathCommand('M', [new_start]))

        # Reconstruct backwards
        curr_end = self.end_pt.copy()
        # Collect segments with their starting point
        segments = []
        c_pt = self.start_pt.copy()
        for cmd in self.commands:
            if cmd.cmd == 'M':
                c_pt = cmd.points[0].copy()
            elif cmd.cmd == 'L':
                segments.append(('L', c_pt.copy(), cmd.points[0].copy()))
                c_pt = cmd.points[0].copy()
            elif cmd.cmd == 'C':
                segments.append(('C', c_pt.copy(), cmd.points[0].copy(), cmd.points[1].copy(), cmd.points[2].copy()))
                c_pt = cmd.points[2].copy()

        # Iterate segments in reverse order
        for seg in reversed(segments):
            if seg[0] == 'L':
                # Line to previous point
                p_prev = seg[1]
                rev_cmds.append(PathCommand('L', [p_prev.copy()]))
            elif seg[0] == 'C':
                # Cubic Bezier: from p3 to p0, with control points p2, p1
                p0, p1, p2, p3 = seg[1], seg[2], seg[3], seg[4]
                rev_cmds.append(PathCommand('C', [p2.copy(), p1.copy(), p0.copy()]))

        if self.is_closed:
            rev_cmds.append(PathCommand('Z', []))

        return CurvePath.from_commands(rev_cmds)

    def close_gap(self, snap: bool = False) -> 'CurvePath':
        """Close an open curve by connecting end_pt back to start_pt."""
        new_cmds = list(self.commands)
        # Strip trailing Z if any
        if new_cmds and new_cmds[-1].cmd == 'Z':
            new_cmds.pop()

        if snap and new_cmds:
            # Snap the final point to start_pt
            if new_cmds[-1].cmd in ('L', 'M'):
                new_cmds[-1].points[0] = self.start_pt.copy()
            elif new_cmds[-1].cmd == 'C':
                new_cmds[-1].points[2] = self.start_pt.copy()
        else:
            # If end_pt != start_pt, add closing L
            if dist2d(self.start_pt, self.end_pt) > 1e-4:
                new_cmds.append(PathCommand('L', [self.start_pt.copy()]))

        new_cmds.append(PathCommand('Z', []))
        return CurvePath.from_commands(new_cmds)

    def translated(self, dx: float, dy: float) -> 'CurvePath':
        new_cmds = [c.translated(dx, dy) for c in self.commands]
        return CurvePath.from_commands(new_cmds)


# ==============================================================================
# SVG Path Parser
# ==============================================================================

def tokenize_path(d: str) -> List[str]:
    """Tokenize SVG path d string into commands and numbers."""
    return re.findall(r'([a-zA-Z]|[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?)', d)


def parse_path_string(d: str) -> List[CurvePath]:
    """Parse SVG path d string into a list of CurvePath objects (one per subpath)."""
    tokens = tokenize_path(d)
    if not tokens:
        return []

    curve_paths: List[CurvePath] = []
    current_cmds: List[PathCommand] = []
    current_pt = np.array([0.0, 0.0])
    start_pt = np.array([0.0, 0.0])

    i = 0
    cmd = None

    while i < len(tokens):
        token = tokens[i]
        if token.isalpha():
            cmd = token
            i += 1

        if cmd is None:
            break

        if cmd in 'Mm':
            if i + 1 >= len(tokens):
                break
            x, y = float(tokens[i]), float(tokens[i+1])
            i += 2
            if cmd == 'm':
                current_pt = current_pt + np.array([x, y])
            else:
                current_pt = np.array([x, y])
            start_pt = current_pt.copy()

            if current_cmds:
                curve_paths.append(CurvePath.from_commands(current_cmds))
                current_cmds = []

            current_cmds.append(PathCommand('M', [current_pt.copy()]))
            # Subsequent numbers after M are implicit L commands
            cmd = 'l' if cmd == 'm' else 'L'

        elif cmd in 'Ll':
            if i + 1 >= len(tokens):
                break
            x, y = float(tokens[i]), float(tokens[i+1])
            i += 2
            if cmd == 'l':
                current_pt = current_pt + np.array([x, y])
            else:
                current_pt = np.array([x, y])
            current_cmds.append(PathCommand('L', [current_pt.copy()]))

        elif cmd in 'Hh':
            if i >= len(tokens):
                break
            x = float(tokens[i])
            i += 1
            if cmd == 'h':
                current_pt[0] += x
            else:
                current_pt[0] = x
            current_cmds.append(PathCommand('L', [current_pt.copy()]))

        elif cmd in 'Vv':
            if i >= len(tokens):
                break
            y = float(tokens[i])
            i += 1
            if cmd == 'v':
                current_pt[1] += y
            else:
                current_pt[1] = y
            current_cmds.append(PathCommand('L', [current_pt.copy()]))

        elif cmd in 'Cc':
            if i + 5 >= len(tokens):
                break
            p0 = current_pt.copy()
            x1, y1 = float(tokens[i]), float(tokens[i+1])
            x2, y2 = float(tokens[i+2]), float(tokens[i+3])
            x, y = float(tokens[i+4]), float(tokens[i+5])
            i += 6
            if cmd == 'c':
                p1 = p0 + np.array([x1, y1])
                p2 = p0 + np.array([x2, y2])
                p3 = p0 + np.array([x, y])
            else:
                p1 = np.array([x1, y1])
                p2 = np.array([x2, y2])
                p3 = np.array([x, y])
            current_cmds.append(PathCommand('C', [p1, p2, p3]))
            current_pt = p3.copy()

        elif cmd in 'Zz':
            current_cmds.append(PathCommand('Z', []))
            current_pt = start_pt.copy()
            curve_paths.append(CurvePath.from_commands(current_cmds))
            current_cmds = []
            cmd = None

        else:
            # Skip unsupported token
            i += 1

    if current_cmds:
        curve_paths.append(CurvePath.from_commands(current_cmds))

    return curve_paths


# ==============================================================================
# Edge Gap Repair & Segment Stitching Engine
# ==============================================================================

@dataclass
class RepairReport:
    total_input_paths: int
    open_curves_repaired: int
    segments_chained: int
    repair_details: List[str]


def repair_and_stitch_curves(paths: List[CurvePath], tolerance: float = 300.0) -> Tuple[List[CurvePath], RepairReport]:
    """
    Repair edge gaps and disconnected lines:
    1. Check if open curve can be chained to other open curves within tolerance.
    2. Close loops when endpoints meet or are within tolerance.
    """
    closed_paths: List[CurvePath] = []
    open_paths: List[CurvePath] = []
    repair_details: List[str] = []
    repaired_count = 0
    chained_count = 0

    for p in paths:
        if p.is_closed or dist2d(p.start_pt, p.end_pt) < 1e-4:
            if not p.is_closed:
                p = p.close_gap()
            closed_paths.append(p)
        else:
            open_paths.append(p)

    # First pass: try chaining open paths together
    merged_open: List[CurvePath] = []
    while open_paths:
        curr = open_paths.pop(0)
        chained_any = False

        # Check self-gap first
        self_gap = dist2d(curr.start_pt, curr.end_pt)
        if self_gap <= tolerance:
            repaired = curr.close_gap()
            closed_paths.append(repaired)
            repaired_count += 1
            repair_details.append(f"Auto-closed self-loop with gap {self_gap:.2f} units at {curr.start_pt.tolist()}")
            continue

        # Look for a matching neighbor
        best_match_idx = -1
        best_dist = float('inf')
        match_type = ''  # 'end_to_start', 'end_to_end', 'start_to_end', 'start_to_start'

        for idx, other in enumerate(open_paths):
            d1 = dist2d(curr.end_pt, other.start_pt)
            d2 = dist2d(curr.end_pt, other.end_pt)
            d3 = dist2d(curr.start_pt, other.end_pt)
            d4 = dist2d(curr.start_pt, other.start_pt)

            min_d = min(d1, d2, d3, d4)
            if min_d < best_dist and min_d <= tolerance:
                best_dist = min_d
                best_match_idx = idx
                if min_d == d1:
                    match_type = 'end_to_start'
                elif min_d == d2:
                    match_type = 'end_to_end'
                elif min_d == d3:
                    match_type = 'start_to_end'
                else:
                    match_type = 'start_to_start'

        if best_match_idx >= 0:
            other = open_paths.pop(best_match_idx)
            if match_type == 'end_to_start':
                # Join curr + other
                other_cmds = [c for c in other.commands if c.cmd != 'M']
                new_cmds = list(curr.commands) + other_cmds
            elif match_type == 'end_to_end':
                # Reverse other and join curr + reversed(other)
                rev_other = other.reversed()
                other_cmds = [c for c in rev_other.commands if c.cmd != 'M']
                new_cmds = list(curr.commands) + other_cmds
            elif match_type == 'start_to_end':
                # Join other + curr
                curr_cmds = [c for c in curr.commands if c.cmd != 'M']
                new_cmds = list(other.commands) + curr_cmds
            else:  # start_to_start
                # Reverse curr and join reversed(curr) + other
                rev_curr = curr.reversed()
                other_cmds = [c for c in other.commands if c.cmd != 'M']
                new_cmds = list(rev_curr.commands) + other_cmds

            chained = CurvePath.from_commands(new_cmds)
            chained_count += 1
            repair_details.append(f"Chained two curve segments with gap {best_dist:.2f} units ({match_type})")

            # Check if now closed
            if dist2d(chained.start_pt, chained.end_pt) <= tolerance:
                closed = chained.close_gap()
                closed_paths.append(closed)
                repaired_count += 1
                repair_details.append(f"Chained curve closed into complete loop (residual gap {dist2d(chained.start_pt, chained.end_pt):.2f})")
            else:
                # Put back for further chaining
                open_paths.insert(0, chained)
        else:
            # Cannot chain with any neighbor; check if self-gap can be closed with relaxed tolerance
            if self_gap <= tolerance * 1.5:
                repaired = curr.close_gap()
                closed_paths.append(repaired)
                repaired_count += 1
                repair_details.append(f"Closed isolated open curve with gap {self_gap:.2f} units")
            else:
                merged_open.append(curr)

    # Any remaining open paths that couldn't be closed
    for unclosed in merged_open:
        gap = dist2d(unclosed.start_pt, unclosed.end_pt)
        if gap <= tolerance * 2.0:
            closed_paths.append(unclosed.close_gap())
            repaired_count += 1
            repair_details.append(f"Closed open curve with larger gap {gap:.2f} units")
        else:
            repair_details.append(f"WARNING: Unclosed dangling curve remains with gap {gap:.2f} units at {unclosed.start_pt.tolist()}")

    report = RepairReport(
        total_input_paths=len(paths),
        open_curves_repaired=repaired_count,
        segments_chained=chained_count,
        repair_details=repair_details
    )
    return closed_paths, report


# ==============================================================================
# Polygon Containment & Object Isolation Hierarchy
# ==============================================================================

@dataclass
class IsolatedObject:
    outer_path: CurvePath
    holes: List[CurvePath] = field(default_factory=list)
    bounds: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # minx, miny, maxx, maxy
    net_area: float = 0.0

    @property
    def width(self) -> float:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> float:
        return self.bounds[3] - self.bounds[1]

    def to_compound_svg_d(self, precision: int = 2) -> str:
        """Return combined compound path d string for outer shell and all interior holes."""
        parts = [self.outer_path.to_svg_d(precision)]
        for h in self.holes:
            parts.append(h.to_svg_d(precision))
        return ' '.join(parts)


def build_isolated_objects(closed_paths: List[CurvePath]) -> List[IsolatedObject]:
    """
    Build isolated objects by resolving topological polygon containment:
    - Polygons with no parent polygon are outer shells.
    - Polygons contained directly inside an outer shell are holes (cutouts/voids).
    """
    valid_paths = [p for p in closed_paths if p.polygon is not None and p.polygon.is_valid and p.area > 0]
    if not valid_paths:
        return []

    # Sort descending by area so potential parents come first
    valid_paths.sort(key=lambda p: p.area, reverse=True)

    n = len(valid_paths)
    parent_map: Dict[int, Optional[int]] = {i: None for i in range(n)}

    # Determine direct parent for each polygon
    for i in range(n):
        poly_child = valid_paths[i].polygon
        # Find smallest container
        minx_c, miny_c, maxx_c, maxy_c = poly_child.bounds
        smallest_parent_idx = None
        smallest_parent_area = float('inf')
        for j in range(n):
            if i != j and valid_paths[j].area > valid_paths[i].area:
                minx_p, miny_p, maxx_p, maxy_p = valid_paths[j].polygon.bounds
                # Fast AABB rejection: parent box must completely enclose child box
                if minx_p > minx_c or miny_p > miny_c or maxx_p < maxx_c or maxy_p < maxy_c:
                    continue
                poly_parent = valid_paths[j].polygon
                # Use contains or covers
                if poly_parent.contains(poly_child) or poly_parent.covers(poly_child):
                    if valid_paths[j].area < smallest_parent_area:
                        smallest_parent_area = valid_paths[j].area
                        smallest_parent_idx = j

        parent_map[i] = smallest_parent_idx

    # Group outer shells and direct holes
    # An outer shell is a node whose depth in the containment tree is even (0, 2, ...).
    # Typically depth 0 = Outer Object, depth 1 = Holes.
    depths: Dict[int, int] = {}

    def get_depth(node_idx: int) -> int:
        if node_idx in depths:
            return depths[node_idx]
        p = parent_map[node_idx]
        if p is None:
            depth = 0
        else:
            depth = get_depth(p) + 1
        depths[node_idx] = depth
        return depth

    for i in range(n):
        get_depth(i)

    # Collect isolated objects (depth 0) and assign their holes (depth 1)
    objects: Dict[int, IsolatedObject] = {}
    for i in range(n):
        if depths[i] == 0:
            outer = valid_paths[i]
            minx, miny, maxx, maxy = outer.polygon.bounds
            objects[i] = IsolatedObject(
                outer_path=outer,
                holes=[],
                bounds=(minx, miny, maxx, maxy),
                net_area=outer.area
            )

    for i in range(n):
        if depths[i] == 1:
            p_idx = parent_map[i]
            if p_idx in objects:
                objects[p_idx].holes.append(valid_paths[i])
                objects[p_idx].net_area -= valid_paths[i].area

    # Return list of isolated objects sorted by outer area descending
    res = list(objects.values())
    res.sort(key=lambda o: o.outer_path.area, reverse=True)
    return res


# ==============================================================================
# SVG Extraction from LibreOffice Draw Export
# ==============================================================================

def extract_paths_from_svg(svg_path: str) -> List[CurvePath]:
    """
    Extract drawing paths from an SVG file, ignoring LibreOffice defs,
    bullet templates, and invisible bounding boxes.
    """
    import xml.etree.ElementTree as ET

    tree = ET.parse(svg_path)
    root = tree.getroot()

    # Strip defs, clipPath, metadata elements completely
    for parent in list(root.iter()):
        for child in list(parent):
            tag = child.tag.split('}')[-1]
            if tag in ('defs', 'clipPath', 'metadata'):
                parent.remove(child)

    extracted_paths: List[CurvePath] = []

    for elem in root.iter():
        tag = elem.tag.split('}')[-1]
        cls = elem.get('class', '')
        if 'BoundingBox' in cls:
            continue

        if tag == 'path' and 'd' in elem.attrib:
            d = elem.attrib['d'].strip()
            stroke = elem.get('stroke', '')
            fill = elem.get('fill', '')
            if stroke == 'none' and fill == 'none':
                continue

            parsed_list = parse_path_string(d)
            for cp in parsed_list:
                if len(cp.sampled_points) >= 2:
                    extracted_paths.append(cp)

    return extracted_paths


# ==============================================================================
# SVG Generation
# ==============================================================================

def generate_enhanced_svg(
    isolated_objects: List[IsolatedObject],
    output_svg_path: str,
    padding: float = 500.0,
    normalize_origin: bool = True,
    fill_color: str = "#6366f1",
    fill_opacity: float = 0.35,
    stroke_color: str = "#4338ca",
    stroke_width: float = 25.0,
    mode: str = "compound"
) -> Dict[str, Any]:
    """
    Generate clean, optimized SVG representing perfectly isolated objects with holes.
    """
    if not isolated_objects:
        raise ValueError("No isolated objects to generate SVG from.")

    # Calculate global bounding box
    all_minx = min(obj.bounds[0] for obj in isolated_objects)
    all_miny = min(obj.bounds[1] for obj in isolated_objects)
    all_maxx = max(obj.bounds[2] for obj in isolated_objects)
    all_maxy = max(obj.bounds[3] for obj in isolated_objects)

    obj_w = all_maxx - all_minx
    obj_h = all_maxy - all_miny

    if normalize_origin:
        # Shift so bounding box starts at (padding, padding)
        dx = -all_minx + padding
        dy = -all_miny + padding
        vb_x = 0.0
        vb_y = 0.0
        vb_w = obj_w + 2 * padding
        vb_h = obj_h + 2 * padding
    else:
        dx = 0.0
        dy = 0.0
        vb_x = all_minx - padding
        vb_y = all_miny - padding
        vb_w = obj_w + 2 * padding
        vb_h = obj_h + 2 * padding

    # Dimensions in mm (LibreOffice SVG 100 units = 1mm)
    w_mm = vb_w / 100.0
    h_mm = vb_h / 100.0

    svg_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg version="1.1" viewBox="{vb_x:.1f} {vb_y:.1f} {vb_w:.1f} {vb_h:.1f}" '
        f'width="{w_mm:.1f}mm" height="{h_mm:.1f}mm" '
        'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">',
        '  <defs>',
        '    <style>',
        '      .isolated-object { vector-effect: non-scaling-stroke; }',
        '      .outer-contour { fill: none; stroke-linejoin: round; stroke-linecap: round; }',
        '      .hole-contour { fill: none; stroke-linejoin: round; stroke-linecap: round; }',
        '      .compound-shape { stroke-linejoin: round; stroke-linecap: round; }',
        '    </style>',
        '  </defs>'
    ]

    for idx, obj in enumerate(isolated_objects, start=1):
        # Translate paths if normalized
        outer_p = obj.outer_path.translated(dx, dy) if normalize_origin else obj.outer_path
        holes_p = [h.translated(dx, dy) if normalize_origin else h for h in obj.holes]

        # Compound path d string
        compound_d = outer_p.to_svg_d()
        if holes_p:
            compound_d += ' ' + ' '.join(h.to_svg_d() for h in holes_p)

        obj_minx, obj_miny, obj_maxx, obj_maxy = obj.bounds
        part_w_mm = (obj_maxx - obj_minx) / 100.0
        part_h_mm = (obj_maxy - obj_miny) / 100.0

        svg_lines.append(f'  <!-- Isolated Object {idx} -->')
        svg_lines.append(
            f'  <g id="isolated_object_{idx}" class="isolated-object" '
            f'data-index="{idx}" data-holes="{len(obj.holes)}" '
            f'data-width-mm="{part_w_mm:.2f}" data-height-mm="{part_h_mm:.2f}">'
        )

        if mode in ("compound", "both"):
            # Master compound path with evenodd rule (renders solid object with real transparent holes)
            svg_lines.append(
                f'    <path id="object_{idx}_compound" class="compound-shape" '
                f'fill="{fill_color}" fill-opacity="{fill_opacity}" '
                f'stroke="{stroke_color}" stroke-width="{stroke_width}" '
                f'fill-rule="evenodd" d="{compound_d}" />'
            )

        if mode in ("nested", "both"):
            # Separate elements for laser / CNC cutting path planning (cut holes first, perimeter last)
            svg_lines.append(f'    <g id="object_{idx}_holes" class="holes-layer">')
            for h_idx, h in enumerate(holes_p, start=1):
                svg_lines.append(
                    f'      <path id="object_{idx}_hole_{h_idx}" class="hole-contour" '
                    f'stroke="#dc2626" stroke-width="{stroke_width}" d="{h.to_svg_d()}" />'
                )
            svg_lines.append('    </g>')
            svg_lines.append(
                f'    <path id="object_{idx}_outer" class="outer-contour" '
                f'stroke="{stroke_color}" stroke-width="{stroke_width}" d="{outer_p.to_svg_d()}" />'
            )

        svg_lines.append('  </g>')

    svg_lines.append('</svg>')
    svg_content = '\n'.join(svg_lines)

    with open(output_svg_path, 'w', encoding='utf-8') as f:
        f.write(svg_content)

    return {
        "output_file": output_svg_path,
        "viewBox": (vb_x, vb_y, vb_w, vb_h),
        "width_mm": w_mm,
        "height_mm": h_mm,
        "total_objects": len(isolated_objects),
        "total_holes": sum(len(o.holes) for o in isolated_objects),
        "file_size_bytes": os.path.getsize(output_svg_path)
    }


# ==============================================================================
# CDR Converter
# ==============================================================================

def convert_cdr_to_svg(cdr_path: str, out_dir: Optional[str] = None) -> str:
    """Convert .cdr to .svg using LibreOffice Draw headless mode."""
    if not os.path.exists(cdr_path):
        raise FileNotFoundError(f"CDR file not found: {cdr_path}")

    # Find libreoffice or soffice executable
    lo_bin = shutil.which("libreoffice") or shutil.which("soffice")
    if not lo_bin:
        # Check standard user local path
        local_lo = os.path.expanduser("~/.local/bin/libreoffice")
        if os.path.exists(local_lo):
            lo_bin = local_lo
        else:
            raise RuntimeError("LibreOffice / soffice not found in PATH or ~/.local/bin.")

    if out_dir is None:
        out_dir = os.path.dirname(os.path.abspath(cdr_path))

    cmd = [
        lo_bin,
        "--headless",
        "--convert-to", "svg",
        cdr_path,
        "--outdir", out_dir
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"LibreOffice conversion failed: {result.stderr}")

    base_name = os.path.splitext(os.path.basename(cdr_path))[0]
    generated_svg = os.path.join(out_dir, f"{base_name}.svg")

    if not os.path.exists(generated_svg):
        raise RuntimeError(f"Expected SVG file was not generated: {generated_svg}")

    return generated_svg


def render_preview_png(svg_path: str, png_path: str, dpi: int = 150) -> Optional[str]:
    """Render high-resolution PNG preview of SVG."""
    # Convert to PDF first via LibreOffice, then pdftoppm to PNG
    lo_bin = shutil.which("libreoffice") or shutil.which("soffice") or os.path.expanduser("~/.local/bin/libreoffice")
    pdftoppm = shutil.which("pdftoppm")

    if not lo_bin or not pdftoppm:
        return None

    out_dir = os.path.dirname(os.path.abspath(png_path))
    base = os.path.splitext(os.path.basename(png_path))[0]
    temp_pdf = os.path.join(out_dir, f"{base}_temp.pdf")

    try:
        # 1. SVG -> PDF
        subprocess.run(
            [lo_bin, "--headless", "--convert-to", "pdf", svg_path, "--outdir", out_dir],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        svg_base = os.path.splitext(os.path.basename(svg_path))[0]
        gen_pdf = os.path.join(out_dir, f"{svg_base}.pdf")

        if os.path.exists(gen_pdf):
            # 2. PDF -> PNG via pdftoppm
            prefix = os.path.join(out_dir, f"{base}_ppm")
            subprocess.run(
                [pdftoppm, "-png", "-r", str(dpi), gen_pdf, prefix],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
            )
            # Find generated png (prefix-1.png)
            ppm_png = f"{prefix}-1.png"
            if os.path.exists(ppm_png):
                shutil.move(ppm_png, png_path)
            # Cleanup pdf
            if os.path.exists(gen_pdf):
                os.remove(gen_pdf)
            return png_path if os.path.exists(png_path) else None
    except Exception:
        pass
    return None


# ==============================================================================
# Main Enhancement Pipeline
# ==============================================================================

def process_file(
    input_path: str,
    output_svg_path: Optional[str] = None,
    tolerance: float = 300.0,
    padding: float = 400.0,
    normalize_origin: bool = True,
    generate_preview: bool = True,
    fill_color: str = "#6366f1",
    fill_opacity: float = 0.35,
    stroke_color: str = "#4338ca",
    stroke_width: float = 25.0,
    mode: str = "compound"
) -> Dict[str, Any]:
    """
    Main processing function:
    1. Converts .cdr to .svg if input is a CDR file.
    2. Extracts curves and paths.
    3. Repairs disconnected lines and closes edge gaps.
    4. Analyzes topological containment to isolate objects and cutouts.
    5. Outputs clean enhanced SVG and optional preview PNG.
    """
    input_ext = os.path.splitext(input_path)[1].lower()
    temp_dir = None

    if input_ext == ".cdr":
        print(f"[*] Step 1: Converting CDR '{os.path.basename(input_path)}' to SVG via LibreOffice...")
        cache_base = os.path.join(os.path.expanduser("~"), ".cache", "cdr_enhancer_temp")
        os.makedirs(cache_base, exist_ok=True)
        temp_dir = tempfile.mkdtemp(prefix="cdr_enhance_", dir=cache_base)
        raw_svg_path = convert_cdr_to_svg(os.path.abspath(input_path), out_dir=temp_dir)
    elif input_ext == ".svg":
        print(f"[*] Step 1: Using direct SVG '{os.path.basename(input_path)}'...")
        raw_svg_path = os.path.abspath(input_path)
    else:
        raise ValueError(f"Unsupported file format '{input_ext}'. Supported: .cdr, .svg")

    if output_svg_path is None:
        base = os.path.splitext(input_path)[0]
        output_svg_path = f"{base}_enhanced.svg"

    try:
        # Step 2: Extract paths
        print("[*] Step 2: Extracting vector curves...")
        raw_paths = extract_paths_from_svg(raw_svg_path)
        print(f"    -> Extracted {len(raw_paths)} drawing path elements.")

        # Step 3: Repair edge gaps and chain disconnected lines
        print(f"[*] Step 3: Fixing holes and disconnected lines (tolerance: {tolerance} units)...")
        closed_paths, report = repair_and_stitch_curves(raw_paths, tolerance=tolerance)
        print(f"    -> Repaired open curves: {report.open_curves_repaired}")
        print(f"    -> Chained segments: {report.segments_chained}")
        for detail in report.repair_details[:5]:
            print(f"       + {detail}")
        if len(report.repair_details) > 5:
            print(f"       + ... and {len(report.repair_details) - 5} more repairs.")

        # Step 4: Topological containment analysis
        print("[*] Step 4: Resolving object boundaries and interior holes...")
        isolated_objects = build_isolated_objects(closed_paths)
        print(f"    -> Identified {len(isolated_objects)} perfectly isolated object(s).")
        for i, obj in enumerate(isolated_objects, start=1):
            print(f"       Part {i}: Dimensions {obj.width/100:.1f}x{obj.height/100:.1f} mm | Interior holes: {len(obj.holes)}")

        # Step 5: Generate enhanced SVG
        print(f"[*] Step 5: Generating enhanced SVG '{os.path.basename(output_svg_path)}'...")
        gen_info = generate_enhanced_svg(
            isolated_objects=isolated_objects,
            output_svg_path=output_svg_path,
            padding=padding,
            normalize_origin=normalize_origin,
            fill_color=fill_color,
            fill_opacity=fill_opacity,
            stroke_color=stroke_color,
            stroke_width=stroke_width,
            mode=mode
        )
        print(f"    -> Saved SVG ({gen_info['file_size_bytes']} bytes).")

        # Step 6: Optional preview
        preview_png_path = None
        if generate_preview:
            png_target = os.path.splitext(output_svg_path)[0] + "_preview.png"
            print(f"[*] Step 6: Rendering PNG visual preview '{os.path.basename(png_target)}'...")
            preview_png_path = render_preview_png(output_svg_path, png_target)
            if preview_png_path:
                print(f"    -> Saved PNG preview ({os.path.getsize(preview_png_path)} bytes).")

        return {
            "input_file": input_path,
            "output_svg": output_svg_path,
            "preview_png": preview_png_path,
            "isolated_objects_count": len(isolated_objects),
            "total_holes_count": sum(len(o.holes) for o in isolated_objects),
            "open_lines_repaired": report.open_curves_repaired,
            "segments_chained": report.segments_chained,
            "dimensions_mm": (gen_info["width_mm"], gen_info["height_mm"])
        }

    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Convert CorelDRAW (.cdr) to SVG and fix edge gaps into perfectly isolated objects with holes."
    )
    parser.add_argument("input", help="Path to input .cdr or .svg file")
    parser.add_argument("-o", "--output", help="Path to output .svg file (default: <input>_enhanced.svg)")
    parser.add_argument("-t", "--tolerance", type=float, default=300.0,
                        help="Tolerance in document units to bridge disconnected lines (default: 300 = ~3mm)")
    parser.add_argument("-p", "--padding", type=float, default=400.0,
                        help="Padding in document units around viewBox (default: 400 = 4mm)")
    parser.add_argument("--no-normalize", action="store_true",
                        help="Do not shift coordinates to origin (preserve original coordinates)")
    parser.add_argument("--no-preview", action="store_true",
                        help="Do not generate a high-res PNG preview")
    parser.add_argument("--fill", default="#6366f1", help="Fill color for isolated object body (default: #6366f1)")
    parser.add_argument("--opacity", type=float, default=0.35, help="Fill opacity (default: 0.35)")
    parser.add_argument("--stroke", default="#4338ca", help="Stroke color (default: #4338ca)")
    parser.add_argument("--stroke-width", type=float, default=25.0, help="Stroke width in units (default: 25)")
    parser.add_argument("--mode", choices=["compound", "nested", "both"], default="compound",
                        help="SVG output mode: compound (evenodd path), nested (outer + holes), or both (default: compound)")

    args = parser.parse_args()

    try:
        summary = process_file(
            input_path=args.input,
            output_svg_path=args.output,
            tolerance=args.tolerance,
            padding=args.padding,
            normalize_origin=not args.no_normalize,
            generate_preview=not args.no_preview,
            fill_color=args.fill,
            fill_opacity=args.opacity,
            stroke_color=args.stroke,
            stroke_width=args.stroke_width,
            mode=args.mode
        )
        print("\n" + "=" * 60)
        print("  CONVERSION & ENHANCEMENT SUCCESSFUL")
        print("=" * 60)
        print(f"  Input File        : {summary['input_file']}")
        print(f"  Output SVG        : {summary['output_svg']}")
        if summary.get('preview_png'):
            print(f"  Preview PNG       : {summary['preview_png']}")
        print(f"  Isolated Objects  : {summary['isolated_objects_count']}")
        print(f"  Internal Holes    : {summary['total_holes_count']}")
        print(f"  Edge Gaps Repaired: {summary['open_lines_repaired']}")
        print(f"  Dimensions (W x H): {summary['dimensions_mm'][0]:.1f} x {summary['dimensions_mm'][1]:.1f} mm")
        print("=" * 60)
    except Exception as e:
        print(f"\n[!] ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
