"""Builds valid synthetic .ibt files for tests.

Matches the binary layout pyirsdk's `irsdk.IBT` class reads (verified
against the pyirsdk source): a Header at offset 0, a DiskSubHeader at
offset 112, an array of 144-byte VarHeader entries, then the raw per-tick
sample buffer. This lets tests exercise the real file-parsing path
(brake_trace.load_ibt) instead of mocking it.
"""

from __future__ import annotations

import struct

# (irsdk var type, name) for each channel. Type codes: 2=int, 4=float, 5=double.
VARS = [
    (2, "Lap"),
    (4, "LapDist"),
    (4, "Brake"),
    (4, "Throttle"),
    (4, "Speed"),
    (5, "SessionTime"),
]
_SIZE = {2: 4, 4: 4, 5: 8}
_FMT = {2: "i", 4: "f", 5: "d"}


def _var_header(vtype: int, offset: int, name: str) -> bytes:
    buf = struct.pack("<iii?", vtype, offset, 1, False).ljust(16, b"\x00")
    buf += name.encode("latin-1").ljust(32, b"\x00")
    buf += b"\x00" * 64  # desc, unused
    buf += b"\x00" * 32  # unit, unused
    assert len(buf) == 144
    return buf


def write_ibt(path, samples, channels=VARS, tick_rate: int = 60) -> None:
    """Write `samples` (each a tuple matching `channels`, in order) to `path`
    as a valid .ibt file. Defaults to the six channels brake_trace.py reads
    (Lap, LapDist, Brake, Throttle, Speed, SessionTime)."""
    samples = list(samples)

    offsets, offset = [], 0
    for vtype, _ in channels:
        offsets.append(offset)
        offset += _SIZE[vtype]
    buf_len = offset

    num_vars = len(channels)
    var_header_offset = 144
    disk_header_offset = 112
    var_headers_bytes = b"".join(
        _var_header(vtype, off, name) for (vtype, name), off in zip(channels, offsets)
    )
    buf_data_offset = var_header_offset + num_vars * 144

    fmt = "<" + "".join(_FMT[vtype] for vtype, _ in channels)
    sample_bytes = b"".join(struct.pack(fmt, *row) for row in samples)
    assert len(sample_bytes) == len(samples) * buf_len

    header = struct.pack(
        "<iiiiiiiiiiiB",
        2, 1, tick_rate,   # version, status, tick_rate
        0, 0, 0,           # session_info_update/len/offset (unused)
        num_vars, var_header_offset,
        1, buf_len, 0, 0,  # num_buf, buf_len, cur_buf_tick_count, cur_buf
    ).ljust(48, b"\x00")
    header += struct.pack("<iii", 0, buf_data_offset, 0).ljust(16, b"\x00")  # var_buf[0]
    header = header.ljust(disk_header_offset, b"\x00")

    lap_count = len({row[0] for row in samples}) if samples else 0
    disk_header = struct.pack("<Qddii", 0, 0.0, 0.0, lap_count, len(samples)).ljust(32, b"\x00")

    file_bytes = bytearray()
    file_bytes += header
    file_bytes += disk_header
    file_bytes = file_bytes.ljust(var_header_offset, b"\x00")
    file_bytes += var_headers_bytes
    file_bytes = file_bytes.ljust(buf_data_offset, b"\x00")
    file_bytes += sample_bytes

    with open(path, "wb") as f:
        f.write(bytes(file_bytes))


# --- Synthetic corner/lap generation ----------------------------------------
# Shape functions used to build believable brake/throttle/speed traces for a
# lap with one or more corners, without needing a real recorded session.

