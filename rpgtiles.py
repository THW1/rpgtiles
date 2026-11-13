#!/usr/bin/env python3
# === CONTEXT ===
# Purpose:     Generate 3D-printable RPG scene tiles (floor + optional walls, doors,
#              windows, low walls, corner posts) from a short text spec, so a tile
#              set can be grown over time and combined freely on a common grid.
# Decisions:   - Grid 25.4 mm (1 inch) by default: fits 25 mm miniature bases and lines
#                up with OpenLOCK-style 1" tiles. OpenForge-style 25.0 mm via --grid.
#              - "Wall on tile": walls stand INSIDE the tile along an edge, so every
#                tile keeps an exact n x m grid footprint and any tile fits any other.
#                Rejected "wall between tiles" - breaks the grid or needs extra parts.
#              - Wall thickness = grid/4: two tiles back to back give a grid/2 wall.
#              - Connector is genderless: every cell edge gets the same dovetail pocket
#                (open to the bottom) and tiles are joined by a loose bow-tie clip.
#                Rejected male/female tabs - they restrict rotation and neighbours.
#                NOT OpenLOCK-compatible: that clip geometry is not reproduced here.
#              - manifold3d for booleans (guaranteed watertight), trimesh only for export.
# Constraints: Python 3, manifold3d, trimesh (3MF export additionally needs lxml, networkx).
# Invariants:  - Pocket positions are on the NOMINAL grid (cell-edge midpoints); only the
#                outer footprint is shrunk by `fit`. Changing this breaks tile alignment.
#              - Pocket and clip must come from the same dovetail() profile; the clip is
#                the profile mirrored and shrunk by `clearance`.
#              - Spec side order is N,E,S,W; N/S strings read west->east, E/W south->north.
#              - Floor styles must keep the cell boundaries readable (they are the game grid).
#              - Wall styles are cut BEFORE door/window openings, so openings stay clean.
#              - Surface styles are shallow CUTS only (grooves / recessed panels), never
#                added relief: nothing can protrude past the footprint, walls print
#                without supports, and one mechanism serves both wall faces.
#                Rejected heightmap displacement for now - large meshes, slow booleans.
#              - Style patterns are anchored to the grid (brick = grid/2 x grid/4, boards
#                = grid/4), so they continue across tile joints in any combination.
#              - Corner posts are addressed per cell ("1.0ne"), not only per tile corner:
#                rpgscene.py needs posts on any cell of a tile to close inner room corners.
# Related:     rpgscene.py imports this module (parse_spec, make_tile, CLI helpers) and
#              generates the spec strings; the spec grammar is the contract between them.
# Tests:       test_rpgtiles.py (python3 -m pytest -q test_rpgtiles.py)
# Limitations: Styles are stylised line patterns, not rough/organic surfaces. Walled 1-wide
#              corridors leave < 1 cell of floor - use >= 2 cells between facing walls.
#              Clip clearance is untested on a real printer; tune --clearance.
# === END CONTEXT ===
"""
Tile spec:  <cols>x<rows>:<N>,<E>,<S>,<W>[:<posts>]

  Each side is one letter per cell, or a single letter repeated for all cells:
    O open   W wall   D wall with doorway   A wall with arched doorway
    F wall with window   L low wall
  posts: corner posts, '+'-separated: ne, nw, se, sw for the tile corners, or
         <col>.<row><corner> for a corner of one cell (col/row from 0, south-west cell is 0.0)

Examples:
  2x2:O,O,O,O        plain floor
  2x2:W,O,O,W        room corner (walls north and west)
  3x2:WDW,O,O,O      north wall with a doorway in the middle cell
  2x2:O,O,O,O:ne     floor with a post in the north-east corner (inner room corners)

Usage:
  rpgtiles.py 2x2:W,O,O,W 3x2:WDW,O,O,O --format 3mf
  rpgtiles.py --set starter --out tiles/
  rpgtiles.py --set starter --wall-style brick --floor-style flagstone

Styles (apply to all tiles of a run; the file name carries them):
  --wall-style   plain | brick | planks | timber (beams with recessed panels)
  --floor-style  grid | flagstone | planks
  --seed         varies the random flagstone layout; same seed -> same tile
"""
import argparse
import math
import os
import random
import re
from dataclasses import dataclass

