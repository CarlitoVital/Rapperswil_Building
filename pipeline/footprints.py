"""Building footprints and heights from swissBUILDINGS3D 3.0.

Reads the ``Floor`` layer of every downloaded tile (the ground-floor outline
of each building, stored either as polygons or as triangulated surfaces),
dissolves it into 2D footprints and keeps, per building: UUID, EGID (the
federal building identifier that links it to the register), object type,
total height, terrain height and the edition year of the tile.

Output: ``data/processed/footprints.parquet`` - one row per footprint part
(buildings with several separate parts get one row each, same UUID).

Usage:  python -m pipeline.footprints
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
import shapely

from . import config as C
from .wkb import tin_to_multipolygon

FIELDS = ["UUID", "OBJEKTART", "EGID", "GESAMTHOEHE", "GELAENDEPUNKT", "HERKUNFT_JAHR"]


def read_tile(gdb, name: str) -> pd.DataFrame:
    # Some tiles declare the layer as TIN, which pyogrio refuses to open. A tiny
    # OGR VRT re-declares it as MultiPolygon; the geometries still arrive as
    # TIN WKB and are patched by ``tin_to_multipolygon``.
    vrt = C.PROCESSED / "vrt" / f"{name}.vrt"
    vrt.parent.mkdir(parents=True, exist_ok=True)
    vrt.write_text(
        "<OGRVRTDataSource><OGRVRTLayer name=\"Floor\">"
        f"<SrcDataSource>{gdb.resolve()}</SrcDataSource><SrcLayer>Floor</SrcLayer>"
        "<GeometryType>wkbMultiPolygon25D</GeometryType>"
        "</OGRVRTLayer></OGRVRTDataSource>")
    meta, _, geom, fields = pyogrio.raw.read(str(vrt), layer="Floor", columns=FIELDS)
    df = pd.DataFrame(dict(zip(meta["fields"], fields)))
    df["geometry"] = shapely.from_wkb([tin_to_multipolygon(w) for w in geom])
    return df


def dissolve(g) -> shapely.Geometry:
    """Triangles / polygon parts of one floor -> its 2D outline (polygon or multipolygon)."""
    parts = shapely.get_parts(shapely.force_2d(g))
    if len(parts) == 1:
        out = shapely.make_valid(parts[0])
    else:
        # snap away the hairline gaps between triangles, union, then undo the growth
        out = shapely.union_all(shapely.buffer(parts, 0.02, join_style="mitre"))
        out = shapely.buffer(out, -0.02, join_style="mitre")
    out = shapely.simplify(out, C.SIMPLIFY)
    polys = [p for p in shapely.get_parts(out)
             if shapely.get_type_id(p) == 3 and p.area >= 1.0]
    return shapely.multipolygons(polys) if len(polys) > 1 else (polys[0] if polys else None)


def main():
    t0 = time.time()
    tiles = json.loads((C.RAW / "sb3d" / "tiles.json").read_text())
    frames = []
    for tile, (edition, _) in tiles.items():
        gdb = next((C.RAW / "sb3d" / f"{tile}_{edition}").glob("*.gdb"))
        df = read_tile(gdb, f"{tile}_{edition}")
        df["tile"], df["edition"] = tile, int(edition)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    n_raw = len(df)

    # keep what lies in the context box (by representative point of the raw geometry)
    rp = shapely.point_on_surface(shapely.force_2d(df.geometry.values))
    x, y = shapely.get_x(rp), shapely.get_y(rp)
    bx = C.CONTEXT_BOX
    df = df[(x >= bx[0]) & (x <= bx[2]) & (y >= bx[1]) & (y <= bx[3])].copy()
    # a building on a tile edge can be delivered with both tiles: keep the newer edition
    df = df.sort_values("edition", ascending=False).drop_duplicates("UUID")
    n_box = len(df)
    dropped_type = df.OBJEKTART.isin(C.DROP_OBJEKTART)
    df = df[~dropped_type]

    t1 = time.time()
    df["geometry"] = [dissolve(g) for g in df.geometry.values]
    df = df[df.geometry.notna()]
    print(f"  dissolved {len(df):,} floors in {time.time() - t1:.0f}s")

    # one row per part
    parts, idx = shapely.get_parts(df.geometry.values, return_index=True)
    out = df.drop(columns="geometry").iloc[idx].reset_index(drop=True)
    out["area"] = shapely.area(parts)
    out["n_holes"] = shapely.get_num_interior_rings(parts)
    keep = out.area >= C.MIN_AREA
    out, parts = out[keep].reset_index(drop=True), parts[keep.values]
    rp = shapely.point_on_surface(parts)
    out["x"], out["y"] = shapely.get_x(rp), shapely.get_y(rp)
    out["EGID"] = out.EGID.fillna(0).astype(np.int64)
    out["geometry"] = shapely.to_wkb(parts)

    C.PROCESSED.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(out, preserve_index=False),
                   C.PROCESSED / "footprints.parquet")
    print(f"  {n_raw:,} floors in {len(tiles)} tiles, {n_box:,} in the box, "
          f"{int(dropped_type.sum())} dropped by type ({', '.join(sorted(C.DROP_OBJEKTART))}); "
          f"{out.UUID.nunique():,} buildings -> {len(out):,} footprint parts, "
          f"{(out.n_holes > 0).sum():,} with courtyards, "
          f"{(out.EGID > 0).mean():.1%} with EGID ({time.time() - t0:.0f}s)")
    print("  object types:", out.drop_duplicates("UUID").OBJEKTART.value_counts().to_dict())


if __name__ == "__main__":
    main()
