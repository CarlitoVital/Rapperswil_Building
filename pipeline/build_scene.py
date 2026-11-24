"""Turn the dated buildings and the terrain into compact arrays for the renderer.

Output (``data/scene/*.npy`` + ``scene.json``), all in a local metric frame:
x = metres east, y = metres north of ``config.ORIGIN`` (LV95), z = metres a.s.l.

Terrain   two regular height grids - a fine one (10 m) around the town, a
          coarse one (40 m) for the hills and lakes around it - each with an
          albedo from vegetation height (forest) and lake surfaces. The fine
          grid is blended into the coarse one along its rim so the two meshes
          meet without cracks.
Buildings footprints as one flat vertex array with offsets (CSR), a roof
          triangulation, ground level, height and the animation time. Order:
          neighbouring municipalities (static), the pre-1919 stock (old town
          outwards), dated buildings by time, undated last - so "everything
          standing at time t" is a prefix.

Usage:  python -m pipeline.build_scene
"""
from __future__ import annotations

import json
import time

import mapbox_earcut as earcut
import numpy as np
import pyarrow.parquet as pq
import rasterio
import shapely
from scipy import ndimage

from . import config as C
from .date_buildings import CONTEXT, STOCK, UNDATED, UNREGISTERED

OUT = C.ROOT / "data" / "scene"
BLEND = 800.0                        # width of the fine -> coarse blend at the rim (m)

# dark cartographic albedo (sRGB hex)
OPEN_LAND = "#2c3a2f"                # meadows and fields
FOREST = "#172a20"
WATER = "#122438"
LAKE_MIN_AREA = 2.0e5                # m2; flat areas at least this large are lakes


def srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def hex_rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)]) / 255.0


def read_raster(name):
    with rasterio.open(C.RAW / "rasters" / f"{name}.tif") as ds:
        a = ds.read(1).astype(np.float32)
        t = ds.transform
    return a, t


def sample(a, t, x, y, order=1):
    """Bilinear sample of raster ``a`` (affine ``t``) at LV95 x, y (pixel centres)."""
    col = (x - t.c) / t.a - 0.5
    row = (y - t.f) / t.e - 0.5
    return ndimage.map_coordinates(a, [row, col], order=order, mode="nearest")


def lake_mask(z, res):
    """Lakes are perfectly flat in swissALTIRegio: large flat patches are water."""
    gy, gx = np.gradient(z.astype(np.float64))
    flat = (np.abs(gx) < 1e-3) & (np.abs(gy) < 1e-3)
    lab, n = ndimage.label(flat)
    if n == 0:
        return flat
    size = ndimage.sum(flat, lab, np.arange(1, n + 1)) * res * res
    keep = np.concatenate([[False], size >= LAKE_MIN_AREA])
    return ndimage.binary_closing(keep[lab], iterations=2)


def albedo(z, vhm, res):
    forest = np.clip((np.nan_to_num(vhm) - 2.5) / 7.0, 0.0, 1.0)[..., None]
    rgb = (srgb_to_linear(hex_rgb(OPEN_LAND)) * (1 - forest)
           + srgb_to_linear(hex_rgb(FOREST)) * forest)
    lake = lake_mask(z, res)
    rgb[lake] = srgb_to_linear(hex_rgb(WATER))
    sig = 0.8
    rgb = np.stack([ndimage.gaussian_filter(rgb[..., k], sig) for k in range(3)], -1)
    return rgb.astype(np.float32), lake


