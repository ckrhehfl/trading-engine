"""Pure DZ labels and fixed peer choice over caller-supplied CC session objects.

No IO, detector replay, portfolio calculation, means or bootstrap. The caller
authenticates original dated membership, both policy identities and all seven
source metadata fields before using these functions. Retained optional source
provenance is not certification. Actual execution separately requires registered
independent reconstruction, review, profiling, CI, merge and durable trial logs.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
import re

from research import intraday_pattern_calibration as cc
from research.intraday_autonomous_round import HYPOTHESIS_IDS


HYPOTHESES = HYPOTHESIS_IDS
GAP_STATES = ("missing", "conflict", "invalid_price")


class OutcomeRefusal(ValueError):
    """Stable reason codes; never echo source values."""


def _aware(value: object) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise OutcomeRefusal("timezone_aware_clock_required")


def _session_clock(opening: datetime, decision: datetime) -> datetime:
    _aware(opening)
    _aware(decision)
    day = opening.astimezone(cc.KST).date().isoformat()
    closing = cc.session_close(day)
    if opening != cc.session_open(day) or not opening < decision < closing:
        raise OutcomeRefusal("invalid_modeled_session_clock")
    return closing


def _symbol(value: object) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"KRX:[0-9A-Z]{6}", value):
        raise OutcomeRefusal("invalid_original_cb_identity")


def _valid_prices(prices: object) -> bool:
    if not isinstance(prices, tuple) or len(prices) != 4:
        return False
    if not all(isinstance(value, Decimal) and value.is_finite() and value > 0
               for value in prices):
        return False
    op, hi, lo, cl = prices
    return lo <= min(op, cl) <= max(op, cl) <= hi


def label60(session: cc.Session, decision: datetime, *, hypothesis_id: str,
            symbol: str | None = None, source_provenance: Mapping | None = None) -> dict:
    """Strict next grid OPEN to +60m OPEN, requiring all 61 minute buckets.

The first five-minute OPEN is strictly after decision even on exact equality.
Ordinary detector decisions are bar.end+2m; the generic label clock also handles
non-grid decisions without silently moving an absent entry to a later price.
H5 must use its fixed decision/entry/exit clocks, including short CC sessions.
"""
    if not isinstance(session, cc.Session) or hypothesis_id not in HYPOTHESES:
        raise OutcomeRefusal("invalid_session_or_hypothesis")
    if symbol is not None:
        _symbol(symbol)
    if source_provenance is not None and not isinstance(source_provenance, Mapping):
        raise OutcomeRefusal("invalid_source_provenance")
    opening = session.opening
    closing = _session_clock(opening, decision)
    entry = opening + ((decision - opening) // cc.FIVE + 1) * cc.FIVE
    cap = entry + 60 * cc.MINUTE
    if hypothesis_id == HYPOTHESES[4]:
        if (decision != closing - 78 * cc.MINUTE
                or entry != closing - 75 * cc.MINUTE or cap != closing - 15 * cc.MINUTE):
            raise OutcomeRefusal("h5_fixed_clock_changed")
    elif decision > opening + 240 * cc.MINUTE:
        raise OutcomeRefusal("ordinary_decision_after_cutoff")
    if not opening < decision < entry <= cap < closing:
        raise OutcomeRefusal("entry_or_exit_outside_same_session")

    counts = Counter({state: 0 for state in GAP_STATES})
    states, prices = [], []
    for offset in range(61):
        minute = entry + offset * cc.MINUTE
        value = session.minutes.get(minute)
        reasons = [gap["state"] for gap in session.gaps
                   if gap["start"] <= minute < gap["end"]]
        if len(reasons) > 1 or any(reason not in GAP_STATES for reason in reasons):
            raise OutcomeRefusal("invalid_label_gap_provenance")
        valid = _valid_prices(value)
        if valid and reasons:
            raise OutcomeRefusal("price_and_gap_provenance_conflict")
        state = "valid" if valid else reasons[0] if reasons else (
            "missing" if value is None else "invalid_price")
        if state != "valid":
            counts[state] += 1
        states.append({"minute": minute, "state": state,
                       "gap_provenance_supplied": bool(reasons)})
        prices.append(value if valid else None)
    missing = sum(counts.values())
    label = {
        "hypothesis_id": hypothesis_id, "symbol": symbol,
        "status": "unknown" if missing else "known",
        "entry_at": entry, "exit_at": cap, "time_cap_decided_at": decision,
        "entry_open": prices[0][0] if prices[0] is not None else None,
        "exit_open": prices[-1][0] if prices[-1] is not None else None,
        "price_change": None,
        "required_minute_buckets": 61, "unknown_minute_buckets": missing,
        "unknown_bucket_reason_counts": dict(counts),
        "unknown_reasons": [f"label_{state}_minute_buckets" for state in GAP_STATES
                            if counts[state]],
        "minute_bucket_states": tuple(states),
        "entry_price_available_at": entry + 3 * cc.MINUTE,
        "label_available_at": cap + 3 * cc.MINUTE,
        "source_provenance": deepcopy(source_provenance),
        "original_timestamp_offsets_ms": tuple(session.source_offsets_ms),
        "source_provenance_certified": False,
        "evidence_level": "assumed", "actual_fill_certified": False,
    }
    if not missing:
        label["price_change"] = Fraction(prices[-1][0]) / Fraction(prices[0][0]) - 1
    return label


def _prefix_state(prefix: tuple[cc.core.Bar, ...] | None, opening: datetime,
                  decision: datetime, required: int) -> dict:
    result = {"status": "unknown", "required_bars": required, "valid_bars": 0,
              "reason": "causal_prefix_not_supplied"}
    if prefix is None:
        return result
    if not isinstance(prefix, tuple):
        raise OutcomeRefusal("causal_prefix_must_be_tuple")
    for index in range(required):
        if index >= len(prefix):
            result["reason"] = "incomplete_causal_prefix"
            return result
        bar = prefix[index]
        if not isinstance(bar, cc.core.Bar):
            raise OutcomeRefusal("invalid_causal_bar_type")
        _aware(bar.available_at)
        _aware(bar.start_at)
        _aware(bar.end_at)
        expected = opening + index * cc.FIVE
        if (bar.start_at != expected or bar.end_at != expected + cc.FIVE
                or bar.available_at != bar.end_at + 2 * cc.MINUTE):
            raise OutcomeRefusal("causal_bar_clock_or_contiguity_changed")
        if bar.available_at > decision:
            result["reason"] = "prefix_not_available_by_decision"
            return result  # Its prices, and all later bars, remain unread.
        if not _valid_prices((bar.open, bar.high, bar.low, bar.close)):
            raise OutcomeRefusal("invalid_causal_bar_prices")
        result["valid_bars"] += 1
    result.update(status="complete", reason=None,
                  last_available_at=opening + required * cc.FIVE + 2 * cc.MINUTE)
    return result


def choose_peer(*, event_code: str, original_cb_ranks: Mapping[str, int],
                causal_prefixes: Mapping[str, tuple[cc.core.Bar, ...]],
                opening: datetime, decision: datetime) -> dict:
    """Choose once from the exact 20 original dated CB members, before labels.