import numpy as np
import trimesh
from manifold3d import CrossSection, Manifold, OpType


@dataclass
class Params:
    grid: float = 25.4          # cell size
    base_t: float = 7.0         # floor slab thickness
    wall_h: float = 40.0        # wall height above the floor
    low_h: float = 12.7         # low wall height above the floor
    wall_t: float = 0.0         # wall thickness; 0 -> grid / 4
    fit: float = 0.1            # footprint shrink per side, so rows of tiles do not grow
    groove_w: float = 0.8       # cell-line groove width
    groove_d: float = 0.4       # cell-line groove depth
    clearance: float = 0.15     # clip-to-pocket gap per side
    # dovetail connector: narrow at the tile edge, wide inside the tile
    neck_w: float = 4.0
    tail_w: float = 7.0
    tail_len: float = 5.0
    pocket_h: float = 3.0       # pocket height from the tile bottom (ceiling is a short bridge)
    wall_style: str = "plain"
    floor_style: str = "grid"
    tex_w: float = 0.8          # style groove width (2 nozzle widths at 0.4 mm)
    tex_d: float = 0.5          # style groove / panel depth
    beam_w: float = 3.0         # timber beam width
    seed: int = 0

    def __post_init__(self):
        if not self.wall_t:
            self.wall_t = self.grid / 4
        # below this the door/arch/window openings degenerate to slivers
        if self.wall_h < 12:
            raise ValueError("wall_h must be at least 12 mm")


STARTER_SET = [
    "2x2:O,O,O,O",      # floor
    "4x4:O,O,O,O",      # large floor
    "2x2:W,O,O,O",      # straight wall
    "2x4:O,O,O,W",      # long wall
    "2x2:W,O,O,W",      # room corner
    "2x2:W,O,W,O",      # corridor, 2 wide (floor between walls = 1.5 cells)
    "2x2:W,W,O,W",      # dead end
    "2x2:WD,O,O,O",     # wall with doorway
    "2x2:WF,O,O,O",     # wall with window
    "2x2:WA,O,O,O",     # wall with arched doorway
    "2x2:L,O,O,O",      # low wall / ruin
    "2x2:O,O,O,O:ne",   # inner-corner post
]


def box(x0, y0, z0, x1, y1, z1):
    return Manifold.cube([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])


# CTX: Single source of the connector profile, so pocket and clip cannot drift apart.
# Used-by: rpgtiles.py::make_tile, rpgtiles.py::make_clip
# Invariant: the clip is this profile mirrored and shrunk by Params.clearance only.
def dovetail(p, v0, v1):
    """Half bow-tie in local (u along edge, v into tile) coordinates."""
    hn, ht = p.neck_w / 2, p.tail_w / 2
    # v0 < 0 lets the pocket cut cleanly through the tile edge
    return [(-hn, v0), (hn, v0), (ht, v1), (-ht, v1)]


# CTX: Defines the spec grammar; the planner builds its strings to match this parser.
# Used-by: rpgtiles.py::make_tile, rpgscene.py::to_str, test_rpgtiles.py::as_tile
# Invariant: posts come back as (col, row, corner) with the tile-corner shorthand resolved.
def parse_spec(spec):
    parts = spec.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"bad spec '{spec}'")
    nx, ny = (int(v) for v in parts[0].lower().split("x"))
    if nx < 1 or ny < 1:
        raise ValueError(f"'{spec}': tile size must be at least 1x1")
    sides = parts[1].upper().split(",")
    if len(sides) != 4:
        raise ValueError(f"'{spec}': need 4 sides N,E,S,W")
    out = []
    for s, n in zip(sides, (nx, ny, nx, ny)):
        s = s * n if len(s) == 1 else s
        if len(s) != n or set(s) - set("OWDAFL"):
            raise ValueError(f"'{spec}': side '{s}' must be {n} letters from OWDAFL")
        out.append(s)
    posts = []   # (col, row, corner)
    for tok in parts[2].lower().split("+") if len(parts) == 3 else []:
        m = re.fullmatch(r"(?:(\d+)\.(\d+))?(ne|nw|se|sw)", tok)
        if not m:
            raise ValueError(f"'{spec}': bad post '{tok}'")
        c = int(m[1]) if m[1] else (nx - 1 if "e" in m[3] else 0)
        r = int(m[2]) if m[1] else (ny - 1 if "n" in m[3] else 0)
        if c >= nx or r >= ny:
            raise ValueError(f"'{spec}': post '{tok}' is outside the tile")
        posts.append((c, r, m[3]))
    return nx, ny, out, posts


