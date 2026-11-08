"""Invented tests for the saved EI roster header's byte and resource bounds."""

import json
from pathlib import Path
import subprocess
import sys

from research import intraday_context_snapshot as snapshot
from research.intraday_context_snapshot_saved_header import stream_roster_header
from research.intraday_ed_sources import TargetCoordinate


def test_saved_header_matches_legacy_canonical_roster():
    day = "2031-02-12"
    symbol = '가상"\\\n'
    targets = (
        TargetCoordinate("S2", symbol, day, 1, 0, "실제 0", b"\x00\xff"),
        TargetCoordinate("S1", symbol, day, 1, None, "관측\n없음", "한글🔥".encode()),
        TargetCoordinate("S1", "KRX:000001", day, 2, 60, "observed", b""),
    )
    groups = snapshot._roster(targets)
    header = stream_roster_header(snapshot, groups)
    assert header == snapshot._header(groups)
    assert header["roster_sha256"] == "8bee82c5daf680273f0aa358d31a8a560faa3d1e3229a37baaa60cf8056adf75"
    assert header["coordinates"] == 2 and header["memberships"] == 3
    assert stream_roster_header(snapshot, {}) == snapshot._header({})


def test_max_metadata_full_roster_header_fits_64mib_and_12cpu_seconds():
    root = Path(__file__).resolve().parents[1]
    code = """
import json, resource, sys, time
from datetime import date, timedelta

address_space = 64 * 1024**2
resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))
resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
sys.path.insert(0, sys.argv[1])
from research import intraday_context_snapshot as snapshot
from research.intraday_context_snapshot_saved_header import stream_roster_header
from research.intraday_ed_sources import TargetCoordinate

start = date(2031, 2, 12)
days = tuple((start + timedelta(days=offset)).isoformat() for offset in range(257))
metadata = b'Z' * 4096
groups = snapshot._roster(tuple(
    TargetCoordinate(scenario, f'KRX:{rank:06}', day, rank, 60, 'observed', metadata)
    for day in days for rank in range(1, 21) for scenario in ('S1', 'S2')
))
header = stream_roster_header(snapshot, groups)
cpu_seconds = time.process_time()
peak_rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
try:
    snapshot._header(groups)
except MemoryError:
    print(json.dumps({'header': header, 'cpu_seconds': cpu_seconds, 'peak_rss_kib': peak_rss_kib}))
else:
    raise AssertionError('legacy header unexpectedly fit the address-space limit')
"""
    run = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(root)],
                         check=True, capture_output=True, text=True, timeout=30)
    result = json.loads(run.stdout)
    assert result["header"] == {
        "schema": snapshot.FEATURE_SCHEMA, "projection": snapshot.FEATURE_PROJECTION,
        "questions": snapshot.SCHEME_ID,
        "roster_sha256": "e95b3f83a90277a2caf3952792e900f8b4e035ef142f70f9a2c17e6428938d64",
        "coordinates": 5140, "memberships": 10280,
    }
    assert result["cpu_seconds"] < 12
    assert result["peak_rss_kib"] < 64 * 1024