def build_terrain(ox, oy):
    zf, tf = read_raster("dem_fine")
    vf, _ = read_raster("vhm_fine")
    zc_src, tc = read_raster("dem_coarse")
    vc_src, _ = read_raster("vhm_coarse")
    fr, cr = C.FINE_RES, C.COARSE_RES
    k = int(round(cr / fr))
    # fine lattice = pixel centres of the fine raster, cropped so that its size is a
    # whole number of coarse cells
    ny, nx = zf.shape
    nx, ny = (nx - 1) // k * k + 1, (ny - 1) // k * k + 1
    zf, vf = zf[:ny, :nx], vf[:ny, :nx]
    fx0, fy1 = tf.c + fr / 2, tf.f - fr / 2                     # NW vertex (LV95)
    # coarse lattice aligned with the fine one, covering TERRAIN_BOX
    bx0, by0, bx1, by1 = C.TERRAIN_BOX
    i_left = int(np.floor((fx0 - bx0 - cr / 2) / cr))
    j_top = int(np.floor((by1 - fy1 - cr / 2) / cr))
    cx0, cy1 = fx0 - i_left * cr, fy1 + j_top * cr
    cnx = int(np.floor((bx1 - cr / 2 - cx0) / cr)) + 1
    cny = int(np.floor((cy1 - (by0 + cr / 2)) / cr)) + 1
    gx = cx0 + np.arange(cnx) * cr
    gy = cy1 - np.arange(cny) * cr
    X, Y = np.meshgrid(gx, gy)
    zc = sample(zc_src, tc, X, Y).astype(np.float32)
    vc = sample(vc_src, tc, X, Y).astype(np.float32)
    zc = np.where(np.isfinite(zc), zc, np.nanmin(zc_src))

    out, meta = {}, {}
    for name, z, v, x0, y1, res in (("coarse", zc, vc, cx0, cy1, cr),
                                    ("fine", zf, vf, fx0, fy1, fr)):
        rgb, lake = albedo(z, v, res)
        out[f"{name}_z"], out[f"{name}_albedo"] = z.astype(np.float32), rgb
        out[f"{name}_lake"] = lake
        meta[name] = dict(x0=float(x0 - ox), y1=float(y1 - oy), res=res,
                          nx=int(z.shape[1]), ny=int(z.shape[0]))
        print(f"  terrain {name}: {z.shape[1]} x {z.shape[0]} vertices @ {res:.0f} m, "
              f"z {np.nanmin(z):.0f}..{np.nanmax(z):.0f} m, lakes {lake.mean():.0%}")

    # blend the rim of the fine grid into the (bilinearly upsampled) coarse grid
    mf, mc = meta["fine"], meta["coarse"]
    ci0 = int(round((mf["x0"] - mc["x0"]) / cr))
    cj0 = int(round((mc["y1"] - mf["y1"]) / cr))
    ncx, ncy = (mf["nx"] - 1) // k + 1, (mf["ny"] - 1) // k + 1
    sub = out["coarse_z"][cj0:cj0 + ncy, ci0:ci0 + ncx]
    yi, xi = np.arange(mf["ny"]) / k, np.arange(mf["nx"]) / k
    grid = np.meshgrid(yi, xi, indexing="ij")
    up = ndimage.map_coordinates(sub, grid, order=1, mode="nearest")
    jj, ii = np.mgrid[0:mf["ny"], 0:mf["nx"]]
    edge = np.minimum.reduce([ii, jj, mf["nx"] - 1 - ii, mf["ny"] - 1 - jj]) * fr
    w = np.clip(1.0 - edge / BLEND, 0.0, 1.0).astype(np.float32)
    w = w * w * (3 - 2 * w)
    out["fine_z"] = (out["fine_z"] * (1 - w) + up * w).astype(np.float32)
    sub_a = out["coarse_albedo"][cj0:cj0 + ncy, ci0:ci0 + ncx]
    up_a = np.stack([ndimage.map_coordinates(sub_a[..., c], grid, order=1, mode="nearest")
                     for c in range(3)], -1)
    out["fine_albedo"] = (out["fine_albedo"] * (1 - w[..., None]) + up_a * w[..., None]).astype(np.float32)
    meta["fine"]["coarse_cells"] = [ci0, cj0, ncx - 1, ncy - 1]   # hole in the coarse mesh
    meta["lake_level"] = float(np.median(out["fine_z"][out["fine_lake"]]))
    for name in ("fine", "coarse"):
        out.pop(f"{name}_lake")
    return out, meta


def sample_grid(z, meta, x, y):
    """Bilinear height of a terrain grid at local metric coordinates."""
    c = (x - meta["x0"]) / meta["res"]
    r = (meta["y1"] - y) / meta["res"]
    return ndimage.map_coordinates(z, [r, c], order=1, mode="nearest")


def jitter(x, y, scale=3.0):
    h = (np.floor(x * scale).astype(np.int64) * 73856093
         ^ np.floor(y * scale).astype(np.int64) * 19349663) & 0xFFFF
    return h / 65535.0


