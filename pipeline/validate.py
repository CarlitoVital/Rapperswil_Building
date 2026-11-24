"""Checks behind the numbers in the film (results are quoted in the README).

    python -m pipeline.validate

1. Coverage: register -> footprints -> drawn buildings.
2. The estimation rule, tested on buildings whose year *is* known: hide the
   year, keep only the period, estimate it, compare - against two baselines
   (middle of the period; a draw from the period's year distribution).
3. Landmarks whose age is common knowledge.
4. Per-period totals: drawn buildings vs. the register.
Writes data/processed/validation.json and out/diagnostic_years.png.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from . import config as C
from .date_buildings import (CONTEXT, DISTRIBUTION, EXACT, NEIGHBOURS, STOCK, UNDATED,
                             UNREGISTERED, hash01, impute, load_register, period_bounds)

# (name, EGID, expected) - expected "stock" = standing before 1919
LANDMARKS = [
    ("Schloss Rapperswil (13th c.)", 2212910, "stock"),
    ("Stadtpfarrkirche St. Johann, old town", 502286872, "stock"),
    ("Kapuzinerkloster Rapperswil (1606)", 502286955, "stock"),
    ("Kloster Wurmsbach (founded 1259)", 502284928, "stock"),
    ("Hotel Schwanen, old town", 1124404, "stock"),
    ("Hochschule Rapperswil (founded 1972)", 502286733, (1970, 1975)),
]


def period_of(year: np.ndarray) -> np.ndarray:
    out = np.zeros(len(year), np.int32)
    for code, (lo, hi) in C.PERIODS.items():
        lo = lo if lo is not None else 0
        out[(year >= lo) & (year <= hi)] = code
    return out


def loo(reg: pd.DataFrame) -> dict:
    """Leave-one-out test of the estimation rule on buildings with an exact year."""
    d = reg[(reg.GSTAT == 1004) & (reg.GBAUJ >= C.YEAR_STOCK)]
    xy = d[["GKODE", "GKODN"]].to_numpy()
    yr = d.GBAUJ.astype(int).to_numpy()
    per = period_of(yr)
    # periods of 2001+ are always given with an exact year, so the rule only ever runs
    # on 1919-2000; test it there
    m = per <= 8019
    est, nb = impute(xy[m], per[m], d.EGID.to_numpy()[m], xy, yr, exclude_self=True)
    truth = yr[m]
    mid = np.array([sum(period_bounds(p)) / 2 for p in per[m]])
    draw = np.zeros(m.sum())
    for code in np.unique(per[m]):
        lo, hi = period_bounds(code)
        pool = np.sort(yr[(yr >= lo) & (yr <= hi)])
        sel = per[m] == code
        u = hash01(d.EGID.to_numpy()[m][sel], 11)
        draw[sel] = pool[np.minimum((u * len(pool)).astype(int), len(pool) - 1)]

    def stats(e, mask=None):
        mask = np.ones(len(e), bool) if mask is None else mask
        err = np.abs(e[mask] - truth[mask])
        return dict(n=int(mask.sum()), mae=round(float(err.mean()), 2),
                    exact=round(float((err == 0).mean()), 3),
                    within_2y=round(float((err <= 2).mean()), 3))
    return dict(
        tested=int(m.sum()),
        share_with_enough_neighbours=round(float(nb.mean()), 3),
        rule_in_use=stats(est.astype(float)),
        rule_where_neighbours_found=stats(est.astype(float), nb),
        baseline_period_middle=stats(mid),
        baseline_period_draw=stats(draw),
        by_period={str(c): dict(rule=stats(est.astype(float), per[m] == c)["mae"],
                                middle=stats(mid, per[m] == c)["mae"])
                   for c in np.unique(per[m])},
    )


def landmarks(bld: pd.DataFrame) -> list[dict]:
    out = []
    by = bld.drop_duplicates("EGID").set_index("EGID")
    for name, egid, exp in LANDMARKS:
        if egid not in by.index:
            out.append(dict(name=name, egid=egid, result="not drawn", ok=False))
            continue
        r = by.loc[egid]
        if r.kind == STOCK:
            got = "before 1919"
        elif r.kind in (UNDATED, UNREGISTERED):
            got = "undated"
        else:
            got = f"{int(r.year)}" + ("" if r.kind == EXACT else " (estimated)")
        ok = (r.kind == STOCK) if exp == "stock" else (exp[0] <= r.year <= exp[1])
        out.append(dict(name=name, egid=int(egid), result=got, ok=bool(ok)))
    return out


def per_period(bld: pd.DataFrame, reg: pd.DataFrame) -> dict:
    ex = reg[reg.GSTAT == 1004]
    b = bld[(bld.kind != CONTEXT)].drop_duplicates("UUID")
    rows = {}
    for code, (lo, hi) in C.PERIODS.items():
        lo = lo if lo is not None else 0
        in_reg = ((ex.GBAUJ.between(lo, hi)) | (ex.GBAUJ.isna() & (ex.GBAUP == code))).sum()
        if code == 8011:
            drawn = (b.kind == STOCK).sum()
        else:
            drawn = (b.kind.isin([EXACT, NEIGHBOURS, DISTRIBUTION]) & b.year.between(lo, hi)).sum()
        rows[f"{lo or '…'}–{hi}"] = dict(register=int(in_reg), drawn=int(drawn))
    return rows


def diagnostic_map(bld: pd.DataFrame, path):
    """Top-down map of the municipality, buildings coloured by year (stage-2 check)."""
    import shapely
    from PIL import Image, ImageDraw, ImageFont
    from render.overlay import Ramp
    b = bld[bld.kind != CONTEXT]
    x0, y0, x1, y1 = 2702500, 1229300, 2712400, 1234600
    s = 0.2                                          # px per metre -> 1980 x 1060
    W, H = int((x1 - x0) * s), int((y1 - y0) * s)
    im = Image.new("RGB", (W, H + 70), (12, 16, 20))
    d = ImageDraw.Draw(im)
    ramp = Ramp()
    ctx = bld[bld.kind == CONTEXT]
    for geoms, cols in ((ctx, None), (b, True)):
        g = shapely.from_wkb(geoms.geometry.values)
        for k, poly in enumerate(g):
            ring = np.asarray(poly.exterior.coords)
            pts = [((px - x0) * s, (y1 - py) * s) for px, py in ring]
            if cols is None:
                fill = (40, 46, 52)
            else:
                r = geoms.iloc[k]
                if r.kind in (UNDATED, UNREGISTERED):
                    fill = (110, 116, 124)
                else:
                    fill = ramp.srgb8(r.year + 0.5 if r.kind != STOCK else C.YEAR_STOCK - 1)
            d.polygon(pts, fill=fill)
    f = ImageFont.truetype(str(C.ROOT / "assets/fonts/Montserrat-SemiBold.ttf"), 22)
    for i, yv in enumerate(range(1920, C.YEAR_END + 1, 2)):
        d.rectangle([20 + i * 18, H + 15, 36 + i * 18, H + 35], fill=ramp.srgb8(yv))
    d.rectangle([20 - 26, H + 15, 20 - 10, H + 35], fill=ramp.srgb8(C.YEAR_STOCK - 1))
    d.text((20, H + 62), "before 1919 · 1920 → 2026 · grey = undated · dark = neighbouring "
           "municipalities", font=f, fill=(200, 200, 200), anchor="ls")
    im.save(path)


def main():
    reg = load_register()
    reg = reg[reg.GGDENR == C.BFS_NR]
    bld = pq.read_table(C.PROCESSED / "buildings.parquet").to_pandas()
    summary = json.loads((C.PROCESSED / "summary.json").read_text())
    res = dict(loo=loo(reg), landmarks=landmarks(bld), per_period=per_period(bld, reg))
    (C.PROCESSED / "validation.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))

    f = summary["footprints"]
    r = summary["register"]
    print(f"register: {r['existing']:,} standing buildings - {r['existing_with_year']:,} exact year, "
          f"{r['existing_period_only']:,} period only, {r['existing_neither']:,} neither")
    print(f"footprints in the municipality: {f['in_municipality']:,} - linked by EGID "
          f"{f['linked_by_egid']:,}, by location {f['linked_by_location']}, demolished (dropped) "
          f"{f['dropped_demolished']}; boxes from the register {f['boxes_from_register']}")
    l = res["loo"]
    print(f"\nestimation rule, {l['tested']:,} buildings with known year (1919-2000) treated as "
          f"period-only; {l['share_with_enough_neighbours']:.0%} have enough neighbours")
    for k in ("rule_in_use", "rule_where_neighbours_found", "baseline_period_middle",
              "baseline_period_draw"):
        s = l[k]
        print(f"  {k:30s} MAE {s['mae']:5.2f} y   exact {s['exact']:5.1%}   within 2 y "
              f"{s['within_2y']:5.1%}   (n={s['n']:,})")
    print("  MAE by period (rule / middle):", {k: f"{v['rule']}/{v['middle']}"
                                              for k, v in l["by_period"].items()})
    print("\nlandmarks:")
    for m in res["landmarks"]:
        print(f"  {'ok ' if m['ok'] else 'XX '} {m['name']:42s} {m['result']}")
    print("\nper period (register / drawn):")
    print("  " + "  ".join(f"{k}: {v['register']}/{v['drawn']}" for k, v in res["per_period"].items()))
    C.ROOT.joinpath("out").mkdir(exist_ok=True)
    diagnostic_map(bld, C.ROOT / "out" / "diagnostic_years.png")
    print("\nwrote data/processed/validation.json, out/diagnostic_years.png")


if __name__ == "__main__":
    main()