# CTX: Fixes side order and reading direction for every per-side loop.
# Used-by: rpgtiles.py::make_tile | Depends: rpgscene.py::rotate mirrors this convention
# Invariant: order N,E,S,W; a change here must be repeated in rpgscene.py::rotate.
def side_frames(nx, ny, g):
    """Per side: (origin, tangent, inward normal, cell count), matching spec order N,E,S,W."""
    X, Y = nx * g, ny * g
    return [
        ((0, Y), (1, 0), (0, -1), nx),   # N, west -> east
        ((X, 0), (0, 1), (-1, 0), ny),   # E, south -> north
        ((0, 0), (1, 0), (0, 1), nx),    # S, west -> east
        ((0, 0), (0, 1), (1, 0), ny),    # W, south -> north
    ]


def local_box(o, t, n, u0, u1, v0, v1, z0, z1):
    xs = [o[0] + u * t[0] + v * n[0] for u in (u0, u1) for v in (v0, v1)]
    ys = [o[1] + u * t[1] + v * n[1] for u in (u0, u1) for v in (v0, v1)]
    return box(min(xs), min(ys), z0, max(xs), max(ys), z1)


def wall_cuts(o, t, n, i, h, p):
    """Shallow cutters for one wall cell, applied identically to both wall faces."""
    g, z0, w, d = p.grid, p.base_t, p.tex_w, p.tex_d
    u0, u1 = i * g, (i + 1) * g
    cuts = []

    def both(a, b, za, zb):
        for v0, v1 in ((-1, d), (p.wall_t - d, p.wall_t + 1)):
            cuts.append(local_box(o, t, n, a, b, v0, v1, za, zb))

    if p.wall_style == "brick":
        bl, bh = g / 2, g / 4
        for k in range(math.ceil(h / bh)):
            z = z0 + k * bh
            if k:
                both(u0, u1, z - w / 2, z + w / 2)
            for j in range(3):   # running bond: odd courses shifted by half a brick
                u = u0 + j * bl + (bl / 2 if k % 2 else 0)
                if u <= u1:
                    both(u - w / 2, u + w / 2, z, min(z + bh, z0 + h))
    elif p.wall_style == "planks":
        for j in range(5):
            u = u0 + j * g / 4
            both(u - w / 2, u + w / 2, z0, z0 + h)
    elif p.wall_style == "timber":
        # beams stay at full thickness: posts on cell boundaries, sill, top rail, mid rail
        b = p.beam_w
        levels = [z0 + b, z0 + h - b]
        if h > 6 * b:
            levels[1:1] = [z0 + h / 2 - b / 2, z0 + h / 2 + b / 2]
        for za, zb in zip(levels[::2], levels[1::2]):
            both(u0 + b / 2, u1 - b / 2, za, zb)
    return cuts


def floor_cuts(nx, ny, p, rng):
    """Extra floor grooves on top of the cell lines; thinner, so the grid stays dominant."""
    g, w = p.grid, p.tex_w * 0.6
    z0, z1 = p.base_t - p.groove_d, p.base_t + 1
    cuts = []
    if p.floor_style == "flagstone":
        for cx in range(nx):
            for cy in range(ny):
                x, y = cx * g, cy * g
                split = rng.choice("nxyt")   # none, one cut either way, or a T
                f = rng.uniform(0.35, 0.65) * g
                if split in "xt":
                    cuts.append(box(x + f - w / 2, y, z0, x + f + w / 2, y + g, z1))
                if split == "y":
                    cuts.append(box(x, y + f - w / 2, z0, x + g, y + f + w / 2, z1))
                if split == "t":
                    f2 = rng.uniform(0.35, 0.65) * g
                    cuts.append(box(x, y + f2 - w / 2, z0, x + f, y + f2 + w / 2, z1))
    elif p.floor_style == "planks":
        for k in range(1, ny * 4):
            if k % 4:
                cuts.append(box(-1, k * g / 4 - w / 2, z0, nx * g + 1, k * g / 4 + w / 2, z1))
    return cuts


