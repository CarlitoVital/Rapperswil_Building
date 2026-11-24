"""Give every building in the municipality its year from the federal register.

Inputs:  data/processed/footprints.parquet (swissBUILDINGS3D), the GWR extract,
         the municipal boundary.
Output:  data/processed/buildings.parquet (one row per drawable footprint) and
         data/processed/summary.json (every number shown in the film).

How a building gets its year (details in README):

1. Link footprint and register record by EGID (fallback: register point inside
   the footprint). Records the register marks as demolished are dropped.
2. Exact year (GBAUJ)                     -> that year (and month, if known)
   Year or period before 1919             -> the opening stock "before 1919"
   Only a construction period (GBAUP)     -> estimated: median year of the
       nearest register neighbours of the same period that have an exact year,
       or, if there are too few nearby, a deterministic draw from that period's
       year distribution in the municipality
   Neither                                -> undated (grey)
3. Footprints without a register record   -> not in the register (grey)
4. Recent register buildings that are not yet in the 3D model (it lags by
   one to three years) are drawn as boxes of the register's floor area and
   storey count - only above-ground classes, never garages or sheds.

Usage:  python -m pipeline.date_buildings
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from scipy.spatial import cKDTree

from . import config as C

# kinds of buildings (stored as uint8, used by the renderer and the overlay)
CONTEXT, STOCK, EXACT, NEIGHBOURS, DISTRIBUTION, UNDATED, UNREGISTERED = range(7)
KIND_NAMES = {CONTEXT: "context (outside the municipality)", STOCK: "before 1919",
              EXACT: "exact year", NEIGHBOURS: "year estimated from neighbours",
              DISTRIBUTION: "year drawn from the period", UNDATED: "in register, undated",
              UNREGISTERED: "not in the register"}

# register classes that are never drawn as boxes: garages and underground
# car parks (1242), small structures such as carports, sheds, pavilions (1274)
NOT_A_BOX = {1242, 1274}
BOX_FROM_YEAR = 2019               # the 3D model editions date from 2022 / 2025
BOX_MIN_AREA = 40.0
STOREY_HEIGHT = 3.0


def hash01(key: np.ndarray, salt: int = 0) -> np.ndarray:
    """Deterministic pseudo-random number in [0, 1) per integer key."""
    k = (np.asarray(key, dtype=np.uint64) + np.uint64(salt * 0x9E3779B9)) & np.uint64(0xFFFFFFFF)
    k = (k * np.uint64(2654435761)) & np.uint64(0xFFFFFFFF)
    k ^= k >> np.uint64(15)
    k = (k * np.uint64(2246822519)) & np.uint64(0xFFFFFFFF)
    k ^= k >> np.uint64(13)
    return k.astype(np.float64) / 2.0 ** 32


# ----------------------------------------------------------------------------
# register
# ----------------------------------------------------------------------------
def load_register() -> pd.DataFrame:
    cols = ["EGID", "GGDENR", "GSTAT", "GKAT", "GKLAS", "GBAUJ", "GBAUM", "GBAUP", "GABBJ",
            "GKODE", "GKODN", "GAREA", "GASTW", "GANZWHG", "GBEZ"]
    df = pd.read_csv(C.RAW / "gwr" / "gebaeude_batiment_edificio.csv", sep="\t", dtype=str,
                     usecols=cols)
    for c in cols:
        if c != "GBEZ":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["EGID"] = df.EGID.astype(np.int64)
    # placeholder years -> "period known, year unknown"
    ph = placeholder_years(df[(df.GGDENR == C.BFS_NR) & (df.GSTAT == 1004)])
    m = df.GBAUJ.isin(ph)
    df.loc[m & df.GBAUP.isna(), "GBAUP"] = [period_of_year(int(v)) for v in df.GBAUJ[m & df.GBAUP.isna()]]
    df.loc[m, "GBAUJ"] = np.nan
    df.attrs["placeholder_years"] = {int(y): int(((df.GGDENR == C.BFS_NR) & m & (df.GBAUP.notna())
                                                  & (df.GSTAT == 1004)).sum()) for y in sorted(ph)}
    df.loc[df.GBAUM == 1, "GBAUM"] = np.nan          # January is the register's default
    # manual corrections: only ever the coarse "before 1919" class, each with a source
    for egid, (period, _why) in C.MANUAL_PERIODS.items():
        m = (df.EGID == egid) & df.GBAUJ.isna() & df.GBAUP.isna()
        df.loc[m, "GBAUP"] = period
    return df


def placeholder_years(existing: pd.DataFrame) -> set[int]:
    """Years that hold PLACEHOLDER_RATIO x more buildings than the years around them."""
    vc = existing.GBAUJ.dropna().astype(int).value_counts()
    out = set()
    for yr, n in vc.items():
        if yr < C.YEAR_STOCK or n < C.PLACEHOLDER_MIN:
            continue
        around = [vc.get(yr + d, 0) for d in (-3, -2, -1, 1, 2, 3)
                  if C.YEAR_STOCK <= yr + d <= C.YEAR_END]
        if around and n > C.PLACEHOLDER_RATIO * max(float(np.median(around)), 1.0):
            out.add(int(yr))
    return out


def period_of_year(year: int) -> int:
    for code, (lo, hi) in C.PERIODS.items():
        if (lo or 0) <= year <= hi:
            return code
    raise ValueError(year)


def period_bounds(code) -> tuple[int, int]:
    lo, hi = C.PERIODS[int(code)]
    return (lo if lo is not None else 0), hi


# ----------------------------------------------------------------------------
# estimating years for buildings with only a construction period
# ----------------------------------------------------------------------------
def impute(t_xy, t_period, t_key, d_xy, d_year, radius=C.IMPUTE_RADIUS, k=C.IMPUTE_K,
           kmin=C.IMPUTE_MIN, exclude_self=False):
    """Years for targets that only have a period.

    t_xy (n, 2), t_period (n,) GWR period codes, t_key (n,) integer keys for the
    deterministic fallback draw; d_xy / d_year: donors with exact years.
    Returns (year, used_neighbours) arrays. With ``exclude_self`` the targets are
    the donors themselves (leave-one-out validation): a donor never informs
    itself.
    """
    year = np.zeros(len(t_xy), np.int32)
    by_nb = np.zeros(len(t_xy), bool)
    for code in np.unique(t_period):
        lo, hi = period_bounds(code)
        dm = (d_year >= lo) & (d_year <= hi)
        tm = np.flatnonzero(t_period == code)
        dy = d_year[dm]
        if len(dy) == 0:                                  # no donors at all: uniform
            year[tm] = lo + np.floor(hash01(t_key[tm], 7) * (hi - lo + 1)).astype(int)
            continue
        tree = cKDTree(d_xy[dm])
        kk = min(k + int(exclude_self), len(dy))
        dist, idx = tree.query(t_xy[tm], k=kk, distance_upper_bound=radius)
        dist, idx = np.atleast_2d(dist), np.atleast_2d(idx)
        if dist.shape[0] != len(tm):
            dist, idx = dist.T, idx.T
        if exclude_self:
            dist, idx = dist[:, 1:], idx[:, 1:]          # nearest hit is the donor itself
        ok = np.isfinite(dist)
        n = ok.sum(1)
        srt = np.sort(dy)
        for j, t in enumerate(tm):
            if n[j] >= kmin:
                year[t] = int(np.floor(np.median(dy[idx[j][ok[j]]]) + 0.5))
                by_nb[t] = True
            else:
                u = hash01(t_key[t:t + 1], 11)[0]
                year[t] = int(srt[min(int(u * len(srt)), len(srt) - 1)])
    return year, by_nb


def assign_years(reg: pd.DataFrame) -> pd.DataFrame:
    """Kind, year and position within the year for existing register buildings."""
    out = pd.DataFrame(index=reg.index)
    y, p = reg.GBAUJ, reg.GBAUP
    stock = (y < C.YEAR_STOCK) | (y.isna() & (p == 8011))
    exact = (y >= C.YEAR_STOCK)
    period_only = y.isna() & p.notna() & (p != 8011)
    kind = np.full(len(reg), UNDATED, np.uint8)
    kind[stock.values] = STOCK
    kind[exact.values] = EXACT
    year = np.full(len(reg), -1, np.int32)
    year[stock.values] = C.YEAR_STOCK - 1
    year[exact.values] = y[exact].astype(int).values

    donors = reg[exact]
    d_xy = donors[["GKODE", "GKODN"]].to_numpy()
    d_year = donors.GBAUJ.astype(int).to_numpy()
    t = reg[period_only]
    est, nb = impute(t[["GKODE", "GKODN"]].to_numpy(), t.GBAUP.astype(int).to_numpy(),
                     t.EGID.to_numpy(), d_xy, d_year)
    po = period_only.values
    year[po] = est
    kind[po] = np.where(nb, NEIGHBOURS, DISTRIBUTION)

    # position within the year: the month where the register has it, else spread
    frac = hash01(reg.EGID.to_numpy(), 3) * 0.9 + 0.05
    m = exact.values & reg.GBAUM.between(1, 12).values
    frac[m] = (reg.GBAUM.values[m] - 0.5) / 12.0
    out["kind"], out["year"], out["frac"] = kind, year, frac
    return out


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    fp = pq.read_table(C.PROCESSED / "footprints.parquet").to_pandas()
    reg_all = load_register()
    boundary = shapely.geometry.shape(json.loads((C.RAW / "boundary.geojson").read_text())["geometry"])

    # register records of the municipality
    reg = reg_all[reg_all.GGDENR == C.BFS_NR].copy()
    existing = reg[reg.GSTAT == 1004].copy()
    existing = existing.join(assign_years(existing))

    # --- link footprints ------------------------------------------------------
    # a building = all parts with one UUID; its position = its largest part
    main_part = fp.sort_values("area", ascending=False).drop_duplicates("UUID")
    inside = shapely.contains_xy(boundary, main_part.x.values, main_part.y.values)
    in_muni_uuid = set(main_part.UUID[inside])
    by_egid = reg_all.set_index("EGID")
    linked_muni = fp.EGID.map(by_egid.GGDENR) == C.BFS_NR
    fp["muni"] = fp.UUID.isin(in_muni_uuid) | linked_muni
    fp["status"] = fp.EGID.map(by_egid.GSTAT)

    # fallback: register points (existing, not linked yet) inside unlinked footprints
    linked = set(fp.EGID[fp.muni & fp.EGID.isin(existing.EGID)])
    loose = existing[~existing.EGID.isin(linked)]
    cand = fp.muni & fp.status.isna()
    polys = shapely.from_wkb(fp.geometry[cand].values)
    tree = shapely.STRtree(polys)
    pi, gi = tree.query(shapely.points(loose.GKODE.values, loose.GKODN.values), predicate="within")
    spatial = {}
    for p_, g_ in zip(pi, gi):                     # first register point per footprint wins
        row = fp.index[cand][g_]
        spatial.setdefault(row, int(loose.EGID.iloc[p_]))
    for row, egid in spatial.items():
        fp.loc[row, "EGID"] = egid
        fp.loc[row, "status"] = 1004
    n_spatial = len(set(fp.loc[list(spatial), "UUID"])) if spatial else 0

    muni = fp[fp.muni].copy()
    ctx = fp[~fp.muni].copy()
    demolished = muni.status.notna() & (muni.status != 1004)
    n_demolished = muni.UUID[demolished].nunique()
    muni = muni[~demolished]

    info = existing.set_index("EGID")
    reg_rows = muni.EGID.isin(info.index)
    muni["kind"] = np.uint8(UNREGISTERED)
    muni["year"] = -1
    muni["frac"] = hash01(muni.EGID.where(muni.EGID > 0, muni.index.to_series()).to_numpy(), 5)
    for col in ("kind", "year", "frac"):
        muni.loc[reg_rows, col] = muni.EGID[reg_rows].map(info[col]).values
    muni["source"] = "swissBUILDINGS3D"
    muni["height"] = muni.GESAMTHOEHE
    miss = ~(muni.height > 0)
    floors = muni.EGID.map(info.GASTW)
    muni.loc[miss, "height"] = (floors[miss] * STOREY_HEIGHT + 1.0).fillna(6.0)

    # --- register buildings not (yet) in the 3D model: boxes ---------------------
    drawn = set(muni.EGID[reg_rows])
    rest = existing[~existing.EGID.isin(drawn)]
    box = rest[(rest.year >= BOX_FROM_YEAR) & (rest.GAREA >= BOX_MIN_AREA)
               & ~rest.GKLAS.isin(NOT_A_BOX)
               & ~rest.GBEZ.fillna("").str.contains("garage|unterirdisch|Unterstand", case=False)]
    side = np.sqrt(box.GAREA.to_numpy())
    geoms = shapely.box(box.GKODE - side / 2, box.GKODN - side / 2,
                        box.GKODE + side / 2, box.GKODN + side / 2)
    boxes = pd.DataFrame(dict(
        UUID=[f"GWR-{e}" for e in box.EGID], OBJEKTART="register box", EGID=box.EGID.values,
        GESAMTHOEHE=np.nan, GELAENDEPUNKT=np.nan, HERKUNFT_JAHR=0, tile="", edition=0,
        area=box.GAREA.values, n_holes=0, x=box.GKODE.values, y=box.GKODN.values,
        geometry=shapely.to_wkb(geoms), muni=True, status=1004.0, kind=box.kind.values,
        year=box.year.values, frac=box.frac.values, source="register (box)",
        height=(box.GASTW.fillna(2) * STOREY_HEIGHT + 1.0).values))
    not_drawn = rest[~rest.EGID.isin(box.EGID)]

    ctx["kind"], ctx["year"], ctx["frac"] = np.uint8(CONTEXT), -1, 0.0
    ctx["source"] = "swissBUILDINGS3D"
    ctx["height"] = ctx.GESAMTHOEHE.where(ctx.GESAMTHOEHE > 0, 6.0)

    out = pd.concat([muni, boxes, ctx], ignore_index=True)
    out["kind"] = out.kind.astype(np.uint8)
    out["year"] = out.year.astype(np.int32)
    out["height"] = out.height.clip(2.5, 120.0).astype(np.float32)
    out["t"] = np.where(out.year > 0, out.year + out.frac, -1.0).astype(np.float64)
    out = out.drop(columns=["muni", "status"])
    pq.write_table(pa.Table.from_pandas(out, preserve_index=False),
                   C.PROCESSED / "buildings.parquet")

    # --- summary: every number the film shows ----------------------------------
    b = out[out.kind != CONTEXT].drop_duplicates("UUID")
    dated = b[b.kind.isin([STOCK, EXACT, NEIGHBOURS, DISTRIBUTION])]
    years = range(C.YEAR_STOCK, C.YEAR_END + 1)
    per_year = {y: dict(exact=int(((dated.year == y) & (dated.kind == EXACT)).sum()),
                        estimated=int(((dated.year == y) & dated.kind.isin([NEIGHBOURS, DISTRIBUTION])).sum()))
                for y in years}
    late = existing[existing.year > C.YEAR_END]
    summary = dict(
        municipality=C.MUNICIPALITY, bfs_nr=C.BFS_NR, year_stock=C.YEAR_STOCK,
        year_end=C.YEAR_END, register_export=str(pd.read_csv(
            C.RAW / "gwr" / "gebaeude_batiment_edificio.csv", sep="\t", usecols=["GEXPDAT"],
            nrows=1).GEXPDAT[0]),
        register=dict(
            records=int(len(reg)), existing=int(len(existing)),
            existing_with_year=int(existing.GBAUJ.notna().sum()),  # after placeholder years
            existing_period_only=int((existing.GBAUJ.isna() & existing.GBAUP.notna()).sum()),
            existing_neither=int((existing.GBAUJ.isna() & existing.GBAUP.isna()).sum()),
            demolished=int((reg.GSTAT == 1007).sum()),
            manual_corrections={str(k): v[1] for k, v in C.MANUAL_PERIODS.items()},
            placeholder_years={str(k): v for k, v in reg_all.attrs["placeholder_years"].items()},
            not_drawn=int(len(not_drawn)),
            not_drawn_by_class={str(int(k)) if pd.notna(k) else "none": int(v)
                                for k, v in not_drawn.GKLAS.value_counts(dropna=False).items()},
            later_than_end=int(len(late))),
        footprints=dict(
            in_municipality=int(fp[fp.muni].UUID.nunique()),
            linked_by_egid=int(fp[fp.muni & fp.EGID.isin(existing.EGID)].UUID.nunique()) - n_spatial,
            linked_by_location=n_spatial,
            dropped_demolished=int(n_demolished),
            boxes_from_register=int(len(boxes)),
            context_buildings=int(ctx.UUID.nunique())),
        buildings=dict(
            total=int(len(b)), dated=int(len(dated)),
            by_kind={KIND_NAMES[k]: int((b.kind == k).sum()) for k in KIND_NAMES if k != CONTEXT},
            stock=int((b.kind == STOCK).sum()),
            undated=int(b.kind.isin([UNDATED, UNREGISTERED]).sum())),
        per_year={str(y): v for y, v in per_year.items()},
        parameters=dict(impute_radius_m=C.IMPUTE_RADIUS, impute_k=C.IMPUTE_K,
                        impute_min=C.IMPUTE_MIN, box_from_year=BOX_FROM_YEAR,
                        box_min_area_m2=BOX_MIN_AREA, storey_height_m=STOREY_HEIGHT),
    )
    (C.PROCESSED / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(json.dumps({k: summary[k] for k in ("register", "footprints", "buildings")}, indent=1,
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
