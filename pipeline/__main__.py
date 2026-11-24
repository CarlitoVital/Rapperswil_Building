"""Run the whole data pipeline: fetch -> footprints -> years -> scene -> checks.

    python -m pipeline              everything (downloads are skipped when present)
    python -m pipeline --no-fetch   reprocess only
"""
from __future__ import annotations

import sys
import time

from . import build_scene, date_buildings, fetch, footprints, validate


def main():
    t0 = time.time()
    steps = [("fetch", fetch.main), ("footprints", footprints.main),
             ("dating", date_buildings.main), ("scene", build_scene.main),
             ("checks", validate.main)]
    if "--no-fetch" in sys.argv:
        steps = steps[1:]
    for name, fn in steps:
        print(f"\n=== {name}")
        fn()
    print(f"\npipeline done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