# CTX: Doorway size shared by the wall opening and the loose door leaf.
# Used-by: rpgtiles.py::wall_cell, rpgtiles.py::make_door
def door_size(p):
    return 0.75 * p.grid, min(34.0, p.wall_h - 5)


def wall_cell(kind, o, t, n, i, p):
    g, z0 = p.grid, p.base_t
    h = p.low_h if kind == "L" else p.wall_h
    wall = local_box(o, t, n, i * g, (i + 1) * g, 0, p.wall_t, z0, z0 + h)
    cuts = wall_cuts(o, t, n, i, h, p)
    if cuts:
        wall -= Manifold.batch_boolean(cuts, OpType.Add)
    mid = (i + 0.5) * g
    w, dh = door_size(p)
    if kind == "D":
        wall -= local_box(o, t, n, mid - w / 2, mid + w / 2, -1, p.wall_t + 1, z0, z0 + dh)
    elif kind == "A":
        # round top instead of a flat lintel: no bridging needed
        wall -= local_box(o, t, n, mid - w / 2, mid + w / 2, -1, p.wall_t + 1, z0, z0 + dh - w / 2)
        cyl = Manifold.cylinder(p.wall_t + 2, w / 2, w / 2, 48, True)
        cyl = cyl.rotate([0, 90, 0] if n[0] else [90, 0, 0])
        wall -= cyl.translate([o[0] + mid * t[0] + p.wall_t / 2 * n[0],
                               o[1] + mid * t[1] + p.wall_t / 2 * n[1], z0 + dh - w / 2])
    elif kind == "F":
        w = 0.5 * g
        wall -= local_box(o, t, n, mid - w / 2, mid + w / 2, -1, p.wall_t + 1,
                          z0 + 0.4 * h, z0 + 0.75 * h)
    return wall


