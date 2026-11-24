# The prompt, rewritten

The original request was:

> Write a better version of my prompt and start implementing:
>
> Goal a Animation of the development of Rapperswil-Jona SG in Switzerland similar to the one
> in my fotos of hong kong - out of a insta reel. Look for sites which provide valide data,
> verified so on. Also look up my repository link:
> https://github.com/CarlitoVital/Fable_5.2_Test_10.11.2026 it is a similar project.
> If there is not enough data, let me know and I choose another swiss city.

Below is a version that gets the same result with fewer round trips. It describes the
reference instead of only pointing at it, names the data that exists in Switzerland, says
what "valid data" means, and settles the decisions an agent would otherwise have to guess.

---

## Goal

Make a short vertical video that shows how **Rapperswil-Jona (canton St. Gallen,
Switzerland)** was built up, building by building, in the style of the attached Instagram
reel (tulparmaps, "Day 9 – Hong Kong buildings construction year") and of my previous
project <https://github.com/CarlitoVital/Fable_5.2_Test_10.11.2026> (La Paz · El Alto).
Reuse that project's architecture (Python pipeline, Numba software renderer, overlay,
config files) wherever it fits.

## The reference – what to keep

- 4:5 portrait frame, dark hill-shaded 3D landscape seen obliquely, filling the frame;
- buildings as extruded 3D blocks that appear in their construction year, coloured on a
  ramp from deep red (old) through orange to pale peach (new); undated buildings in grey;
- top left: letter-spaced kicker and heavy sans-serif title;
- bottom left: big year counter, running building count, histogram "buildings completed
  per year" with a playhead;
- two footer lines: a methods note, then author and data credits.

Keep that look. Never show the town upside down (the reel's comments complain about
exactly that): camera on the lake side, south or south-west of the town, so Lake Zurich and
the Obersee are in the foreground; a moderate turn away from north-up is fine if it fills
the portrait frame better.

## Data – official sources first, tested, and honest

Switzerland publishes everything needed as open government data – use it instead of
satellite estimates:

| Need | Dataset | Publisher |
|---|---|---|
| Construction year / period per building | Federal Register of Buildings and Dwellings (GWR), public download per canton (`GBAUJ` year, `GBAUP` period, `GSTAT` status, `EGID`) | Federal Statistical Office (BFS) |
| Footprints and heights | swissBUILDINGS3D 3.0, layer `Floor` (has `EGID`, `GESAMTHOEHE`) | swisstopo |
| Terrain | swissALTIRegio 10 m (or swissALTI3D 2 m) | swisstopo |
| Municipal boundary | swissBOUNDARIES3D (BFS no. 3340) | swisstopo |
| Forest / land-cover tint | NFI vegetation height model, or Arealstatistik | WSL/FOEN, BFS |

Rules:

1. **Check coverage before building anything** and report it: how many standing buildings
   the register has for Rapperswil-Jona, how many carry an exact year, how many only a
   period, how many neither. If less than about half can be dated, stop and tell me – I will
   pick another Swiss city.
2. **Link by EGID**, not by guesswork; fall back to point-in-polygon only where the EGID is
   missing, and report both rates.
3. **No invented precision.** Where the register gives only a period (e.g. 1961–1970), the
   year shown is an estimate – say so on screen and keep exact and estimated years
   distinguishable (e.g. solid vs. faded bars). Test the estimation rule by hiding known
   years and measuring the error against simple baselines.
4. **Undated stays undated** – shown in grey and counted, never silently placed.
5. The timeline starts where the register starts being specific (it lumps everything
   before 1919 together): open with "before 1919" as one stock, then year by year.
6. Only buildings standing today are drawn (the register has demolition years only since
   about 1999) – state it.
7. Look for register artefacts before trusting the years: placeholder years (a year with
   far more buildings than its neighbours), default months, records without any date
   (e.g. the castle), and the 3D model lagging the register for the newest buildings.
   Handle each with a written rule, not by hand – except a few documented landmark fixes.

## Deliverables

1. `out/*.mp4` – 1080 × 1350 px (4:5), 30 fps, about 30 s, H.264, plus a 1080 × 1920 (9:16)
   Reels version and a poster frame.
2. A reproducible pipeline: `python -m pipeline` (fetch + process), `python -m
   render.animate` (render). Raw data is downloaded, never committed.
3. `render/config.py` for everything visual, `pipeline/config.py` for area, sources and
   rules.
4. `README.md` with the source table and licences, the dating method in plain language, the
   checks and their results, the limitations, and how to point it at another Swiss
   municipality (should only need the BFS number).
5. `docs/CAPTION.md` – post text in German and English.

## Visual spec

- Area: the municipality of Rapperswil-Jona (31 km²) with the lake, the Seedamm and the
  hills behind; neighbouring towns may appear as dim, undated context.
- Start close on the old town while the pre-1919 stock rises (centre outwards), then pull
  back as the town grows, one beat per year to today; new buildings rise and glow briefly;
  finish on a still hold.
- Heights from swissBUILDINGS3D, exaggerated just enough to read at town scale.
- Three or four quiet place labels for orientation (Rapperswil, Jona, Kempraten, Bollingen).
- Montserrat, legible on a phone.

## Constraints

- Renders on a laptop without a GPU; no accounts, keys or paid services.
- Credit every source in the frame (GWR licence: attribution mandatory).
- Keep the repository small: code, fonts, final video, poster, a summary of the numbers.

## How to work

Show me something at each stage before going on: (1) the coverage table, (2) a top-down
map of building years, (3) one 3D still, (4) a low-resolution preview, (5) the final render.
Ask before decisions that change the story (start year, area, what counts as a building);
decide the rest and note it in the README.

## Done means

- [ ] Every number on screen traces back to a source file and a rule in the code.
- [ ] Known landmarks come out right (old town and castle before 1919; recent districts
      after 2000), and the per-period totals match the register.
- [ ] The video plays on a phone, the lettering is readable, nothing is cut off.
- [ ] Someone else can rerun it from the README.
