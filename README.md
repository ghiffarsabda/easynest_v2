# CDR to SVG Converter & Geometry Enhancer (EasyNest v2)

A Python tool that converts CorelDRAW (`.cdr`) drawings into clean, enhanced SVG files. It detects and repairs edge gaps (open/disconnected lines) and reconstructs the geometry into **perfectly isolated objects with internal holes (cutouts/voids) isolated**.

---

## 🛠️ How It Works

1. **CDR to SVG Conversion**: Invokes LibreOffice headless engine (`libcdr`) to extract native vector paths from CorelDRAW files.
2. **Path Cleaning**: Strips out LibreOffice metadata, presentation slides, bullet character definitions (`defs`), and invisible bounding boxes.
3. **Edge Gap & Disconnected Line Repair**:
   - Detects open curve paths where the endpoint does not meet the starting point.
   - Automatically bridges gaps and stitches adjacent curve segments within a configurable tolerance.
   - Smoothly closes loops with exact cubic Bezier curvature preservation.
4. **Topological Hierarchy & Hole Isolation (Shapely)**:
   - Evaluates polygon containment (`parent.contains(child)`).
   - Classifies Level 0 boundaries as **Outer Part Shells**.
   - Classifies Level 1 internal cutouts (screw holes, decorative slots, vents) as **Holes**.
5. **Output Generation**:
   - Generates a clean SVG with a single compound path using `fill-rule="evenodd"`.
   - Supports nesting mode (`--mode compound`, `nested`, or `both`) with distinct layers for outer perimeters and hole cutouts.
   - Snugly crops the viewBox with customizable padding around the object.
   - Generates high-resolution PNG visual preview side-by-side.

---

## 🚀 Quick Start

### Basic CLI Usage

```bash
# Convert and enhance sample CDR
python3 cdr_enhancer.py "GAR TURBO D.cdr"

# Convert another file
python3 cdr_enhancer.py "GAR NMAX NEW G.cdr"
```

Output files created:
- `GAR TURBO D_enhanced.svg` — Production-ready vector file.
- `GAR TURBO D_enhanced_preview.png` — Rendered preview for visual inspection.

### Options & Flags

| Flag | Default | Description |
|------|---------|-------------|
| `-o`, `--output` | `<input>_enhanced.svg` | Custom output SVG file path |
| `-t`, `--tolerance` | `300.0` (~3 mm) | Maximum gap distance to bridge disconnected lines |
| `-p`, `--padding` | `400.0` (4 mm) | Padding around viewBox border |
| `--mode` | `compound` | SVG format: `compound` (`evenodd`), `nested` (separate hole layers), or `both` |
| `--fill` | `#6366f1` | Color for solid part body |
| `--opacity` | `0.35` | Fill opacity |
| `--stroke` | `#4338ca` | Outline stroke color |
| `--stroke-width` | `25.0` (0.25 mm) | Stroke thickness |
| `--no-preview` | False | Skip PNG preview generation |
| `--no-normalize` | False | Preserve original document coordinate space |

### Python API Integration

```python
from cdr_enhancer import process_file

result = process_file(
    input_path="GAR TURBO D.cdr",
    output_svg_path="output.svg",
    tolerance=300.0,
    mode="compound"
)

print(f"Isolated objects: {result['isolated_objects_count']}")
print(f"Total holes: {result['total_holes_count']}")
print(f"Repaired gaps: {result['open_lines_repaired']}")
```

---

## 🏭 Industrial True-Shape Nesting CLI (`industrial_nest.py`)

A production-grade 2D irregular nesting tool designed for CNC laser cutting, routers, and plotters. It features multi-strategy tournament optimization (concentric curve-hugging lattice, true-shape BLF, and boundary strip void filling) to achieve **the highest possible sheet yield (max fit)** with adjustable kerf.

### CLI Usage

