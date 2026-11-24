"""Everything that defines how the animation *looks* - edit freely.

Data questions (area, sources, dating rules) live in ``pipeline/config.py``.
Coordinates are Swiss LV95 (metres); the scene's local origin is
``pipeline.config.ORIGIN`` (Schloss Rapperswil).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENE = ROOT / "data" / "scene"
FONTS = ROOT / "assets" / "fonts"
OUT = ROOT / "out"
NAME = "rapperswil_jona_buildings"   # output file stem

# --- output -----------------------------------------------------------------
WIDTH, HEIGHT = 1080, 1350        # 4:5 portrait (Instagram feed); --format 9x16 gives 1080 x 1920
TALL_FRAME_MARGINS = (250, 320)   # room kept free for the Reels interface in taller frames (px)
FPS = 30
SUPERSAMPLE = 4                   # render at 4x and average: clean edges, no shimmer
CRF = 17                          # x264 quality (lower = better, bigger)

# --- timeline (seconds) -----------------------------------------------------
T_OPEN = 0.6                      # empty landscape, title only
T_STOCK = 3.0                     # the town of 1918 rises, old town outwards
T_STOCK_HOLD = 0.7
T_PER_YEAR = 0.215                # 1919 ... 2026 (108 years -> 23 s)
T_REVEAL = 1.4                    # undated buildings appear in grey
T_HOLD = 3.2                      # final hold
RISE = 0.45                       # seconds a building takes to grow
FLASH = 0.40                      # e-folding time of the "new building" glow
LIFE = 3.0                        # after this a building is static (baked)

# --- camera -----------------------------------------------------------------
# Local metres east / north of Schloss Rapperswil; z in metres a.s.l.
# azimuth 0 = north-up (camera south of the town, looking north);
# elevation = degrees above the horizon; distance in metres; fov vertical (4:5 frame).
# The shot starts close on the old town and pulls back as the town grows; it
# eases from CAMERA_START to CAMERA_END and is still during the final hold.
# Azimuth 45: camera south-west of the town, the lake in the foreground, north
# to the upper left (a turn, not a flip - the town never reads upside down).
CAMERA_START = dict(target=(250.0, 380.0, 420.0), azimuth=38.0, elevation=48.0,
                    distance=12500.0, fov=22.0)
CAMERA_END = dict(target=(2005.0, 120.0, 420.0), azimuth=45.0, elevation=47.0,
                  distance=22000.0, fov=22.0)

Z_REF = 406.0                     # metres a.s.l. that become z = 0 (Lake Zurich)
TERRAIN_EXAGGERATION = 1.6
BUILDING_EXAGGERATION = 3.0       # houses are small at 10 km across - let them stand
MIN_BUILDING_PX = 1.4             # footprints are widened to at least this (supersampled px)
SKIRT = 4.0                       # walls reach this far below ground (hides gaps on slopes)

# --- light & terrain shading ------------------------------------------------
LIGHT_AZIMUTH_FROM_CAMERA = -50.0  # degrees: light from the upper left of the frame
LIGHT_ELEVATION = 36.0
TERRAIN_AMBIENT = 0.32
TERRAIN_DIFFUSE = 1.30
TERRAIN_GAIN = 0.80
TERRAIN_OCCLUSION = 0.50          # darken valleys / brighten ridges (0 = off)
BACKGROUND = "#06090d"
FOG_COLOR = "#0a1118"
FOG_START = 40000.0               # metres from the camera
FOG_LENGTH = 30000.0
FOG_STRENGTH = 0.85
VIGNETTE = 0.38

WALL_AMBIENT, WALL_DIFFUSE = 0.50, 0.62
WALL_FOOT = 0.55                  # wall brightness at ground level relative to the eaves
ROOF_GAIN = 1.12

# --- colour -----------------------------------------------------------------
# Building colour by year: deep crimson (before 1919) -> red -> orange -> pale peach.
RAMP = [
    (1918.0, "#5c0e22"),
    (1919.0, "#93142d"),
    (1945.0, "#c51b2c"),
    (1965.0, "#e5402b"),
    (1985.0, "#f2763a"),
    (2005.0, "#f8ad69"),
    (2026.99, "#fde8cf"),
]
FLASH_COLOR = "#fff4dc"
FLASH_MIX = 0.9                   # how far a brand-new building is pushed towards FLASH_COLOR
UNDATED_COLOR = "#5d656e"
CONTEXT_COLOR = "#2a3034"         # neighbouring municipalities: there, but not part of the story
SHOW_CONTEXT = True
BLOOM = 0.7                       # glow strength of new construction (0 = off)

# --- words ------------------------------------------------------------------
KICKER = "LAKE ZURICH  ·  SWITZERLAND"
TITLE = "RAPPERSWIL-JONA"
CHART_LABEL = "BUILDINGS COMPLETED PER YEAR"
STOCK_LABEL = "AND EARLIER"
ESTIMATE_NOTE = True              # legend: faded bar tops = year estimated from the period
ESTIMATE_ALPHA = 125              # opacity (0-255) of the estimated part of each bar
NOTE = ("Federal building register (GWR), buildings standing in 2026 · faded bars: year "
        "estimated from the register's construction period · grey = undated")
AUTHOR = ""                       # e.g. "Author: Your Name" - shown bottom left when set
CREDITS = ("Data: BFS – Eidg. Gebäude- und Wohnungsregister · swisstopo – swissBUILDINGS3D 3.0, "
           "swissALTIRegio, swissBOUNDARIES3D · WSL/BAFU – LFI vegetation height")

# Place labels: (text, E, N in LV95, offset of the text from its anchor in px at 1080 wide)
LABELS = [
    ("RAPPERSWIL", 2704300.0, 1231610.0, (70, 62)),
    ("JONA", 2706950.0, 1232650.0, (0, -86)),
    ("KEMPRATEN", 2703650.0, 1232650.0, (-40, -64)),
    ("BOLLINGEN", 2710600.0, 1231150.0, (-96, -46)),
]

TEXT_COLOR = "#e9e6e1"
TEXT_DIM = "#9aa3ab"
ACCENT = "#ef4a35"
