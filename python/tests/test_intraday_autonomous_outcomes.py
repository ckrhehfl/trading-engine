"""Invented fixtures for pure DZ outcomes, controls and component integration."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_autonomous_outcomes as dz
from research import intraday_autonomous_signals as signals
from research import intraday_pattern_calibration as cc


H1, _, _, _, H5 = dz.HYPOTHESES
OPEN = cc.session_open("2026-09-01")
DECISION = OPEN + 67 * cc.MINUTE
RANKS = {f"KRX:{rank:06d}": rank for rank in range(1, 21)}
EVENT, LOWER, HIGHER = "KRX:000010", "KRX:000009", "KRX:000011"


def session(day="2026-09-01", missing=None, conflict=None):
    """Create exact supplied CC structures, without any input loader or database."""
    opening, closing = cc.session_open(day), cc.session_close(day)
    minutes, gaps = {}, []
    for offset in range(int((closing - opening) / cc.MINUTE)):
        minute = opening + offset * cc.MINUTE
        price = Decimal(1000 + offset)
        minutes[minute] = (price, price + 10, price - 10, price + 2)
        if offset in {missing, conflict}:
            minutes[minute] = None
            gaps.append({"start": minute, "end": minute + cc.MINUTE,
                         "state": "conflict" if offset == conflict else "missing"})
    bars = []
    for index in range(int((closing - opening) / cc.FIVE)):
        start = opening + index * cc.FIVE
        values = [minutes[start + offset * cc.MINUTE] for offset in range(5)]
        if any(value is None for value in values):
            break
        bars.append(cc.core.Bar(start, start + cc.FIVE, start + cc.FIVE + 2 * cc.MINUTE,
                    values[0][0], max(value[1] for value in values),
                    min(value[2] for value in values), values[-1][3]))
    return cc.Session(opening, minutes, tuple(bars), tuple(gaps), (12000, 0))


def label(s=None, decision=DECISION, code=EVENT, hypothesis_id=H1):
    return dz.label60(s or session(), decision, hypothesis_id=hypothesis_id, symbol=code)


def choice(prefixes=None, decision=DECISION, opening=OPEN, ranks=None):
    return dz.choose_peer(event_code=EVENT, original_cb_ranks=RANKS if ranks is None else ranks,
                          causal_prefixes=prefixes if prefixes is not None else
                          {LOWER: session().prefix, HIGHER: session().prefix},
                          opening=opening, decision=decision)


def test_complete_all61_exact_open_ratio_and_provenance_not_close_ratio():
    provenance = {"source": "invented", "data_vintage": "invented-v1", "retrieved_at": None}
    result = dz.label60(session(), DECISION, hypothesis_id=H1, source_provenance=provenance)
    assert result["status"] == "known"
    assert result["entry_at"] == OPEN + 70 * cc.MINUTE
    assert result["exit_at"] == OPEN + 130 * cc.MINUTE
    assert result["price_change"] == Fraction(1130, 1070) - 1
    assert result["required_minute_buckets"] == len(result["minute_bucket_states"]) == 61
    assert result["unknown_minute_buckets"] == 0
    assert result["entry_price_available_at"] == OPEN + 73 * cc.MINUTE
    assert result["label_available_at"] == OPEN + 133 * cc.MINUTE
    assert result["original_timestamp_offsets_ms"] == (12000, 0)
    assert result["source_provenance"] == provenance
    provenance["source"] = "changed"
    assert result["source_provenance"]["source"] == "invented"
    assert not result["actual_fill_certified"]


@pytest.mark.parametrize("offset", [70, 100, 130])
def test_one_missing_bucket_keeps_exact_clocks_and_unknown(offset):
    result = label(session(missing=offset))
    assert result["status"] == "unknown" and result["price_change"] is None
    assert result["unknown_minute_buckets"] == 1
    assert result["unknown_bucket_reason_counts"] == {"missing": 1, "conflict": 0, "invalid_price": 0}
    assert result["entry_at"] == OPEN + 70 * cc.MINUTE
    assert result["exit_at"] == OPEN + 130 * cc.MINUTE


def test_conflict_and_invalid_price_are_not_imputed_or_labelled_known():
    s = session(conflict=101)
    s.minutes[OPEN + 110 * cc.MINUTE] = (Decimal("NaN"),) * 4
    result = label(s)
    assert result["unknown_bucket_reason_counts"] == {"missing": 0, "conflict": 1, "invalid_price": 1}
    assert result["unknown_minute_buckets"] == 2 and result["price_change"] is None


def test_supplied_gap_cannot_coexist_with_valid_price():
    s = session()
    gap = {"start": OPEN + 100 * cc.MINUTE, "end": OPEN + 101 * cc.MINUTE, "state": "conflict"}
    with pytest.raises(dz.OutcomeRefusal, match="price_and_gap_provenance_conflict"):
        label(replace(s, gaps=(gap,)))


@pytest.mark.parametrize("decision_offset,entry_offset", [(65, 70), (67, 70), (70, 75)])
def test_strict_open_clock_equality_and_late_decision(decision_offset, entry_offset):
    result = label(decision=OPEN + decision_offset * cc.MINUTE)
    assert result["entry_at"] == OPEN + entry_offset * cc.MINUTE
    assert result["exit_at"] == OPEN + (entry_offset + 60) * cc.MINUTE


def test_missing_entry_is_not_replaced_by_later_price():
    result = label(session(missing=70))
    assert result["entry_open"] is None and result["status"] == "unknown"
    assert result["entry_at"] == OPEN + 70 * cc.MINUTE


def test_intrabar_extremes_and_later_gaps_cannot_change_open_to_open_label():
    s = session(missing=200)
    s.minutes[OPEN + 90 * cc.MINUTE] = (
        Decimal(1090), Decimal(1000000), Decimal(1), Decimal(2))
    result = label(s)
    assert result["status"] == "known"
    assert result["price_change"] == Fraction(6, 107)
    assert result["exit_at"] == OPEN + 130 * cc.MINUTE


@pytest.mark.parametrize("day,session_minutes", [("2026-09-01", 390), ("2026-01-02", 330), ("2025-11-13", 390)])
def test_h5_fixed_clock_including_short330(day, session_minutes):
    s = session(day)
    close = cc.session_close(day)
    assert (close - s.opening) / cc.MINUTE == session_minutes
    result = label(s, close - 78 * cc.MINUTE, hypothesis_id=H5)
    assert result["entry_at"] == close - 75 * cc.MINUTE
    assert result["exit_at"] == close - 15 * cc.MINUTE
    assert result["status"] == "known"
    with pytest.raises(dz.OutcomeRefusal, match="h5_fixed_clock_changed"):
        label(s, close - 77 * cc.MINUTE, hypothesis_id=H5)


def test_ordinary_cutoff_and_cross_day_refused():
    with pytest.raises(dz.OutcomeRefusal, match="ordinary_decision_after_cutoff"):
        label(decision=OPEN + 242 * cc.MINUTE)
    with pytest.raises(dz.OutcomeRefusal, match="invalid_modeled_session_clock"):
        label(decision=DECISION + timedelta(days=1))


def test_nearest_rank_tie_lower_rank_and_all_twenty_retained():
    selected = choice()
    assert selected["peer_code"] == LOWER
    assert selected["peer_rank"] == 9 and len(selected["candidate_states"]) == 20
    assert selected["candidate_states"]["KRX:000001"]["status"] == "unknown"
    assert selected["candidate_states"][EVENT]["status"] == "event_not_candidate"
    assert selected["required_bars"] == 13
    assert selected["candidate_states"][LOWER]["last_available_at"] == DECISION


def test_prefix_requires_last_scheduled_available_bar_and_no_skip():
    s = session()
    result = choice({LOWER: s.prefix[:12], HIGHER: s.prefix[:13]})
    assert result["peer_code"] == HIGHER
    assert result["candidate_states"][LOWER]["reason"] == "incomplete_causal_prefix"
    broken = s.prefix[:5] + s.prefix[6:]
    with pytest.raises(dz.OutcomeRefusal, match="contiguity_changed"):
        choice({LOWER: broken})


def test_unavailable_bar_prices_and_future_noise_are_never_read():
    s = session()
    bad_future = replace(s.prefix[13], open=object(), high=object(), low=object(), close=object())
    prefixes = {LOWER: s.prefix[:13] + (bad_future, object()), HIGHER: s.prefix}
    assert choice(prefixes)["peer_code"] == LOWER
    late = replace(s.prefix[12], available_at=DECISION + cc.MINUTE,
                   open=object(), high=object(), low=object(), close=object())
    with pytest.raises(dz.OutcomeRefusal, match="causal_bar_clock_or_contiguity_changed"):
        choice({LOWER: s.prefix[:12] + (late,), HIGHER: s.prefix})


def test_h5_peer_prefix_uses_fixed_late_decision_not_ordinary_cutoff():
    s = session("2026-01-02")
    decision = cc.session_close("2026-01-02") - 78 * cc.MINUTE
    result = choice({LOWER: s.prefix[:49], HIGHER: s.prefix[:50]}, decision, s.opening)
    assert result["required_bars"] == 50 and result["peer_code"] == HIGHER


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "out_of_range", "boolean", "identity"])
def test_original_rank_or_identity_corruption_refused(mutation):
    ranks = dict(RANKS)
    if mutation == "duplicate":
        ranks[LOWER] = 11
    elif mutation == "missing":
        del ranks[EVENT]
    elif mutation == "out_of_range":
        ranks[LOWER] = 21
    elif mutation == "boolean":
        ranks["KRX:000001"] = True
    else:
        ranks["not-an-original-code"] = ranks.pop(LOWER)
    with pytest.raises(dz.OutcomeRefusal):
        choice(ranks=ranks)
    with pytest.raises(dz.OutcomeRefusal, match="identity_outside_original_cb"):
        choice({"KRX:999999": session().prefix})


def test_no_peer_is_unknown_and_event_only_outcome_survives():
    selection = choice({})
    assert selection["status"] == "unknown" and selection["peer_code"] is None
    matched = dz.match_outcomes(label(), selection)
    assert matched["event_observed"] and not matched["pair_complete"]
    assert matched["event_only_price_change"] == Fraction(6, 107)
    assert matched["paired_difference"] is None
    assert matched["pair_unknown_reasons"] == ["no_complete_causal_peer"]


def test_future_outcomes_cannot_change_preselected_peer_or_replace_missing_peer():
    s = session()
    before = choice({LOWER: s.prefix, HIGHER: s.prefix})
    s.minutes[OPEN + 130 * cc.MINUTE] = None
    after = choice({LOWER: s.prefix, HIGHER: s.prefix})
    assert before == after and after["peer_code"] == LOWER
    missing_peer = label(s, code=LOWER)
    matched = dz.match_outcomes(label(), before, missing_peer)
    assert matched["event_observed"] and not matched["pair_complete"]
    assert matched["peer_selection"]["peer_code"] == LOWER
    assert matched["event_only_price_change"] == Fraction(6, 107)
    with pytest.raises(dz.OutcomeRefusal, match="peer_label_identity_changed"):
        dz.match_outcomes(label(), before, label(code=HIGHER))


def test_complete_pair_and_event_unknown_keep_separate_denominators():
    selected = choice()
    matched = dz.match_outcomes(label(), selected, label(code=LOWER))
    assert matched["pair_complete"] and matched["paired_difference"] == Fraction(0)
    unknown = dz.match_outcomes(label(session(missing=90)), selected, label(code=LOWER))
    assert not unknown["event_observed"] and not unknown["pair_complete"]
    assert unknown["event_only_price_change"] is None
    assert unknown["pair_unknown_reasons"] == ["event_label_unknown"]


def test_pair_clock_mismatch_refused():
    with pytest.raises(dz.OutcomeRefusal, match="paired_outcome_clocks"):
        dz.match_outcomes(label(), choice(), label(decision=DECISION + 5 * cc.MINUTE, code=LOWER))


def test_pair_cannot_accept_absent_clocks_or_changed_hold_length():
    missing_clock = label()
    del missing_clock["entry_at"]
    with pytest.raises(dz.OutcomeRefusal, match="timezone_aware_clock_required"):
        dz.match_outcomes(missing_clock, choice())
    short_label = label()
    short_label["exit_at"] -= 30 * cc.MINUTE
    with pytest.raises(dz.OutcomeRefusal, match="invalid_outcome_label_clocks"):
        dz.match_outcomes(short_label, choice())


@pytest.mark.parametrize("day,tax", [("2025-12-28", 28), ("2025-12-29", 33), ("2025-12-31", 33), (date(2026, 1, 2), 33)])
def test_exact_additive_costs_trade_date_boundary_and_uncertified_fee(day, tax):
    gross = Fraction(1, 100)
    result = dz.costs(gross, day)
    assert result["scenarios"]["primary"] == {"cost_bps": Fraction(33), "net": Fraction(67, 10000)}
    assert result["scenarios"]["tax_date_only"]["net"] == gross - Fraction(tax, 10000)
    assert result["scenarios"]["slippage_stress"]["net"] == Fraction(57, 10000)
    assert not result["actual_fee_certified"] and not result["secondary_can_qualify"]


def test_float_gross_datetime_and_noncanonical_trade_dates_refused():
    with pytest.raises(dz.OutcomeRefusal, match="exact_fraction"):
        dz.costs(0.01, "2025-12-29")
    for day in (DECISION, "20251229", "2025-13-01"):
        with pytest.raises(dz.OutcomeRefusal, match="invalid_trade_date"):
            dz.costs(Fraction(0), day)


@pytest.mark.parametrize("day", ["2026-09-01", "2026-01-02", "2025-11-13"])
def test_h5_signal_peer_and_label_share_the_original_session_clock(day):
    event = session(day)
    closing = cc.session_close(day)
    detection = signals.detect_bars(H5, event.prefix, opening=event.opening, closing=closing)
    assert detection.state == "signal"
    assert detection.decision_at == closing - 78 * cc.MINUTE
    selection = choice({LOWER: event.prefix}, detection.decision_at, event.opening)
    provenance = {"source": "invented", "data_vintage": "fixture-v1", "retrieved_at": None}
    event_label = dz.label60(event, detection.decision_at, hypothesis_id=H5,
                            symbol=EVENT, source_provenance=provenance)
    peer_label = dz.label60(event, detection.decision_at, hypothesis_id=H5,
                           symbol=LOWER, source_provenance=provenance)
    matched = dz.match_outcomes(event_label, selection, peer_label)
    assert matched["pair_complete"]
    assert matched["paired_difference"] == Fraction(0)
    assert matched["peer_selection"]["peer_code"] == LOWER
    assert event_label["entry_at"] == closing - 75 * cc.MINUTE
    assert event_label["exit_at"] == closing - 15 * cc.MINUTE
    assert len(event_label["minute_bucket_states"]) == 61
    assert event_label["source_provenance"] == provenance


def test_h5_future_peer_label_gap_preserves_signal_and_preselection():
    event = session()
    peer = session(missing=340)
    detection = signals.detect_bars(H5, event.prefix, opening=OPEN,
                                    closing=cc.session_close("2026-09-01"))
    selection = choice({LOWER: peer.prefix, HIGHER: event.prefix}, detection.decision_at)
    event_label = label(event, detection.decision_at, hypothesis_id=H5)
    peer_label = label(peer, detection.decision_at, code=LOWER, hypothesis_id=H5)
    matched = dz.match_outcomes(event_label, selection, peer_label)
    assert detection.state == "signal"
    assert selection["peer_code"] == LOWER
    assert matched["event_observed"] and not matched["pair_complete"]
    assert matched["event_only_price_change"] == Fraction(60, 1315)
    assert matched["pair_unknown_reasons"] == ["peer_label_unknown"]
