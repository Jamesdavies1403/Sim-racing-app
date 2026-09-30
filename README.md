# Sim Racing Training App

A desktop training app for iRacing drivers — from Rookie to Pro — that turns
raw telemetry into game-like feedback: XP, levels, badges, and detailed
corner-by-corner scoring (braking, apex, throttle) against a personal best
or a target/coach lap.

This repo is being built incrementally.

- **Step 1**: a standalone script that reads iRacing's `.ibt` telemetry
  files and plots two laps' brake traces on top of each other, to prove out
  file parsing before building any scoring or UI on top of it.
- **Step 2**: a scoring model that turns those brake traces into per-corner
  scores and coaching tips against a target lap.

## Step 1: Brake trace comparison script

[`scripts/brake_trace.py`](scripts/brake_trace.py) reads one or two `.ibt`
files, extracts `Brake`, `Throttle`, `Speed`, and `LapDist` per lap, and
plots the brake trace (0–1) of two chosen laps against lap distance (in
meters) on the same chart — so you can visually compare where and how hard
one lap brakes versus another (e.g. your lap vs. a personal best, or your
lap vs. a friend's).

### Beginner setup

You don't need any programming experience to run this — just follow these
steps.

**1. Install Python**

- Windows: download Python 3.11+ from [python.org/downloads](https://www.python.org/downloads/)
  and run the installer. On the first screen, tick **"Add python.exe to PATH"**
  before clicking Install.
- Mac: download Python 3.11+ from the same link, or run `brew install python`
  if you already use Homebrew.

Verify it worked by opening a terminal (Command Prompt/PowerShell on
Windows, Terminal on Mac) and running:

```bash
python --version
```

(On Mac/Linux you may need `python3 --version` instead.)

**2. Get this project onto your computer**

If you have `git` installed:

```bash
git clone <this-repo-url>
cd Sim-racing-app
```

Otherwise, download the repo as a ZIP from GitHub and extract it, then open
a terminal in that folder.

**3. Create a virtual environment (keeps this project's packages separate
from everything else on your computer)**

```bash
python -m venv .venv
```

Activate it:

- Windows (PowerShell): `.venv\Scripts\Activate.ps1`
- Windows (Command Prompt): `.venv\Scripts\activate.bat`
- Mac/Linux: `source .venv/bin/activate`

You'll know it worked because your terminal prompt now starts with
`(.venv)`.

**4. Install the required packages**

```bash
pip install -r requirements.txt
```

This installs `pyirsdk` (reads iRacing's telemetry file format) and
`matplotlib` (draws the plot).

**5. Find your `.ibt` telemetry files**

iRacing automatically records telemetry for every session by default. Files
are saved as `.ibt` and are normally found here:

- Windows: `Documents\iRacing\telemetry\`

Each file is named after the car and track, e.g.
`mx5mx52016_watkinsglen boot_2024-06-01_18-30-15.ibt`. If the folder is
empty, open iRacing's app, go to **Options → Telemetry**, and make sure
**"Always record telemetry"** is switched on, then complete a session.

Copy or note the path to a `.ibt` file to use with the script.

### Running it

First, see what laps are in a file and roughly how long they took:

```bash
python scripts/brake_trace.py "C:\Users\you\Documents\iRacing\telemetry\yourfile.ibt" --list-laps
```

This prints something like:

```
Laps found in yourfile.ibt:
  lap   samples   ~lap time
    1     3612      98.412s
    2     3598      97.220s
    3     3605      97.905s
```

Then compare two laps from the same file (e.g. lap 2 vs lap 3):

```bash
python scripts/brake_trace.py "yourfile.ibt" --lap1 2 --lap2 3
```

A window will pop up showing both brake traces plotted against lap
distance, so you can compare braking points, peak brake pressure, and
release shape at a glance.

To compare a lap from one session against a lap from a different session
(e.g. your lap vs. a baseline/coach lap):

```bash
python scripts/brake_trace.py "your_lap.ibt" --lap1 4 --file2 "baseline.ibt" --lap2 2
```

To save the chart as an image instead of opening a window (useful if
you're running this over SSH or want to share the image):

```bash
python scripts/brake_trace.py "yourfile.ibt" --lap1 2 --lap2 3 --out brake_compare.png
```

### Troubleshooting

- **"is missing expected channel(s)"** — the `.ibt` file wasn't recorded
  with the channels this script needs. Make sure telemetry recording is
  enabled in iRacing and record a fresh session.
- **"Lap N not found"** — run with `--list-laps` first to see which lap
  numbers actually exist in that file.
- Lap 0 (or negative lap numbers) can show up for out-laps or before the
  session properly starts — that's normal, just pick a real flying lap.

## Step 2: Corner scoring model

[`scripts/corner_scoring.py`](scripts/corner_scoring.py) builds on Step 1 to
turn the brake/throttle/speed traces into full per-corner scores and
coaching tips, comparing a lap against a target lap (your own personal
best, a provided baseline, or — paid tier — a fast coach lap).

Each braking event anchors one "corner". For every corner it finds, it
scores eleven things against the target, each 0–100, grouped into five
phases:

- **Entry**: speed carried to the brake point, vs target.
- **Brake hit**: brake point (lap distance vs target), time to peak
  pressure, and peak pressure level.
- **Trail / release**: release start point, smoothness of the trail-off
  (re-presses and jerkiness are penalized), and whether the release
  finishes before or after the apex, relative to how the target does it.
- **Apex**: minimum corner speed, vs target.
- **Exit**: throttle pickup point, time to full throttle, and smoothness
  of the application (lifts/jerkiness are penalized).

Each corner gets an overall score plus plain-English tips by default. Add
`--expert` for the full numeric breakdown per metric (grouped by phase),
`--plot` to save an annotated brake/throttle comparison image, and `--out`
to write a full JSON report (always includes the raw per-corner trace, for
the dashboard in Step 4).

`--tier` matches the product's free/paid split: `free` scores braking only
(brake hit + trail/release — six metrics, meant for scoring against a
personal best or a provided baseline lap); `paid` (the default) scores all
eleven metrics across all five phases, meant for scoring against a fast
coach lap.

### Running it

```bash
# Score lap 4 of your session against lap 2 of a baseline/coach lap
python scripts/corner_scoring.py your_session.ibt --lap 4 --target baseline.ibt --target-lap 2

# Score two laps within the same file (e.g. vs your own personal best)
python scripts/corner_scoring.py session.ibt --lap 4 --target-lap 2

# Free-tier scoring: braking only, against a personal best/baseline
python scripts/corner_scoring.py session.ibt --lap 4 --target-lap 2 --tier free

# Full breakdown, annotated plot, and a JSON report for tooling
python scripts/corner_scoring.py session.ibt --lap 4 --target-lap 2 --expert --plot zones.png --out report.json
```

Example output:

```
Corner score: 81/100 across 2 matched corner(s)

Corner @ 192m — 67/100
  - You brake 10m later than the target here — good if you're carrying more
    speed in, but make sure you're still hitting the apex.
  - You re-pressed the brake 3 time(s) while trailing off — try one smooth,
    continuous release instead.
  - Your apex speed is 8.8 km/h slower than the target's — there may be more
    rotation/speed available through the middle of the corner.
```

A note on the model: corners are detected automatically from the brake
trace (no manual corner markers needed), and matched to the target lap's
corners by lap distance. The apex point is approximated as the lowest-speed
point from the brake peak through a short window past release — a
placeholder for a proper corner/apex detector (e.g. from steering/yaw
data), which can replace it later without changing how scoring works.

## Step 3: XP, levels, and badges

[`scripts/progression.py`](scripts/progression.py) is the game layer on top
of Step 2: it turns a scored lap into XP, a Rookie-to-Pro level, and
badges, tracked persistently in a small local profile file.

- **XP**: a flat completion bonus plus each corner's score (0–100) as XP —
  so both scoring more corners and scoring them well earns more XP.
- **Levels**: 30 levels across 6 tiers — Rookie, Class D, Class C, Class B,
  Class A, Pro — 5 numbered levels per tier. Later levels take meaningfully
  longer than early ones.
- **Badges**: rule-based achievements evaluated after every scored lap —
  first lap scored, a clean/smooth lap, hitting the brake point or apex
  speed on every corner, a streak of good laps, an XP milestone, and more.

```bash
# Award XP for a scored lap (Step 2's --out) and update a profile file
python scripts/progression.py report.json --profile profile.json
```

```
+164 XP (164 total)
Level: Rookie 1 (64/300 XP)
New badge: First Lap Scored — Score your first lap.
```

## Step 4: Dashboard + brake-replication trainer

[`scripts/dashboard.py`](scripts/dashboard.py) builds a deliberately simple
HTML dashboard from one or more Step 2 reports, applying them through
Step 3's progression in session order so the page shows XP, level-ups, and
badge unlocks as they actually happened lap by lap. The resting page is
just: a level/XP line, a Practice card, a flat per-lap score list, and a
collapsed badge count — the detailed per-corner breakdown lives inside
Practice mode instead of cluttering the review.

**Practice mode** is an interactive trainer, not just a report. Pick a
corner, and it walks you through:

1. A countdown, then a target brake-pressure trace plays out on a chart.
2. You hold and drag a pedal control (mouse or touch) in real time to
   replicate it — dragging further down means more brake pressure.
3. A live overlay tells you what's happening as you go: "BRAKE NOW" when
   you're late to the brake point, "MORE BRAKE" / "EASE OFF" when you're
   off the target pressure, "ON TARGET" when you're matching it.
4. When the run ends, an overlay shows your trace against the target, a
   score, and 2–3 plain-English tips (braked early/late, too soft/hard,
   jerky release) — then Try Again or move to the next corner.

The practice scoring is a simplified, client-side port of the same
brake-point/peak-pressure/time-to-peak/smoothness model from Step 2 — it
runs entirely in the browser against the real target traces already in the
report data, no server or Python involved once the page is built.

```bash
# Score each lap of a session first, in order:
python scripts/corner_scoring.py session.ibt --lap 1 --target-lap 0 --out lap1.json
python scripts/corner_scoring.py session.ibt --lap 2 --target-lap 0 --out lap2.json

# Then build the dashboard from those reports, in the same order:
python scripts/dashboard.py lap1.json lap2.json --profile profile.json --out dashboard.html
```

Open `dashboard.html` in a browser. Running it again later with more laps
appended continues the same profile, so the dashboard reflects ongoing
progress rather than resetting each time.

This is a sample dashboard over synthetic test data — see it at
https://claude.ai/artifact/QQCdxgBcgqvgVGAg4q8u4w.

## Step 5: Tests

The whole pipeline (`brake_trace.py`, `corner_scoring.py`, `progression.py`,
`dashboard.py`) has an automated test suite under [`tests/`](tests), built
on [`tests/ibt_builder.py`](tests/ibt_builder.py) — a from-scratch,
dependency-free writer for the `.ibt` binary format (verified against
`pyirsdk`'s own source), so tests exercise the real file-parsing path
instead of mocking it.

```bash
pip install -r requirements-dev.txt
pytest -v
```

CI ([`.github/workflows/tests.yml`](.github/workflows/tests.yml)) runs this
on every push and pull request.

All of this project's testing so far — this suite included — has been
against **synthetic** `.ibt` fixtures, because no real recorded iRacing
session has been run through it yet. That's the single most important
thing to do next; see below.

## Roadmap

Roughly in priority order:

### Do this first
- **Run it against a real recorded session.** Record a lap or two in
  iRacing (or find a `.ibt` under `Documents\iRacing\telemetry\`) and run
  it through Steps 1–4 end to end. Everything so far has only been
  validated against hand-built synthetic fixtures (see `tests/ibt_builder.py`)
  — real telemetry is noisier, may have channels this doesn't expect, and
  is the real test of whether zone detection and scoring hold up. Whatever
  breaks first is the next task.

### Correctness & robustness
- Replace the apex heuristic (lowest speed in a window after brake
  release) with a real corner/apex detector — steering angle or lateral-G
  data would let it find the true geometric apex instead of assuming it's
  near the brake zone. `brake_trace.py` would need to extract a
  `SteeringWheelAngle` or `LatAccel` channel first.
- Handle incidents and off-tracks: a spin or an off-track excursion mid-lap
  currently just looks like weird brake/speed data and would produce a
  confusing score. Detect `LapDist` discontinuities or an `OnPitRoad`/
  incident channel and either skip or flag the affected corners.
- Guard against mismatched comparisons: nothing currently checks that the
  user lap and target lap are even the same track/car. Compare lap length
  (`max(LapDist)`) as a sanity check before scoring, and warn if they
  differ.
- Decide what happens with more corners than a target has (or vice versa)
  on a genuinely different track layout — right now unmatched zones are
  silently dropped from the score, which is fine for "same track, off day"
  but not for "wrong track entirely."

### Product completeness (per the original brief)
- **Baseline lap library**: a way to import/manage multiple `.ibt` files
  and pick "personal best" automatically (the free tier needs this —
  right now the user must name the exact file/lap manually).
- **Coach laps** (paid tier): some source of "fast coach lap" `.ibt`
  files per track/car combo — this is a content problem as much as a code
  one.
- Feed practice-run results back into XP/badges (currently practice mode
  is scored but doesn't touch the profile at all).
- More badge variety and a few more levels' worth of milestone content as
  real usage data comes in.

### Turning this into an actual desktop app
Right now this is a set of CLI scripts plus a generated static HTML page —
not yet the "desktop app" from the original brief. In rough order:
1. Auto-detect the iRacing telemetry folder (`Documents\iRacing\telemetry\`
   on Windows) and list sessions instead of requiring a file path.
2. A real UI shell (Tauri or Electron would let the existing HTML/JS
   dashboard become the UI directly, with Python running underneath via a
   sidecar process or a small local HTTP server; PyQt/PySide is the
   alternative if the UI gets rebuilt in Python instead).
3. Live mode: iRacing's SDK also exposes telemetry over shared memory while
   a session is running — a live brake-pressure overlay *during* a real
   session (not just the after-the-fact `.ibt` file and the practice
   trainer) would be a genuinely different, higher-value feature.
4. Packaging: a installer/build step so this is a double-click app for a
   non-technical user, not "clone the repo and run Python scripts."

### Nice-to-haves
- A lap browser / session history view in the dashboard.
- Exporting a shareable "coach report" (PDF or image) for a single lap.
- Multi-user profiles if this is ever used by more than one driver on the
  same machine.
