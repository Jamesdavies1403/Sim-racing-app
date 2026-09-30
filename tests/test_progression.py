import json

import pytest

import progression as pr


def _report(overall_score, zone_scores=None):
    """A minimal report dict shaped like corner_scoring.report_to_dict()'s
    output -- progression.py only reads this plain-dict shape."""
    if zone_scores is None:
        zone_scores = [{"key": "brake_point", "score": overall_score}]
    return {
        "user_lap": "test.ibt - Lap 1",
        "target_lap": "test.ibt - Lap 0",
        "overall_score": overall_score,
        "zones": [{"overall_score": overall_score, "metrics": zone_scores}],
    }


# --- Levels ------------------------------------------------------------------

def test_level_for_xp_never_negative_into_level():
    for xp in [0, 1, 50, 99, 100, 150, 399, 400, 1391, 50000]:
        info = pr.level_for_xp(xp)
        assert info.xp_into_level >= 0, f"negative xp_into_level at xp={xp}: {info}"
        assert 1 <= info.level <= pr.MAX_LEVEL


def test_level_for_xp_zero_is_rookie_one_with_zero_progress():
    info = pr.level_for_xp(0)
    assert info.level == 1
    assert info.title == "Rookie 1"
    assert info.xp_into_level == 0
    assert info.progress_fraction == 0.0


def test_level_thresholds_are_monotonic():
    prev = -1
    for level in range(1, pr.MAX_LEVEL + 1):
        threshold = pr.xp_threshold(level)
        assert threshold > prev
        prev = threshold


def test_level_title_moves_through_tiers():
    assert pr.level_title(1) == "Rookie 1"
    assert pr.level_title(5) == "Rookie 5"
    assert pr.level_title(6) == "Class D 1"
    assert pr.level_title(pr.MAX_LEVEL) == "Pro 5"


def test_progress_fraction_caps_at_one_at_max_level():
    info = pr.level_for_xp(pr.xp_threshold(pr.MAX_LEVEL) + 10_000)
    assert info.level == pr.MAX_LEVEL
    assert info.xp_for_next_level == 0
    assert info.progress_fraction == 1.0


# --- XP -----------------------------------------------------------------

def test_compute_lap_xp_is_completion_bonus_plus_corner_scores():
    report = _report(90, zone_scores=[{"key": "k", "score": 90}])
    report["zones"] = [
        {"overall_score": 80, "metrics": []},
        {"overall_score": 95, "metrics": []},
    ]
    xp = pr.compute_lap_xp(report)
    assert xp == pr.LAP_COMPLETION_XP + 80 + 95


# --- Badges ---------------------------------------------------------------

def test_first_lap_scored_badge_awarded_once():
    profile = pr.default_profile()
    result1 = pr.award_lap(profile, _report(50))
    assert any(b["id"] == "first_scored_lap" for b in result1["new_badges"])

    result2 = pr.award_lap(profile, _report(50))
    assert not any(b["id"] == "first_scored_lap" for b in result2["new_badges"])
    assert "first_scored_lap" in profile["badges"]


def test_clean_sweep_badge_requires_95_overall():
    profile = pr.default_profile()
    result = pr.award_lap(profile, _report(94))
    assert not any(b["id"] == "clean_sweep" for b in result["new_badges"])

    result = pr.award_lap(profile, _report(95))
    assert any(b["id"] == "clean_sweep" for b in result["new_badges"])


def test_on_a_roll_badge_needs_five_in_a_row():
    profile = pr.default_profile()
    for _ in range(4):
        result = pr.award_lap(profile, _report(85))
        assert not any(b["id"] == "on_a_roll" for b in result["new_badges"])
    result = pr.award_lap(profile, _report(85))
    assert any(b["id"] == "on_a_roll" for b in result["new_badges"])


def test_streak_resets_below_80():
    profile = pr.default_profile()
    for _ in range(4):
        pr.award_lap(profile, _report(85))
    pr.award_lap(profile, _report(50))  # breaks the streak
    assert profile["current_streak_80"] == 0


def test_award_lap_accumulates_xp_and_history():
    profile = pr.default_profile()
    pr.award_lap(profile, _report(80))
    pr.award_lap(profile, _report(90))
    assert profile["xp"] == 2 * pr.LAP_COMPLETION_XP + 80 + 90
    assert profile["laps_scored"] == 2
    assert len(profile["history"]) == 2
    assert profile["best_overall_score"] == 90


# --- Profile persistence ---------------------------------------------------

def test_profile_round_trips_through_disk(tmp_path):
    path = tmp_path / "profile.json"
    profile = pr.load_profile(path)  # missing file -> default
    pr.award_lap(profile, _report(88))
    pr.save_profile(profile, path)

    reloaded = pr.load_profile(path)
    assert reloaded["xp"] == profile["xp"]
    assert reloaded["laps_scored"] == 1


def test_result_to_dict_is_json_serializable(tmp_path):
    profile = pr.default_profile()
    result = pr.award_lap(profile, _report(50))  # stays in level 1 (< 100 xp)
    payload = pr.result_to_dict(result, profile)
    json.dumps(payload)  # must not raise
    assert payload["level_after"]["level"] == 1
    assert all("earned" in b for b in payload["badges"])


def test_cli_end_to_end(tmp_path, capsys):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_report(96)))
    profile_path = tmp_path / "profile.json"

    exit_code = pr.main([str(report_path), "--profile", str(profile_path)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "XP" in out
    assert profile_path.exists()
    assert json.loads(profile_path.read_text())["laps_scored"] == 1


def test_cli_missing_report_errors(tmp_path, capsys):
    exit_code = pr.main([str(tmp_path / "missing.json")])
    assert exit_code == 1
    assert "does not exist" in capsys.readouterr().err
