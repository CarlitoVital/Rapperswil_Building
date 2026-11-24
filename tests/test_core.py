"""Checks for the logic everything else rests on: reading the 3D model, the
dating rules, the rasteriser, the timeline and the colour ramp."""
import struct

import numpy as np
import pandas as pd
import shapely

from pipeline import config as C
from pipeline import date_buildings as D
from pipeline.wkb import tin_to_multipolygon
from render import raster as R
from render.animate import Timeline
from render.overlay import Ramp


# ----------------------------------------------------------------------------
# reading swissBUILDINGS3D
# ----------------------------------------------------------------------------
def _tin_wkb(triangles, code=1016, member=1017):
    """ISO WKB of a TIN Z made of the given triangles (each 3 xyz points)."""
    out = struct.pack("<BII", 1, code, len(triangles))
    for tri in triangles:
        ring = list(tri) + [tri[0]]
        out += struct.pack("<BIII", 1, member, 1, len(ring))
        for p in ring:
            out += struct.pack("<ddd", *p)
    return out


def test_tin_becomes_a_readable_multipolygon():
    tris = [[(0, 0, 400), (10, 0, 400), (10, 10, 400)], [(0, 0, 400), (10, 10, 400), (0, 10, 400)]]
    g = shapely.from_wkb(tin_to_multipolygon(_tin_wkb(tris)))
    assert g.geom_type == "MultiPolygon" and shapely.get_num_geometries(g) == 2
    assert shapely.union_all(shapely.get_parts(shapely.force_2d(g))).area == 100.0


def test_legacy_25d_flag_and_plain_polygons_pass():
    tris = [[(0, 0, 1), (4, 0, 1), (0, 4, 1)]]
    g = shapely.from_wkb(tin_to_multipolygon(_tin_wkb(tris, code=0x80000010, member=0x80000011)))
    assert g.geom_type == "MultiPolygon" and g.has_z
    poly = shapely.to_wkb(shapely.box(0, 0, 1, 1))
    assert tin_to_multipolygon(poly) == poly


# ----------------------------------------------------------------------------
# dating rules
# ----------------------------------------------------------------------------
def test_hash_is_deterministic_and_uniform():
    k = np.arange(1, 50001, dtype=np.int64) * 7919
    h = D.hash01(k, 3)
    assert np.array_equal(h, D.hash01(k, 3))
    assert not np.array_equal(h, D.hash01(k, 4))
    assert h.min() >= 0 and h.max() < 1
    assert np.abs(np.histogram(h, 10, (0, 1))[0] / len(h) - 0.1).max() < 0.01


def test_placeholder_year_is_detected_and_ordinary_peaks_are_not():
    years = [1919] * 145 + [1920] * 12 + [1921] * 2 + [1922] * 1 + [1923] * 5
    years += [2001] * 103 + [1998] * 56 + [1999] * 52 + [2000] * 44 + [2002] * 83 + [2003] * 65
    reg = pd.DataFrame(dict(GBAUJ=np.array(years, float)))
    assert D.placeholder_years(reg) == {1919}


def test_periods_cover_every_year_once():
    for y in range(1000, C.YEAR_END + 1):
        code = D.period_of_year(y)
        lo, hi = D.period_bounds(code)
        assert lo <= y <= hi


def test_estimate_follows_the_neighbours_of_the_same_period():
    # an estate of 1964 next to the target, a 1979 estate (other period) even closer
    d_xy = np.array([[0, 10], [10, 0], [-10, 0], [0, -10], [3, 3], [-3, -3]], float)
    d_year = np.array([1964, 1964, 1965, 1963, 1979, 1979])
    year, nb = D.impute(np.array([[0.0, 0.0]]), np.array([8014]), np.array([42]), d_xy, d_year)
    assert nb[0] and year[0] == 1964


def test_estimate_falls_back_to_the_period_distribution():
    d_xy = np.array([[5000.0, 0.0], [5000.0, 10.0], [5000.0, 20.0]])
    d_year = np.array([1962, 1966, 1969])
    year, nb = D.impute(np.array([[0.0, 0.0]] * 50), np.full(50, 8014), np.arange(50),
                        d_xy, d_year)
    assert not nb.any()
    assert set(year) <= {1962, 1966, 1969} and len(set(year)) > 1


def test_leave_one_out_never_uses_the_building_itself():
    d_xy = np.array([[0.0, 0.0], [500.0, 0.0], [510.0, 0.0], [520.0, 0.0]])
    d_year = np.array([1961, 1970, 1970, 1970])
    year, nb = D.impute(d_xy[:1], np.array([8014]), np.array([1]), d_xy, d_year,
                        exclude_self=True)
    assert not nb[0]                       # its own record must not count as a neighbour
    assert year[0] == 1970                 # drawn from the others