```bash
# Nest on a custom sheet (e.g. 500x300 mm) with 2mm kerf
python3 industrial_nest.py "GAR TURBO D.cdr" --sheet 500x300 --kerf 2.0 --margin 5.0

# Nest on a standard 4x8 ft sheet (122x244 cm)
python3 industrial_nest.py "GAR TURBO D.cdr" --sheet "122x244cm" --kerf 2.0 --margin 5.0
```

### Options & Flags

| Flag | Default | Description |
|------|---------|-------------|
| `-s`, `--sheet` | *(Required)* | Sheet size in `WxH` (e.g. `500x300`, `122x244cm`, `2440x1220`) |
| `-k`, `--kerf` | `2.0` (mm) | Tool cutting kerf / part-to-part safety gap |
| `-m`, `--margin` | `5.0` (mm) | Border margin from sheet edges |
| `-r`, `--rotations` | `4` | Allowed rotations: `2` (0,180°), `4` (0,90,180,270°), `free` (15° step) |
| `-o`, `--output` | Auto | Output nested SVG file path |
| `--no-preview` | False | Skip PNG preview rendering |

### Benchmark Results on `GAR TURBO D`

| Layout Sheet | Dimensions | Kerf | Margin | Units Nested | Material Yield | Cut Length |
|--------------|------------|------|--------|--------------|----------------|------------|
| Small Sheet  | 500 x 300 mm | 2.0 mm | 5.0 mm | **14 parts** (Max Fit) | **68.88%** | 15.68 m |
| Full 4x8 ft  | 122 x 244 cm | 2.0 mm | 5.0 mm | **305 parts** (Max Fit) | **75.62%** | 341.51 m |

---

## 🧬 Superposition Multi-Universe Concurrent Mixed Nesting

When nesting multiple part types simultaneously (e.g. large primary parts like `windshield.svg` mixed with smaller filler parts like `GAR TURBO D.cdr`), the engine activates **Superposition Mode**:

1. **Multi-Universe Concurrency (`multiprocessing`)**:
   - Concurrently spawns and evaluates competing candidate layout universes across all CPU cores in parallel:
     - **Universe 1**: Honeycomb Staggered Interlock (4 columns of primary parts nested horizontally).
     - **Universe 2**: Compact Left-Biased Layout (3 columns packed left, leaving an open 200 mm secondary corridor).
     - **Universe 3**: Centered Balanced Layout (symmetric dual corridors).
     - **Universe 4**: Zero-Drift Cluster Tessellation.
2. **Recursive Multi-Wave Void Filler**:
   - Wave 1: Edge Corridor & Margin Micro-Scan (top/bottom margins and lateral strips).
   - Wave 2: Inter-Column Bay & Waist Recess Inundation.
   - Wave 3: Boundary Anchor Multi-Angle Probing (16–24 angles with strict zero collision).
3. **Evolutionary Selection & Tournament Scoreboard**:
   - Scores each universe based on net part area, material yield %, and total part count.
   - Automatically crowns the fittest champion universe and exports the production SVG and PNG preview.

### Mixed Nesting Benchmark (122 x 244 cm Sheet)

```bash
python3 industrial_nest.py windshield.svg "GAR TURBO D.cdr" \
  --sheet "122x244cm" --kerf 2.0 --margin 5.0 --sheet-cost 35.0 --machine-rate 75.0
```

| Strategy / Universe | Windshields | GAR TURBO D | Total Parts | Material Yield | Cut Length | Unit Cost ($) | Solve Time |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Honeycomb Staggered (Champion)** | **26 units** | **20 units** | **46 units** | **75.41%** | **66.75 m** | **$1.495** | **19.17s** |
| Centered Balanced | 21 units | 50 units | 71 units | 69.30% | 91.81 m | $1.161 | 15.62s |
| Compact Left-Biased | 21 units | 35 units | 56 units | 65.58% | 75.01 m | $1.296 | 10.98s |
| Zero-Drift Cluster | 21 units | 35 units | 56 units | 65.58% | 75.01 m | $1.314 | 11.67s |

