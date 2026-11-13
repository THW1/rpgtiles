#!/usr/bin/env python3
# === CONTEXT ===
# Purpose:     Turn a text map of a scene into the list of tiles needed to build it:
#              which specs, how many of each, and where each one goes.
# Decisions:   - The map marks ROOMS, not walls: walls are derived wherever a room cell
#                borders void or another room. That matches the wall-on-tile scheme of
#                rpgtiles.py (walls live on tile edges, there are no wall cells).
#              - Tiles are cut on a fixed lattice of --tile x --tile cells from the map's
#                south-west corner. A lattice block that is incomplete or spans two rooms
#                falls back to 1x1 tiles. Rejected a general rectangle packing - harder to
#                predict, and rooms aligned to the lattice already give few tile types.
#              - Specs are reduced to a canonical rotation, so tiles that differ only by
#                rotation are printed once and counted together.
# Invariants:  - rotate() must match a 90 degree counter-clockwise turn of the mesh
#                ((x, y) -> (-y, x)); the placement table reports turns in that sense.
#              - Side order and reading direction follow rpgtiles.py (N,E,S,W; N/S west->east,
#                E/W south->north).
# Related:     Imports rpgtiles.py for spec parsing checks, parameters and export.
# Tests:       test_rpgtiles.py (rotation against mesh rotation, map parsing, doors, posts)
# Limitations: No walls inside a tile, so a wall between two rooms must fall on the tile
#              lattice or the affected blocks become 1x1 tiles.
# === END CONTEXT ===
"""
Map format (text file, north at the top, one character per cell):

  space   nothing
  a-z     a room; cells with the same letter form one room. Walls are added where a
          room borders nothing or another room.
  A-Z     same room, and this cell gets a doorway toward the neighbouring other room
          (toward the outside if there is no other room next to it)
  .       open ground: floor without walls

Example:
  aaaa    bbbb
  aaaAccccBbbb
  aaaaccccbbbb

Usage:
  rpgscene.py map.txt --out scene/ --wall-style brick --floor-style flagstone
"""
import argparse
import os
from collections import Counter

import rpgtiles

NB = {"n": (0, 1), "e": (1, 0), "s": (0, -1), "w": (-1, 0)}
TURN = {"ne": "nw", "nw": "sw", "sw": "se", "se": "ne"}


def read_map(text):
    rows = text.expandtabs().splitlines()
    rows.reverse()   # row 0 = south
    cells = {(c, r): ch for r, ln in enumerate(rows) for c, ch in enumerate(ln) if ch != " "}
    bad = {ch for ch in cells.values() if not (ch.isalpha() or ch == ".")}
    if bad or not cells:
        raise ValueError(f"map needs letters, '.' and spaces only; found {sorted(bad)}")
    # shift to (0, 0): blank rows inside the map are kept, indentation and blank
    # lines around it must not move the tile lattice
    c0, r0 = min(c for c, _ in cells), min(r for _, r in cells)
    return {(c - c0, r - r0): ch for (c, r), ch in cells.items()}


def room(cells, pos):
    ch = cells.get(pos)
    return ch.lower() if ch else None


# CTX: Decides wall / door / open per cell edge; both sides of a shared edge must agree.
# Used-by: rpgscene.py::block_spec
# Invariant: a door between two rooms is reported by BOTH cells, or the openings do not line up.
def edge(cells, pos, d):
    """Feature letter for one edge of one cell."""
    me, here = cells[pos], room(cells, pos)
    nbs = {k: (pos[0] + v[0], pos[1] + v[1]) for k, v in NB.items()}
    there = room(cells, nbs[d])
    if here == "." or there == here:
        return "O"
    if there:   # another room (or open ground): door if either side asks for one
        return "D" if me.isupper() or cells[nbs[d]].isupper() else "W"
    has_other = any(room(cells, q) not in (None, here) for q in nbs.values())
    return "D" if me.isupper() and not has_other else "W"