def make_tile(spec, p):
    nx, ny, sides, posts = parse_spec(spec)
    g = p.grid
    X, Y = nx * g, ny * g
    tile = box(p.fit, p.fit, 0, X - p.fit, Y - p.fit, p.base_t)

    # cell-line grooves, including the perimeter so tile joints look like any other line
    zt, hw = p.base_t - p.groove_d, p.groove_w / 2
    for k in range(nx + 1):
        if p.floor_style == "planks" and 0 < k < nx:
            # board ends: staggered by half a cell on alternate board pairs, so boards
            # are one cell long and the cell columns can still be counted
            for j in range(ny * 4):
                x = k * g - (g / 2 if (j // 2) % 2 else 0)
                tile -= box(x - hw, j * g / 4, zt, x + hw, (j + 1) * g / 4, p.base_t + 1)
            continue
        tile -= box(k * g - hw, -1, zt, k * g + hw, Y + 1, p.base_t + 1)
    for k in range(ny + 1):
        tile -= box(-1, k * g - hw, zt, X + 1, k * g + hw, p.base_t + 1)

    extra = floor_cuts(nx, ny, p, random.Random(f"{spec}|{p.seed}"))
    if extra:
        tile -= Manifold.batch_boolean(extra, OpType.Add)

    for (o, t, n, _), side in zip(side_frames(nx, ny, g), sides):
        for i, kind in enumerate(side):
            if kind != "O":
                tile += wall_cell(kind, o, t, n, i, p)
            # one pocket per cell-edge midpoint, on walled edges too (rooms back to back)
            u = (i + 0.5) * g
            pts = [(o[0] + (u + a) * t[0] + b * n[0], o[1] + (u + a) * t[1] + b * n[1])
                   for a, b in dovetail(p, -0.5, p.tail_len)]
            if t[0] * n[1] - t[1] * n[0] < 0:   # keep the polygon counter-clockwise
                pts.reverse()
            tile -= CrossSection([pts]).extrude(p.pocket_h + 1).translate([0, 0, -1])

    for c, r, corner in posts:
        x = (c + 1) * g - p.wall_t if "e" in corner else c * g
        y = (r + 1) * g - p.wall_t if "n" in corner else r * g
        tile += box(x, y, p.base_t, x + p.wall_t, y + p.wall_t, p.base_t + p.wall_h)

    # walls and posts sit on the nominal edge; trim them to the shrunk footprint
    return tile ^ box(p.fit, p.fit, -1, X - p.fit, Y - p.fit, 1000)


def make_clip(p):
    half = dovetail(p, 0, p.tail_len)
    bowtie = half + [(-u, -v) for u, v in half[2:]]   # mirror the tail to the other tile
    cs = CrossSection([bowtie]).offset(-p.clearance)
    return cs.extrude(p.pocket_h - 0.4)


def make_door(p):
    """Loose door leaf for 'D' doorways: stands on a foot plate, printed upright as is."""
    w, dh = door_size(p)
    w, h, th, foot = w - 0.6, dh - 0.6, 2.0, 1.2
    door = box(-w / 2, -th / 2, 0, w / 2, th / 2, h) + box(-w / 2, -5, 0, w / 2, 5, foot)
    for j in (-1, 0, 1):   # board lines on both faces
        for y0, y1 in ((-th, -th / 2 + 0.4), (th / 2 - 0.4, th)):
            door -= box(j * w / 4 - 0.4, y0, foot, j * w / 4 + 0.4, y1, h + 1)
    return door


def to_trimesh(m):
    mesh = m.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3],
                           faces=np.asarray(mesh.tri_verts), process=False)


def add_common_args(ap):
    ap.add_argument("--out", default="tiles", help="output directory")
    ap.add_argument("--format", choices=["stl", "3mf"], default="stl")
    styles = {"wall_style": ["plain", "brick", "planks", "timber"],
              "floor_style": ["grid", "flagstone", "planks"]}
    for name, default in vars(Params()).items():
        ap.add_argument("--" + name.replace("_", "-"), type=type(default), default=None,
                        choices=styles.get(name),
                        help="default grid/4" if name == "wall_t" else f"default {default}")


def params_from_args(a):
    return Params(**{k: v for k, v in vars(a).items()
                     if k in Params.__dataclass_fields__ and v is not None})


def tile_name(spec, p):
    name = "tile_" + spec.replace(":", "_").replace(",", "-").replace("+", "-")
    if (p.wall_style, p.floor_style) != ("plain", "grid"):
        name += f"_{p.wall_style}-{p.floor_style}"
    if p.seed:
        name += f"_s{p.seed}"
    return name


def export_parts(specs, p, out, fmt):
    """Write the accessories (clip, door leaf) and one file per tile spec."""
    os.makedirs(out, exist_ok=True)
    parts = [("clip", make_clip(p)), ("door", make_door(p))]
    parts += [(tile_name(s, p), make_tile(s, p)) for s in specs]
    for name, m in parts:
        mesh = to_trimesh(m)
        path = os.path.join(out, f"{name}.{fmt}")
        mesh.export(path)
        x, y, z = mesh.extents
        print(f"{path}: {x:.1f} x {y:.1f} x {z:.1f} mm, {mesh.volume / 1000:.1f} cm3, "
              f"watertight={mesh.is_watertight}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("specs", nargs="*", help="tile specs, e.g. 2x2:W,O,O,W")
    ap.add_argument("--set", choices=["starter"], help="generate a predefined set")
    add_common_args(ap)
    a = ap.parse_args()
    specs = a.specs + (STARTER_SET if a.set else [])
    if not specs:
        ap.error("give at least one tile spec or --set starter")
    try:
        export_parts(specs, params_from_args(a), a.out, a.format)
    except ValueError as e:
        ap.error(str(e))


if __name__ == "__main__":
    main()