Only scheduled bars available by decision are inspected. Future tuple elements,
minute dictionaries and outcomes are not input to this choice. Missing prefix
keys retain their original members as unknown, never remove the denominator.
The caller must separately validate dated membership and both policy identities.
"""
    _session_clock(opening, decision)
    if not isinstance(original_cb_ranks, Mapping) or not isinstance(causal_prefixes, Mapping):
        raise OutcomeRefusal("invalid_peer_input_mapping")
    for code, rank in original_cb_ranks.items():
        _symbol(code)
        if type(rank) is not int or not 1 <= rank <= 20:
            raise OutcomeRefusal("invalid_original_cb_rank")
    if len(original_cb_ranks) != 20 or set(original_cb_ranks.values()) != set(range(1, 21)):
        raise OutcomeRefusal("original_cb_rank_population_changed")
    if event_code not in original_cb_ranks or not set(causal_prefixes) <= set(original_cb_ranks):
        raise OutcomeRefusal("peer_identity_outside_original_cb")
    required = int((decision - opening - 2 * cc.MINUTE) // cc.FIVE)
    if required < 1:
        raise OutcomeRefusal("no_completed_causal_bar_at_decision")
    states, candidates = {}, []
    for code, rank in sorted(original_cb_ranks.items(), key=lambda item: (item[1], item[0])):
        if code == event_code:
            states[code] = {"rank": rank, "status": "event_not_candidate"}
            continue
        state = _prefix_state(causal_prefixes.get(code), opening, decision, required)
        states[code] = {"rank": rank, **state}
        if state["status"] == "complete":
            candidates.append((abs(rank - original_cb_ranks[event_code]), rank, code))
    chosen = min(candidates)[2] if candidates else None
    return {
        "status": "selected" if chosen is not None else "unknown",
        "reason": None if chosen is not None else "no_complete_causal_peer",
        "event_code": event_code, "event_rank": original_cb_ranks[event_code],
        "peer_code": chosen, "peer_rank": original_cb_ranks[chosen] if chosen else None,
        "decision_at": decision, "required_bars": required, "candidate_states": states,
        "rule": "minimum_absolute_original_rank_distance_then_lower_rank_then_code",
        "future_labels_inspected": False, "replacement_permitted": False,
    }


def _known_label(label: Mapping) -> bool:
    if not isinstance(label, Mapping) or label.get("status") not in {"known", "unknown"}:
        raise OutcomeRefusal("invalid_outcome_label")
    for key in ("entry_at", "exit_at", "time_cap_decided_at"):
        _aware(label.get(key))
    if (label.get("hypothesis_id") not in HYPOTHESES
            or label["exit_at"] - label["entry_at"] != 60 * cc.MINUTE
            or label["time_cap_decided_at"] >= label["entry_at"]):
        raise OutcomeRefusal("invalid_outcome_label_clocks")
    known = label["status"] == "known"
    if (known and not isinstance(label.get("price_change"), Fraction)) or (
            not known and label.get("price_change") is not None):
        raise OutcomeRefusal("inconsistent_outcome_price_change")
    return known


def match_outcomes(event_label: Mapping, peer_selection: Mapping,
                   peer_label: Mapping | None = None) -> dict:
    """Retain event-only and complete-pair values without estimating any means."""
    event_known = _known_label(event_label)
    if (not isinstance(peer_selection, Mapping)
            or peer_selection.get("status") not in {"selected", "unknown"}
            or event_label.get("time_cap_decided_at") != peer_selection.get("decision_at")):
        raise OutcomeRefusal("selection_and_event_clock_mismatch")
    if event_label.get("symbol") is not None and event_label["symbol"] != peer_selection.get("event_code"):
        raise OutcomeRefusal("selection_and_event_identity_mismatch")
    chosen = peer_selection.get("peer_code")
    if (peer_selection["status"] == "selected") != (chosen is not None):
        raise OutcomeRefusal("inconsistent_peer_selection")
    peer_known = False
    if peer_label is not None:
        if chosen is None:
            raise OutcomeRefusal("peer_label_without_preselected_peer")
        peer_known = _known_label(peer_label)
        if peer_label.get("symbol") is not None and peer_label["symbol"] != chosen:
            raise OutcomeRefusal("peer_label_identity_changed")
        if any(event_label.get(key) != peer_label.get(key) for key in (
                "entry_at", "exit_at", "time_cap_decided_at", "hypothesis_id")):
            raise OutcomeRefusal("paired_outcome_clocks_or_hypothesis_changed")
    complete = event_known and peer_known
    return {
        "event_label": deepcopy(event_label), "peer_selection": deepcopy(peer_selection),
        "peer_label": deepcopy(peer_label),
        "event_observed": event_known, "pair_complete": complete,
        "event_only_price_change": event_label["price_change"] if event_known else None,
        "paired_event_price_change": event_label["price_change"] if complete else None,
        "paired_peer_price_change": peer_label["price_change"] if complete else None,
        "paired_difference": event_label["price_change"] - peer_label["price_change"] if complete else None,
        "pair_unknown_reasons": ([] if complete else
            (["event_label_unknown"] if not event_known else [])
            + (["no_complete_causal_peer"] if chosen is None else
               ["peer_label_not_supplied"] if peer_label is None else
               ["peer_label_unknown"] if not peer_known else [])),
    }


def costs(gross: Fraction, trade_date: date | str) -> dict:
    """Fixed additive price-return scenarios; never measured cash PnL/fees."""
    if not isinstance(gross, Fraction):
        raise OutcomeRefusal("exact_fraction_gross_required")
    if isinstance(trade_date, str):
        try:
            day = date.fromisoformat(trade_date)
        except ValueError as exc:
            raise OutcomeRefusal("invalid_trade_date") from exc
        if day.isoformat() != trade_date:
            raise OutcomeRefusal("invalid_trade_date")
    elif type(trade_date) is date:
        day = trade_date
    else:
        raise OutcomeRefusal("invalid_trade_date")
    bps = {"primary": Fraction(33),
           "tax_date_only": Fraction(28 if day < date(2025, 12, 29) else 33),
           "slippage_stress": Fraction(43)}
    return {
        "trade_date": day.isoformat(), "gross": gross,
        "scenarios": {name: {"cost_bps": cost, "net": gross - cost / 10000}
                      for name, cost in bps.items()},
        "basis": "fixed_additive_price_return_assumption_not_account_fee_or_fill_measurement",
        "actual_fee_certified": False, "actual_fill_certified": False,
        "historical_tax_certified": False, "secondary_can_qualify": False,
    }
