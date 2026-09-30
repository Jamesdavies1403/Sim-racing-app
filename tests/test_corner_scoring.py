import json

import pytest

import brake_trace
import corner_scoring as cs
import ibt_builder as ib


def _lap_from_corners(corners, lap_num=0):
    """Build a LapData directly (no file I/O) from corner specs, for tests
    that only need the in-memory scoring pipeline."""
    rows, _ = ib.build_lap_samples(lap_num, corners)
    lap = brake_trace.LapData(lap_num=lap_num)
    for _, dist, brake, throttle, speed, t in rows:
        lap.lap_dist.append(dist)
        lap.brake.append(brake)
        lap.throttle.append(throttle)
        lap.speed.append(speed)
        lap.session_time.append(t)
    return lap


def test_detect_brake_zones_finds_each_corner():
    corners = [ib.one_corner(zone_start=190.0), ib.one_corner(zone_start=620.0, zone_len=80.0, peak=0.7)]
    lap = _lap_from_corners(corners)

    zones = cs.detect_brake_zones(lap)

    assert len(zones) == 2
    for z in zones:
        assert z.start_idx < z.peak_idx <= z.release_start_idx <= z.end_idx


def test_score_lap_perfect_self_match_is_near_100():
    corners = [ib.one_corner(zone_start=190.0)]
    lap = _lap_from_corners(corners)

    report = cs.score_lap(lap, lap)

    assert report.overall_score == pytest.approx(100.0, abs=0.5)
    assert len(report.zone_scores) == 1
    assert not report.unmatched_user_zones


def test_later_brake_point_is_detected_and_scored_down():
    target_corners = [ib.one_corner(zone_start=180.0)]
    user_corners = [ib.one_corner(zone_start=200.0)]  # brakes 20m later
    target_lap = _lap_from_corners(target_corners)
    user_lap = _lap_from_corners(user_corners)

    report = cs.score_lap(user_lap, target_lap)

    assert len(report.zone_scores) == 1
    metrics_by_key = {m.key: m for m in report.zone_scores[0].metrics}
    bp = metrics_by_key["brake_point"]
    assert bp.delta == pytest.approx(20.0, abs=1.0)
    assert bp.score < 100.0
    assert report.zone_scores[0].overall_score < 100.0


def test_smoothness_penalizes_release_reversal():
    clean_corners = [ib.one_corner(zone_start=190.0)]
    # a blip strong enough to actually rise above the natural release curve
    # (see corner_scoring conversation history: a too-small blip just floors
    # the taper without creating a real re-press).
    jerky_corners = [ib.one_corner(zone_start=190.0, blip=(0.62, 0.80, 0.35))]

    target_lap = _lap_from_corners(clean_corners)
    user_lap = _lap_from_corners(jerky_corners)

    report = cs.score_lap(user_lap, target_lap)
    metrics_by_key = {m.key: m for m in report.zone_scores[0].metrics}
    smoothness = metrics_by_key["smoothness"]

    assert report.zone_scores[0].user.reversal_count > 0
    assert smoothness.score < 90.0
    assert any("re-pressed" in tip for tip in report.zone_scores[0].tips)


def test_match_zones_respects_tolerance():
    user_lap = _lap_from_corners([ib.one_corner(zone_start=190.0), ib.one_corner(zone_start=620.0, zone_len=80.0)])
    target_lap = _lap_from_corners([ib.one_corner(zone_start=190.0)])  # only the first corner exists

    report = cs.score_lap(user_lap, target_lap)

    assert len(report.zone_scores) == 1
    assert len(report.unmatched_user_zones) == 1
    assert report.unmatched_user_zones[0].start_dist == pytest.approx(620.0, abs=5.0)


def test_no_matching_zones_gives_empty_report():
    user_lap = _lap_from_corners([ib.one_corner(zone_start=190.0)])
    target_lap = _lap_from_corners([ib.one_corner(zone_start=800.0)])

    report = cs.score_lap(user_lap, target_lap)

    assert report.zone_scores == []
    assert report.overall_score == 0.0
    assert len(report.unmatched_user_zones) == 1


def test_free_tier_only_scores_braking_metrics():
    corners = [ib.one_corner(zone_start=190.0)]
    lap = _lap_from_corners(corners)

    report = cs.score_lap(lap, lap, tier="free")

    assert report.tier == "free"
    keys = {m.key for m in report.zone_scores[0].metrics}
    assert keys == cs.FREE_TIER_METRIC_KEYS


def test_paid_tier_scores_all_eleven_metrics():
    corners = [ib.one_corner(zone_start=190.0)]
    lap = _lap_from_corners(corners)

    report = cs.score_lap(lap, lap, tier="paid")

    keys = {m.key for m in report.zone_scores[0].metrics}
    assert keys == set(cs.METRIC_WEIGHTS.keys())


def test_invalid_tier_raises():
    corners = [ib.one_corner(zone_start=190.0)]
    lap = _lap_from_corners(corners)
    with pytest.raises(ValueError):
        cs.score_lap(lap, lap, tier="bogus")


def test_cli_end_to_end_writes_valid_json(tmp_path):
    path = tmp_path / "session.ibt"
    corners = [ib.one_corner(zone_start=190.0), ib.one_corner(zone_start=620.0, zone_len=80.0, peak=0.7)]
    rows0, t = ib.build_lap_samples(0, corners)
    rows1, _ = ib.build_lap_samples(1, corners, t_offset=t)
    ib.write_ibt(path, rows0 + rows1)

    out_json = tmp_path / "report.json"
    exit_code = cs.main([str(path), "--lap", "1", "--target-lap", "0", "--out", str(out_json)])

    assert exit_code == 0
    report = json.loads(out_json.read_text())
    assert report["tier"] == "paid"
    assert len(report["zones"]) == 2
    assert "overall_score" in report


def test_cli_bad_lap_errors(tmp_path, capsys):
    path = tmp_path / "session.ibt"
    corners = [ib.one_corner(zone_start=190.0)]
    rows, _ = ib.build_lap_samples(0, corners)
    ib.write_ibt(path, rows)

    exit_code = cs.main([str(path), "--lap", "5", "--target-lap", "0"])
    assert exit_code == 1
    assert "not found" in capsys.readouterr().err
