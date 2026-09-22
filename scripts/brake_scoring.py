#!/usr/bin/env python3
"""Score a driver's braking against a target lap, corner by corner.

Step 2 of the sim racing training app: turn the raw brake/throttle/speed/
lap-distance traces from Step 1 (brake_trace.py) into per-corner braking
scores and coaching feedback, comparing a user's lap against a target lap
(a personal best, a provided baseline, or — paid tier — a fast coach lap).

For each braking zone found in the user's lap, we detect a matching zone in
the target lap and score six things:

    Initial hit:
      - brake point       (lap distance where braking begins, vs target)
      - time to peak       (seconds from brake point to peak pressure)
      - peak pressure       (how hard, 0-1, vs target)
    Release:
      - release start point (lap distance where trail-off begins, vs target)
      - smoothness           (re-presses / jerkiness during the release)
      - release vs apex       (does release finish before/after the apex,
                                relative to how the target does it)

Each metric gets a 0-100 score (100 = matches the target within tolerance),
combined into an overall per-zone score and a plain-English tip. Use --expert
for the full numeric breakdown, --out to write a JSON report (always includes
the raw per-zone trace, for a future detailed-trace UI), and --plot to save
an annotated brake-trace comparison image.

Usage:
    # Compare lap 4 of your session against lap 2 of a baseline/coach lap
    python brake_scoring.py your_session.ibt --lap 4 --target baseline.ibt --target-lap 2

    # Compare two laps within the same file (e.g. vs your own personal best)
    python brake_scoring.py session.ibt --lap 4 --target-lap 2

    # Full numeric breakdown + annotated plot + JSON report for tooling
    python brake_scoring.py session.ibt --lap 4 --target-lap 2 --expert --plot zones.png --out report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import matplotlib.pyplot as plt

from brake_trace import LapData, load_ibt, split_into_laps

# --- Brake zone detection -------------------------------------------------

START_THRESHOLD = 0.05       # brake input that counts as "on the brakes"
END_THRESHOLD = 0.02         # brake input that counts as "off the brakes"
MIN_RELEASE_SAMPLES = 3      # consecutive low-brake samples needed to confirm release
MIN_ZONE_SAMPLES = 5         # ignore blips shorter than this (noise, not a real brake zone)
RELEASE_START_DROP_FRAC = 0.10   # must drop >=10% off peak (and >=0.03 absolute) to count as "releasing"
RELEASE_START_DROP_FLOOR = 0.03
APEX_SEARCH_DISTANCE_M = 60.0    # how far past release-finish to look for the apex (min-speed point)
REVERSAL_EPS = 0.01              # brake increase bigger than this during release counts as a "re-press"

# --- Zone matching between user and target lap ----------------------------

ZONE_MATCH_TOLERANCE_M = 120.0  # max distance between two zones' brake points to consider them "the same corner"

# --- Scoring tolerances/penalties ------------------------------------------
# score = 100 within `tolerance` of target, then loses `penalty` points per
# unit of extra deviation, floored at 0. Tuned so a "close, human" difference
# stays near 100 and a clearly wrong technique drops toward 0.

BRAKE_POINT_TOLERANCE_M, BRAKE_POINT_PENALTY = 3.0, 4.0
TIME_TO_PEAK_TOLERANCE_S, TIME_TO_PEAK_PENALTY = 0.05, 400.0
PEAK_PRESSURE_TOLERANCE, PEAK_PRESSURE_PENALTY = 0.03, 300.0
RELEASE_START_TOLERANCE_M, RELEASE_START_PENALTY = 4.0, 3.0
RELEASE_VS_APEX_TOLERANCE_M, RELEASE_VS_APEX_PENALTY = 3.0, 3.0
ROUGHNESS_TOLERANCE, ROUGHNESS_PENALTY = 0.05, 250.0
REVERSAL_PENALTY_PER_COUNT = 15.0

# Relative importance of each metric in the overall zone score.
METRIC_WEIGHTS = {
    "brake_point": 1.0,
    "time_to_peak": 1.0,
    "peak_pressure": 1.0,
    "release_start": 1.0,
    "smoothness": 1.5,
    "release_vs_apex": 1.0,
}

TIP_SCORE_THRESHOLD = 90.0  # only surface a tip when a metric scores below this


@dataclass
class BrakeZone:
    start_idx: int
    peak_idx: int
    release_start_idx: int
    end_idx: int  # release-finish index (brake back near zero)


@dataclass
class ZoneFeatures:
    zone_index: int
    start_dist: float
    peak_dist: float
    peak_brake: float
    time_to_peak: float
    release_start_dist: float
    release_finish_dist: float
    apex_dist: float
    apex_speed: float
    release_vs_apex: float  # release_finish_dist - apex_dist
    reversal_count: int
    roughness: float
    raw: dict = field(default_factory=dict)  # lap_dist/brake/throttle/speed slice for plotting/export


@dataclass
class MetricScore:
    key: str
    label: str
    unit: str
    user_value: float
    target_value: float
    delta: float
    score: float


@dataclass
class ZoneScore:
    corner_label: str
    user: ZoneFeatures
    target: ZoneFeatures
    metrics: list
    overall_score: float
    tips: list


@dataclass
class LapScoreReport:
    user_label: str
    target_label: str
    zone_scores: list
    unmatched_user_zones: list
    overall_brake_score: float


def detect_brake_zones(lap: LapData) -> list:
    """Find contiguous braking events using hysteresis, and locate the peak
    and the point release begins within each one."""
    n = len(lap.brake)
    zones = []
    i = 0
    while i < n:
        if lap.brake[i] < START_THRESHOLD:
            i += 1
            continue

        start = i
        peak_idx = i
        peak_val = lap.brake[i]
        below_run = 0
        j = i
        while j < n:
            if lap.brake[j] > peak_val:
                peak_val = lap.brake[j]
                peak_idx = j
            if lap.brake[j] < END_THRESHOLD:
                below_run += 1
                if below_run >= MIN_RELEASE_SAMPLES:
                    break
            else:
                below_run = 0
            j += 1
        end_idx = min(j, n - 1)

        if end_idx - start + 1 >= MIN_ZONE_SAMPLES:
            release_threshold = peak_val - max(RELEASE_START_DROP_FLOOR, peak_val * RELEASE_START_DROP_FRAC)
            release_start_idx = end_idx
            for k in range(peak_idx, end_idx + 1):
                if lap.brake[k] <= release_threshold:
                    release_start_idx = k
                    break
            zones.append(BrakeZone(start, peak_idx, release_start_idx, end_idx))

        i = end_idx + 1

    return zones


def _find_apex(lap: LapData, zone: BrakeZone) -> tuple:
    """Approximate the apex as the lowest-speed point from the brake peak
    through a short window past release. A real corner-apex detector (e.g.
    from steering/yaw data) can replace this later without changing the
    scoring model."""
    n = len(lap.speed)
    release_dist = lap.lap_dist[zone.end_idx]
    search_end = zone.end_idx
    while search_end < n - 1 and lap.lap_dist[search_end] - release_dist < APEX_SEARCH_DISTANCE_M:
        search_end += 1

    window = range(zone.peak_idx, search_end + 1)
    apex_idx = min(window, key=lambda k: lap.speed[k])
    return lap.lap_dist[apex_idx], lap.speed[apex_idx]


def extract_zone_features(lap: LapData, zone: BrakeZone, zone_index: int) -> ZoneFeatures:
    start_dist = lap.lap_dist[zone.start_idx]
    peak_dist = lap.lap_dist[zone.peak_idx]
    peak_brake = lap.brake[zone.peak_idx]
    time_to_peak = lap.session_time[zone.peak_idx] - lap.session_time[zone.start_idx]
    release_start_dist = lap.lap_dist[zone.release_start_idx]
    release_finish_dist = lap.lap_dist[zone.end_idx]
    apex_dist, apex_speed = _find_apex(lap, zone)

    release_brake = lap.brake[zone.peak_idx:zone.end_idx + 1]
    reversal_count = sum(
        1 for k in range(1, len(release_brake)) if release_brake[k] - release_brake[k - 1] > REVERSAL_EPS
    )
    total_variation = sum(abs(release_brake[k] - release_brake[k - 1]) for k in range(1, len(release_brake)))
    net_change = release_brake[0] - release_brake[-1] if release_brake else 0.0
    roughness = max(0.0, total_variation - net_change)

    pad_before = max(0, zone.start_idx - 5)
    pad_after = min(len(lap.brake), zone.end_idx + 6)
    raw = {
        "lap_dist": lap.lap_dist[pad_before:pad_after],
        "brake": lap.brake[pad_before:pad_after],
        "throttle": lap.throttle[pad_before:pad_after],
        "speed": lap.speed[pad_before:pad_after],
    }

    return ZoneFeatures(
        zone_index=zone_index,
        start_dist=start_dist,
        peak_dist=peak_dist,
        peak_brake=peak_brake,
        time_to_peak=time_to_peak,
        release_start_dist=release_start_dist,
        release_finish_dist=release_finish_dist,
        apex_dist=apex_dist,
        apex_speed=apex_speed,
        release_vs_apex=release_finish_dist - apex_dist,
        reversal_count=reversal_count,
        roughness=roughness,
        raw=raw,
    )


def extract_all_zones(lap: LapData) -> list:
    return [extract_zone_features(lap, zone, i) for i, zone in enumerate(detect_brake_zones(lap))]


def match_zones(user_zones: list, target_zones: list) -> tuple:
    """Greedy nearest-neighbour matching by brake point, one-to-one."""
    pairs = []
    for u in user_zones:
        for t in target_zones:
            pairs.append((abs(u.start_dist - t.start_dist), u, t))
    pairs.sort(key=lambda p: p[0])

    used_user, used_target = set(), set()
    matched = []
    for dist, u, t in pairs:
        if dist > ZONE_MATCH_TOLERANCE_M:
            break
        if id(u) in used_user or id(t) in used_target:
            continue
        used_user.add(id(u))
        used_target.add(id(t))
        matched.append((u, t))

    matched.sort(key=lambda pair: pair[0].start_dist)
    unmatched = [u for u in user_zones if id(u) not in used_user]
    return matched, unmatched


def _score(diff: float, tolerance: float, penalty: float) -> float:
    d = abs(diff)
    if d <= tolerance:
        return 100.0
    return max(0.0, 100.0 - penalty * (d - tolerance))


def score_zone(user: ZoneFeatures, target: ZoneFeatures) -> ZoneScore:
    metrics = [
        MetricScore(
            "brake_point", "Brake point", "m",
            user.start_dist, target.start_dist, user.start_dist - target.start_dist,
            _score(user.start_dist - target.start_dist, BRAKE_POINT_TOLERANCE_M, BRAKE_POINT_PENALTY),
        ),
        MetricScore(
            "time_to_peak", "Time to peak pressure", "s",
            user.time_to_peak, target.time_to_peak, user.time_to_peak - target.time_to_peak,
            _score(user.time_to_peak - target.time_to_peak, TIME_TO_PEAK_TOLERANCE_S, TIME_TO_PEAK_PENALTY),
        ),
        MetricScore(
            "peak_pressure", "Peak brake pressure", "",
            user.peak_brake, target.peak_brake, user.peak_brake - target.peak_brake,
            _score(user.peak_brake - target.peak_brake, PEAK_PRESSURE_TOLERANCE, PEAK_PRESSURE_PENALTY),
        ),
        MetricScore(
            "release_start", "Release start point", "m",
            user.release_start_dist, target.release_start_dist,
            user.release_start_dist - target.release_start_dist,
            _score(
                user.release_start_dist - target.release_start_dist,
                RELEASE_START_TOLERANCE_M, RELEASE_START_PENALTY,
            ),
        ),
        MetricScore(
            "smoothness", "Release smoothness", "",
            user.roughness, 0.0, user.roughness,
            max(
                0.0,
                100.0
                - REVERSAL_PENALTY_PER_COUNT * user.reversal_count
                - ROUGHNESS_PENALTY * max(0.0, user.roughness - ROUGHNESS_TOLERANCE),
            ),
        ),
        MetricScore(
            "release_vs_apex", "Release finish vs apex", "m",
            user.release_vs_apex, target.release_vs_apex, user.release_vs_apex - target.release_vs_apex,
            _score(
                user.release_vs_apex - target.release_vs_apex,
                RELEASE_VS_APEX_TOLERANCE_M, RELEASE_VS_APEX_PENALTY,
            ),
        ),
    ]

    total_weight = sum(METRIC_WEIGHTS[m.key] for m in metrics)
    overall = sum(m.score * METRIC_WEIGHTS[m.key] for m in metrics) / total_weight

    tips = generate_tips(user, metrics)
    corner_label = f"Corner @ {user.start_dist:.0f}m"
    return ZoneScore(corner_label, user, target, metrics, overall, tips)


def generate_tips(user: ZoneFeatures, metrics: list) -> list:
    by_key = {m.key: m for m in metrics}
    tips = []

    bp = by_key["brake_point"]
    if bp.score < TIP_SCORE_THRESHOLD:
        if bp.delta > 0:
            tips.append(
                f"You brake {bp.delta:.0f}m later than the target here — good if you're carrying more "
                "speed in, but make sure you're still hitting the apex."
            )
        else:
            tips.append(
                f"You brake {abs(bp.delta):.0f}m earlier than the target — try holding off a little "
                "longer to carry more speed into the corner."
            )

    ttp = by_key["time_to_peak"]
    if ttp.score < TIP_SCORE_THRESHOLD:
        if ttp.delta > 0:
            tips.append(
                f"It takes you {ttp.user_value:.2f}s to reach peak pressure vs {ttp.target_value:.2f}s "
                "for the target — try squeezing the brake firmer and faster on the initial hit."
            )
        else:
            tips.append(
                "You reach peak brake pressure faster than the target here — good aggressive initial "
                "hit, just make sure it's controlled."
            )

    pp = by_key["peak_pressure"]
    if pp.score < TIP_SCORE_THRESHOLD:
        if pp.delta < 0:
            tips.append(
                f"Your peak brake pressure ({pp.user_value:.0%}) is lower than the target's "
                f"({pp.target_value:.0%}) — there's more stopping power available here."
            )
        else:
            tips.append(
                f"You're braking harder than the target here ({pp.user_value:.0%} vs "
                f"{pp.target_value:.0%}) — fine if it's controlled, but watch for lockups."
            )

    rs = by_key["release_start"]
    if rs.score < TIP_SCORE_THRESHOLD:
        tips.append(
            f"You start releasing the brake {abs(rs.delta):.0f}m "
            f"{'later' if rs.delta > 0 else 'earlier'} than the target."
        )

    sm = by_key["smoothness"]
    if sm.score < TIP_SCORE_THRESHOLD:
        if user.reversal_count > 0:
            tips.append(
                f"You re-pressed the brake {user.reversal_count} time(s) while trailing off — try one "
                "smooth, continuous release instead."
            )
        else:
            tips.append("Your brake release is a little jerky here — aim for a smoother, more even taper.")

    rva = by_key["release_vs_apex"]
    if rva.score < TIP_SCORE_THRESHOLD:
        if rva.delta > 0:
            tips.append(
                "You're trailing the brake past the apex more than the target — try releasing a touch "
                "earlier to get back to throttle sooner."
            )
        else:
            tips.append(
                "You're releasing the brake before the apex earlier than the target does — you may be "
                "able to carry the brake a little deeper here."
            )

    if not tips:
        tips.append("Very close to the target through this braking zone — nice work.")

    return tips


def score_lap(user_lap: LapData, target_lap: LapData) -> LapScoreReport:
    user_zones = extract_all_zones(user_lap)
    target_zones = extract_all_zones(target_lap)
    matched, unmatched = match_zones(user_zones, target_zones)

    zone_scores = [score_zone(u, t) for u, t in matched]
    overall = sum(zs.overall_score for zs in zone_scores) / len(zone_scores) if zone_scores else 0.0

    return LapScoreReport(
        user_label="user",
        target_label="target",
        zone_scores=zone_scores,
        unmatched_user_zones=unmatched,
        overall_brake_score=overall,
    )


# --- Reporting --------------------------------------------------------------


def print_report(report: LapScoreReport, expert: bool) -> None:
    print(f"\nBrake score: {report.overall_brake_score:.0f}/100 "
          f"across {len(report.zone_scores)} matched braking zone(s)")

    for zs in report.zone_scores:
        print(f"\n{zs.corner_label} — {zs.overall_score:.0f}/100")
        for tip in zs.tips:
            print(f"  - {tip}")
        if expert:
            print(f"  {'metric':<24}{'you':>10}{'target':>10}{'delta':>10}{'score':>8}")
            for m in zs.metrics:
                unit = m.unit
                print(
                    f"  {m.label:<24}{m.user_value:>10.3f}{m.target_value:>10.3f}"
                    f"{m.delta:>+10.3f}{m.score:>8.0f}" + (f" {unit}" if unit else "")
                )

    if report.unmatched_user_zones:
        unmatched_str = ", ".join(f"{z.start_dist:.0f}m" for z in report.unmatched_user_zones)
        print(
            f"\nNote: {len(report.unmatched_user_zones)} braking zone(s) in your lap had no close match "
            f"in the target lap (brake points: {unmatched_str}) and were not scored."
        )


def report_to_dict(report: LapScoreReport, user_label: str, target_label: str) -> dict:
    return {
        "user_lap": user_label,
        "target_lap": target_label,
        "overall_brake_score": report.overall_brake_score,
        "zones": [
            {
                "corner_label": zs.corner_label,
                "overall_score": zs.overall_score,
                "tips": zs.tips,
                "metrics": [asdict(m) for m in zs.metrics],
                "user_trace": zs.user.raw,
                "target_trace": zs.target.raw,
            }
            for zs in report.zone_scores
        ],
        "unmatched_user_zones": [
            {"start_dist": z.start_dist, "raw": z.raw} for z in report.unmatched_user_zones
        ],
    }


def plot_annotated_zones(user_lap: LapData, target_lap: LapData, report: LapScoreReport,
                          user_label: str, target_label: str, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.plot(user_lap.lap_dist, user_lap.brake, color="tab:red", linewidth=1.3, label=user_label)
    ax.plot(target_lap.lap_dist, target_lap.brake, color="tab:blue", linewidth=1.3, label=target_label)

    for zs in report.zone_scores:
        ax.axvline(zs.user.start_dist, color="tab:red", linestyle=":", alpha=0.4)
        ax.plot(zs.user.peak_dist, zs.user.peak_brake, marker="o", color="tab:red", markersize=5)
        ax.annotate(
            f"{zs.overall_score:.0f}",
            xy=(zs.user.start_dist, 1.0),
            xytext=(0, 4),
            textcoords="offset points",
            fontsize=8,
            ha="center",
            color="dimgray",
        )

    ax.set_xlabel("Lap Distance (m)")
    ax.set_ylabel("Brake (0 = off, 1 = full)")
    ax.set_title("Brake Trace Comparison — scored zones")
    ax.set_ylim(-0.02, 1.08)
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    print(f"\nSaved annotated plot to {out_path}")


# --- CLI ---------------------------------------------------------------------


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path, help="Path to the user's .ibt file")
    parser.add_argument("--lap", type=int, required=True, help="Lap number to score")
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Path to the target .ibt file (personal best / baseline / coach lap). "
        "Defaults to the same file as --lap.",
    )
    parser.add_argument("--target-lap", type=int, required=True, help="Lap number to score against")
    parser.add_argument("--expert", action="store_true", help="Print the full numeric metric breakdown per zone")
    parser.add_argument("--plot", type=Path, default=None, help="Save an annotated brake-trace comparison image")
    parser.add_argument("--out", type=Path, default=None, help="Write the full scoring report as JSON")

    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if not args.file.exists():
        print(f"error: {args.file} does not exist", file=sys.stderr)
        return 1
    target_path = args.target or args.file
    if not target_path.exists():
        print(f"error: {target_path} does not exist", file=sys.stderr)
        return 1

    try:
        user_laps = split_into_laps(load_ibt(args.file))
        target_laps = user_laps if target_path == args.file else split_into_laps(load_ibt(target_path))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.lap not in user_laps:
        available = ", ".join(str(n) for n in sorted(user_laps))
        print(f"error: lap {args.lap} not found in {args.file}. Available laps: {available}", file=sys.stderr)
        return 1
    if args.target_lap not in target_laps:
        available = ", ".join(str(n) for n in sorted(target_laps))
        print(f"error: lap {args.target_lap} not found in {target_path}. Available laps: {available}", file=sys.stderr)
        return 1

    user_lap = user_laps[args.lap]
    target_lap = target_laps[args.target_lap]
    user_label = f"{args.file.name} - Lap {args.lap}"
    target_label = f"{target_path.name} - Lap {args.target_lap}"

    report = score_lap(user_lap, target_lap)
    if not report.zone_scores:
        print("No matching braking zones were found between these two laps.", file=sys.stderr)
        return 1

    print_report(report, args.expert)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(report_to_dict(report, user_label, target_label), f, indent=2)
        print(f"\nWrote scoring report to {args.out}")

    if args.plot:
        plot_annotated_zones(user_lap, target_lap, report, user_label, target_label, args.plot)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