def build_buildings(ox, oy):
    tbl = pq.read_table(C.PROCESSED / "buildings.parquet").to_pandas()
    geom = shapely.from_wkb(tbl.geometry.values)
    geom = shapely.polygons(shapely.get_exterior_ring(geom))     # courtyards filled
    kind = tbl.kind.to_numpy()
    t = tbl.t.to_numpy()
    x = tbl.x.to_numpy() - ox
    y = tbl.y.to_numpy() - oy

    # order: context, the pre-1919 stock (old town outwards, with a ragged edge),
    # dated buildings by time, undated last (also outwards)
    dist = np.hypot(x, y)
    spread = (dist + 400.0) * (0.55 + 0.9 * jitter(x, y))
    ctx = kind == CONTEXT
    stock = kind == STOCK
    und = np.isin(kind, [UNDATED, UNREGISTERED])
    key_g = np.select([ctx, stock, und], [0, 1, 3], 2)
    key_t = np.where(ctx | stock | und, 0.0, t)
    key_d = np.where(stock | und, spread, -y)
    order = np.lexsort((key_d, key_t, key_g))
    tbl = tbl.iloc[order].reset_index(drop=True)
    geom, kind, t, x, y = geom[order], kind[order], t[order], x[order], y[order]
    ctx, stock, und = ctx[order], stock[order], und[order]
    n = len(tbl)

    intro = np.zeros(n, np.float32)
    for m in (stock, und):
        k = int(m.sum())
        intro[m] = (np.arange(k) + 0.5) / max(k, 1)
    first = (~tbl.UUID.duplicated()).to_numpy()                  # count buildings, not parts

    geom = shapely.orient_polygons(geom, exterior_cw=False)
    coords, (ring_off, poly_off) = shapely.to_ragged_array(geom)[1:]
    assert len(poly_off) == n + 1 and np.all(np.diff(poly_off) == 1)
    counts = np.diff(ring_off) - 1                                # drop the closing vertex
    keep = np.ones(len(coords), bool)
    keep[ring_off[1:] - 1] = False
    xy = (coords[keep] - [ox, oy]).astype(np.float32)
    off = np.concatenate([[0], np.cumsum(counts)]).astype(np.int64)

    # make every ring counter-clockwise (outward wall normals rely on it)
    seg = np.repeat(np.arange(n), counts)
    nxt = np.arange(len(xy)) + 1
    nxt[off[1:] - 1] = off[:-1]
    cross = xy[:, 0].astype(np.float64) * xy[nxt, 1] - xy[nxt, 0].astype(np.float64) * xy[:, 1]
    signed = np.bincount(seg, weights=cross, minlength=n)
    for b in np.flatnonzero(signed < 0):
        xy[off[b]:off[b + 1]] = xy[off[b]:off[b + 1]][::-1]

    # roofs: fan for triangles, split quads, ear clipping for the rest
    t0 = time.time()
    roof_counts = np.maximum(counts - 2, 0)
    roof_off = np.concatenate([[0], np.cumsum(roof_counts)]).astype(np.int64)
    roof = np.zeros((int(roof_off[-1]), 3), np.int32)
    for b in np.flatnonzero(counts > 4):
        ring = xy[off[b]:off[b + 1]].astype(np.float64)
        tri = earcut.triangulate_float64(ring, np.array([len(ring)], np.uint32)).reshape(-1, 3)
        m = min(len(tri), roof_counts[b])
        roof[roof_off[b]:roof_off[b] + m] = tri[:m]
    tri3 = np.flatnonzero(counts == 3)
    roof[roof_off[tri3]] = [0, 1, 2]
    quad = np.flatnonzero(counts == 4)
    q = xy[off[quad][:, None] + np.arange(4)[None, :]].astype(np.float64)

    def turn(a, b, c):
        return (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    use13 = (turn(q[:, 0], q[:, 1], q[:, 2]) < 0) | (turn(q[:, 2], q[:, 3], q[:, 0]) < 0)
    roof[roof_off[quad]] = np.where(use13[:, None], [0, 1, 3], [0, 1, 2])
    roof[roof_off[quad] + 1] = np.where(use13[:, None], [1, 2, 3], [0, 2, 3])
    print(f"  roofs triangulated in {time.time() - t0:.1f}s")

    out = dict(
        ring_xy=xy, ring_off=off, roof_idx=roof, roof_off=roof_off,
        cx=x.astype(np.float32), cy=y.astype(np.float32),
        height=tbl.height.to_numpy().astype(np.float32),
        t=t.astype(np.float32),
        intro=intro, kind=kind.astype(np.uint8), year=tbl.year.to_numpy().astype(np.int16),
        first=first,
    )
    stats = dict(n=int(n), n_context=int(ctx.sum()), n_stock=int(stock.sum()),
                 n_undated=int(und.sum()), n_dated=int(n - ctx.sum() - und.sum()),
                 buildings_stock=int((first & stock).sum()),
                 buildings_dated=int((first & ~ctx & ~und).sum()),
                 buildings_undated=int((first & und).sum()),
                 vertices=int(len(xy)), roof_triangles=int(len(roof)))
    m = ~ctx
    return out, stats, (x[m].min(), y[m].min(), x[m].max(), y[m].max())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ox, oy = C.ORIGIN
    print("buildings")
    bld, stats, bbox = build_buildings(ox, oy)
    print("terrain")
    ter, tmeta = build_terrain(ox, oy)
    bld["ground"] = sample_grid(ter["fine_z"], tmeta["fine"], bld["cx"], bld["cy"]).astype(np.float32)
    for k, v in {**bld, **ter}.items():
        np.save(OUT / f"{k}.npy", v)
    summary = json.loads((C.PROCESSED / "summary.json").read_text())
    meta = dict(origin_lv95=list(C.ORIGIN), crs=C.CRS, year_stock=C.YEAR_STOCK,
                year_end=C.YEAR_END, terrain=tmeta, buildings=stats,
                per_year=summary["per_year"], register_export=summary["register_export"],
                bbox_buildings=[float(v) for v in bbox])
    (OUT / "scene.json").write_text(json.dumps(meta, indent=1))
    size = sum(f.stat().st_size for f in OUT.iterdir()) / 1e6
    print(f"wrote {OUT} ({size:.0f} MB): {json.dumps(stats)}")


if __name__ == "__main__":
    main()