def cell_posts(cells, pos):
    """Inner room corners: both neighbours are the same room but the diagonal is not."""
    here, (c, r) = room(cells, pos), pos
    if here == ".":
        return []
    out = []
    for corner in TURN:
        dx, dy = (1 if "e" in corner else -1), (1 if "n" in corner else -1)
        if (room(cells, (c + dx, r)) == here and room(cells, (c, r + dy)) == here
                and room(cells, (c + dx, r + dy)) != here):
            out.append(corner)
    return out


def block_spec(cells, c0, r0, n):
    side = lambda d, cs: "".join(edge(cells, q, d) for q in cs)
    cols, rws = range(c0, c0 + n), range(r0, r0 + n)
    sides = [side("n", [(c, r0 + n - 1) for c in cols]), side("e", [(c0 + n - 1, r) for r in rws]),
             side("s", [(c, r0) for c in cols]), side("w", [(c0, r) for r in rws])]
    posts = [f"{c - c0}.{r - r0}{k}" for c in cols for r in rws for k in cell_posts(cells, (c, r))]
    return n, n, sides, posts


def to_str(nx, ny, sides, posts):
    s = f"{nx}x{ny}:" + ",".join(sides)
    return s + (":" + "+".join(sorted(posts)) if posts else "")


# CTX: Spec-level equivalent of turning the printed tile; drives counting and placement.
# Used-by: rpgscene.py::canonical | Depends: rpgtiles.py::side_frames
# Invariant: equals a 90 deg ccw mesh turn (test_rpgtiles.py::test_rotate_matches_mesh_rotation).
def rotate(nx, ny, sides, posts):
    """One 90 degree counter-clockwise turn, (x, y) -> (-y, x)."""
    n, e, s, w = sides
    new_posts = []
    for tok in posts:
        cr, k = tok[:-2], tok[-2:]
        c, r = (int(v) for v in cr.split("."))
        new_posts.append(f"{ny - 1 - r}.{c}{TURN[k]}")
    return ny, nx, [e[::-1], s, w[::-1], n], new_posts


def canonical(tile):
    """Smallest spec string among the 4 rotations, and the turns from it to `tile`."""
    best, t = None, tile
    for j in range(4):
        if best is None or to_str(*t) < best[0]:
            best = (to_str(*t), (4 - j) % 4)
        t = rotate(*t)
    return best


def plan(cells, n):
    """-> list of (canonical spec, ccw turns, col, row) with col/row = south-west cell."""
    placed, done = [], set()
    for (c, r) in sorted(cells):
        if (c, r) in done:
            continue
        c0, r0 = c - c % n, r - r % n
        block = [(x, y) for x in range(c0, c0 + n) for y in range(r0, r0 + n)]
        whole = len({room(cells, q) for q in block}) == 1 and block[0] in cells
        if whole and n > 1:
            done.update(block)
            placed.append((*canonical(block_spec(cells, c0, r0, n)), c0, r0))
        else:
            done.add((c, r))
            placed.append((*canonical(block_spec(cells, c, r, 1)), c, r))
    return placed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("map", help="text file with the scene map")
    ap.add_argument("--tile", type=int, default=2, help="tile edge length in cells (default 2)")
    rpgtiles.add_common_args(ap)
    a = ap.parse_args()
    try:
        p = rpgtiles.params_from_args(a)
    except ValueError as e:
        ap.error(str(e))

    try:
        placed = plan(read_map(open(a.map).read()), a.tile)
    except ValueError as e:
        ap.error(str(e))
    counts = Counter(spec for spec, *_ in placed)
    doors = sum(spec.split(":")[1].count("D") * k for spec, k in counts.items())
    lines = ["Print list:"]
    lines += [f"  {k:3d} x {rpgtiles.tile_name(s, p)}" for s, k in sorted(counts.items())]
    lines += [f"  up to {doors} x door (two back-to-back doorways share one)",
              "  clip: one per pair of neighbouring tiles is enough", "",
              "Placement (col,row = south-west cell of the tile, turns = 90 deg counter-clockwise):"]
    lines += [f"  {c:3d},{r:<3d} {t} turn(s)  {s}" for s, t, c, r in sorted(placed, key=lambda x: (x[3], x[2]))]
    print("\n".join(lines))
    rpgtiles.export_parts(sorted(counts), p, a.out, a.format)
    with open(os.path.join(a.out, "scene_plan.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
