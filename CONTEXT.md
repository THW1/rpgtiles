# Project context: rpgtiles
Verified-at: initial commit

## Purpose
Generate 3D-printable, clip-connected RPG scene tiles (floor plus walls, doors, arches,
windows, low walls, posts) from short text specs, and plan a whole scene from a text map.
Single colour, single level.

## Layout (load-bearing files only)
```
rpgtiles.py        tile, clip and door-leaf geometry; spec grammar; styles; CLI
rpgscene.py        text map -> tile specs, counts and placement; CLI
test_rpgtiles.py   pytest suite for both
example_map.txt    sample map for rpgscene.py
```

## Flow
- `rpgtiles.py::main` -> `rpgtiles.py::export_parts` -> `rpgtiles.py::make_tile` per spec
  (plus `rpgtiles.py::make_clip`, `rpgtiles.py::make_door`) -> STL/3MF files.
- `rpgscene.py::main` -> `rpgscene.py::read_map` -> `rpgscene.py::plan` ->
  `rpgscene.py::canonical` -> `rpgtiles.py::export_parts`, and writes `scene_plan.txt`.

## Relationships
- rpgscene.py::to_str -> rpgtiles.py::parse_spec (spec grammar is the contract)
- rpgscene.py::rotate -> rpgtiles.py::side_frames (side order and reading direction)
- rpgscene.py::main -> rpgtiles.py::add_common_args, rpgtiles.py::params_from_args,
  rpgtiles.py::tile_name (shared CLI options and file naming)
- rpgtiles.py::make_clip <-> rpgtiles.py::make_tile via rpgtiles.py::dovetail (connector fit)
- rpgtiles.py::make_door <-> rpgtiles.py::wall_cell via rpgtiles.py::door_size

## Run and test
```
pip install manifold3d trimesh lxml networkx pytest
python3 rpgtiles.py --set starter --wall-style brick --floor-style flagstone --out tiles/
python3 rpgscene.py example_map.txt --out scene/
python3 -m pytest -q test_rpgtiles.py
```

## Conventions
- Units are mm. Sides are N,E,S,W; N/S read west->east, E/W south->north.
- Map rows: north at the top; internally row 0 is the southernmost row.
- Styles apply to a whole run and are part of the file name.

## Known limitations
See the `Limitations` fields in the headers of `rpgtiles.py` and `rpgscene.py`.
Nothing has been printed yet: clip and door-leaf clearances are untested.
