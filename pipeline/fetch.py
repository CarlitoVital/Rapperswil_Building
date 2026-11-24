"""Download everything the pipeline needs (about 350 MB, a few minutes).

    python -m pipeline.fetch

* GWR (Federal Register of Buildings and Dwellings) for the canton - BFS
* municipal boundary - swissBOUNDARIES3D via api3.geo.admin.ch
* swissBUILDINGS3D 3.0 tiles covering CONTEXT_BOX, newest edition per tile
* terrain (swissALTIRegio) and vegetation height (NFI) - windows read
  straight out of the national cloud-optimised GeoTIFFs, nothing else
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import time
import zipfile
from pathlib import Path

import numpy as np
import pyproj
import rasterio
import requests
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.windows import from_bounds

from . import config as C

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "building-age-animation/1.0 (open-data research)"


def download(url: str, path: Path, tries: int = 5) -> Path:
    """Stream ``url`` to ``path`` (skipped if it is already complete)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(tries):
        try:
            with SESSION.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                size = int(r.headers.get("content-length", 0))
                if path.exists() and size and path.stat().st_size == size:
                    return path
                tmp = path.with_suffix(path.suffix + ".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                if size and tmp.stat().st_size != size:
                    raise IOError(f"incomplete download of {url}")
                tmp.replace(path)
                return path
        except (requests.RequestException, IOError) as e:
            if attempt == tries - 1:
                raise
            wait = 2 ** attempt
            print(f"    retry in {wait}s ({e})")
            time.sleep(wait)
    return path


# ----------------------------------------------------------------------------
# register and boundary
# ----------------------------------------------------------------------------
def fetch_gwr():
    out = C.RAW / "gwr"
    z = download(C.GWR_URL.format(canton=C.CANTON), out / f"{C.CANTON}.zip")
    with zipfile.ZipFile(z) as zf:
        for name in ("gebaeude_batiment_edificio.csv", "kodes_codes_codici.csv", "license.pdf"):
            zf.extract(name, out)
    print(f"  GWR {C.CANTON.upper()}: {z.stat().st_size / 1e6:.0f} MB")


def fetch_boundary():
    r = SESSION.get(C.BOUNDARY_URL.format(bfs=C.BFS_NR), timeout=60)
    r.raise_for_status()
    cur = [f for f in r.json()["results"] if f["properties"].get("is_current_jahr")]
    if len(cur) != 1:
        raise RuntimeError(f"expected one current boundary for BFS {C.BFS_NR}, got {len(cur)}")
    f = cur[0]
    out = C.RAW / "boundary.geojson"
    out.write_text(json.dumps({"type": "Feature", "properties": f["properties"],
                               "geometry": f["geometry"]}))
    p = f["properties"]
    print(f"  boundary: {p['gemname']} ({p['jahr']}), {p['gemflaeche'] / 100:.2f} km2")


# ----------------------------------------------------------------------------
# swissBUILDINGS3D 3.0
# ----------------------------------------------------------------------------
def wgs84_bbox(box):
    tr = pyproj.Transformer.from_crs(C.CRS, "EPSG:4326", always_xy=True)
    xs, ys = np.meshgrid(np.linspace(box[0], box[2], 5), np.linspace(box[1], box[3], 5))
    lon, lat = tr.transform(xs.ravel(), ys.ravel())
    return min(lon), min(lat), max(lon), max(lat)


def stac_items(collection: str, bbox) -> list[dict]:
    url = (f"{C.STAC}/collections/{collection}/items?limit=100&bbox="
           + ",".join(f"{v:.5f}" for v in bbox))
    items = []
    while url:
        r = SESSION.get(url, timeout=60)
        r.raise_for_status()
        d = r.json()
        items += d["features"]
        url = next((l["href"] for l in d.get("links", []) if l["rel"] == "next"), None)
    return items


def sb3d_tiles() -> dict[str, tuple[str, str]]:
    """Newest edition of every map-sheet tile in CONTEXT_BOX: {tile: (edition, url)}."""
    best: dict[str, tuple[str, str]] = {}
    for it in stac_items(C.SB3D_COLLECTION, wgs84_bbox(C.CONTEXT_BOX)):
        parts = it["id"].split("_")            # swissbuildings3d_3_0_<edition>_<tile>
        if len(parts) != 5:                    # national "full coverage" items
            continue
        edition, tile = parts[3], parts[4]
        gdb = [a["href"] for a in it["assets"].values() if a["href"].endswith(".gdb.zip")]
        if gdb and (tile not in best or edition > best[tile][0]):
            best[tile] = (edition, gdb[0])
    return dict(sorted(best.items()))


def fetch_sb3d():
    tiles = sb3d_tiles()
    root = C.RAW / "sb3d"

    def one(item):
        tile, (edition, url) = item
        z = download(url, root / f"{tile}_{edition}.gdb.zip")
        dest = root / f"{tile}_{edition}"
        if not any(dest.glob("*.gdb")):
            with zipfile.ZipFile(z) as zf:
                zf.extractall(dest)
        return tile, edition, z.stat().st_size

    with cf.ThreadPoolExecutor(4) as ex:
        for tile, edition, size in ex.map(one, tiles.items()):
            print(f"  swissBUILDINGS3D {tile} ({edition}): {size / 1e6:.0f} MB")
    (root / "tiles.json").write_text(json.dumps(tiles, indent=1))


# ----------------------------------------------------------------------------
# rasters
# ----------------------------------------------------------------------------
def read_window(url: str, box, res: float, out: Path, resampling=Resampling.average):
    """Read ``box`` of a remote COG at ``res`` metres and save it as GeoTIFF."""
    nx = int(round((box[2] - box[0]) / res))
    ny = int(round((box[3] - box[1]) / res))
    with rasterio.Env(**C.gdal_env()), rasterio.open("/vsicurl/" + url) as ds:
        w = from_bounds(*box, transform=ds.transform)
        a = ds.read(1, window=w, out_shape=(ny, nx), resampling=resampling, masked=True)
        nodata = ds.nodata
    a = a.filled(np.nan).astype(np.float32)
    prof = dict(driver="GTiff", width=nx, height=ny, count=1, dtype="float32", crs=C.CRS,
                transform=from_origin(box[0], box[3], res, res), nodata=np.nan,
                compress="deflate", tiled=True)
    with rasterio.open(out, "w", **prof) as dst:
        dst.write(a, 1)
    bad = np.isnan(a).mean()
    print(f"  {out.name}: {nx} x {ny} @ {res:.0f} m, {np.nanmin(a):.1f} .. {np.nanmax(a):.1f}"
          + (f", {bad:.1%} no data" if bad else "") + f" (source nodata {nodata})")


def fetch_rasters():
    r = C.RAW / "rasters"
    r.mkdir(parents=True, exist_ok=True)
    read_window(C.DEM_URL, C.CONTEXT_BOX, C.FINE_RES, r / "dem_fine.tif", Resampling.bilinear)
    read_window(C.DEM_URL, C.TERRAIN_BOX, C.COARSE_RES, r / "dem_coarse.tif")
    read_window(C.VHM_URL, C.CONTEXT_BOX, C.FINE_RES, r / "vhm_fine.tif", Resampling.bilinear)
    read_window(C.VHM_URL, C.TERRAIN_BOX, C.COARSE_RES, r / "vhm_coarse.tif")


def main():
    t0 = time.time()
    print("register");  fetch_gwr()
    print("boundary");  fetch_boundary()
    print("buildings"); fetch_sb3d()
    print("rasters");   fetch_rasters()
    print(f"fetched in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
