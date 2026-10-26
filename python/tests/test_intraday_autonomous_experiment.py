"""Invented top20 source grids exercise population, timing and statistical gates."""

from copy import deepcopy
from fractions import Fraction
import gc
import hashlib
import weakref

import pytest

from research import intraday_autonomous_experiment as experiment


cc = experiment.cc
H2 = experiment.registry.HYPOTHESIS_IDS[1]
DAYS = ("2025-09-04", "2025-09-11", "2025-09-18")
SYMBOLS = tuple(f"KRX:{number:06d}" for number in range(1, 21))


def raw_rows(day, symbol, *, recovery=False, terminal="103"):
    opening = cc.session_open(day)
    rows = [{"symbol": symbol, "interval": "1m",
        "open_time_ms": int((opening + index * cc.MINUTE).timestamp()) * 1000 + 123,
        "open": "100", "high": "100", "low": "100", "close": "100",
        "fetched_at": "2026-10-20T00:00:00+00:00"} for index in range(390)]
    if recovery:
        for row in rows[:60]:
            row.update(high="102", low="98", close="99")
        for row in rows[60:]:
            row.update(open="101", high="101", low="101", close="101")
        rows[130].update(open=terminal, high=terminal, low=terminal, close=terminal)
    return rows


def inputs(days=DAYS, observations=None):
    observations = observations or {}
    upstream = {
        "study_id": "intraday-universe-sources-v1",
        "mode": "discovery-universe-source-and-coverage-only", **cc.FLAGS,
        "calendar": {"rows": [{"date": day, "fetched_at": None} for day in days], "certified": False},
        "variants": {},
    }
    for policy in ("D+1", "D+2"):
        upstream["variants"][policy] = {"days": [{
            "session": day, "observation_date": "2025-08-29",
            "selected_at": day + "T08:30:00+09:00", "available_at": "2025-09-01T08:30:00+09:00",
            "retrieved_at": {"fixture": "2026-10-20T00:00:00+00:00"},
            "source": "invented monthly source", "is_final": None,
            "data_vintage": {"fixture": "b" * 64}, "evidence_level": "assumed",
            "targets": [{"symbol": symbol, "rank": rank, "stored_row_count": None,
                "state": "no_stored_rows_unknown_reason"} for rank, symbol in enumerate(SYMBOLS, 1)],
        } for day in days], "summary": {"fixture_unknowns_preserved": True}}
    chunks = [{"symbol": symbol, "session": day, "observation_date": day,
        "available_at": None, "retrieved_at": "2026-10-20T01:00:00+00:00",
        "source": "collector klines", "is_final": None, "evidence_level": "assumed",
        "rows": deepcopy(observations.get((day, symbol), []))}
        for day in days for symbol in SYMBOLS]
    return upstream, chunks


def event_inputs(*, missing_peer_label=False, missing_event_label=False, extremes=False):
    observations = {}
    for index, day in enumerate(DAYS[:2]):
        observations[(day, SYMBOLS[0])] = raw_rows(day, SYMBOLS[0], recovery=True,
            terminal=("200" if index == 0 else "10") if extremes else str(103 + index))
        observations[(day, SYMBOLS[1])] = raw_rows(day, SYMBOLS[1])
        observations[(day, SYMBOLS[2])] = raw_rows(day, SYMBOLS[2])
    if missing_peer_label:
        observations[(DAYS[0], SYMBOLS[1])].pop(120)
    if missing_event_label:
        observations[(DAYS[0], SYMBOLS[0])].pop(120)
    return inputs(observations=observations)


def build(upstream, chunks):
    return experiment.build_experiment(upstream, iter(chunks), hypothesis_id=H2)


