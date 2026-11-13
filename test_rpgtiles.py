"""Run: python3 -m pytest -q test_rpgtiles.py"""
import pytest

import rpgscene
import rpgtiles
from rpgtiles import Params, make_tile, parse_spec

P = Params()


def as_tile(spec):
    nx, ny, sides, posts = parse_spec(spec)
    return nx, ny, sides, [f"{c}.{r}{k}" for c, r, k in posts]


@pytest.mark.parametrize("spec", [
    "2x2:O,O,O,O", "2x2:W,O,O,W", "3x2:WDW,O,L,WF", "1x3:A,WWL,O,O:0.2ne",
    "2x3:WF,OOW,O,O:1.0se+0.2nw+ne",
])
def test_rotate_matches_mesh_rotation(spec):
    """rotate() on the spec must give the same solid as turning the mesh 90 deg ccw."""
    t = as_tile(spec)
    turned = make_tile(spec, P).rotate([0, 0, 90]).translate([t[1] * P.grid, 0, 0])
    from_spec = make_tile(rpgscene.to_str(*rpgscene.rotate(*t)), P)
    assert (turned - from_spec).volume() + (from_spec - turned).volume() < 1e-3


def test_four_turns_are_identity():
    t = as_tile("2x3:WF,OOW,O,O:1.0se+0.2nw")
    r = t
    for _ in range(4):
        r = rpgscene.rotate(*r)
    assert rpgscene.to_str(*r) == rpgscene.to_str(*t)


def test_canonical_is_rotation_invariant():
    t = as_tile("2x2:WD,O,O,W:1.0se")
    names = set()
    for _ in range(4):
        names.add(rpgscene.canonical(t)[0])
        t = rpgscene.rotate(*t)
    assert len(names) == 1


def test_blank_row_inside_map_is_kept():
    cells = rpgscene.read_map("aa\naa\n\n\nbb\nbb\n")
    assert {r for _, r in cells} == {0, 1, 4, 5}


def test_map_indent_does_not_shift_lattice():
    a = rpgscene.plan(rpgscene.read_map("aaaa\naaaa\n"), 2)
    b = rpgscene.plan(rpgscene.read_map("   aaaa\n   aaaa\n"), 2)
    assert a == b and len(a) == 2


def test_map_rejects_unknown_characters():
    with pytest.raises(ValueError):
        rpgscene.read_map("a#a\n")


def test_door_between_rooms_is_on_both_tiles():
    cells = rpgscene.read_map("aAbb\naabb\n")
    assert rpgscene.edge(cells, (1, 1), "e") == "D" and rpgscene.edge(cells, (2, 1), "w") == "D"
    assert rpgscene.edge(cells, (1, 0), "e") == "W"


def test_inner_corner_gets_post():
    cells = rpgscene.read_map("aa  \naaaa\n")
    assert rpgscene.cell_posts(cells, (1, 0)) == ["ne"]
    assert rpgscene.cell_posts(cells, (2, 0)) == []   # wall on its own north edge instead


@pytest.mark.parametrize("bad", ["2x2:W,O,O", "2x2:WX,O,O,O", "0x2:O,O,O,O", "2x2:O,O,O,O:5.5ne"])
def test_bad_specs_are_rejected(bad):
    with pytest.raises(ValueError):
        parse_spec(bad)


@pytest.mark.parametrize("ws", ["plain", "brick", "planks", "timber"])
@pytest.mark.parametrize("fs", ["grid", "flagstone", "planks"])
def test_styles_give_one_closed_solid(ws, fs):
    m = make_tile("2x2:WD,A,L,WF:1.0se", Params(wall_style=ws, floor_style=fs))
    assert m.volume() > 0 and len(m.decompose()) == 1
