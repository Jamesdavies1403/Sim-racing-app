import json
import re

import dashboard
import corner_scoring as cs
import ibt_builder as ib


def _make_reports(tmp_path):
    """Score two laps against a target and write their JSON reports, the
    way the README's Step 2 -> Step 4 pipeline expects."""
    path = tmp_path / "session.ibt"
    corners = [ib.one_corner(zone_start=190.0), ib.one_corner(zone_start=620.0, zone_len=80.0, peak=0.7)]
    rows0, t = ib.build_lap_samples(0, corners)
    rows1, t = ib.build_lap_samples(1, corners, t_offset=t)
    rows2, _ = ib.build_lap_samples(2, corners, t_offset=t)
    ib.write_ibt(path, rows0 + rows1 + rows2)

    report_paths = []
    for lap in (1, 2):
        out = tmp_path / f"lap{lap}.json"
        exit_code = cs.main([str(path), "--lap", str(lap), "--target-lap", "0", "--out", str(out)])
        assert exit_code == 0
        report_paths.append(out)
    return report_paths


def test_build_bundle_applies_reports_in_order(tmp_path):
    report_paths = _make_reports(tmp_path)
    profile_path = tmp_path / "profile.json"

    bundle = dashboard.build_bundle(report_paths, profile_path)

    assert [lap["lap_number"] for lap in bundle["laps"]] == [1, 2]
    assert bundle["final_profile"]["laps_scored"] == 2
    # profile.json should be persisted for a future run to continue from
    assert profile_path.exists()
    assert json.loads(profile_path.read_text())["laps_scored"] == 2


def test_render_dashboard_embeds_valid_json_with_no_leftover_placeholder(tmp_path):
    report_paths = _make_reports(tmp_path)
    bundle = dashboard.build_bundle(report_paths, tmp_path / "profile.json")

    html = dashboard.render_dashboard(bundle)

    assert dashboard.DATA_PLACEHOLDER not in html
    match = re.search(
        r'<script id="dashboard-data" type="application/json">(.*?)</script>', html, re.S
    )
    assert match is not None
    embedded = json.loads(match.group(1))
    assert len(embedded["laps"]) == 2


def test_cli_end_to_end(tmp_path):
    report_paths = _make_reports(tmp_path)
    profile_path = tmp_path / "profile.json"
    out_html = tmp_path / "dashboard.html"

    exit_code = dashboard.main(
        [str(p) for p in report_paths] + ["--profile", str(profile_path), "--out", str(out_html)]
    )

    assert exit_code == 0
    assert out_html.exists()
    html = out_html.read_text()
    assert "__DASHBOARD_DATA__" not in html
    assert "Apex" in html  # sanity: it's the real template, not an empty file


def test_cli_missing_report_errors(tmp_path, capsys):
    exit_code = dashboard.main([str(tmp_path / "missing.json")])
    assert exit_code == 1
    assert "does not exist" in capsys.readouterr().err


def test_rerunning_continues_the_same_profile(tmp_path):
    report_paths = _make_reports(tmp_path)
    profile_path = tmp_path / "profile.json"

    dashboard.build_bundle(report_paths[:1], profile_path)
    first_xp = json.loads(profile_path.read_text())["xp"]

    dashboard.build_bundle(report_paths[1:], profile_path)
    second_xp = json.loads(profile_path.read_text())["xp"]

    assert second_xp > first_xp