def brake_shape(dist, zone_start, zone_len, peak, hold_frac=0.15, blip=None):
    """A ramp-up / hold / taper-down brake trace over [zone_start, zone_start+zone_len].
    `blip` is an optional (t_start, t_end, height) added ON TOP of the taper
    (as fractions of zone_len) to simulate a re-press during release."""
    if not (zone_start <= dist <= zone_start + zone_len):
        return 0.0
    t = (dist - zone_start) / zone_len
    rise_end = 0.35
    hold_end = rise_end + hold_frac
    if t < rise_end:
        val = peak * (t / rise_end)
    elif t < hold_end:
        val = peak
    else:
        tail = (t - hold_end) / (1 - hold_end)
        val = peak * (1 - tail)
    val = max(0.0, val)
    if blip and blip[0] <= t <= blip[1]:
        b_start, b_end, b_height = blip
        mid, half = (b_start + b_end) / 2, (b_end - b_start) / 2
        val = max(0.0, val + b_height * (1 - abs(t - mid) / half))
    return val


def throttle_shape(dist, pickup_dist, ramp_len, ceiling=1.0, blip=None):
    """0 before pickup_dist, ramps 0->ceiling over ramp_len, holds after.
    `blip` is an optional (t_start, t_end, depth) dip during the ramp, as
    fractions of ramp_len, to simulate a lift during application."""
    if dist < pickup_dist:
        return 0.0
    t = (dist - pickup_dist) / ramp_len
    val = ceiling if t >= 1.0 else ceiling * t
    if blip and blip[0] <= t <= blip[1]:
        b_start, b_end, b_depth = blip
        mid, half = (b_start + b_end) / 2, (b_end - b_start) / 2
        val = max(0.0, val - b_depth * (1 - abs(t - mid) / half))
    return max(0.0, min(1.0, val))


def apex_dip_shape(dist, center, width, depth):
    """A downward speed bump centered on the apex region — models real
    mid-corner speed scrub independent of the brake/throttle traces."""
    if depth <= 0:
        return 0.0
    half = width / 2.0
    if abs(dist - center) > half:
        return 0.0
    return depth * (1 - abs(dist - center) / half)


def build_lap_samples(
    lap_num, corners, lap_len=1000.0, samples_per_lap=400, dt=1.0 / 60,
    t_offset=0.0, base_speed=55.0, brake_decel=30.0, throttle_accel=8.0,
):
    """corners: list of dicts, each with a 'brake' kwargs dict (for
    brake_shape), and optionally 'throttle' (for throttle_shape) and
    'apex_penalty' (for apex_dip_shape) kwargs dicts.

    Returns (rows, next_t_offset) where rows are
    (lap, lap_dist, brake, throttle, speed, session_time) tuples.
    """
    step = lap_len / samples_per_lap
    rows = []
    t = t_offset
    for i in range(samples_per_lap):
        dist = step * i
        brake = 0.0
        throttle_candidates = []
        for c in corners:
            brake = max(brake, brake_shape(dist, **c["brake"]))
            if "throttle" in c:
                throttle_candidates.append(throttle_shape(dist, **c["throttle"]))
        throttle = max(throttle_candidates) if throttle_candidates else 0.0
        if brake > 0.02:
            throttle = 0.0
        penalty = 0.0
        for c in corners:
            if "apex_penalty" in c:
                penalty = max(penalty, apex_dip_shape(dist, **c["apex_penalty"]))
        speed = base_speed - brake_decel * brake + throttle_accel * throttle - penalty
        rows.append((lap_num, dist, brake, throttle, speed, t))
        t += dt
    return rows, t


def one_corner(zone_start=190.0, zone_len=90.0, peak=0.85, blip=None,
                pickup_dist=None, ramp_len=60.0, exit_blip=None, apex_penalty=None):
    """Convenience: a single corner spec for build_lap_samples, with a
    brake zone and (by default) a matching throttle-application zone just
    after it."""
    corner = {"brake": dict(zone_start=zone_start, zone_len=zone_len, peak=peak, blip=blip)}
    if pickup_dist is None:
        pickup_dist = zone_start + zone_len + 10.0
    corner["throttle"] = dict(pickup_dist=pickup_dist, ramp_len=ramp_len, blip=exit_blip)
    if apex_penalty is not None:
        corner["apex_penalty"] = apex_penalty
    return corner