def test_all_original_targets_provenance_and_zero_weeks_survive():
    upstream, chunks = event_inputs()
    original_upstream, original_chunks = deepcopy(upstream), deepcopy(chunks)
    report = build(upstream, chunks)
    assert upstream == original_upstream and chunks == original_chunks
    assert report["source_target_matrix"] == upstream["variants"]
    assert len(report["sources"]) == 60
    source = report["sources"][0]
    assert source["data_vintage"] == hashlib.sha256(cc._canonical(chunks[0]) + b"\n").hexdigest()
    assert source["source_offset_counts_ms"] == {123: 390}
    assert source["raw_chunk_metadata"]["available_at"] is None
    assert source["raw_chunk_metadata"]["is_final"] is None
    for variant in report["variants"].values():
        assert len(variant["days"]) == 60
        assert variant["counts"] == {"targets": 60, "signals": 2, "known_no_signal": 4,
            "unknown_signals": 54, "event_known": 2, "event_unknown": 0,
            "pair_complete": 2, "pair_unknown": 0}
        assert variant["by_week"]["2025-09-15"]["event_known"] == 0
        assert variant["frequency"]["known_opportunities_per_week"] == 2 / 3
        assert len(variant["by_symbol"]) == 20
        assert variant["calendar_halves"]["second"]["counts"]["targets"] == 0
    assert not report["followup_marker"]["both_policies"]
    assert all(value is False for key, value in report.items() if key.endswith("certified"))


def test_same_clock_peer_is_selected_before_any_label_and_not_replaced(monkeypatch):
    upstream, chunks = event_inputs(missing_peer_label=True)
    timeline = []
    original_select, original_label = experiment.outcomes.choose_peer, experiment.outcomes.label60

    def choose(**kwargs):
        timeline.append("select")
        return original_select(**kwargs)

    def label(*args, **kwargs):
        timeline.append("label")
        assert timeline[-2] in {"select", "label"}
        return original_label(*args, **kwargs)

    monkeypatch.setattr(experiment.outcomes, "choose_peer", choose)
    monkeypatch.setattr(experiment.outcomes, "label60", label)
    report = build(upstream, chunks)
    assert timeline == ["select", "label", "label"] * 4
    row = report["variants"]["D+1"]["days"][0]
    assert row["peer_selection"]["peer_code"] == SYMBOLS[1]
    assert row["peer_source_index"] == 1
    assert row["event_label"]["status"] == "known"
    assert row["peer_label"]["status"] == "unknown"
    assert row["peer_label"]["unknown_minute_buckets"] == 1
    assert row["pair"]["event_observed"] and not row["pair"]["pair_complete"]
    assert row["pair"]["paired_difference"] is None
    assert row["pair"]["pair_unknown_reasons"] == ["peer_label_unknown"]
    own = report["variants"]["D+1"]["own_sizing"]
    assert own["event_observed"]["known_labels"] == 2
    assert own["paired_event"]["known_labels"] == 1
    assert own["paired_event"]["unknown_labels"] == 1


def test_late_gap_keeps_signal_and_exact_clocks_but_unknown_event():
    report = build(*event_inputs(missing_event_label=True))
    row = report["variants"]["D+1"]["days"][0]
    opening = cc.session_open(DAYS[0])
    assert row["signal"]["state"] == "signal"
    assert row["signal"]["decision_at"] == opening + 67 * cc.MINUTE
    assert row["event_label"]["entry_at"] == opening + 70 * cc.MINUTE
    assert row["event_label"]["exit_at"] == opening + 130 * cc.MINUTE
    assert row["event_label"]["status"] == "unknown"
    assert row["event_label"]["price_change"] is None
    assert row["event_label"]["unknown_minute_buckets_detail"] == [{
        "minute": opening + 120 * cc.MINUTE, "state": "missing", "gap_provenance_supplied": True}]


def test_no_causal_peer_retains_event_result_and_unknown_pair():
    upstream, chunks = inputs(observations={(DAYS[0], SYMBOLS[0]): raw_rows(DAYS[0], SYMBOLS[0], recovery=True)})
    report = build(upstream, chunks)
    row = report["variants"]["D+1"]["days"][0]
    assert row["peer_selection"]["peer_code"] is None
    assert row["peer_selection"]["candidate_counts"]["unknown"] == 19
    assert len(row["peer_selection"]["unknown_candidates"]) == 19
    assert row["pair"]["event_only_price_change"] == Fraction(2, 101)
    assert row["pair"]["pair_unknown_reasons"] == ["no_complete_causal_peer"]


