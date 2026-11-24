"""What is mapped: area, sources, timeline and dating rules.

How it *looks* (camera, colours, texts) lives in ``render/config.py``.
To map another Swiss municipality, change ``BFS_NR``, ``CANTON``, ``ORIGIN``
and the two boxes below; everything else follows.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

# --------------------------------------------------------------------------
# Area
# --------------------------------------------------------------------------
BFS_NR = 3340                      # Rapperswil-Jona (merged from Rapperswil and Jona in 2007)
MUNICIPALITY = "Rapperswil-Jona"
CANTON = "sg"                      # the register is downloaded per canton
CRS = "EPSG:2056"                  # Swiss LV95 - metric, so no reprojection anywhere

# Local origin handed to the renderer (x = metres east, y = metres north of it):
# Schloss Rapperswil, on the hill of the old town.
ORIGIN = (2704280.0, 1231620.0)

# LV95 boxes (E min, N min, E max, N max)
# Buildings and the fine terrain: the municipality (2702742..2712147 /
# 1229530..1234329) with the lake, the Seedamm and the neighbouring villages.
CONTEXT_BOX = (2699500.0, 1225500.0, 2716500.0, 1239000.0)
# Coarse terrain backdrop - large enough that the tilted camera never sees an edge.
TERRAIN_BOX = (2676000.0, 1206000.0, 2736000.0, 1264000.0)
FINE_RES = 10.0                    # m, native resolution of swissALTIRegio
COARSE_RES = 40.0

# --------------------------------------------------------------------------
# Timeline
# --------------------------------------------------------------------------
# The register lumps everything older than 1919 into one construction period
# ("vor 1919"), so that is the stock the film opens with.
YEAR_STOCK = 1919
YEAR_END = 2026                    # the register export used is from November 2026

# GWR construction periods (code -> first, last year); 8023 is "after 2015"
PERIODS = {
    8011: (None, 1918), 8012: (1919, 1945), 8013: (1946, 1960), 8014: (1961, 1970),
    8015: (1971, 1980), 8016: (1981, 1985), 8017: (1986, 1990), 8018: (1991, 1995),
    8019: (1996, 2000), 8020: (2001, 2005), 8021: (2006, 2010), 8022: (2011, 2015),
    8023: (2016, YEAR_END),
}

# --------------------------------------------------------------------------
# Sources - all open government data, plain HTTPS, no keys
# --------------------------------------------------------------------------
# Federal Register of Buildings and Dwellings (GWR), Federal Statistical Office
GWR_URL = "https://public.madd.bfs.admin.ch/{canton}.zip"
# Municipal boundary (swissBOUNDARIES3D) through the geo.admin.ch API
BOUNDARY_URL = (
    "https://api3.geo.admin.ch/rest/services/api/MapServer/find?"
    "layer=ch.swisstopo.swissboundaries3d-gemeinde-flaeche.fill&searchText={bfs}"
    "&searchField=gde_nr&contains=false&returnGeometry=true&geometryFormat=geojson&sr=2056"
)
# swisstopo STAC catalogue: swissBUILDINGS3D 3.0 (footprints, heights, EGID)
STAC = "https://data.geo.admin.ch/api/stac/v0.9"
SB3D_COLLECTION = "ch.swisstopo.swissbuildings3d_3_0"
# swissALTIRegio, 10 m terrain model of Switzerland (one cloud-optimised GeoTIFF)
DEM_URL = ("https://data.geo.admin.ch/ch.swisstopo.swissaltiregio/swissaltiregio/"
           "swissaltiregio_2056_5728.tif")
# National Forest Inventory vegetation height model (Sentinel-2, 10 m, 2025)
VHM_URL = ("https://data.geo.admin.ch/ch.bafu.landesforstinventar-vegetationshoehenmodell_sentinel/"
           "landesforstinventar-vegetationshoehenmodell_sentinel_2025/"
           "landesforstinventar-vegetationshoehenmodell_sentinel_2025_2056.tif")

# --------------------------------------------------------------------------
# Buildings
# --------------------------------------------------------------------------
# swissBUILDINGS3D object types that are not buildings in the register's sense
DROP_OBJEKTART = {"Mauer gross", "Bruecke gedeckt", "Flugdach", "Unterirdisches Gebaeude"}
MIN_AREA = 6.0                     # m2; smaller footprint fragments are dropped
SIMPLIFY = 0.3                     # m; footprint simplification tolerance

# --------------------------------------------------------------------------
# Dating rules (see README "How a building gets its year")
# --------------------------------------------------------------------------
# Buildings with only a construction period get a year from register neighbours
# of the same period with an exact year: the median of the nearest
# IMPUTE_K within IMPUTE_RADIUS, if at least IMPUTE_MIN of them are found.
# Otherwise the year is drawn (deterministically, from the EGID) from the
# municipality's distribution of exact years within that period.
IMPUTE_RADIUS = 150.0              # m
IMPUTE_K = 7
IMPUTE_MIN = 3

# Placeholder years: a year that holds far more buildings than the years around
# it is a filler, not a date. In Rapperswil-Jona 145 buildings carry exactly 1919
# (the first year of the 1919-1945 period) against 12 in 1920 and 2 in 1921;
# canton-wide 1918-1920 hold 8,500 buildings against about 100 a year after. Such
# years are treated as "period known, year unknown".
PLACEHOLDER_RATIO = 5.0            # x the median of the three years either side
PLACEHOLDER_MIN = 20               # buildings
# The register's construction month is mostly a default: about 90 % of the known
# months are January. January is therefore ignored; other months are used.

# The few corrections made by hand, each with its source. Only the coarse
# "before 1919" class is ever assigned this way - never an exact year.
MANUAL_PERIODS = {
    # EGID: (GWR period code, reason)
    2212910: (8011, "Schloss Rapperswil: no year or period in the register; the castle was "
                    "built around 1220-1230 (Historisches Lexikon der Schweiz, 'Rapperswil (SG)')"),
}


def gdal_env() -> dict:
    """GDAL settings for HTTP range reads of cloud-optimised GeoTIFFs."""
    env = dict(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
        GDAL_HTTP_MAX_RETRY="5",
        GDAL_HTTP_RETRY_DELAY="2",
        GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
        VSI_CACHE="TRUE",
        VSI_CACHE_SIZE=str(64 * 1024 * 1024),
    )
    if not (os.environ.get("CURL_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")):
        import certifi
        env["CURL_CA_BUNDLE"] = certifi.where()
    return env