<p align="center">
  <img src="windshield_GAR_TURBO_D_enhanced_mixed_nested_1220x2440_preview.png" alt="Industrial Mixed Nesting Preview" width="450" />
  <br/>
  <em>Figure: Production Max-Fit Nested 122 x 244 cm Sheet (26 Windshields + 20 GAR TURBO D, 2.0 mm kerf, zero collisions, all internal holes preserved).</em>
</p>

---

## 🧠 TypeSafe Jev System One AI Intelligence Integration

EasyNest v2 integrates **TypeSafe's Jev Foundation Model** ([docs.typesafe.ai](https://docs.typesafe.ai)) for sub-50ms calibrated industrial judgments across 5 core pillars where deterministic algorithms fall short:

### The 5 Active Jev Pillars:

1. **Pillar 1: Semantic Shape Mating & Affinity Guidance**
   - Deterministic nesting sorts blindly by descending area ($O(N!)$ misses).
   - Jev evaluates topological curvature, aspect ratios, concavity depths, and complementary voids before running the tournament.
   - Recommends the optimal tessellation strategy (e.g. *Honeycomb Staggered* vs *Compact Corridor*) and twin-block clustering.

2. **Pillar 2: Shop Floor Economics & Machine TCO (True Cost per Part)**
   - Deterministic nesting only optimizes for raw geometric Scrap %.
   - Jev factors in true shop floor economics:
     $$\text{Total Cost} = \text{Sheet Material } \$ + \text{Laser Beam Time } \$ + \text{Piercing Wear } \$ - \text{Remnant Salvage } \$$$
     $$\text{Unit Cost} = \frac{\text{Total Cost}}{\text{Total Finished Parts}}$$
   - Evaluates whether squeezing in extra parts is worth the additional laser beam run time on expensive machines ($75/hr+).
   - Crowns the **Jev Economic Champion**.

3. **Pillar 3: Thermal Distortion & Lead-In Piercing Defense**
   - Tight kerfs (e.g. 2.0 mm) on thin materials can cause thermal distortion, burn-through, or tip-up collisions with cutouts.
   - Jev assesses material type (Cast Acrylic, Stainless, Aluminum, Mild Steel) and bridge gaps to recommend assist gas protocols and CAM path interleaving.

4. **Pillar 4: Remnant Off-Cut Sheet Portfolio Allocation**
   - Analyzes residual scrap geometry and corridor widths.
   - Classifies off-cuts into *Reusable Prime Strip*, *Secondary Hobby Stock*, or *Unrecoverable Scrap*.
   - Calculates salvage value to credit back into the manufacturing ledger.

5. **Pillar 5: Semantic CAD Geometry Intent Classification**
   - Classifies vector paths in CAD/CDR files into functional categories: `outer_boundary`, `mounting_aperture`, `decorative_vent`, or `cad_artifact` (stray marks or accidental duplicates).
   - Prevents corrupted nests by validating production readiness.

### Economic & TCO CLI Flags

```bash
python3 industrial_nest.py windshield.svg "GAR TURBO D.cdr" \
  --sheet "122x244cm" \
  --kerf 2.0 \
  --margin 5.0 \
  --sheet-cost 35.0 \
  --machine-rate 75.0 \
  --material "3mm Cast Acrylic"
```

| Flag | Default | Description |
|------|---------|-------------|
| `--sheet-cost` | `35.0` ($) | Raw sheet material cost in USD |
| `--machine-rate` | `75.0` ($/hr) | CNC laser / router hourly rate |
| `--material` | `"3mm Cast Acrylic"` | Material description for thermal & assist gas advice |
| `--typesafe-key` | Env `TYPESAFE_API_KEY` | Custom TypeSafe API key |
| `--no-jev` | `False` | Disable Jev AI advisory engine |


