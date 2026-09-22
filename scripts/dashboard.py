#!/usr/bin/env python3
"""Build the Apex Coach dashboard: an HTML page showing XP, level, badges,
and a per-corner brake/apex/throttle breakdown.

Step 4 of the sim racing training app: turns the scoring (Step 2,
corner_scoring.py) and progression (Step 3, progression.py) pipeline output
into the visual dashboard the driver actually looks at.

Usage:
    # Score each lap of a session first, in order:
    python corner_scoring.py session.ibt --lap 1 --target-lap 0 --out lap1.json
    python corner_scoring.py session.ibt --lap 2 --target-lap 0 --out lap2.json

    # Then build the dashboard from those reports, in the same order:
    python dashboard.py lap1.json lap2.json --profile profile.json --out dashboard.html

Each report is applied to the profile in order (via progression.award_lap),
so XP, level-ups, and badge unlocks in the dashboard reflect the session as
it actually happened lap by lap. The profile file is updated and saved, so
running this again later with new laps continues the same progression.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from progression import award_lap, load_profile, result_to_dict, save_profile

TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard_template.html"
DATA_PLACEHOLDER = "__DASHBOARD_DATA__"


def build_bundle(report_paths: list, profile_path: Path) -> dict:
    profile = load_profile(profile_path)
    laps = []
    for i, path in enumerate(report_paths, start=1):
        with open(path) as f:
            report = json.load(f)
        result = award_lap(profile, report)
        laps.append({
            "lap_number": i,
            "report": report,
            "xp_result": result_to_dict(result, profile),
        })
    save_profile(profile, profile_path)
    return {"laps": laps, "final_profile": profile}


def render_dashboard(bundle: dict) -> str:
    template = TEMPLATE_PATH.read_text()
    json_text = json.dumps(bundle).replace("<", "\\u003c")
    return template.replace(DATA_PLACEHOLDER, json_text)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reports", type=Path, nargs="+", help="corner_scoring.py JSON reports, in session order")
    parser.add_argument("--profile", type=Path, default=Path("profile.json"), help="Path to the profile JSON file")
    parser.add_argument("--out", type=Path, default=Path("dashboard.html"), help="Output HTML file")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    for path in args.reports:
        if not path.exists():
            print(f"error: {path} does not exist", file=sys.stderr)
            return 1
    if not TEMPLATE_PATH.exists():
        print(f"error: template not found at {TEMPLATE_PATH}", file=sys.stderr)
        return 1

    bundle = build_bundle(args.reports, args.profile)
    html = render_dashboard(bundle)
    args.out.write_text(html)
    print(f"Wrote dashboard to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
