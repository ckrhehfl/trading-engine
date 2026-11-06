"""Invented-only independent EG math, censoring and persisted-output checks."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_highvol_events as producer
from research import intraday_highvol_reference as reference
from research import intraday_ed_sources as sources
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPEN = datetime(2026, 1, 30, 9, tzinfo=timezone(timedelta(hours=9)))
SYMBOL = "KRX:000001"


def session(*, opening=OPEN, changes=None, shares=Decimal(0), omitted=()):
    changes = {} if changes is None else changes
    minutes = {}
    for index in range(300):
        if index in omitted:
            continue
        op = Decimal(100)
        close = changes.get(index, op)
        at = opening + index * MINUTE
        prices = (op, max(op, close) + 1, min(op, close) - 1, close)
        minutes[at] = Minute(SYMBOL, at, at + 3 * MINUTE, prices, shares,
                             "observed", "missing" if shares is None else "observed")
    return SessionInputs(SYMBOL, opening, opening + 390 * MINUTE, minutes)


def previous_metrics(current, *, gap=None, multiplier=1):
    openings = tuple(current.opening - timedelta(days=20 - index) for index in range(20))
    history = {}
    for index, opening in enumerate(openings):
        if index != gap:
            metric = reference.reference_window(session(opening=opening, changes={14: Decimal(multiplier + 100)}), 15)
            history[opening] = metric
    return openings, history


def test_exact_anchored_energy_and_independent_volume():
    observed = session(changes={0: Decimal(102), 1: Decimal(99), 14: Decimal(103)})
    got = reference.reference_window(observed, 15)
    assert got.energy == Fraction(4 + 9 + 1 + 9, 10000)
    assert got.signed_move == Fraction(3, 100)
    assert got.direction_efficiency == Fraction(3, 9)
    assert got.relative_range == Fraction(6, 100)
    assert got.shares == 0 and not got.volume_issues
    assert got.price_available_at == OPEN + 17 * MINUTE
    assert got == producer.measure_window(observed, 15, OPEN + 17 * MINUTE)
    without_volume = reference.reference_window(session(shares=None), 15)
    assert without_volume.energy == 0 and without_volume.shares is None
    assert without_volume.zero_path is True and without_volume.direction_efficiency is None
    assert len(without_volume.volume_issues) == 15


def test_missing_price_preserves_available_volume_and_never_fabricates_energy():
    observed = session()
    at = OPEN + 3 * MINUTE
    rows = dict(observed.minutes)
    rows[at] = replace(rows[at], price_state="missing", ohlc=None)
    observed = replace(observed, minutes=rows)
    got = reference.reference_window(observed, 15)
    assert got.price_status == "unavailable" and got.energy is None
    assert got.shares == 0
    assert tuple(issue.reason for issue in got.price_issues) == ("price_missing",)
    assert got == producer.measure_window(observed, 15, OPEN + 17 * MINUTE)


def test_reference_never_calls_producer_science_or_visits_future_suffix(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("producer science must not supply reference expectations")

    monkeypatch.setattr(producer, "measure_window", fail)
    monkeypatch.setattr(producer, "classify_window", fail)
    monkeypatch.setattr(producer, "group_episodes", fail)
    observed = session(changes={14: Decimal(104)})

    class PrefixOnly(dict):
        def get(self, key, default=None):
            assert key < OPEN + 15 * MINUTE
            return super().get(key, default)

    got = reference.reference_window(replace(observed, minutes=PrefixOnly(observed.minutes)), 15)
    expected, history = previous_metrics(got)
    decision = reference.reference_decision(got, expected, history)
    assert decision.status == "high"


def test_reference_rejects_future_availability_and_malformed_prices():
    original = session()
    for mutation in (
        {"available_at": OPEN + 18 * MINUTE},
        {"available_at": OPEN + MINUTE},
        {"ohlc": (Decimal(100), Decimal(98), Decimal(99), Decimal(100))},
        {"ohlc": (Decimal(100), Decimal("NaN"), Decimal(99), Decimal(100))},
    ):
        rows = dict(original.minutes)
        rows[OPEN] = replace(rows[OPEN], **mutation)
        with pytest.raises(reference.CoverageVerificationRefusal):
            reference.reference_window(replace(original, minutes=rows), 15)


def test_exact20_rank_strict_ties_and_missing_dates():
    current = reference.reference_window(session(changes={14: Decimal(101)}), 15)
    expected, history = previous_metrics(current)
    tied = reference.reference_decision(current, expected, history)
    assert tied.status == "not_high" and tied.threshold_tie is True
    assert tied.threshold == Fraction(1, 10000)
    assert tied == producer.classify_window(current, expected, history)
    absent = dict(history)
    del absent[expected[8]]
    unknown = reference.reference_decision(current, expected, absent)
    assert unknown.status == "unavailable" and unknown.threshold is None
    assert unknown.reason == "prior_history_unavailable"
    warmup = reference.reference_decision(current, expected[1:], {at: value for at, value in history.items() if at != expected[0]})
    assert warmup.reason == "insufficient_original_history"
    with pytest.raises(reference.CoverageVerificationRefusal):
        reference.reference_decision(current, expected, {**history, OPEN: current})


def decisions(statuses):
    result = []
    observed = session(changes={index: Decimal(101 + index % 2) for index in range(300)})
    for index, offset in enumerate(reference.GRID):
        metric = reference.reference_window(observed, offset)
        status = statuses.get(index, "not_high")
        threshold = metric.energy / 2 if status == "high" else metric.energy
        result.append(producer.HighVolDecision(metric, (), (), threshold, status, "fixture", False, False))
    return tuple(result)


def test_episode_unknown_bridges_do_not_invent_new_onsets():
    rows = decisions({0: "high", 1: "unavailable", 2: "high", 6: "high", 7: "unavailable", 57: "high"})
    got = reference.reference_episodes(rows)
    assert got == producer.group_episodes(rows)
    assert got.observed_high_windows == 4
    assert got.confirmed_onsets == 2 and got.left_censored_clusters == 1
    assert got.exact_onset_count is None
    assert got.episodes[0].unknown_bridge_offsets == (20,)
    assert got.episodes[1].trailing_unknown_offsets == (50,)
    assert got.episodes[1].right_censored is True
    assert got.episodes[2].right_censored is True


def test_known_quiet_after_unknown_can_establish_a_later_new_onset():
    rows = decisions({1: "unavailable", 3: "high"})
    got = reference.reference_episodes(rows)
    assert got.confirmed_onsets == 1 and not got.episodes[0].left_censored
    assert got.exact_onset_count is None
    complete = reference.reference_episodes(decisions({3: "high", 4: "high", 9: "high"}))
    assert complete.exact_onset_count == 2


def test_anchor_scaling_changes_neither_energy_nor_direction():
    original = session(changes={index: Decimal(100 + index % 3) for index in range(30)})
    scaled = replace(original, minutes={at: replace(row, ohlc=tuple(value * 7 for value in row.ohlc))
                                         for at, row in original.minutes.items()})
    a, b = reference.reference_window(original, 30), reference.reference_window(scaled, 30)
    assert (a.energy, a.signed_move, a.relative_range, a.direction_efficiency) == (
        b.energy, b.signed_move, b.relative_range, b.direction_efficiency)


def calendar(days):
    targets = tuple(sources.TargetCoordinate(scenario, SYMBOL, day, 1, 300,
        "stored_rows_completeness_unverified", f"{scenario} {day} opaque".encode())
        for day in days for scenario in ("S1", "S2"))
    return sources.UniverseCalendar(tuple(days), targets, "a" * 64)


def raw_chunk(day, kind, *, volatile=False, omit=()):
    opening = sources.calendar_source.session_open(day)
    rows = []
    for index in range(300):
        if index in omit:
            continue
        row = {"open_time_ms": int((opening + index * MINUTE).timestamp()) * 1000,
               "fetched_at": "2026-10-30T00:00:00Z"}
        if kind == "CC":
            close = 100 + (5 if volatile and 40 <= index < 75 else 1) * (1 if index % 2 else -1)
            row.update(symbol=SYMBOL, interval="1m", open="100", high=str(max(100, close) + 1),
                       low=str(min(100, close) - 1), close=str(close))
        else:
            row.update(volume="0" if index == 0 else "10", quote_volume=None)
        rows.append(row)
    value = {"symbol": SYMBOL, "session": day, "observation_date": day, "available_at": None,
             "retrieved_at": "2026-10-30T00:00:00Z", "source": "collector klines", "is_final": None,
             "evidence_level": "assumed", "rows": rows}
    if kind == "CX":
        value.update(daily_rows=[], daily_retrieved_at="2026-10-30T00:00:00Z")
    return value


class Reader:
    def __init__(self, days, *, gap_day=None):
        self.values = {(kind, SYMBOL, day): raw_chunk(day, kind, volatile=day == days[-1],
                                                   omit=(7,) if kind == "CC" and day == gap_day else ())
                       for day in days for kind in ("CC", "CX")}
        self.calls = []

    def load_raw_chunk(self, kind, symbol, day):
        self.calls.append((kind, symbol, day))
        return self.values.get((kind, symbol, day))

    def assert_unchanged(self):
        pass


def test_original21day_population_and_independent_report_equal_production(tmp_path):
    from research import intraday_highvol_coverage as storage
    from research.intraday_highvol_population import iter_coverage_records

    days, current = [], date(2026, 1, 5)
    while len(days) < 21:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    universe = calendar(days)
    raw = Reader(days, gap_day=days[3])
    records = tuple(iter_coverage_records(universe, raw, lambda _: None))
    independent = tuple(reference.iter_reference_records(universe, raw, lambda _: None))
    assert records == independent
    assert records[-1].decisions[0].reason == "prior_history_unavailable"
    assert any(row.status == "high" for row in records[-1].decisions)
    assert all(row.reason == "insufficient_original_history" for row in records[0].decisions)
    path = tmp_path / "coverage.jsonl.gz"
    binding = {"invented_only": True}
    seal, produced_report = storage.write_coverage(path, universe, binding, records, lambda _: None)
    verified_report = reference.verify_coverage(path, seal, universe, raw, binding, lambda _: None)
    assert verified_report == produced_report
    scenario = verified_report["scenarios"]["S1"]
    assert scenario["target_days"] == 21
    assert scenario["minute_segments"]["first_hour"]["expected_minutes"] == 21 * 60
    assert scenario["minute_segments"]["first_hour"]["price_states"]["price_missing"] == 1
    assert scenario["minute_segments"]["first_hour"]["zero_volume_minutes"] == 21
    assert len(scenario["weekly"]) == 5
    assert scenario["weekly"][0]["observed_clusters"] == 0
    assert scenario["episodes"]["confirmed_onsets"] >= 1


@pytest.mark.parametrize("field", ["energy", "censor", "scope", "minute_coverage", "volume"])
def test_saved_scientific_mutations_fail_independent_raw_reconstruction(field):
    from research import intraday_highvol_coverage as storage

    universe = calendar(["2026-01-05"])
    raw = Reader(universe.sessions)
    record = next(reference.iter_reference_records(universe, raw, lambda _: None))
    value = storage.project(record)
    if field == "energy":
        value["decisions"][0]["current"]["energy"] = {"fraction": [99, 100]}
    elif field == "censor":
        value["episodes"]["exact_onset_count"] = 0
    elif field == "scope":
        value["targets"][0]["rank"] = True  # bool is not an integer identity
    elif field == "minute_coverage":
        value["minute_coverage"][0]["zero_volume_minutes"] += 1
    else:
        value["decisions"][0]["current"]["shares"] = None
    with pytest.raises(reference.CoverageVerificationRefusal, match="stored_highvol_record_differs"):
        reference.verify_records([value], universe, raw, lambda _: None)


def test_saved_header_binding_and_population_length_are_verified(tmp_path):
    from research import intraday_highvol_coverage as storage

    universe = calendar(["2026-01-05"])
    raw = Reader(universe.sessions)
    records = tuple(reference.iter_reference_records(universe, raw, lambda _: None))
    path = tmp_path / "scope.jsonl.gz"
    seal, _ = storage.write_coverage(path, universe, {"binding": "original"}, records, lambda _: None)
    with pytest.raises(reference.CoverageVerificationRefusal, match="stored_highvol_header_differs"):
        reference.verify_coverage(path, seal, universe, raw, {"binding": "changed"}, lambda _: None)
    encoded = storage.project(records[0])
    with pytest.raises(reference.CoverageVerificationRefusal, match="extra_stored_highvol_record"):
        reference.verify_records([encoded, encoded], universe, raw, lambda _: None)
    with pytest.raises(reference.CoverageVerificationRefusal, match="stored_highvol_record_differs"):
        reference.verify_records([], universe, raw, lambda _: None)


def test_minute_support_late_price_and_actual_zero_are_distinct():
    from research.intraday_highvol_population import minute_coverage

    original = session()
    rows = dict(original.minutes)
    rows[OPEN] = replace(rows[OPEN], available_at=OPEN + 4 * MINUTE)
    rows[OPEN + MINUTE] = replace(rows[OPEN + MINUTE], volume_state="missing", shares=None)
    observed = replace(original, minutes=rows)
    got = reference.reference_minute_coverage(observed, 0, 60)
    assert got == minute_coverage(observed)[0]
    assert dict(got.volume_states) == {"late_at_assumed_clock": 1, "observed": 58, "volume_missing": 1}
    assert got.zero_volume_minutes == 58


@pytest.mark.parametrize("reason", ["prior_price_chunk_absent", "prior_symbol_outside_preserved_population"])
def test_prior_missing_source_is_not_a_quiet_baseline_or_older_replacement(reason):
    from research.intraday_highvol_population import iter_coverage_records

    days = ("2026-01-05", "2026-01-06")
    universe, raw = calendar(days), Reader(days)
    if reason == "prior_price_chunk_absent":
        del raw.values[("CC", SYMBOL, days[0])]
    else:
        universe = replace(universe, targets=tuple(replace(target, symbol="KRX:000002")
            if target.session == days[0] else target for target in universe.targets))
    actual = tuple(iter_coverage_records(universe, raw, lambda _: None))
    independent = tuple(reference.iter_reference_records(universe, raw, lambda _: None))
    assert actual == independent
    last = independent[-1].decisions[0]
    assert last.prior_energies == (None,)
    assert last.history_issues[0].reason == reason
    assert len(last.expected_prior_openings) == 1
    assert last.threshold is None


def test_self_consistent_wrong_footer_is_rejected_by_independent_report(tmp_path, monkeypatch):
    from research import intraday_highvol_coverage as storage

    universe, raw = calendar(["2026-01-05"]), Reader(["2026-01-05"])
    records = tuple(reference.iter_reference_records(universe, raw, lambda _: None))
    original = storage.CoverageSummary.finish

    def wrong_report(self):
        result = original(self)
        result["scenarios"]["S1"]["episodes"]["confirmed_onsets"] += 1
        return result

    monkeypatch.setattr(storage.CoverageSummary, "finish", wrong_report)
    path = tmp_path / "wrong-report.jsonl.gz"
    seal, _ = storage.write_coverage(path, universe, {}, records, lambda _: None)
    with pytest.raises(reference.CoverageVerificationRefusal, match="stored_highvol_footer_differs"):
        reference.verify_coverage(path, seal, universe, raw, {}, lambda _: None)
