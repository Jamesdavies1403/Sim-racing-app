#!/usr/bin/env python3
"""XP, levels, and badges — the game layer on top of corner scoring.

Step 3 of the sim racing training app: turn a scored lap (the JSON report
produced by corner_scoring.py) into XP, a Rookie-to-Pro level, and badges,
tracked persistently in a small local profile file.

This module only reads the plain dict shape that
`corner_scoring.report_to_dict()` produces (overall_score, and per-zone
overall_score/metrics) — it doesn't depend on corner_scoring's internals, so
it can score XP for a lap regardless of how that report was produced.

Usage:
    # Award XP for a scored lap and update (or create) a profile file
    python progression.py report.json --profile profile.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROFILE_VERSION = 1
DEFAULT_PROFILE_PATH = Path("profile.json")

# --- Levels: Rookie -> Class D -> Class C -> Class B -> Class A -> Pro -----
# 5 numbered levels per tier, 30 levels total. Cumulative XP needed to REACH
# level n is 100 * n^2, so early levels come quickly and later ones take
# meaningfully longer (a training-app progression curve, not a sprint).

TIERS = ["Rookie", "Class D", "Class C", "Class B", "Class A", "Pro"]
LEVELS_PER_TIER = 5
MAX_LEVEL = len(TIERS) * LEVELS_PER_TIER


def xp_threshold(level: int) -> int:
    """Cumulative XP required to REACH this level (level 0 = 0 XP)."""
    if level <= 0:
        return 0
    return 100 * level * level


def tier_for_level(level: int) -> str:
    index = min((max(level, 1) - 1) // LEVELS_PER_TIER, len(TIERS) - 1)
    return TIERS[index]


def level_title(level: int) -> str:
    tier = tier_for_level(level)
    rank_in_tier = ((max(level, 1) - 1) % LEVELS_PER_TIER) + 1
    return f"{tier} {rank_in_tier}"


@dataclass
class LevelInfo:
    level: int
    title: str
    xp_into_level: int
    xp_for_next_level: int  # None-equivalent (0) once MAX_LEVEL is reached

    @property
    def progress_fraction(self) -> float:
        if self.xp_for_next_level <= 0:
            return 1.0
        return min(1.0, self.xp_into_level / self.xp_for_next_level)


def level_for_xp(total_xp: int) -> LevelInfo:
    level = 1
    while level < MAX_LEVEL and total_xp >= xp_threshold(level + 1):
        level += 1
    xp_into_level = total_xp - xp_threshold(level)
    xp_for_next_level = xp_threshold(level + 1) - xp_threshold(level) if level < MAX_LEVEL else 0
    return LevelInfo(level, level_title(level), xp_into_level, xp_for_next_level)


# --- XP -----------------------------------------------------------------

LAP_COMPLETION_XP = 20  # flat XP just for submitting a scored lap


def compute_lap_xp(report: dict) -> int:
    """XP = a flat completion bonus + each corner's score (0-100) as XP."""
    corner_xp = sum(round(zone["overall_score"]) for zone in report["zones"])
    return LAP_COMPLETION_XP + corner_xp


# --- Badges ---------------------------------------------------------------
# Each rule takes (report, profile-after-this-lap-but-before-badges) and
# returns True if it should be (re-)awarded. Badges are only granted once —
# `award_lap` filters out ones the profile already has.

def _metric_scores(report: dict, key: str) -> list:
    return [
        m["score"]
        for zone in report["zones"]
        for m in zone["metrics"]
        if m["key"] == key
    ]


def _all_zones_score_at_least(report: dict, key: str, threshold: float) -> bool:
    scores = _metric_scores(report, key)
    return bool(scores) and all(s >= threshold for s in scores)


BADGES = {
    "first_scored_lap": {
        "name": "First Lap Scored",
        "description": "Score your first lap.",
        "rule": lambda report, profile: profile["laps_scored"] == 1,
    },
    "clean_sweep": {
        "name": "Clean Sweep",
        "description": "Score 95+ overall on a lap.",
        "rule": lambda report, profile: report["overall_score"] >= 95,
    },
    "on_the_money": {
        "name": "On The Money",
        "description": "Hit the brake point (within tolerance) on every corner of a lap.",
        "rule": lambda report, profile: _all_zones_score_at_least(report, "brake_point", 95),
    },
    "smooth_operator": {
        "name": "Smooth Operator",
        "description": "Perfectly smooth brake release on every corner of a lap.",
        "rule": lambda report, profile: _all_zones_score_at_least(report, "smoothness", 100),
    },
    "apex_hunter": {
        "name": "Apex Hunter",
        "description": "Match the target's apex speed (within tolerance) on every corner of a lap.",
        "rule": lambda report, profile: _all_zones_score_at_least(report, "apex_speed", 90),
    },
    "clean_exit": {
        "name": "Clean Exit",
        "description": "Perfectly smooth throttle application on every corner of a lap.",
        "rule": lambda report, profile: _all_zones_score_at_least(report, "exit_smoothness", 100),
    },
    "corner_master": {
        "name": "Corner Master",
        "description": "Score a perfect 100 on a single corner.",
        "rule": lambda report, profile: any(z["overall_score"] >= 100 for z in report["zones"]),
    },
    "on_a_roll": {
        "name": "On a Roll",
        "description": "Score 80+ overall on 5 laps in a row.",
        "rule": lambda report, profile: profile["current_streak_80"] >= 5,
    },
    "century_club": {
        "name": "Century Club",
        "description": "Reach 10,000 total XP.",
        "rule": lambda report, profile: profile["xp"] >= 10000,
    },
}