def test_missing_causal_prefix_skips_nearest_rank_before_outcomes():
    upstream, chunks = event_inputs()
    chunks[1]["rows"].pop(40)
    report = build(upstream, chunks)
    row = report["variants"]["D+1"]["days"][0]
    assert row["peer_selection"]["peer_code"] == SYMBOLS[2]
    nearest = next(item for item in row["peer_selection"]["unknown_candidates"]
                   if item["symbol"] == SYMBOLS[1])
    assert nearest["reason"] == "incomplete_causal_prefix"
    assert nearest["valid_bars"] == 8


def test_negative_observed_prices_and_costs_do_not_qualify():
    observations = {}
    for index, day in enumerate(DAYS[:2]):
        observations[(day, SYMBOLS[0])] = raw_rows(day, SYMBOLS[0], recovery=True, terminal=str(99 - index))
        observations[(day, SYMBOLS[1])] = raw_rows(day, SYMBOLS[1])
    report = build(*inputs(observations=observations))
    for variant in report["variants"].values():
        stats = variant["statistics"]["event_observed"]
        assert stats["gross"]["mean_exact"] == Fraction(-5, 202)
        assert stats["net"]["primary"]["mean"] < stats["gross"]["mean"] < 0
        assert not variant["followup_marker"]["event_net33_positive"]
        assert not variant["followup_marker"]["paired_mean_positive"]
    assert not report["followup_marker"]["both_policies"]


def test_fixed_calendar_tax_boundary_changes_only_secondary_net():
    days = ("2025-12-26", "2025-12-29")
    observations = {(day, SYMBOLS[0]): raw_rows(day, SYMBOLS[0], recovery=True,
        terminal=str(103 + index)) for index, day in enumerate(days)}
    observations.update({(day, SYMBOLS[1]): raw_rows(day, SYMBOLS[1]) for day in days})
    report = build(*inputs(days=days, observations=observations))
    stats = report["variants"]["D+1"]["statistics"]["event_observed"]
    assert stats["net"]["tax_date_only"]["mean_exact"] - stats["net"]["primary"]["mean_exact"] == Fraction(5, 20000)
    assert stats["net"]["tax_date_only"]["design_se"] != stats["net"]["primary"]["design_se"]


def test_constant_zero_variance_retains_arithmetic_without_inventing_uncertainty():
    observations = {(day, SYMBOLS[0]): raw_rows(day, SYMBOLS[0], recovery=True)
                    for day in DAYS[:2]}
    report = build(*inputs(observations=observations))
    own = report["variants"]["D+1"]["own_sizing"]["event_observed"]
    stats = report["variants"]["D+1"]["statistics"]["event_observed"]
    assert own["uninformative_reason"] == "zero_dispersion_no_uncertainty_estimate"
    assert not own["sizing_pass"]
    assert stats["gross"]["mean_exact"] == Fraction(2, 101)
    assert stats["gross"]["interval95"] is None
    assert stats["primary_net_interval995"] is None


def test_constant_labels_with_unequal_daily_counts_cannot_gain_roundoff_power():
    reference = [f"2025-09-{day:02d}" for day in range(4, 29)]
    records = [{"session": day, "label": Fraction(2, 101)}
        for index, day in enumerate(reference) for _ in range(index % 20 + 1)]
    own = experiment._own_sizing(records, reference)
    assert own["event_sd"] == 0
    assert own["uninformative_reason"] == "zero_dispersion_no_uncertainty_estimate"
    assert not own["sizing_pass"]
    stats = experiment._statistics(records, own, reference, difference=False, checkpoint=lambda: None)
    assert stats["gross"]["mean_exact"] == Fraction(2, 101)
    assert stats["gross"]["interval95"] is None
    assert stats["primary_net_interval995"] is None


