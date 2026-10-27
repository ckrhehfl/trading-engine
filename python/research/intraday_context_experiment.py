"""EB pure day-streamed comparison of ONE frozen EA setup and two policies.

The caller authenticates CB/CC/CX artifacts, registration and execution budget.
This module opens no paths and supplies no runner. Assumed availability, vendor
price/share comparability and fills remain assumptions, even for known labels.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
from statistics import NormalDist

from research import intraday_activity_readiness as cx
from research import intraday_context_outcomes as outcomes
from research import intraday_context_preparation as ea
from research import intraday_pattern_calibration as cc
from research import intraday_autonomous_signals as signals


MODE = "fixed-ea-context-exploratory-comparison"
PRICE_BASIS = "ASSUMPTION: saved vendor KRW prices; corporate-action comparability uncertified"
SHARE_BASIS = "ASSUMPTION: saved vendor share counts; corporate-action comparability uncertified"
FLAGS = {**cc.FLAGS, "exploratory_only": True,
         "underpowered_descriptive_authorized": True, "observed_only_comparison": True,
         "actual_portfolio_pnl": False, "actual_fill_certified": False,
         "historical_volume_availability_certified": False,
         "price_share_basis_certified": False, "strategy_pass": False,
         "followup_pass": False, "reserved_access_allowed": False}
SOURCE_FIELDS = ("observation_date", "available_at", "retrieved_at", "source",
                 "is_final", "evidence_level")


class ExperimentRefusal(ValueError):
    """Invalid contracts fail closed; unavailable observations stay unknown."""


def _require(value: bool, reason: str) -> None:
    if not value:
        raise ExperimentRefusal(reason)


def _instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
        _require(parsed.utcoffset() is not None, "aware_source_clock_required")
        return parsed
    except (TypeError, ValueError):
        raise ExperimentRefusal("invalid_source_clock") from None


def _week(day: str) -> str:
    parsed = date.fromisoformat(day)
    return (parsed - timedelta(days=parsed.weekday())).isoformat()


def _source(chunk: Mapping) -> dict:
    """Bind exact row-order timestamps/retrievals to their original raw chunk.

    Original immutable caller-pinned archives retain the actual metadata rows.
    The ordered digest preserves offsets, duplicates and retrieval changes;
    their values are not redundantly copied into every derived report.
    """
    linkage = [[row["open_time_ms"], row["fetched_at"]] for row in chunk["rows"]]
    return {"session": chunk["session"], "symbol": chunk["symbol"],
            "data_vintage": hashlib.sha256(cc._canonical(chunk) + b"\n").hexdigest(),
            "raw_chunk_metadata": {key: deepcopy(chunk[key]) for key in SOURCE_FIELDS},
            "input_rows": len(linkage),
            "row_linkage_sha256": hashlib.sha256(cc._canonical(linkage)).hexdigest(),
            "source_row_metadata_location": "original_pinned_archive_chunk"}


def _read_price(chunk: Mapping, day: str, symbol: str) -> tuple[cc.Session, dict]:
    _require(isinstance(chunk, Mapping) and set(chunk) == {*SOURCE_FIELDS, "symbol", "session", "rows"}
             and chunk["symbol"] == symbol and chunk["session"] == day
             and chunk["observation_date"] == day and chunk["available_at"] is None
             and chunk["source"] == "collector klines" and chunk["is_final"] is None
             and chunk["evidence_level"] == "assumed", "price_chunk_identity_or_metadata_changed")
    _instant(chunk["retrieved_at"])
    _require(isinstance(chunk["rows"], list) and len(chunk["rows"]) <= 600, "price_row_budget_exceeded")
    for row in chunk["rows"]:
        _require(isinstance(row, Mapping) and set(row) == set(cc.COLUMNS)
                 and row["symbol"] == symbol and (row["fetched_at"] is None
                 or isinstance(row["fetched_at"], str)), "invalid_price_row")
    return cc.aggregate_session(chunk["rows"], cc.session_open(day)), _source(chunk)


def _price_source(source: Mapping) -> ea.PriceSource:
    meta = source["raw_chunk_metadata"]
    return ea.PriceSource(_instant(meta["retrieved_at"]), meta["source"], meta["is_final"],
                          source["data_vintage"], "assumed")


def _opening_prices(rows: list[Mapping]) -> dict[datetime, Decimal | None]:
    """A unique raw OPEN survives invalid later HLC; duplicate buckets do not."""
    result = {}
    for row in rows:
        at = datetime.fromtimestamp(row["open_time_ms"] // 60_000 * 60, cc.KST)
        if at in result:
            result[at] = None
            continue
        value = None
        try:
            if isinstance(row["open"], str):
                parsed = Decimal(row["open"])
                if parsed.is_finite() and parsed > 0:
                    value = parsed
        except InvalidOperation:
            pass
        result[at] = value
    return result


def _provenance(source: Mapping, end: datetime) -> ea.Provenance:
    meta = _price_source(source)
    return ea.Provenance(end, end + 2 * cc.MINUTE, meta.retrieved_at,
                         meta.source, meta.is_final, meta.data_vintage, "assumed")


def _volume(chunk: Mapping, day: str, symbol: str) -> tuple[ea.VolumeWindow | None, dict]:
    rows = cx._validated(chunk, day, symbol, cx.COLUMNS)
    source = _source(chunk)
    # The daily capture is retained as metadata only; it never fills minutes.
    source["daily_retrieved_at"] = chunk["daily_retrieved_at"]
    opening = cc.session_open(day)
    buckets = {}
    for row in rows:
        buckets.setdefault(row["open_time_ms"] // 60_000 * 60_000, []).append(row)
    minutes, states = [], Counter()
    for index in range(60):
        at = opening + index * cc.MINUTE
        found = buckets.get(int(at.timestamp()) * 1000, [])
        state = ("missing" if not found else "conflict" if len(found) != 1
                 else cx.number_state(found[0]["volume"]))
        states[state] += 1
        if state in {"zero", "nonzero"}:
            minutes.append(ea.VolumeMinute(at, Decimal(found[0]["volume"]),
                                           _provenance(source, at + cc.MINUTE)))
    source["first60_states"] = dict(states)
    if len(minutes) != 60:
        return None, source
    return ea.VolumeWindow(symbol, date.fromisoformat(day), opening, tuple(minutes), SHARE_BASIS), source


def _universe(membership: Mapping, day: str) -> ea.MonthlyUniverse:
    observed = (datetime.combine(date.fromisoformat(membership["observation_date"]),
                                 datetime.min.time(), cc.KST)
                + timedelta(days=1) - timedelta(microseconds=1))
    retrieved = max(_instant(value) for value in membership["retrieved_at"].values())
    meta = ea.Provenance(observed, _instant(membership["available_at"]), retrieved,
                         membership["source"], membership["is_final"],
                         hashlib.sha256(cc._canonical(membership)).hexdigest(), "assumed")
    return ea.MonthlyUniverse(date.fromisoformat(day).replace(day=1),
        tuple(target["symbol"] for target in membership["targets"]),
        _instant(membership["selected_at"]), meta)


def _window(session: cc.Session, source: Mapping, symbol: str,
            start: datetime, end: datetime) -> ea.PriceWindow | None:
    values = [session.minutes.get(start + n * cc.MINUTE)
              for n in range(int((end - start) / cc.MINUTE))]
    if not values or any(value is None for value in values):
        return None
    return ea.PriceWindow(symbol, start.astimezone(cc.KST).date(), start, end,
                          values[0][0], values[-1][3], _provenance(source, end), PRICE_BASIS)


def _own_sizing(records: list[dict], reference: list[str]) -> dict:
    plan = cc.dispersion(records, reference)
    values = [row["label"] for row in records if row["label"] is not None]
    se, mde = plan["design_se"], plan["mde_bps"]
    constant = len(values) > 1 and len(set(values)) == 1
    defined = se is not None and se > 0 and mde is not None and not constant
    plan.update(status="uninformative" if not defined else "underpowered" if mde > 33 else "defined",
                sizing_pass=bool(defined and mde <= 33),
                underpowered_descriptive_authorized=True, requires_operator_choice=False,
                uninformative_reason="zero_dispersion" if constant else plan["planning_constraint"] if not defined else None)
    return plan


def _statistics(records: list[dict], plan: Mapping) -> dict:
    values = [row["label"] for row in records if row["label"] is not None]
    mean = sum(values, Fraction()) / len(values) if values else None
    result = {"known_labels": len(values), "status": plan["status"],
              "descriptive_interval_method": "normal95_max_naive_session_cluster_five_session_block_se",
              "session_cluster_to_naive_se_ratio": (plan["session_cluster_se"] / plan["naive_se"]
                  if plan["naive_se"] and plan["session_cluster_se"] is not None else None),
              "corrected_to_naive_se_ratio": plan["corrected_to_naive_se_ratio"]}
    for name, cost in (("gross", 0), ("net33", 33), ("net43", 43)):
        exact = mean - Fraction(cost, 10000) if mean is not None else None
        radius = (NormalDist().inv_cdf(.975) * plan["design_se"]
                  if plan["status"] != "uninformative" else None)
        result[name] = {"mean_exact": exact,
                        "positive_proportion_exact": Fraction(sum(v > Fraction(cost, 10000) for v in values), len(values)) if values else None,
                        "interval95": [float(exact) - radius, float(exact) + radius]
                        if exact is not None and radius is not None else None}
    return result


def _records(rows: list[dict], arm: str) -> list[dict]:
    return [{"session": row["session"], "label": row["outcome"]["gross"]}
            for row in rows if row["outcome"] is not None
            and (arm == "basic_all" and row["outcome"]["status"] != "declined"
                 or arm != "basic_all" and row["common_context_observable"]
                 and row["outcome"]["status"] != "declined" and row["arms"][arm] == "selected")]


def _counts(rows: list[dict]) -> dict:
    signals_count = Counter(row["signal"]["state"] for row in rows)
    labels = Counter(row["outcome"]["status"] for row in rows if row["outcome"] is not None)
    return {"targets": len(rows), "signals": signals_count["signal"],
            "known_no_signal": signals_count["known_no_signal"], "unknown_signals": signals_count["unknown"],
            "context_unknown": sum(row["signal"]["state"] == "signal" and not row["common_context_observable"] for row in rows),
            "known_labels": labels["known"], "unknown_labels": labels["unknown"],
            "entry_declined": labels["declined"],
            "common_feature_candidates": sum(row["common_context_observable"] for row in rows),
            "common_feature_and_price_candidates": sum(row["common_price_observable"] for row in rows)}


def _arm_counts(rows: list[dict], arm: str) -> dict:
    candidates = [row for row in rows if row["signal"]["state"] == "signal"]
    selected = [row for row in candidates if arm == "basic_all" or row["arms"][arm] == "selected"]
    states = Counter(row["outcome"]["status"] for row in selected)
    return {"selected_predecline": len(selected), "selected_known": states["known"],
            "selected_unknown_price": states["unknown"], "selected_entry_declined": states["declined"],
            "context_unknown": sum(row["arms"][arm] == "unknown" for row in candidates) if arm != "basic_all" else 0,
            "context_rejected": sum(row["arms"][arm] == "rejected" for row in candidates) if arm != "basic_all" else 0,
            "comparison_known": sum(row["label"] is not None for row in _records(rows, arm))}


def _contributions(rows: list[dict], arms: tuple[str, ...]) -> dict:
    return {"counts": _counts(rows), "arms": {arm: {
        "counts": _arm_counts(rows, arm),
        "gross_sum_exact": sum((item["label"] for item in _records(rows, arm)
                                if item["label"] is not None), Fraction()),
        "net33_sum_exact": sum((item["label"] - Fraction(33, 10000) for item in _records(rows, arm)
                                if item["label"] is not None), Fraction())} for arm in arms}}


def _summarize(variant: dict, reference: list[str], weeks: list[str], arms: tuple[str, ...]) -> None:
    rows = variant["days"]
    variant["counts"] = _counts(rows)
    variant["arm_counts"] = {arm: _arm_counts(rows, arm) for arm in arms}
    variant["by_week"] = {week: _contributions([r for r in rows if _week(r["session"]) == week], arms) for week in weeks}
    variant["frequency"] = {arm: {"reference_weeks": len(weeks), "weekly_objective": 15,
        "comparison_known_per_week": Fraction(variant["arm_counts"][arm]["comparison_known"], len(weeks)),
        "selected_known_per_week": Fraction(variant["arm_counts"][arm]["selected_known"], len(weeks)),
        "objective_met_observed": variant["arm_counts"][arm]["selected_known"] >= 15 * len(weeks),
        "objective_met_primary_comparison": variant["arm_counts"][arm]["comparison_known"] >= 15 * len(weeks),
        "zero_selected_known_weeks": sum(v["arms"][arm]["counts"]["selected_known"] == 0 for v in variant["by_week"].values()),
        "possible_weekly_upper_bound": Fraction(variant["arm_counts"][arm]["selected_known"]
            + variant["arm_counts"][arm]["selected_unknown_price"] + variant["arm_counts"][arm]["context_unknown"]
            + variant["counts"]["unknown_signals"], len(weeks)),
        "upper_bound_is_loose_not_imputed_trades": True, "forced_trades": False} for arm in arms}
    first, last = (date.fromisoformat(cc.WINDOW[key]) for key in ("start", "end"))
    midpoint = first + (last - first) // 2
    variant["calendar_halves"] = {name: {"start": start.isoformat(), "end": end.isoformat(),
        **_contributions([r for r in rows if start.isoformat() <= r["session"] <= end.isoformat()], arms)}
        for name, start, end in (("first", first, midpoint), ("second", midpoint + timedelta(days=1), last))}
    variant["by_symbol"] = {symbol: _contributions([r for r in rows if r["symbol"] == symbol], arms)
                            for symbol in sorted({r["symbol"] for r in rows})}
    variant["statistics"] = {arm: _statistics(_records(rows, arm), variant["own_sizing"][arm]) for arm in arms}
    common = variant["statistics"]["basic"]["gross"]["mean_exact"]
    variant["selected_minus_common"] = {arm: {
        "mean_delta_exact": variant["statistics"][arm]["gross"]["mean_exact"] - common
        if common is not None and variant["statistics"][arm]["gross"]["mean_exact"] is not None else None,
        "contrast_interval": None, "p_value": None,
        "uncertainty_not_estimated_for_overlapping_groups": True}
        for arm in arms if arm not in {"basic", "basic_all"}}


def build_experiment(upstream: Mapping, price_chunks: Iterable[Mapping], volume_chunks: Iterable[Mapping] | None = None,
                     *, setup_id: str, inputs_authenticated: bool, authorization_reference: str,
                     checkpoint: Callable[[], None] | None = None) -> dict:
    """Consume original day/symbol order once, retaining at most prior20 volumes.

    Synthetic small calendars with original 20-member daily geometry are valid.
    Caller checks actual pinned population counts and hashes. BOTH policies'
    own-label plans precede ALL published means, including basic_all diagnostic.
    All primary arms share feature-observable AND price-observable candidates.
    """
    _require(setup_id in ea.SETUPS, "unregistered_setup")
    _require(inputs_authenticated is True, "caller_authenticated_inputs_required")
    _require(isinstance(authorization_reference, str) and bool(authorization_reference.strip()), "authorization_reference_required")
    _require(isinstance(upstream, Mapping) and upstream.get("mode") == "discovery-universe-source-and-coverage-only", "wrong_upstream_mode")
    _require(not isinstance(price_chunks, Mapping) and not isinstance(volume_chunks, Mapping), "ordered_chunk_iterables_required")
    _require(setup_id != "EA-A" or volume_chunks is not None, "ea_a_volume_archive_required")
    _require(setup_id != "EA-B" or volume_chunks is None, "ea_b_does_not_consume_volume")
    matrix, union = cc._target_matrix(upstream)
    _require(all(type(target["rank"]) is int for variant in matrix.values() for day in variant["days"]
                 for target in day["targets"]), "invalid_original_rank_type")
    calendar = [row["date"] for row in upstream["calendar"]["rows"]]
    _require(calendar == sorted(set(calendar)), "invalid_full_calendar")
    reference, weeks = list(union), sorted({_week(day) for day in union})
    check = checkpoint or (lambda: None)
    arms = ("basic", "volume_only", "market_relative_only", "both", "basic_all") if setup_id == "EA-A" else ("basic", "market_context", "basic_all")
    report = {"mode": MODE, "setup_id": setup_id, **FLAGS, "authorization_reference": authorization_reference,
        "preparation_spec_sha256": ea.REGISTERED_CANONICAL_SHA256,
        "reference_sessions": reference, "reference_weeks": weeks,
        "source_target_matrix": deepcopy(upstream["variants"]), "upstream_calendar": deepcopy(upstream["calendar"]),
        "price_basis": PRICE_BASIS, "share_basis": SHARE_BASIS,
        "monthly_observation_end_representation": "ASSUMPTION: dated snapshot end-of-day KST",
        "sources": [], "volume_sources": [], "variants": {policy: {"days": []} for policy in ("D+1", "D+2")},
        "comparison_population": "same_all_features_and_known_price_candidates_for_every_primary_arm",
        "basic_all_role": "separate_unrestricted_price_diagnostic_not_primary_control",
        "policy_results_not_pooled": True, "no_followup_or_promotion_decision": True}
    prices = iter(price_chunks)
    volumes = iter(volume_chunks) if volume_chunks is not None else None
    history = deque(maxlen=20)
    total_rows, total_volume_rows = 0, 0
    for day_index, day in enumerate(reference):
        check()
        sessions, source_indices, current_volumes, volume_indices, opens = {}, {}, {}, {}, {}
        window_cache = {}
        for symbol in sorted(union[day]):
            check()
            try:
                chunk = next(prices)
                volume_chunk = next(volumes) if volumes is not None else None
            except StopIteration:
                raise ExperimentRefusal("missing_input_chunk") from None
            session, source = _read_price(chunk, day, symbol)
            current, volume_source = _volume(volume_chunk, day, symbol) if volume_chunk is not None else (None, None)
            if volume_chunk is not None:
                _require(Counter((r["open_time_ms"], r["fetched_at"]) for r in chunk["rows"])
                         == Counter((r["open_time_ms"], r["fetched_at"]) for r in volume_chunk["rows"]),
                         "price_volume_raw_timestamp_retrieval_linkage_changed")
            total_rows += source["input_rows"]
            _require(total_rows <= cc.RESOURCES["maximum_rows"], "input_population_budget_exceeded")
            total_volume_rows += volume_source["input_rows"] if volume_source is not None else 0
            _require(total_volume_rows <= cc.RESOURCES["maximum_rows"], "volume_population_budget_exceeded")
            sessions[symbol], current_volumes[symbol] = session, current
            opens[symbol] = _opening_prices(chunk["rows"])
            source_indices[symbol] = len(report["sources"])
            volume_indices[symbol] = len(report["volume_sources"]) if volume_source is not None else None
            report["sources"].append(source)
            if volume_source is not None:
                report["volume_sources"].append(volume_source)
        prior_dates = tuple(date.fromisoformat(d) for d in calendar if d < day)[-20:]
        prior = {key: values for key, values in history}
        detections = {symbol: signals.detect_bars(signals.HYPOTHESES[0 if setup_id == "EA-A" else 2],
            session.prefix, opening=session.opening, closing=cc.session_close(day)) for symbol, session in sessions.items()}
        for policy in ("D+1", "D+2"):
            membership = matrix[policy]["days"][day_index]
            universe = _universe(membership, day)
            for member in membership["targets"]:
                check()
                symbol = member["symbol"]
                session, detection = sessions[symbol], detections[symbol]
                source = report["sources"][source_indices[symbol]]
                target = ea.Target(symbol, date.fromisoformat(day), policy, session.opening,
                                   cc.session_close(day), PRICE_BASIS, _price_source(source))
                first, last = {}, {}
                if detection.state == "signal":
                    end = detection.signal_bar_end
                    for peer in universe.members:
                        if peer == symbol:
                            continue
                        peer_source = report["sources"][source_indices[peer]]
                        first_key = (peer, session.opening,
                                     end if setup_id == "EA-A" else session.opening + 30 * cc.MINUTE)
                        last_key = (peer, end - 15 * cc.MINUTE, end)
                        for key in (first_key, last_key):
                            if key not in window_cache:
                                window_cache[key] = _window(sessions[peer], peer_source, *key)
                        first[peer], last[peer] = window_cache[first_key], window_cache[last_key]
                volume = (ea.VolumeInputs(prior_dates, {d: cc.session_open(d.isoformat()) for d in prior_dates},
                    current_volumes[symbol], {d: prior.get(d.isoformat(), {}).get(symbol) for d in prior_dates})
                    if setup_id == "EA-A" and len(prior_dates) == 20 else None)
                candidate = ea.prepare_candidate(setup_id, session.prefix, target=target, universe=universe,
                    first_window_peers=first, last15_peers=last, volume=volume)
                label = outcomes.exploratory_outcome(candidate, session.minutes,
                    source=outcomes.OutcomeSource(symbol, target.session_date, policy, PRICE_BASIS,
                        target.price_source, source["data_vintage"], authorization_reference),
                    inputs_authenticated=True, opening_prices=opens[symbol]) if detection.state == "signal" else None
                report["variants"][policy]["days"].append({"session": day, "symbol": symbol,
                    "membership_day_index": day_index, "membership_target": deepcopy(member),
                    "source_index": source_indices[symbol], "volume_source_index": volume_indices[symbol],
                    "peer_source_indices": {peer: source_indices[peer] for peer in candidate.fixed_peers},
                    "expected_volume_reference_dates": [d.isoformat() for d in prior_dates] if setup_id == "EA-A" else [],
                    "volume_reference_calendar_complete": len(prior_dates) == 20 if setup_id == "EA-A" else None,
                    "signal": asdict(candidate.basic_candidate), "arms": dict(candidate.arms),
                    "features": {name: {"value": feature.value, "reasons": feature.reasons}
                                 for name, feature in candidate.features.items()},
                    "fixed_peers": candidate.fixed_peers, "entry_at": candidate.entry_at,
                    "cap_at": candidate.cap_at, "structural_stop": candidate.structural_stop,
                    "common_context_observable": candidate.common_context_observable,
                    "common_price_observable": candidate.common_context_observable and label is not None and label["status"] == "known",
                    "outcome": label})
        history.append((day, current_volumes))
        # No full-session raw rows, price maps or prefix bars escape the day.
        del sessions, session, chunk, volume_chunk, detections, opens, window_cache
    for iterator in (prices, volumes):
        if iterator is None:
            continue
        try:
            next(iterator)
        except StopIteration:
            pass
        else:
            raise ExperimentRefusal("extra_input_chunk")
    report["input_evidence"] = {"unique_target_days": len(report["sources"]), "price_rows": total_rows,
        "volume_rows": sum(s["input_rows"] for s in report["volume_sources"]),
        "pin_authentication": "caller_assertion_not_proven_by_pure_builder"}
    for variant in report["variants"].values():
        variant["own_sizing"] = {}
        for arm in arms:
            check()
            variant["own_sizing"][arm] = _own_sizing(_records(variant["days"], arm), reference)
    for variant in report["variants"].values():
        check()
        _summarize(variant, reference, weeks, arms)
    check()
    return report
