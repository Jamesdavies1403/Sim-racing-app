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

## Step 2: Brake scoring model

[`scripts/brake_scoring.py`](scripts/brake_scoring.py) builds on Step 1 to
turn the brake trace into per-corner scores and coaching tips, comparing a
lap against a target lap (your own personal best, a provided baseline, or —
paid tier — a fast coach lap).

For every braking zone it finds, it scores six things against the target,
each 0–100:

- **Initial hit**: brake point (lap distance vs target), time to peak
  pressure, and peak pressure level.
- **Release**: release start point, smoothness of the trail-off (re-presses
  and jerkiness are penalized), and whether the release finishes before or
  after the apex, relative to how the target does it.

Each corner gets an overall score plus plain-English tips by default. Add
`--expert` for the full numeric breakdown per metric, `--plot` to save an
annotated brake-trace image with each corner's score marked, and `--out` to
write a full JSON report (always includes the raw per-corner trace, for a
future detailed-trace view).

### Running it

```bash
# Score lap 4 of your session against lap 2 of a baseline/coach lap
python scripts/brake_scoring.py your_session.ibt --lap 4 --target baseline.ibt --target-lap 2

# Score two laps within the same file (e.g. vs your own personal best)
python scripts/brake_scoring.py session.ibt --lap 4 --target-lap 2

# Full breakdown, annotated plot, and a JSON report for tooling
python scripts/brake_scoring.py session.ibt --lap 4 --target-lap 2 --expert --plot zones.png --out report.json
```

Example output:

```
Brake score: 81/100 across 2 matched braking zone(s)

Corner @ 192m — 67/100
  - You brake 10m later than the target here — good if you're carrying more
    speed in, but make sure you're still hitting the apex.
  - You re-pressed the brake 3 time(s) while trailing off — try one smooth,
    continuous release instead.
```

A note on the model: braking zones are detected automatically from the
brake trace (no manual corner markers needed), and matched to the target
lap's zones by lap distance. The apex point used for the "release vs apex"
metric is approximated as the lowest-speed point shortly after the brake
release — a placeholder for a proper corner/apex detector, which can
replace it later without changing how scoring works.

### What's next

XP, levels, badges, and the Rookie-to-Pro progression layer, plus scoring
the rest of the corner (entry, apex speed, exit throttle) for the paid
tier, on top of this telemetry and scoring pipeline.
