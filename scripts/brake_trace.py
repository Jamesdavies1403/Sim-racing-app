#!/usr/bin/env python3
"""Read iRacing .ibt telemetry files and compare two laps' brake traces.

Step 1 of the sim racing training app: prove out .ibt parsing and a basic
brake-vs-distance comparison plot before any scoring/gamification is built.

Usage:
    # See what laps are in a file, with approximate lap times
    python brake_trace.py path/to/session.ibt --list-laps

    # Compare lap 4 and lap 7 from the same file
    python brake_trace.py path/to/session.ibt --lap1 4 --lap2 7

    # Compare a lap from one file against a lap from another (e.g. your
    # lap vs a coach/baseline lap)
    python brake_trace.py my_lap.ibt --lap1 3 --file2 baseline.ibt --lap2 5

    # Save the plot instead of opening a window (useful over SSH/headless)
    python brake_trace.py session.ibt --lap1 4 --lap2 7 --out brake_compare.png
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import irsdk
import matplotlib.pyplot as plt

# The telemetry channels this step needs. iRacing records hundreds of
# channels; we only pull the ones relevant to brake/throttle/speed scoring.
CHANNELS = ["Lap", "LapDist", "Brake", "Throttle", "Speed"]
OPTIONAL_CHANNELS = ["LapCurrentLapTime"]


@dataclass
class LapData:
    lap_num: int
    lap_dist: list = field(default_factory=list)
    brake: list = field(default_factory=list)
    throttle: list = field(default_factory=list)
    speed: list = field(default_factory=list)
    lap_time_samples: list = field(default_factory=list)

    @property
    def approx_lap_time(self):
        return max(self.lap_time_samples) if self.lap_time_samples else None


def load_ibt(path: Path) -> dict:
    """Open an .ibt file and pull the raw per-sample channel data."""
    ibt = irsdk.IBT()
    ibt.open(str(path))
    try:
        available = set(ibt.var_headers_names or [])
        missing = [c for c in CHANNELS if c not in available]
        if missing:
            raise RuntimeError(
                f"{path} is missing expected channel(s): {missing}. "
                "Make sure telemetry recording was enabled for this session."
            )
        data = {name: ibt.get_all(name) for name in CHANNELS}
        for name in OPTIONAL_CHANNELS:
            if name in available:
                data[name] = ibt.get_all(name)
    finally:
        ibt.close()
    return data


def split_into_laps(data: dict) -> dict:
    """Group per-sample channel data into per-lap arrays keyed by lap number."""
    laps: dict[int, LapData] = {}
    has_lap_time = "LapCurrentLapTime" in data
    n_samples = len(data["Lap"])

    for i in range(n_samples):
        lap_num = data["Lap"][i]
        # iRacing uses negative lap numbers before the session properly starts.
        if lap_num is None or lap_num < 0:
            continue
        lap = laps.setdefault(lap_num, LapData(lap_num=lap_num))
        lap.lap_dist.append(data["LapDist"][i])
        lap.brake.append(data["Brake"][i])
        lap.throttle.append(data["Throttle"][i])
        lap.speed.append(data["Speed"][i])
        if has_lap_time:
            lap.lap_time_samples.append(data["LapCurrentLapTime"][i])

    return laps


def print_lap_summary(label: str, laps: dict) -> None:
    print(f"\nLaps found in {label}:")
    print(f"{'lap':>5}  {'samples':>8}  {'~lap time':>10}")
    for lap_num in sorted(laps):
        lap = laps[lap_num]
        lap_time = lap.approx_lap_time
        lap_time_str = f"{lap_time:.3f}s" if lap_time is not None else "n/a"
        print(f"{lap_num:>5}  {len(lap.lap_dist):>8}  {lap_time_str:>10}")


def plot_brake_comparison(
    laps_a: dict,
    lap_num_a: int,
    label_a: str,
    laps_b: dict,
    lap_num_b: int,
    label_b: str,
    out_path: Path | None,
) -> None:
    for laps, lap_num, source in [(laps_a, lap_num_a, "file1"), (laps_b, lap_num_b, "file2")]:
        if lap_num not in laps:
            available = ", ".join(str(n) for n in sorted(laps))
            raise ValueError(
                f"Lap {lap_num} not found in {source}. Available laps: {available}"
            )

    lap_a = laps_a[lap_num_a]
    lap_b = laps_b[lap_num_b]

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(lap_a.lap_dist, lap_a.brake, color="tab:red", linewidth=1.5, label=label_a)
    ax.plot(lap_b.lap_dist, lap_b.brake, color="tab:blue", linewidth=1.5, label=label_b)
    ax.set_xlabel("Lap Distance (m)")
    ax.set_ylabel("Brake (0 = off, 1 = full)")
    ax.set_title("Brake Trace Comparison")
    ax.set_ylim(-0.02, 1.02)
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()

    if out_path:
        fig.savefig(out_path, dpi=150)
        print(f"\nSaved plot to {out_path}")
    else:
        plt.show()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file1", type=Path, help="Path to the primary .ibt file")
    parser.add_argument(
        "--file2",
        type=Path,
        default=None,
        help="Path to a second .ibt file (defaults to file1, for comparing two laps in the same session)",
    )
    parser.add_argument("--lap1", type=int, help="Lap number from file1 to plot")
    parser.add_argument("--lap2", type=int, help="Lap number from file2 (or file1) to plot")
    parser.add_argument(
        "--list-laps",
        action="store_true",
        help="List available lap numbers and approximate lap times, then exit",
    )
    parser.add_argument("--out", type=Path, default=None, help="Save the plot to this image file instead of opening a window")

    args = parser.parse_args(argv)

    if not args.list_laps and (args.lap1 is None or args.lap2 is None):
        parser.error("--lap1 and --lap2 are required unless --list-laps is set")

    return args


def main(argv=None) -> int:
    args = parse_args(argv)

    if not args.file1.exists():
        print(f"error: {args.file1} does not exist", file=sys.stderr)
        return 1
    if args.file2 and not args.file2.exists():
        print(f"error: {args.file2} does not exist", file=sys.stderr)
        return 1

    try:
        data_a = load_ibt(args.file1)
        laps_a = split_into_laps(data_a)

        if args.file2:
            data_b = load_ibt(args.file2)
            laps_b = split_into_laps(data_b)
        else:
            laps_b = laps_a
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.list_laps:
        print_lap_summary(str(args.file1), laps_a)
        if args.file2:
            print_lap_summary(str(args.file2), laps_b)
        return 0

    label_a = f"{args.file1.name} - Lap {args.lap1}"
    label_b = f"{(args.file2 or args.file1).name} - Lap {args.lap2}"

    try:
        plot_brake_comparison(laps_a, args.lap1, label_a, laps_b, args.lap2, label_b, args.out)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