# --- Profile persistence ---------------------------------------------------


def default_profile() -> dict:
    return {
        "version": PROFILE_VERSION,
        "xp": 0,
        "laps_scored": 0,
        "best_overall_score": 0.0,
        "current_streak_80": 0,
        "badges": [],
        "history": [],
    }


def load_profile(path: Path) -> dict:
    if not path.exists():
        return default_profile()
    with open(path) as f:
        profile = json.load(f)
    profile.setdefault("current_streak_80", 0)
    profile.setdefault("history", [])
    return profile


def save_profile(profile: dict, path: Path) -> None:
    with open(path, "w") as f:
        json.dump(profile, f, indent=2)


HISTORY_LIMIT = 20


def award_lap(profile: dict, report: dict) -> dict:
    """Apply a scored lap to the profile in place, and return a summary of
    what happened (XP gained, level up, new badges)."""
    level_before = level_for_xp(profile["xp"])

    xp_earned = compute_lap_xp(report)
    profile["xp"] += xp_earned
    profile["laps_scored"] += 1
    profile["best_overall_score"] = max(profile["best_overall_score"], report["overall_score"])
    profile["current_streak_80"] = (
        profile["current_streak_80"] + 1 if report["overall_score"] >= 80 else 0
    )

    new_badges = []
    for badge_id, badge in BADGES.items():
        if badge_id in profile["badges"]:
            continue
        if badge["rule"](report, profile):
            profile["badges"].append(badge_id)
            new_badges.append(badge_id)

    level_after = level_for_xp(profile["xp"])

    profile["history"].append({
        "user_lap": report.get("user_lap"),
        "target_lap": report.get("target_lap"),
        "overall_score": report["overall_score"],
        "xp_earned": xp_earned,
    })
    profile["history"] = profile["history"][-HISTORY_LIMIT:]

    return {
        "xp_earned": xp_earned,
        "total_xp": profile["xp"],
        "level_before": level_before,
        "level_after": level_after,
        "leveled_up": level_after.level > level_before.level,
        "new_badges": [
            {"id": bid, "name": BADGES[bid]["name"], "description": BADGES[bid]["description"]}
            for bid in new_badges
        ],
    }


def badge_catalog(profile: dict) -> list:
    """All badges, marked earned/locked — for a UI to render a full case."""
    return [
        {"id": bid, "name": b["name"], "description": b["description"], "earned": bid in profile["badges"]}
        for bid, b in BADGES.items()
    ]


def _level_info_to_dict(info: LevelInfo) -> dict:
    return {
        "level": info.level,
        "title": info.title,
        "xp_into_level": info.xp_into_level,
        "xp_for_next_level": info.xp_for_next_level,
        "progress_fraction": info.progress_fraction,
    }


def result_to_dict(result: dict, profile: dict) -> dict:
    return {
        "xp_earned": result["xp_earned"],
        "total_xp": result["total_xp"],
        "level_before": _level_info_to_dict(result["level_before"]),
        "level_after": _level_info_to_dict(result["level_after"]),
        "leveled_up": result["leveled_up"],
        "new_badges": result["new_badges"],
        "badges": badge_catalog(profile),
        "laps_scored": profile["laps_scored"],
        "best_overall_score": profile["best_overall_score"],
        "current_streak_80": profile["current_streak_80"],
    }


# --- CLI ---------------------------------------------------------------------


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", type=Path, help="Path to a corner_scoring.py JSON report")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH, help="Path to the profile JSON file")
    parser.add_argument("--out", type=Path, default=None, help="Write the XP/level/badge result as JSON")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if not args.report.exists():
        print(f"error: {args.report} does not exist", file=sys.stderr)
        return 1

    with open(args.report) as f:
        report = json.load(f)

    profile = load_profile(args.profile)
    result = award_lap(profile, report)
    save_profile(profile, args.profile)

    print(f"+{result['xp_earned']} XP ({result['total_xp']} total)")
    print(f"Level: {result['level_after'].title} "
          f"({result['level_after'].xp_into_level}/{result['level_after'].xp_for_next_level or '-'} XP)")
    if result["leveled_up"]:
        print(f"Level up! {result['level_before'].title} -> {result['level_after'].title}")
    for badge in result["new_badges"]:
        print(f"New badge: {badge['name']} — {badge['description']}")

    if args.out:
        with open(args.out, "w") as f:
            json.dump(result_to_dict(result, profile), f, indent=2)
        print(f"\nWrote XP/level/badge summary to {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
