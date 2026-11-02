"""Invented raw-only original-population and nullable reentry report checks."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from research import intraday_ed_sources as sources
from research import intraday_trader_inputs as original
from research import intraday_trader_reentry as science
from research import intraday_trader_reentry_inputs as inputs
from research import intraday_trader_reentry_reference as reference
from research import intraday_trader_report as report
import test_intraday_trader_reentry as fixture


DAYS = ("2026-06-03", "2026-06-04", "2026-06-05", "2026-06-08")
SYMBOL = fixture.SYMBOL
BINDING = {key: "a" * 64 for key in report.BINDING_KEYS - {"caller"}} | {"caller": "invented-reentry"}


def universe(days=DAYS):
    targets = tuple(sources.TargetCoordinate(clock, SYMBOL, day, 1, None,
        "no_stored_rows_unknown_reason", report.codec.canonical({"day": day}))
        for clock in ("S1", "S2") for day in days)
    return sources.UniverseCalendar(tuple(days), targets, "b" * 64)


def price(day, case="a"):
    if case == "missing":
        return None
    if case == "a":
        session = fixture.a_inputs()[0]
    elif case == "b":
        session = fixture.b_inputs()[0]
    elif case == "no_parent":
        session = fixture.sessions([(100, 105, 95, 100)] * 78)[0]
    elif case == "pre_pullback":
        blocks = fixture.a_blocks()
        blocks[7] = (112, 122, 103, 104)
        session = fixture.sessions(blocks)[0]
    elif case == "entry_cancel":
        session = fixture.a_inputs()[0]
        session.minutes[fixture.OPEN + 58 * fixture.M] = replace(session.minutes[fixture.OPEN + 58 * fixture.M],
            ohlc=tuple(map(Decimal, (130, 131, 114, 115))))
    elif case == "expired":
        blocks = fixture.b_blocks()[:11] + [(102, 103, 95, 102)] * 3 + [(102, 103, 94, 98), *fixture.b_blocks()[15:20]]
        session = fixture.sessions(blocks, filler=(106, 107, 105, 106))[0]
    else:
        raise AssertionError("unknown invented case")
    opening = sources.calendar_source.session_open(day)
    rows = [dict(symbol=SYMBOL, interval="1m", open_time_ms=int((opening + (at - session.opening)).timestamp()) * 1000,
        fetched_at=None, **dict(zip(("open", "high", "low", "close"), map(str, item.ohlc), strict=True)))
        for at, item in session.minutes.items()]
    return dict(symbol=SYMBOL, session=day, observation_date=day, available_at=None,
        retrieved_at="2026-10-30T00:00:00Z", source="collector klines", is_final=None,
        evidence_level="assumed", rows=rows)


class Reader:
    def __init__(self, chunks=None):
        self.chunks = chunks or {}
        self.calls, self.checks = [], 0

    def assert_unchanged(self):
        self.checks += 1

    def load_raw_chunk(self, kind, symbol, day):
        self.calls.append((kind, symbol, day))
        return deepcopy(self.chunks.get((kind, symbol, day)))


def reader(days=DAYS, cases=("a", "b", "missing", "no_parent")):
    return Reader({("CC", SYMBOL, day): price(day, case) for day, case in zip(days, cases, strict=True)})


def geometry(calendar):
    count = len(calendar.sessions)
    return dict(sessions=count, targets_per_scenario=count, union_coordinates=count,
                cc_chunks=count, cc_rows=count * 390, cx_chunks=count, cx_rows=0, cx_daily_rows=0)


def records(calendar=None, source=None):
    calendar = universe() if calendar is None else calendar
    return list(report.report_records(calendar, reader() if source is None else source,
        binding=BINDING, geometry=geometry(calendar), invented_only=True, checkpoint=lambda _: None, profile="reentry"))


def verify(rows, calendar=None, source=None):
    calendar = universe() if calendar is None else calendar
    return report.verify_report(iter(rows), universe=calendar, raw_reader=reader() if source is None else source,
        binding=BINDING, geometry=geometry(calendar), invented_only=True, checkpoint=lambda _: None, profile="reentry")


def test_nullable_additional_retains_original_four_slot_denominators():
    rows = records()
    assert rows[0]["policies"] == list(science.POLICIES)
    summary = rows[-1]["summary"]
    for clock in ("S1", "S2"):
        for policy in science.POLICIES:
            row = summary[clock][policy]
            assert row["original_targets"] == 4
            assert sum(row["slot_states"].values()) == 4
        additional = summary[clock]["EE-A2R-ADDITIONAL"]
        assert additional["null_support_states"]["unknown"] >= 1
        assert additional["null_support_states"]["unsupported"] >= 1
        assert additional["returns"]["net33"]["count"] < 4
    assert verify(rows)["verified"]


def reseal(rows):
    summary = report._Summary(tuple(rows[0]["calendar"]["sessions"]), "reentry")
    for row in rows[1:-1]:
        summary.add(report._decode_record(row, "reentry"))
    rows[-1]["summary"] = summary.finish()
    rows[-1]["decoded_prefix_sha256"] = hashlib.sha256(b"".join(
        report.codec.canonical(row) + b"\n" for row in rows[:-1])).hexdigest()
    return rows


def test_original_iterator_coalesces_both_clocks_once_and_keeps_source_checks():
    calendar, source, phases = universe(), reader(), []
    values = tuple(inputs.iter_original_sessions(calendar, source, phases.append))
    assert len(values) == 4 and source.calls == [(kind, SYMBOL, day) for day in DAYS for kind in ("CC", "CX")]
    assert source.checks == 8 and phases == [original.BEFORE_SESSION, original.AFTER_SESSION] * 4
    for record, day in zip(values, DAYS, strict=True):
        assert record.targets == tuple(item for item in calendar.targets if item.session == day)
        assert record.result.opening.date().isoformat() == day
        assert record.mathematical_verification.verified
        assert tuple(slot[0] for slot in science.policy_slots(record.result)) == science.POLICIES
    invalid = replace(calendar.targets[-1], rank=True)
    untouched = reader()
    with pytest.raises(sources.SourceRefusal):
        tuple(inputs.iter_original_sessions(replace(calendar, targets=(*calendar.targets[:-1], invalid)), untouched, lambda _: None))
    assert untouched.calls == [] and untouched.checks == 0


def test_four_day_saved_stream_verifies_without_producer_science_or_inline_oracles(monkeypatch):
    rows, sink = records(), BytesIO()
    digest = report.write_report(iter(rows), sink, lambda _: None, profile="reentry")
    def forbidden(*args, **kwargs):
        raise AssertionError("saved verifier called producer science")
    for name in ("evaluate_session", "evaluate_verified_session", "_attempt", "_detection", "_derive_reset"):
        monkeypatch.setattr(science, name, forbidden)
    monkeypatch.setattr(science.scenarios, "detect_bars", forbidden)
    monkeypatch.setattr(science.paths, "evaluate_path", forbidden)
    stream = report.read_report(BytesIO(sink.getvalue()), digest.compressed_sha256, digest.decoded_sha256,
                                lambda _: None, profile="reentry")
    proof = report.verify_report(stream, universe=universe(), raw_reader=reader(), binding=BINDING,
        geometry=geometry(universe()), invented_only=True, checkpoint=lambda _: None, profile="reentry")
    assert proof["verified"] and proof["record_stream_exhausted"]
    assert proof["record_counts"] == {"session": 4, "targets": {"S1": 4, "S2": 4}}
    assert set(proof["candidate_counts"]["S1"]) == set(science.POLICIES)
    for clock, policies in proof["summary"].items():
        for policy, row in policies.items():
            assert proof["candidate_counts"][clock][policy] == row["eligible"]["true"]


def test_resealed_path_and_terminal_provenance_forgery_fails_fresh_raw_math():
    for kind in ("return", "reset_level", "source_metadata"):
        rows = records()
        first = rows[1]["payload"]["result"]["families"][0]
        if kind == "return":
            first["second"]["path"]["net33"] = {"fraction": [999, 1]}
        elif kind == "reset_level":
            first["reset"]["impulse_high"] = {"decimal": "129"}
            first["reset"]["previous"]["frozen_target"] = {"decimal": "129"}
            first["second"]["detection"]["candidate"]["previous_terminal"]["frozen_target"] = {"decimal": "129"}
        else:
            rows[1]["payload"]["result"]["source_metadata"]["symbol"] = "KRX:999999"
        reseal(rows)
        with pytest.raises(reference.math.ReferenceRefusal, match="mathematical_mismatch"):
            verify(rows)


@pytest.mark.parametrize("mutation", ("null", "attempt", "receipt_bool", "first_identity"))
def test_closed_record_contract_rejects_fake_nulls_attempt_one_and_receipts(mutation):
    rows = records()
    first = rows[1]["payload"]["result"]["families"][0]
    if mutation == "null":
        first["second"] = None
    elif mutation == "attempt":
        first["second"]["detection"]["attempt_number"] = 1
    elif mutation == "receipt_bool":
        rows[1]["payload"]["mathematical_verification"]["first_count"] = True
    else:
        first["reset"]["first_sha256"] = "0" * 64
    with pytest.raises((report.ReportRefusal, ValueError)):
        report.write_report(iter(rows), BytesIO(), lambda _: None, profile="reentry")


def test_expired_and_entry_cancelled_firsts_remain_exact_reasons_in_summary():
    calendar = universe(DAYS[:2])
    rows = records(calendar, reader(DAYS[:2], ("entry_cancel", "expired")))
    summary = rows[-1]["summary"]["S1"]
    assert summary["EE-A2R-ADDITIONAL"]["terminal_reasons"]["entry_outside_frozen_levels"] == 1
    assert summary["EE-B-BASE-ADDITIONAL"]["terminal_reasons"]["confirmation_timeout"] == 1
    assert verify(rows, calendar, reader(DAYS[:2], ("entry_cancel", "expired")))["verified"]


def test_same_framing_checks_order_full_eof_and_primary_profile_separation():
    rows, sink = records(), BytesIO()
    for broken in (rows[:-1], [*rows[:1], *rows[2:]], [*rows[:2], rows[1], *rows[2:]]):
        with pytest.raises(report.ReportRefusal):
            report.write_report(iter(broken), BytesIO(), lambda _: None, profile="reentry")
    with pytest.raises(report.ReportRefusal):
        report.write_report(iter(rows), BytesIO(), lambda _: None)
    digest = report.write_report(iter(rows), sink, lambda _: None, profile="reentry")
    for raw in (sink.getvalue()[:-4], sink.getvalue() + b"extra"):
        with pytest.raises(report.ReportRefusal):
            list(report.read_report(BytesIO(raw), digest.compressed_sha256, digest.decoded_sha256,
                                   lambda _: None, profile="reentry"))


def test_primary_and_mechanism1_cold_imports_do_not_require_reentry_roster():
    program = '''
import builtins
import os
import sys
sys.path.insert(0, os.getcwd())
blocked = {"intraday_trader_reentry", "intraday_trader_reentry_reference", "intraday_trader_reentry_inputs"}
original = builtins.__import__
def guarded(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[-1] in blocked or (name == "research" and blocked.intersection(fromlist)):
        raise ImportError("old profile loaded new project source")
    return original(name, globals, locals, fromlist, level)
builtins.__import__ = guarded
from research import intraday_trader_report as report
assert report._profile("primary")[0] == "EE-original-population-price-paths-v1"
assert report._profile("mechanism1")[0] == "EE-original-population-mechanism1-v1"
assert not any("research." + name in sys.modules for name in blocked)
try:
    report._profile("reentry2")
except report.ReportRefusal:
    pass
else:
    raise AssertionError("unregistered profile accepted")
'''
    result = subprocess.run([sys.executable, "-I", "-S", "-c", program],
        cwd=Path(__file__).resolve().parents[1], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
