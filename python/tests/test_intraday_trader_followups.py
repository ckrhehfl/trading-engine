"""Invented-only mechanism science, original geometry and saved-report checks."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from io import BytesIO
from pathlib import Path
import subprocess
import sys

import pytest

from research import intraday_ed_snapshot as codec
from research import intraday_ed_sources as sources
from research import intraday_trader_followup_inputs as inputs
from research import intraday_trader_followup_reference as reference
from research import intraday_trader_followups as science
from research import intraday_trader_reference as old_reference
from research import intraday_trader_report as report
from research import intraday_trader_session as primary
from test_intraday_trader_inputs import Reader, chunk, a_blocks
from test_intraday_trader_report import blocks, BINDING


DAY = "2026-06-03"
SYMBOL = "KRX:000001"
SYMBOLS = tuple(f"KRX:{index:06d}" for index in range(1, 21))
MINUTE = timedelta(minutes=1)


def universe(*, s1_only=False):
    metadata = codec.canonical({"session": DAY, "available_at": f"{DAY}T08:00:00+09:00",
                                "selected_at": f"{DAY}T08:30:00+09:00"})
    targets = tuple(sources.TargetCoordinate(clock, symbol, DAY, rank, None,
        "no_stored_rows_unknown_reason", metadata) for clock in ("S1", "S2")
        for rank, symbol in enumerate(SYMBOLS if clock == "S2" or not s1_only else (*SYMBOLS[1:], "KRX:000021"), 1))
    return sources.UniverseCalendar((DAY,), targets, "a" * 64)


def raw_reader(family="B", *, peer_count=19, peer_close="101", s1_only=False):
    values = {("CC", SYMBOL, DAY): chunk(blocks=blocks(family))}
    for symbol in SYMBOLS[1:peer_count + 1]:
        values[("CC", symbol, DAY)] = chunk(symbol=symbol, blocks=[(100, max(100, Decimal(peer_close)),
            min(100, Decimal(peer_close)), peer_close)] * 60)
    return Reader(values)


def single(family="B", **kwargs):
    calendar = universe()
    reader = raw_reader(family, **kwargs)
    stream = inputs.iter_original_sessions(calendar, reader, lambda _: None)
    return next(stream), reader, calendar


def test_a_2r_is_frozen_from_signal_and_stop_and_keeps_original_structure():
    calendar = universe()
    reader = raw_reader("A")
    price = reader.chunks[("CC", SYMBOL, DAY)]
    original = primary.evaluate_session(sources.session_inputs(SYMBOL, DAY, price, None).session,
                                        scenarios=("EE-A",)).results[0]
    result, _, _ = single("A")
    changed = result.result.results[0]
    assert replace(changed.detection.candidate, scenario_id="EE-A", target=original.detection.candidate.target) == original.detection.candidate
    candidate = changed.detection.candidate
    assert candidate.target == candidate.signal.close + 2 * (candidate.signal.close - candidate.stop)
    assert changed.path.plan.target == candidate.target
    assert result.peer_context is None
    # An entry OPEN change never retargets the pre-entry objective.
    at = changed.path.entry_at
    for row in price["rows"]:
        if row["open_time_ms"] == int(at.timestamp()) * 1000:
            row["open"] = "130"
            row["high"] = "131"
    mutated = next(inputs.iter_original_sessions(calendar, reader, lambda _: None))
    assert mutated.result.results[0].detection.candidate.target == candidate.target
    assert mutated.result.results[0].path.reason == "entry_outside_frozen_levels"


@pytest.mark.parametrize("peer_close,expected", [("101", True), ("99", False), ("100", False)])
def test_peer_sign_exact_mean_and_information_removed_common_support(peer_close, expected):
    value, reader, _ = single(peer_close=peer_close)
    context = value.peer_context
    assert context.state == "known" and context.complete_count == 19
    assert context.mean == Fraction(Decimal(peer_close)) / 100 - 1
    positive, common = value.result.results[1:]
    assert positive.detection.candidate.eligible is expected
    assert common.detection.candidate.eligible is True
    assert positive.detection.candidate.signal == common.detection.candidate.signal
    assert positive.detection.candidate.stop == common.detection.candidate.stop
    if expected:
        assert replace(positive.path, plan=common.path.plan) == common.path
    assert len(context.windows) == 19 and all(len(item.observations) == 15 for item in context.windows)
    assert all(item.membership_available_at < context.decision_at for item in context.windows)
    assert all(atom.available_at <= context.decision_at for item in context.windows for atom in item.observations)
    assert len(reader.calls) == len(set(reader.calls)) == 40


@pytest.mark.parametrize("peer_count,known", [(4, False), (5, True)])
def test_quorum_unknown_is_shared_and_never_synthesized_zero(peer_count, known):
    value, _, _ = single(peer_count=peer_count)
    assert value.peer_context.complete_count == peer_count
    assert (value.peer_context.mean is not None) is known
    for policy in value.result.results[1:]:
        assert policy.detection.candidate.eligible is (True if known else None)
        assert policy.path.plan.admission == ("pass" if known else "unknown")
        if not known:
            assert policy.path.status == "unknown"


def test_missing_conflict_and_invalid_are_separate_and_all_atoms_retained():
    reader, calendar = raw_reader(), universe()
    for symbol, modification in zip(SYMBOLS[1:4], ("missing", "conflict", "invalid"), strict=True):
        raw = reader.chunks[("CC", symbol, DAY)]
        row = raw["rows"][45]
        if modification == "missing":
            raw["rows"].remove(row)
        elif modification == "conflict":
            raw["rows"].append(deepcopy(row))
        else:
            row["open"] = "not-a-number"
    value = next(inputs.iter_original_sessions(calendar, reader, lambda _: None))
    assert [item.state for item in value.peer_context.windows[:3]] == ["missing", "conflict", "invalid"]
    assert value.peer_context.complete_count == 16
    assert all(len(item.observations) == 15 for item in value.peer_context.windows)


def test_whole_original_population_and_membership_fail_before_raw_lookup():
    calendar = universe()
    last = calendar.targets[-1]
    invalid = replace(last, metadata=codec.canonical({"session": DAY, "available_at": f"{DAY}T10:00:00+09:00",
                                                     "selected_at": f"{DAY}T08:30:00+09:00"}))
    reader = raw_reader()
    with pytest.raises(sources.SourceRefusal, match="membership_noncausal"):
        tuple(inputs.iter_original_sessions(replace(calendar, targets=(*calendar.targets[:-1], invalid)), reader, lambda _: None))
    assert reader.calls == [] and reader.checks == 0


def test_s1_only_target_uses_original_s2_twenty_and_union_once():
    calendar, reader = universe(s1_only=True), raw_reader()
    reader.chunks[("CC", "KRX:000021", DAY)] = chunk(symbol="KRX:000021", blocks=blocks("B"))
    values = tuple(inputs.iter_original_sessions(calendar, reader, lambda _: None))
    target = next(item for item in values if item.result.symbol == "KRX:000021")
    assert len(target.targets) == 1 and target.targets[0].scenario == "S1"
    assert len(target.peer_context.windows) == 20
    assert len(reader.calls) == len(set(reader.calls)) == 42
    assert all(item.symbol != "KRX:000021" for item in target.peer_context.windows)


def test_producer_mutation_cannot_supply_independent_reference(monkeypatch):
    real = science.evaluate_session
    def corrupt(session, *, peers, **kwargs):
        peers = list(peers)
        peers[0].raw["rows"][45]["close"] = "999"
        return real(session, peers=peers, **kwargs)
    monkeypatch.setattr(science, "evaluate_session", corrupt)
    with pytest.raises(old_reference.ReferenceRefusal, match="scientific_signature"):
        single()


def geometry(calendar):
    count = len(inputs.population(calendar))
    return dict(sessions=1, targets_per_scenario=20, union_coordinates=count,
                cc_chunks=count, cc_rows=6000, cx_chunks=count, cx_rows=0, cx_daily_rows=0)


def report_rows(calendar, reader):
    return list(report.report_records(calendar, reader, binding=BINDING, geometry=geometry(calendar),
        invented_only=True, checkpoint=lambda _: None, profile="mechanism1"))


def test_closed_report_roundtrip_and_fresh_saved_reference(monkeypatch):
    calendar = universe()
    rows = report_rows(calendar, raw_reader())
    assert rows[0]["policies"] == list(science.POLICIES)
    assert rows[0]["scope"] == report.report_scope("mechanism1")
    sink = BytesIO()
    digest = report.write_report(iter(rows), sink, lambda _: None, profile="mechanism1")
    def forbidden(*_args, **_kwargs):
        raise AssertionError("producer called by saved reference")
    monkeypatch.setattr(science, "evaluate_session", forbidden)
    monkeypatch.setattr(science.scenarios, "detect_bars", forbidden)
    monkeypatch.setattr(science.paths, "evaluate_path", forbidden)
    stream = report.read_report(BytesIO(sink.getvalue()), digest.compressed_sha256,
        digest.decoded_sha256, lambda _: None, profile="mechanism1")
    proof = report.verify_report(stream, universe=calendar, raw_reader=raw_reader(), binding=BINDING,
        geometry=geometry(calendar), invented_only=True, checkpoint=lambda _: None, profile="mechanism1")
    assert proof["verified"] and proof["record_stream_exhausted"]
    assert proof["record_counts"] == {"session": 20, "targets": {"S1": 20, "S2": 20}}
    assert proof["claim"] == report.report_scope("mechanism1")
    assert set(proof["summary"]["S1"]) == set(science.POLICIES)
    with pytest.raises(report.ReportRefusal):
        report.write_report(iter(rows), BytesIO(), lambda _: None)
    with pytest.raises(report.ReportRefusal, match="profile_differs"):
        report.report_scope("arbitrary")


@pytest.mark.parametrize("field", ["mean", "source", "membership", "atom", "bool"])
def test_saved_peer_evidence_tampering_refused(field):
    calendar = universe()
    rows = report_rows(calendar, raw_reader())
    context = rows[1]["payload"]["peer_context"]
    if field == "mean":
        context["mean"] = {"fraction": [999, 1]}
    elif field == "source":
        context["windows"][0]["decoded_chunk_sha256"] = "f" * 64
    elif field == "membership":
        context["windows"][0]["membership_selected_at"] = f"{DAY}T08:31:00+09:00"
    elif field == "atom":
        context["windows"][0]["observations"][0]["decoded_rows"] = []
    else:
        context["complete_count"] = True
    with pytest.raises((report.ReportRefusal, old_reference.ReferenceRefusal)):
        report.verify_report(iter(rows), universe=calendar, raw_reader=raw_reader(), binding=BINDING,
            geometry=geometry(calendar), invented_only=True, checkpoint=lambda _: None, profile="mechanism1")


def test_decimal_context_cannot_change_target_or_peer_mean():
    with localcontext() as context:
        context.prec = 2
        a, _, _ = single("A")
        b, _, _ = single(peer_close="100.00000001")
    assert a.result.results[0].detection.candidate.target == Decimal("129")
    assert b.peer_context.mean == Fraction(1, 10_000_000_000)


def test_equal_weights_include_every_complete_peer_without_ranking_or_replacement():
    reader, calendar = raw_reader(peer_count=6), universe()
    # Five +1% peers and one -10% peer: all six count, not just the quorum five.
    negative = reader.chunks[("CC", SYMBOLS[6], DAY)]
    for row in negative["rows"]:
        row.update(open="100", high="100", low="90", close="90")
    value = next(inputs.iter_original_sessions(calendar, reader, lambda _: None))
    assert value.peer_context.complete_count == 6
    assert value.peer_context.mean == Fraction(-1, 120)
    assert value.result.results[1].detection.candidate.eligible is False
    assert value.result.results[2].detection.candidate.eligible is True
    # Every peer return uses first OPEN and last CLOSE, never an average of
    # constituent minute returns. Intermediate prices do not enter its value.
    for row in negative["rows"][46:59]:
        row.update(open="1000", high="1000", low="900", close="900")
    changed = next(inputs.iter_original_sessions(calendar, reader, lambda _: None))
    assert changed.peer_context.mean == value.peer_context.mean


def test_target_future_missingness_does_not_select_peer_admission():
    reader, calendar = raw_reader(), universe()
    target = reader.chunks[("CC", SYMBOL, DAY)]
    # B confirms at 60m, D=62, first subsequent whole-minute OPEN is 63.
    target["rows"] = target["rows"][:63]
    value = next(inputs.iter_original_sessions(calendar, reader, lambda _: None))
    assert value.peer_context.mean == Fraction(1, 100)
    assert value.result.results[1].detection.candidate.eligible is True
    assert value.result.results[1].path.status == "unknown"


def test_peer_identity_corruption_is_refused_not_a_missing_peer():
    reader, calendar = raw_reader(), universe()
    reader.chunks[("CC", SYMBOLS[1], DAY)]["symbol"] = "KRX:999999"
    with pytest.raises(sources.SourceRefusal, match="identity_differs"):
        next(inputs.iter_original_sessions(calendar, reader, lambda _: None))


def test_saved_report_retains_full_eof_and_order_checks_for_second_profile():
    calendar = universe()
    rows = report_rows(calendar, raw_reader())
    for broken in (rows[:-1], [*rows[:1], *rows[2:]], [*rows[:2], rows[1], *rows[2:]]):
        with pytest.raises(report.ReportRefusal):
            report.write_report(iter(broken), BytesIO(), lambda _: None, profile="mechanism1")
    sink = BytesIO()
    digest = report.write_report(iter(rows), sink, lambda _: None, profile="mechanism1")
    with pytest.raises(report.ReportRefusal):
        list(report.read_report(BytesIO(sink.getvalue()[:-5]), digest.compressed_sha256,
            digest.decoded_sha256, lambda _: None, profile="mechanism1"))


def test_day_cache_retains_one_day_and_protects_private_original_raw():
    calendar, reader = universe(), raw_reader()
    cache = inputs.DayRawCache(calendar, reader)
    first, _ = cache.own(SYMBOL, DAY)
    first["rows"][0]["open"] = "999"
    again, _ = cache.own(SYMBOL, DAY)
    assert again["rows"][0]["open"] != "999"
    assert reader.calls == [("CC", SYMBOL, DAY), ("CX", SYMBOL, DAY)]
    with pytest.raises(sources.SourceRefusal, match="nonoriginal_raw_request"):
        cache.own("KRX:999999", DAY)
    other = "2026-06-04"
    more = tuple(replace(item, session=other, metadata=codec.canonical({"session": other,
        "available_at": f"{other}T08:00:00+09:00", "selected_at": f"{other}T08:30:00+09:00"})) for item in calendar.targets)
    cache = inputs.DayRawCache(replace(calendar, sessions=(DAY, other), targets=(*calendar.targets, *more)), reader)
    cache.own(SYMBOL, DAY)
    cache.own(SYMBOLS[1], DAY)
    cache.own(SYMBOL, other)
    assert cache.day == other and set(cache._raw) == {SYMBOL}


def test_fresh_primary_report_import_does_not_load_followup_project_modules():
    program = '''
import builtins
import os
import sys
sys.path.insert(0, os.getcwd())
forbidden = {"intraday_trader_followups", "intraday_trader_followup_inputs", "intraday_trader_followup_reference"}
original_import = builtins.__import__
def checked(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[-1] in forbidden or (name == "research" and forbidden.intersection(fromlist)):
        raise ImportError("primary tried loading unregistered followup module")
    return original_import(name, globals, locals, fromlist, level)
builtins.__import__ = checked
from research import intraday_trader_report as report
assert report._profile("primary")[0] == report.SCHEMA
assert report.report_scope() == "all supplied original targets and first-attempt price-path mathematics; compressed pins/EOF require exhausted read_report; no archive/account/fill certification"
assert not any("research." + name in sys.modules for name in forbidden)
try:
    report._profile("arbitrary")
except report.ReportRefusal:
    pass
else:
    raise AssertionError("arbitrary profile accepted")
'''
    completed = subprocess.run([sys.executable, "-I", "-S", "-c", program],
                               cwd=Path(__file__).resolve().parents[1],
                               check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_raw_copy_preserves_exact_scalars_nested_invalid_payloads_and_aliases():
    nested = [None, True, 1, 1.25, "text", {"invalid_price": ["mutable"]}]
    original = {"first": nested, "alias": nested, "fallback": (Decimal("1.1"), {"nested": []})}
    original["cycle"] = original
    copied = inputs._copy_raw(original)
    assert copied is not original and copied["cycle"] is copied
    assert copied["first"] is copied["alias"] and copied["first"] is not nested
    assert [type(value) for value in copied["first"]] == [type(value) for value in nested]
    copied["first"][-1]["invalid_price"].append("changed")
    copied["fallback"][1]["nested"].append("changed")
    assert nested[-1]["invalid_price"] == ["mutable"]
    assert original["fallback"][1]["nested"] == []


def test_raw_copy_keeps_unknown_object_deepcopy_behavior():
    class Unknown:
        def __init__(self, values):
            self.values = values

        def __deepcopy__(self, memo):
            copied = Unknown([])
            memo[id(self)] = copied
            copied.values = deepcopy(self.values, memo)
            return copied

    unknown = Unknown([1, {"nested": []}])
    copied = inputs._copy_raw({"unknown": unknown, "alias": unknown})
    assert type(copied["unknown"]) is Unknown and copied["unknown"] is copied["alias"]
    assert copied["unknown"] is not unknown
    copied["unknown"].values[-1]["nested"].append("changed")
    assert unknown.values == [1, {"nested": []}]