def test_four_plans_for_both_policies_precede_any_reported_means(monkeypatch):
    completed = []
    original_sizing, original_stats = experiment._own_sizing, experiment._statistics

    def size(*args, **kwargs):
        result = original_sizing(*args, **kwargs)
        completed.append(result)
        return result

    def statistics(*args, **kwargs):
        assert len(completed) == 8
        return original_stats(*args, **kwargs)

    monkeypatch.setattr(experiment, "_own_sizing", size)
    monkeypatch.setattr(experiment, "_statistics", statistics)
    report = build(*event_inputs(extremes=True))
    variant = report["variants"]["D+1"]
    own = variant["own_sizing"]["event_observed"]
    assert own["mde_bps"] > 33 and not own["sizing_pass"]
    assert own["status"] == "underpowered" and not own["requires_operator_choice"]
    expected = (Fraction(200, 101) - 1 + Fraction(10, 101) - 1) / 2
    stats = variant["statistics"]["event_observed"]
    assert stats["gross"]["mean_exact"] == expected
    assert stats["net"]["primary"]["mean_exact"] == expected - Fraction(33, 10000)
    assert stats["net"]["tax_date_only"]["mean_exact"] == expected - Fraction(28, 10000)
    assert stats["net"]["slippage_stress"]["mean_exact"] == expected - Fraction(43, 10000)
    assert not variant["followup_marker"]["conditional_followup"]


def test_empty_population_has_no_fabricated_mean_and_keeps_all_targets():
    report = build(*inputs())
    variant = report["variants"]["D+1"]
    assert variant["counts"]["unknown_signals"] == 60
    for stats in variant["statistics"].values():
        assert stats["gross"]["mean"] is None
        assert stats["gross"]["interval95"] is None
        assert stats["primary_net_interval995"] is None
    assert all(not plan["sizing_pass"] for plan in variant["own_sizing"].values())


@pytest.mark.parametrize("mutation, reason", [
    (lambda rows: rows.reverse(), "order_or_identity"),
    (lambda rows: rows.pop(), "missing_input_chunk"),
    (lambda rows: rows.append(deepcopy(rows[-1])), "extra_input_chunk"),
    (lambda rows: rows[0].update(available_at="2025-09-04T09:00:00+09:00"), "metadata"),
    (lambda rows: rows[0].update(retrieved_at="2026-10-20T00:00:00"), "retrieval_clock"),
    (lambda rows: rows[0]["rows"][0].update(symbol=SYMBOLS[1]), "columns_identity"),
    (lambda rows: rows[0]["rows"][0].update(volume="1"), "columns_identity"),
])
def test_changed_chunk_scope_fails_closed(mutation, reason):
    upstream, chunks = event_inputs()
    mutation(chunks)
    with pytest.raises(experiment.ExperimentRefusal, match=reason):
        build(upstream, chunks)


def test_original_membership_cannot_drop_target_or_become_noncausal():
    upstream, chunks = inputs()
    upstream["variants"]["D+2"]["days"][0]["targets"].pop()
    with pytest.raises(cc.CalibrationRefusal, match="target_population_changed"):
        build(upstream, chunks)
    upstream, chunks = inputs()
    upstream["variants"]["D+1"]["days"][0]["selected_at"] = DAYS[0] + "T09:00:00+09:00"
    with pytest.raises(cc.CalibrationRefusal, match="noncausal_membership"):
        build(upstream, chunks)


@pytest.mark.parametrize("rank", [True, 1.0])
def test_numeric_rank_alias_is_rejected_even_without_signals(rank):
    upstream, chunks = inputs()
    upstream["variants"]["D+1"]["days"][0]["targets"][0]["rank"] = rank
    with pytest.raises(experiment.ExperimentRefusal, match="invalid_original_cb_rank_type"):
        build(upstream, chunks)


def test_stream_releases_previous_day_sessions_before_next_day_intake(monkeypatch):
    upstream, chunks = event_inputs()
    references = []
    original = cc.aggregate_session

    def aggregate(*args, **kwargs):
        session = original(*args, **kwargs)
        references.append(weakref.ref(session))
        return session

    def stream():
        for index, chunk in enumerate(chunks):
            if index and index % 20 == 0:
                gc.collect()
                assert all(reference() is None for reference in references)
            yield chunk

    monkeypatch.setattr(cc, "aggregate_session", aggregate)
    report = experiment.build_experiment(upstream, stream(), hypothesis_id=H2)
    assert report["input_evidence"]["unique_target_days"] == 60


def test_old_detector_and_io_are_not_called(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("old replay or input acquisition")

    monkeypatch.setattr(cc, "calibrate_session", forbidden)
    monkeypatch.setattr(cc, "_read_session", forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    report = build(*inputs())
    assert report["input_evidence"]["queried_rows"] == 0
