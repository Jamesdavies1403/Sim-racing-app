import pytest

import brake_trace
import ibt_builder as ib


def _two_lap_file(tmp_path, corners=None):
    corners = corners or [ib.one_corner()]
    path = tmp_path / "session.ibt"
    rows0, t = ib.build_lap_samples(0, corners)
    rows1, _ = ib.build_lap_samples(1, corners, t_offset=t)
    ib.write_ibt(path, rows0 + rows1)
    return path


def test_load_and_split_two_laps(tmp_path):
    path = _two_lap_file(tmp_path)

    data = brake_trace.load_ibt(path)
    laps = brake_trace.split_into_laps(data)

    assert set(laps.keys()) == {0, 1}
    assert len(laps[0].lap_dist) == 400
    assert len(laps[0].brake) == len(laps[0].session_time) == 400


def test_session_time_is_monotonic_within_a_lap(tmp_path):
    path = _two_lap_file(tmp_path)
    laps = brake_trace.split_into_laps(brake_trace.load_ibt(path))

    st = laps[0].session_time
    assert all(b > a for a, b in zip(st, st[1:]))


def test_negative_lap_numbers_are_excluded(tmp_path):
    path = tmp_path / "with_outlap.ibt"
    corners = [ib.one_corner()]
    rows, _ = ib.build_lap_samples(0, corners)
    # prepend a few samples tagged as an out-lap (negative lap number)
    outlap_rows = [(-1, r[1], 0.0, 0.0, r[4], r[5] - 1.0) for r in rows[:5]]
    ib.write_ibt(path, outlap_rows + rows)

    laps = brake_trace.split_into_laps(brake_trace.load_ibt(path))
    assert -1 not in laps
    assert 0 in laps


def test_missing_channel_raises_runtime_error(tmp_path):
    path = tmp_path / "no_session_time.ibt"
    channels = ib.VARS[:-1]  # drop SessionTime
    rows = [(0, float(i), 0.0, 0.0, 50.0) for i in range(20)]
    ib.write_ibt(path, rows, channels=channels)

    with pytest.raises(RuntimeError, match="missing expected channel"):
        brake_trace.load_ibt(path)


def test_cli_list_laps(tmp_path, capsys):
    path = _two_lap_file(tmp_path)
    exit_code = brake_trace.main([str(path), "--list-laps"])
    out = capsys.readouterr().out

    assert exit_code == 0
    lines = [line.split() for line in out.splitlines() if line.strip()]
    lap_rows = {line[0]: line[1] for line in lines if line[0] in ("0", "1")}
    assert lap_rows == {"0": "400", "1": "400"}


def test_cli_missing_file_errors(tmp_path, capsys):
    missing = tmp_path / "nope.ibt"
    exit_code = brake_trace.main([str(missing), "--lap1", "0", "--lap2", "1"])
    assert exit_code == 1
    assert "does not exist" in capsys.readouterr().err


def test_cli_bad_lap_number_errors(tmp_path, capsys):
    path = _two_lap_file(tmp_path)
    exit_code = brake_trace.main([str(path), "--lap1", "0", "--lap2", "99"])
    assert exit_code == 1
    assert "not found" in capsys.readouterr().err


def test_cli_plot_writes_file(tmp_path):
    path = _two_lap_file(tmp_path)
    out_png = tmp_path / "compare.png"
    exit_code = brake_trace.main([str(path), "--lap1", "0", "--lap2", "1", "--out", str(out_png)])
    assert exit_code == 0
    assert out_png.exists()
    assert out_png.stat().st_size > 0
