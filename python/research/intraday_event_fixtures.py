"""Invented EL event paths in the existing authenticated raw-archive geometry.

Only the old calendar/universe framing and row-count arithmetic are reused.
These prices are newly specified EL cases, never an old signal or outcome mask.
The complete 257-session archive remains invented even when a 32-day shard is
used for bounded qualification. No original path, result or network is opened.
"""

import gzip
from hashlib import sha256
import os

from research import intraday_campaign_wsl_transport as wire
from research.intraday_ed_comparison_unit import _invented_calendar, _invented_universe
from research.intraday_trader_unit import invented_geometry
from research.intraday_pattern_calibration import session_open


PROFILE = "el-event-cases-v2"
CASES = ("a_confirm", "b_reclaim", "c_resume", "a_expiry", "c_no_pullback", "a_active_gap",
         "a_intrabar_ambiguous", "a_future_gap", "c_no_resume", "no_event", "a_invalidated", "invalid_numeric")


def prices(case: int, minute: int) -> tuple[str, str, str, str]:
    if type(case) is not int or not 0 <= case < len(CASES) or type(minute) is not int or minute < 0:
        raise ValueError("EL fixed invented case and minute required")
    close, high, low = 10000, 10050, 9950
    if case in (0, 3, 5, 6, 7, 10, 11):
        a = {21: (9900, 10000, 9880), 22: (9800, 9900, 9780), 23: (9650, 9800, 9630),
             24: (9660, 9700, 9635), 25: (9670, 9710, 9630), 26: (9740, 9760, 9640),
             27: (9700, 9760, 9640), 28: (9750, 9800, 9700), 29: (9780, 9810, 9720)}
        if minute in a:
            close, high, low = a[minute]
        if case == 3 and minute >= 24:
            close, high, low = 9660, 9710, 9635
        if case == 6 and minute == 27:
            close, high, low = 9700, 10100, 9500
        if case == 10 and minute == 24:
            close, high, low = 9660, 9700, 9590
    elif case == 1:
        if minute in (33, 34, 35):
            close, high, low = {33: (9900, 10000, 9880), 34: (9910, 9940, 9870),
                                35: (9980, 10000, 9900)}[minute]
        elif minute >= 40:
            close, high, low = 10300, 10350, 10250
    elif case in (2, 4, 8):
        if 21 <= minute <= 25:
            close = (10050, 10100, 10150, 10200, 10350)[minute - 21]
            high, low = close + 50, close - 50
        elif minute >= 26:
            close, high, low = {26: (10400, 10420, 10380), 27: (10360, 10410, 10350),
                28: (10340, 10390, 10300), 29: (10430, 10440, 10360)}.get(minute, (10430, 10450, 10410))
            if case == 2 and minute >= 40:
                close, high, low = 10800, 10850, 10750
            if case == 4:
                close = 10350 + 60 * (min(minute, 35) - 25)
                high, low = close + 20, close - 50
            if case == 8 and minute >= 28:
                close, high, low = 10340, 10390, 10300
    return str(close), str(high), str(low), str(close)


def raw_chunk(kind, day, symbol, row_count, ordinal, *, daily=False):
    """Lossless raw fixture, with gaps represented by missing minute coordinates."""
    if kind not in ("CC", "CX") or type(row_count) is not int or row_count < 1:
        raise ValueError("EL invented archive chunk contract differs")
    case = ordinal % len(CASES)
    stamp = int(session_open(day).timestamp()) * 1000
    rows = []
    for index in range(row_count):
        minute = index + int(case == 5 and index >= 25) + int(case == 7 and index >= 30)
        row = {"open_time_ms": stamp + minute * 60000, "fetched_at": "2026-11-11T00:00:00Z"}
        if kind == "CC":
            row.update(symbol=symbol, interval="1m", **dict(zip(("open", "high", "low", "close"), prices(case, minute))))
            if case == 11 and minute == 22:
                row["open"] = "invalid-invented-number"
        else:
            row.update(volume="0" if minute % 40 == 0 else "100", quote_volume=None)
            if case == 11 and minute == 32:
                row["volume"] = "invalid-invented-number"
        rows.append(row)
    chunk = {"symbol": symbol, "session": day, "observation_date": day, "available_at": None,
        "retrieved_at": "2026-11-11T00:00:00Z", "source": "collector klines", "is_final": None,
        "evidence_level": "assumed", "rows": rows}
    if kind == "CX":
        chunk.update(daily_rows=[{"open_time_ms": stamp, "fetched_at": "2026-11-11T00:00:00Z",
                                  "volume": "100000", "quote_volume": None}] if daily else [],
                     daily_retrieved_at="2026-11-11T00:00:00Z")
    return chunk


def invented_population(sessions):
    """Exercise a 23-name daily union within the original full geometry."""
    geometry, days = invented_geometry(sessions), _invented_calendar(sessions)
    codes = tuple(f"{number:06}" for number in range(1, 19))
    universe, coordinates = _invented_universe(days, codes, geometry)
    if sessions >= 3:
        earlier, later = (universe["variants"][variant]["days"] for variant in ("D+1", "D+2"))
        # Move one disagreement each from days two and three to day one.
        # Each scenario still has twenty targets, with eighteen total extras.
        for target, symbol in zip(later[0]["targets"][-3:], ("KRX:900003", "KRX:900004", "KRX:900005"), strict=True):
            target["symbol"] = symbol
        for i in (1, 2):
            later[i]["targets"][-1]["symbol"] = earlier[i]["targets"][-1]["symbol"]
        keys = sorted({(day["session"], target["symbol"]) for variant in (earlier, later)
                       for day in variant for target in day["targets"]})
        wire.require(len(keys) == geometry["union_coordinates"], "EL invented population geometry differs")
        base, remainder = divmod(geometry["cc_rows"], len(keys))
        row_counts = {key: base + (i < remainder) for i, key in enumerate(keys)}
        for variant in (earlier, later):
            for day in variant:
                for target in day["targets"]:
                    target["stored_row_count"] = row_counts[day["session"], target["symbol"]]
        coordinates = tuple((day, symbol, row_counts[day, symbol]) for day, symbol in keys)
    return geometry, universe, coordinates


def make_invented(directory, sessions, checkpoint):
    """Write fresh exclusive CB/CC/CX archives; never modify a saved fixture."""
    geometry, universe, coordinates = invented_population(sessions)
    wire.exclusive_bytes(directory / "CB", wire.encode(universe))
    decoded = {}
    for kind in ("CC", "CX"):
        digest = sha256()
        descriptor = os.open(directory / kind, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            with gzip.GzipFile(filename="", fileobj=stream, mode="wb", compresslevel=1, mtime=0) as compressed:
                for index, (day, symbol, count) in enumerate(coordinates):
                    checkpoint("el_invented_raw_chunk")
                    raw = wire.encode(raw_chunk(kind, day, symbol, count, index,
                                                daily=index < geometry["cx_daily_rows"]))
                    compressed.write(raw)
                    digest.update(raw)
            stream.flush()
            os.fsync(stream.fileno())
        decoded[kind] = digest.hexdigest()
    wire.fsync_directory(directory)
    return {"profile": PROFILE, "geometry": geometry, "decoded_archives": decoded}
