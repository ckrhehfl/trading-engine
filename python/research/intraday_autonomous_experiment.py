"""Pure streamed producer for the five fixed DZ hypotheses, one per invocation.

The caller supplies original CB membership and CC chunks, authenticates their
pins, and owns the shared execution budget. No path, database, network, trial
logging or previous detector is invoked here. Only one day of Session objects
is retained; the report keeps compact observations and explicit unknowns.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, timedelta
from fractions import Fraction
import hashlib
from statistics import NormalDist

from research import intraday_autonomous_outcomes as outcomes
from research import intraday_autonomous_round as registry
from research import intraday_autonomous_signals as signals
from research import intraday_pattern_calibration as cc


SPECIFICATION_ID = registry.SPECIFICATION_ID
MODE = "fixed-intraday-autonomous-observed-comparison"
POPULATIONS = {
    "event_observed": "event_only_price_change",
    "paired_event": "paired_event_price_change",
    "paired_peer": "paired_peer_price_change",
    "paired_difference": "paired_difference",
}
SCENARIOS = ("primary", "tax_date_only", "slippage_stress")
FLAGS = {
    **cc.FLAGS,
    "actual_fee_certified": False,
    "actual_fill_certified": False,
    "historical_tax_certified": False,
    "actual_portfolio_pnl": False,
    "family_adopted": False,
    "strategy_pass": False,
    "reserved_access_allowed": False,
    "exploratory_only": True,
    "observed_only_comparison": True,
    "underpowered_descriptive_authorized": True,
}
SOURCE_FIELDS = ("observation_date", "available_at", "retrieved_at", "source",
                 "is_final", "evidence_level")
GAP_STATES = ("missing", "conflict", "invalid_price")


class ExperimentRefusal(ValueError):
    """Stable codes do not expose caller data."""


def _week(day: str) -> str:
    value = date.fromisoformat(day)
    return (value - timedelta(days=value.weekday())).isoformat()


def _read_chunk(chunk: Mapping, day: str, symbol: str) -> tuple[cc.Session, dict]:
    if (not isinstance(chunk, Mapping) or chunk.get("symbol") != symbol
            or chunk.get("session") != day):
        raise ExperimentRefusal("input_chunk_order_or_identity_mismatch")
    if (set(chunk) != {"symbol", "session", *SOURCE_FIELDS, "rows"}
            or chunk["observation_date"] != day or chunk["available_at"] is not None
            or chunk["source"] != "collector klines" or chunk["is_final"] is not None
            or chunk["evidence_level"] != "assumed"):
        raise ExperimentRefusal("input_chunk_metadata_mismatch")
    try:
        retrieved = datetime.fromisoformat(chunk["retrieved_at"])
        if retrieved.utcoffset() is None:
            raise ValueError
    except (TypeError, ValueError):
        raise ExperimentRefusal("invalid_input_retrieval_clock") from None
    raw = chunk["rows"]
    if not isinstance(raw, list) or len(raw) > cc.RESOURCES["maximum_session_rows"]:
        raise ExperimentRefusal("input_session_row_budget_exceeded")
    for row in raw:
        if (not isinstance(row, Mapping) or set(row) != set(cc.COLUMNS)
                or row["symbol"] != symbol
                or row["fetched_at"] is not None and not isinstance(row["fetched_at"], str)):
            raise ExperimentRefusal("input_raw_columns_identity_or_retrieval_mismatch")
    session = cc.aggregate_session(raw, cc.session_open(day))
    gap_counts = {state: sum(int((gap["end"] - gap["start"]) / cc.MINUTE)
        for gap in session.gaps if gap["state"] == state) for state in GAP_STATES}
    source = {
        "session": day, "symbol": symbol,
        "data_vintage": hashlib.sha256(cc._canonical(chunk) + b"\n").hexdigest(),
        "raw_chunk_metadata": {key: deepcopy(chunk[key]) for key in SOURCE_FIELDS},
        "input_rows": len(raw),
        "source_fetched_at": sorted({row["fetched_at"] for row in raw if row["fetched_at"] is not None}),
        "source_offset_counts_ms": dict(Counter(session.source_offsets_ms)),
        "gap_minute_counts": gap_counts,
    }
    return session, source


def _compact_label(label: Mapping) -> dict:
    omitted = {"hypothesis_id", "symbol", "source_provenance",
               "original_timestamp_offsets_ms", "minute_bucket_states"}
    return {**{key: value for key, value in label.items() if key not in omitted},
        "unknown_minute_buckets_detail": [item for item in label["minute_bucket_states"]
                                          if item["state"] != "valid"]}


def _compact_peer(selection: Mapping) -> dict:
    states = selection["candidate_states"]
    counts = {state: sum(item["status"] == state for item in states.values())
              for state in ("complete", "unknown", "event_not_candidate")}
    return {**{key: value for key, value in selection.items() if key != "candidate_states"},
        "candidate_counts": counts,
        "unknown_candidates": [{"symbol": symbol, **item}
            for symbol, item in states.items() if item["status"] == "unknown"]}


def _counts(rows: list[dict]) -> dict:
    state = Counter(row["signal"]["state"] for row in rows)
    events = [row for row in rows if row["pair"] is not None]
    known = sum(row["pair"]["event_observed"] for row in events)
    paired = sum(row["pair"]["pair_complete"] for row in events)
    return {"targets": len(rows), "signals": state["signal"],
        "known_no_signal": state["known_no_signal"], "unknown_signals": state["unknown"],
        "event_known": known, "event_unknown": len(events) - known,
        "pair_complete": paired, "pair_unknown": len(events) - paired}


def _records(rows: list[dict], field: str) -> list[dict]:
    return [{"session": row["session"], "label": row["pair"][field]}
            for row in rows if row["pair"] is not None]


def _own_sizing(records: list[dict], reference: list[str]) -> dict:
    values = [row["label"] for row in records if row["label"] is not None]
    exact_constant = len(values) >= 2 and len(set(values)) == 1
    own = cc.dispersion(records, reference)
    se, mde = own["design_se"], own["mde_bps"]
    zero_dispersion = exact_constant or se == 0 or own["event_sd"] == 0
    defined = se is not None and se > 0 and mde is not None and not zero_dispersion
    own.update(
        sizing_pass=defined and mde <= 33,
        status="uninformative" if not defined else "underpowered" if mde > 33 else "defined",
        uninformative_reason=("zero_dispersion_no_uncertainty_estimate" if zero_dispersion
            else own["planning_constraint"] if not defined else None),
        underpowered_descriptive_authorized=True,
        requires_operator_choice=False,
    )
    return own


def _estimate(records: list[dict], plan: Mapping) -> dict:
    values = [row["label"] for row in records if row["label"] is not None]
    exact = sum(values, Fraction()) / len(values) if values else None
    mean = float(exact) if exact is not None else None
    se = plan["design_se"]
    interval = None
    if (mean is not None and se is not None and se > 0
            and plan["event_sd"] != 0 and len(set(values)) > 1):
        radius = NormalDist().inv_cdf(.975) * se
        interval = [mean - radius, mean + radius]
    return {"mean_exact": exact, "mean": mean, "design_se": se,
        "corrected_to_naive_se_ratio": plan["corrected_to_naive_se_ratio"],
        "interval95": interval}


def _statistics(records: list[dict], own: Mapping, reference: list[str],
                *, difference: bool, checkpoint: Callable[[], None]) -> dict:
    gross = _estimate(records, own)
    net = {}
    for scenario in SCENARIOS:
        checkpoint()
        adjusted = [{"session": row["session"], "label": (
            row["label"] if difference or row["label"] is None else
            outcomes.costs(row["label"], row["session"])["scenarios"][scenario]["net"])}
            for row in records]
        plan = cc.dispersion(adjusted, reference) if scenario == "tax_date_only" and not difference else own
        net[scenario] = _estimate(adjusted, plan)
    primary = net["primary"]
    interval995 = None
    if primary["interval95"] is not None:
        radius = NormalDist().inv_cdf(.9975) * primary["design_se"]
        interval995 = [primary["mean"] - radius, primary["mean"] + radius]
    return {"known_labels": own["known_labels"], "unknown_labels": own["unknown_labels"],
        "status": own["status"], "gross": gross, "net": net,
        "primary_net_interval995": interval995}


def _contribution(rows: list[dict], total: Mapping) -> dict:
    counts = _counts(rows)
    event = [row for row in rows if row["pair"] is not None and row["pair"]["event_observed"]]
    paired = [row for row in rows if row["pair"] is not None and row["pair"]["pair_complete"]]
    return {"counts": counts,
        "event_gross_sum": sum((row["pair"]["event_only_price_change"] for row in event), Fraction()),
        "event_net33_sum": sum((outcomes.costs(row["pair"]["event_only_price_change"],
            row["session"])["scenarios"]["primary"]["net"] for row in event), Fraction()),
        "pair_difference_sum": sum((row["pair"]["paired_difference"] for row in paired), Fraction()),
        "event_exposure_share": counts["event_known"] / total["event_known"] if total["event_known"] else None,
        "pair_exposure_share": counts["pair_complete"] / total["pair_complete"] if total["pair_complete"] else None}


def _summarize(variant: dict, reference: list[str], weeks: list[str],
               checkpoint: Callable[[], None]) -> None:
    rows = variant["days"]
    counts = _counts(rows)
    by_week = {week: _counts([row for row in rows if _week(row["session"]) == week]) for week in weeks}
    variant.update(counts=counts, by_week=by_week, frequency={
        "reference_weeks": len(weeks),
        "known_opportunities_per_week": counts["event_known"] / len(weeks),
        "signals_per_week": counts["signals"] / len(weeks),
        "possible_opportunities_per_week_upper_bound": (
            counts["event_known"] + counts["event_unknown"] + counts["unknown_signals"]) / len(weeks),
        "weekly_objective": 15, "objective_met": counts["event_known"] >= 15 * len(weeks),
        "zero_known_event_weeks": sum(item["event_known"] == 0 for item in by_week.values()),
    })
    start, end = date.fromisoformat(cc.WINDOW["start"]), date.fromisoformat(cc.WINDOW["end"])
    midpoint = start + (end - start) // 2
    variant["calendar_halves"] = {
        name: {"start": first.isoformat(), "end": last.isoformat(),
            **_contribution([row for row in rows if first.isoformat() <= row["session"] <= last.isoformat()], counts)}
        for name, first, last in (("first", start, midpoint), ("second", midpoint + timedelta(days=1), end))
    }
    variant["by_symbol"] = {symbol: _contribution([row for row in rows if row["symbol"] == symbol], counts)
                            for symbol in sorted({row["symbol"] for row in rows})}
    variant["statistics"] = {name: _statistics(_records(rows, field), variant["own_sizing"][name],
        reference, difference=name == "paired_difference", checkpoint=checkpoint)
        for name, field in POPULATIONS.items()}
    event = variant["statistics"]["event_observed"]
    paired = variant["statistics"]["paired_difference"]["gross"]
    lower = event["primary_net_interval995"]
    tests = {
        "required_own_sizing_pass": all(plan["sizing_pass"] for plan in variant["own_sizing"].values()),
        "event_net33_positive": event["net"]["primary"]["mean"] is not None and event["net"]["primary"]["mean"] > 0,
        "paired_mean_positive": paired["mean"] is not None and paired["mean"] > 0,
        "event_net33_lower995_positive": lower is not None and lower[0] > 0,
        "weekly_frequency_at_least15": variant["frequency"]["objective_met"],
    }
    variant["followup_marker"] = {**tests, "conditional_followup": all(tests.values()), "strategy_pass": False}


def build_experiment(upstream: Mapping, chunks: Iterable[Mapping], *, hypothesis_id: str,
                     checkpoint: Callable[[], None] | None = None) -> dict:
    """Consume all original CC-order chunks without retaining raw input days.

    Small invented original-like top20 grids are supported. Actual hash/count,
    resource, registration and publication checks remain caller obligations.
    The four conditional own-label plans for BOTH policies precede any means.
    """
    if hypothesis_id not in registry.HYPOTHESIS_IDS:
        raise ExperimentRefusal("unregistered_hypothesis")
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    if not isinstance(upstream, Mapping) or upstream.get("mode") != "discovery-universe-source-and-coverage-only":
        raise ExperimentRefusal("wrong_upstream_mode")
    matrix, union = cc._target_matrix(upstream)
    if any(type(target["rank"]) is not int for variant in matrix.values()
           for day in variant["days"] for target in day["targets"]):
        raise ExperimentRefusal("invalid_original_cb_rank_type")
    reference = list(union)
    weeks = sorted({_week(day) for day in reference})
    if isinstance(chunks, Mapping):
        raise ExperimentRefusal("ordered_chunk_iterable_required")
    iterator = iter(chunks)
    result = {
        "study_id": SPECIFICATION_ID, "mode": MODE, "hypothesis_id": hypothesis_id,
        "reference_sessions": reference, "reference_weeks": weeks,
        "upstream_calendar": deepcopy(upstream["calendar"]),
        "source_target_matrix": deepcopy(upstream["variants"]),
        "sources": [], "variants": {policy: {"days": []} for policy in ("D+1", "D+2")},
        **FLAGS,
    }
    row_count = 0
    for day_index, day in enumerate(reference):
        checkpoint()
        sessions, source_indices = {}, {}
        for symbol in sorted(union[day]):
            checkpoint()
            try:
                chunk = next(iterator)
            except StopIteration:
                raise ExperimentRefusal("missing_input_chunk") from None
            session, source = _read_chunk(chunk, day, symbol)
            row_count += source["input_rows"]
            if row_count > cc.RESOURCES["maximum_rows"]:
                raise ExperimentRefusal("input_population_budget_exceeded")
            source_indices[symbol] = len(result["sources"])
            result["sources"].append(source)
            sessions[symbol] = session
        detections = {symbol: signals.detect_bars(hypothesis_id, session.prefix,
            opening=session.opening, closing=cc.session_close(day)) for symbol, session in sessions.items()}
        for policy in ("D+1", "D+2"):
            membership = matrix[policy]["days"][day_index]
            ranks = {target["symbol"]: target["rank"] for target in membership["targets"]}
            prefixes = {symbol: sessions[symbol].prefix for symbol in ranks}
            for target in membership["targets"]:
                checkpoint()
                symbol, detection = target["symbol"], detections[target["symbol"]]
                row = {"session": day, "symbol": symbol, "source_index": source_indices[symbol],
                    "membership_day_index": day_index, "membership_target": deepcopy(target),
                    "signal": asdict(detection), "peer_source_index": None,
                    "peer_selection": None, "event_label": None, "peer_label": None, "pair": None}
                if detection.state == "signal":
                    # Selection receives prefixes only and precedes BOTH label calls.
                    selection = outcomes.choose_peer(event_code=symbol, original_cb_ranks=ranks,
                        causal_prefixes=prefixes, opening=sessions[symbol].opening, decision=detection.decision_at)
                    event_label = outcomes.label60(sessions[symbol], detection.decision_at,
                        hypothesis_id=hypothesis_id, symbol=symbol)
                    peer_code = selection["peer_code"]
                    peer_label = None if peer_code is None else outcomes.label60(sessions[peer_code],
                        detection.decision_at, hypothesis_id=hypothesis_id, symbol=peer_code)
                    pair = outcomes.match_outcomes(event_label, selection, peer_label)
                    row.update(peer_source_index=source_indices[peer_code] if peer_code else None,
                        peer_selection=_compact_peer(selection), event_label=_compact_label(event_label),
                        peer_label=_compact_label(peer_label) if peer_label else None,
                        pair={key: value for key, value in pair.items()
                              if key not in {"event_label", "peer_selection", "peer_label"}})
                result["variants"][policy]["days"].append(row)
        # No Session or raw-row reference escapes this iteration.
        del sessions, detections, prefixes, session, chunk
    try:
        next(iterator)
    except StopIteration:
        pass
    else:
        raise ExperimentRefusal("extra_input_chunk")
    result["input_evidence"] = {
        "unique_target_days": len(result["sources"]), "queried_rows": row_count,
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder",
    }
    for variant in result["variants"].values():
        variant["own_sizing"] = {}
        for name, field in POPULATIONS.items():
            checkpoint()
            variant["own_sizing"][name] = _own_sizing(_records(variant["days"], field), reference)
    for variant in result["variants"].values():
        _summarize(variant, reference, weeks, checkpoint)
    marker = all(variant["followup_marker"]["conditional_followup"] for variant in result["variants"].values())
    result["followup_marker"] = {"both_policies": marker, "strategy_pass": False,
        "reserved_access_allowed": False, "requires_separate_validation_plan": marker}
    checkpoint()
    return result