def test_assign_years_classes():
    reg = pd.DataFrame(dict(
        EGID=[1, 2, 3, 4, 5, 6],
        GBAUJ=[1000.0, 1975.0, np.nan, np.nan, np.nan, 2024.0],
        GBAUM=[np.nan, 6.0, np.nan, np.nan, np.nan, np.nan],
        GBAUP=[8011.0, 8015.0, 8011.0, 8015.0, np.nan, 8023.0],
        GKODE=[0.0, 10.0, 20.0, 15.0, 30.0, 40.0], GKODN=[0.0] * 6))
    out = D.assign_years(reg)
    assert out.kind.tolist() == [D.STOCK, D.EXACT, D.STOCK, D.DISTRIBUTION, D.UNDATED, D.EXACT]
    assert out.year.tolist()[:3] == [C.YEAR_STOCK - 1, 1975, C.YEAR_STOCK - 1]
    assert 1971 <= out.year[3] <= 1980
    assert abs(out.frac[1] - 5.5 / 12) < 1e-9          # June


# ----------------------------------------------------------------------------
# rasteriser
# ----------------------------------------------------------------------------
def _buffers(w=100, h=100):
    return (np.zeros((h, w, 3), np.float32), np.zeros((h, w), np.float32),
            np.zeros((h, w), np.float32))


def test_two_triangles_tile_a_square_without_gaps():
    color, depth, emis = _buffers()
    for tri in ((0.0, 0.0, 100.0, 0.0, 0.0, 100.0), (100.0, 0.0, 0.0, 100.0, 100.0, 100.0)):
        x0, y0, x1, y1, x2, y2 = tri
        R._tri(color, depth, emis, 100, 100, x0, y0, 1.0, x1, y1, 1.0, x2, y2, 1.0,
               1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0)
    assert (depth > 0).all()


def test_nearer_triangle_wins_regardless_of_order():
    for order in ((1.0, 2.0), (2.0, 1.0)):
        color, depth, emis = _buffers()
        for inv_w in order:
            R._tri(color, depth, emis, 100, 100, 0.0, 0.0, inv_w, 100.0, 0.0, inv_w,
                   0.0, 100.0, inv_w, inv_w, 0.0, 0.0, inv_w, 0.0, 0.0, inv_w, 0.0, 0.0, 0.0)
        assert color[10, 10, 0] == 2.0


def _one_building(grow, height=30.0):
    W = H = 200
    color, depth, emis = _buffers(W, H)
    ring = np.array([[-20, -20], [20, -20], [20, 20], [-20, 20]], np.float32)  # CCW
    M, _ = R.orbit_camera((0.0, 0.0, 0.0), 20.0, 50.0, 400.0, 30.0, 1.0)
    R.draw_buildings(color, depth, emis, np.ascontiguousarray(M), W, H,
                     ring, np.array([0, 4]), np.array([[0, 1, 2], [0, 2, 3]], np.int32),
                     np.array([0, 2]), np.zeros(1, np.float32), np.full(1, height, np.float32),
                     0, 1, np.full(1, grow, np.float32), np.ones((1, 3), np.float32),
                     np.zeros(1, np.float32), 0.0, 1.0, 1.0, 0.0, 1.0,
                     0.7, 0.7, 0.5, 0.5, 0.5, 1.2, 0.0)
    return color, depth


def test_building_is_drawn_lit_and_grows():
    _, hidden = _one_building(0.0)
    assert (hidden > 0).sum() == 0
    _, half = _one_building(0.5)
    color_full, full = _one_building(1.0)
    assert 0 < (half > 0).sum() < (full > 0).sum()
    lit = color_full[full > 0]
    assert lit.max() > 1.0 and lit.min() < 0.6


# ----------------------------------------------------------------------------
# timeline and colour
# ----------------------------------------------------------------------------
def test_timeline_roundtrip_and_order():
    tl = Timeline(C.YEAR_STOCK - 1, C.YEAR_END)
    for y in (1919.0, 1964.5, 2026.99):
        assert abs(tl.year_pos(tl.tau_of(y)) - y) < 1e-9
    assert 0 < tl.t_stock < tl.t_years < tl.t_reveal < tl.t_end
    assert tl.year_pos(tl.t_years) == float(C.YEAR_STOCK)


def test_ramp_gets_lighter_with_time():
    ramp = Ramp()
    lum = [float(ramp.linear(y) @ [0.2126, 0.7152, 0.0722]) for y in (1918, 1940, 1970, 2000, 2026)]
    assert lum == sorted(lum)
