"""Pure EC adapters for stored EB plans and day-streamed original CC prices.

The caller owns report/archive pins, logged access, budgets and independent
verification. These functions open no files and replay no signal detectors or
old outcome labels. Every original signal survives projection; old outcome
states are evidence only. A fully exhausted price iterator authenticates every
original target chunk, including targets with no observed signal.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from copy import deepcopy
from datetime import datetime
from fractions import Fraction
import re

from research import intraday_campaign_readiness as readiness
from research import intraday_context_experiment as eb


MODE = "ec-stored-account-inputs"
POLICIES = readiness.POLICIES


class AccountInputsRefusal(ValueError):
    """Malformed plans or broken source links never become missing prices."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise AccountInputsRefusal(reason)


def _stop(value: object) -> Fraction:
    """Decode only the positive canonical Fraction shape saved by CC."""
    _require(isinstance(value, Mapping) and set(value) == {"numerator", "denominator"},
             "invalid_structural_stop")
    _require(all(type(value[key]) is str and re.fullmatch(r"[1-9][0-9]*", value[key])
                 for key in ("numerator", "denominator")), "invalid_structural_stop")
    try:
        result = Fraction(int(value["numerator"]), int(value["denominator"]))
    except (ValueError, ZeroDivisionError):
        raise AccountInputsRefusal("invalid_structural_stop") from None
    _require(str(result.numerator) == value["numerator"]
             and str(result.denominator) == value["denominator"], "noncanonical_structural_stop")
    return result


def project_account_inputs(report: Mapping, setup_id: str) -> dict:
    """Validate a parsed canonical EB report and retain its original plans.

    Candidate admission has no old outcome status, return or price-completeness
    input. Even old declined/unknown-price candidates remain. Clock-infeasible
    plans remain counted with an explicit causal ineligibility marker.
    """
    metadata = readiness.project_report(report, setup_id)
    result = {"mode": MODE, "setup_id": setup_id,
              "reference_sessions": metadata["reference_sessions"],
              "reference_weeks": metadata["reference_weeks"],
              **{key: deepcopy(report[key]) for key in
                 ("source_target_matrix", "upstream_calendar", "sources", "volume_sources")},
              "variants": {}, "performance_computed": False, "account_path_proven": False}
    for policy in POLICIES:
        original = {(row["session"], row["symbol"]): row
                    for row in report["variants"][policy]["days"]}
        dates = {day: {"targets": [], "candidates": []} for day in result["reference_sessions"]}
        projected = metadata["variants"][policy]
        for row in projected["days"]:
            day, symbol = row["session"], row["symbol"]
            source = original[(day, symbol)]
            # Outcome metadata stays outside the object used for admission.
            target = {"symbol": symbol, "membership_day_index": row["membership_day_index"],
                      "source_index": row["source_index"], "volume_source_index": row["volume_source_index"],
                      "signal_state": row["signal_state"], "signal_reason": row["signal_reason"],
                      "source_state": row["source_state"],
                      "original_outcome": {"status": row["outcome_status"], "reason": row["outcome_reason"]}}
            dates[day]["targets"].append(target)
            if row["signal_state"] != "signal":
                continue
            decision, entry, cap = (datetime.fromisoformat(row[key])
                                    for key in ("decision_at", "entry_at", "cap_at"))
            eligible = eb.cc.session_open(day) < decision < entry < cap < eb.cc.session_close(day)
            dates[day]["candidates"].append({"strategy": setup_id[-1], "symbol": symbol,
                "decision_at": decision, "entry_at": entry, "cap_exit_at": cap,
                "structural_stop": _stop(source.get("structural_stop")),
                "clock_eligible": eligible,
                "clock_ineligible_reason": None if eligible else "same_day_cap_infeasible"})
        for content in dates.values():
            content["counts"] = {
                "targets": len(content["targets"]), "signals": len(content["candidates"]),
                "known_no_signal": sum(row["signal_state"] == "known_no_signal" for row in content["targets"]),
                "unknown_signals": sum(row["signal_state"] == "unknown" for row in content["targets"]),
                "clock_ineligible": sum(not row["clock_eligible"] for row in content["candidates"])}
        result["variants"][policy] = {"dates": dates, "counts": projected["counts"],
            "by_week": projected["by_week"], "arm_counts": projected["arm_counts"],
            "feature_counts": projected["feature_counts"]}
    return result


def _linked_inputs(a: Mapping, b: Mapping) -> dict[str, list[str]]:
    for value, setup in ((a, "EA-A"), (b, "EA-B")):
        _require(isinstance(value, Mapping) and value.get("mode") == MODE
                 and value.get("setup_id") == setup, "invalid_account_projection")
    for field in ("reference_sessions", "reference_weeks", "source_target_matrix", "upstream_calendar", "sources"):
        _require(eb.cc._canonical(a[field]) == eb.cc._canonical(b[field]), f"setup_{field}_differ")
    union = {day: set() for day in a["reference_sessions"]}
    for policy in POLICIES:
        for membership in a["source_target_matrix"][policy]["days"]:
            union[membership["session"]].update(target["symbol"] for target in membership["targets"])
    expected = {(day, symbol) for day, symbols in union.items() for symbol in symbols}
    for value in (a, b):
        sources = value["sources"]
        _require(len(sources) == len(expected)
                 and {(row["session"], row["symbol"]) for row in sources} == expected,
                 "source_population_changed")
    return {day: sorted(symbols) for day, symbols in union.items()}


def iter_account_price_days(a: Mapping, b: Mapping,
                            price_chunks: Iterable[Mapping]) -> Iterator[dict]:
    """Authenticate the full union once, retaining at most one day's prices.

    Exhaust this iterator before declaring complete input evidence. Day receipts
    prove only their own consumed chunks. Source SHA and ordered row-linkage are
    checked against BOTH stored reports. Price maps retain every original signal
    symbol regardless of old outcome/context states, and no other symbol. Raw
    OPEN survives invalid later HLC exactly as in the reviewed EB parser.
    """
    yield from _iter_price_days(a, b, price_chunks, include_context=False)


def iter_context_price_days(a: Mapping, b: Mapping,
                            price_chunks: Iterable[Mapping]) -> Iterator[dict]:
    """Expose the same authenticated day before candidate-symbol retention.

    Original monthly members without a detected signal can still supply causal
    peer windows. Empty/invalid minutes remain in their original Session state;
    this projection neither replaces peers nor certifies their availability.
    The consumer must exhaust the iterator and enforce its feature clocks.
    """
    yield from _iter_price_days(a, b, price_chunks, include_context=True)


def _iter_price_days(a: Mapping, b: Mapping, price_chunks: Iterable[Mapping], *,
                     include_context: bool) -> Iterator[dict]:
    union = _linked_inputs(a, b)
    pins = [{(source["session"], source["symbol"]): source for source in value["sources"]}
            for value in (a, b)]
    chunks = iter(price_chunks)
    total_rows = 0
    days = list(union)
    for day_index, day in enumerate(days):
        retained = {candidate["symbol"] for value in (a, b) for policy in POLICIES
                    for candidate in value["variants"][policy]["dates"][day]["candidates"]}
        _require(retained <= set(union[day]), "candidate_outside_original_targets")
        minute_bars, open_prices, evidence = {}, {}, []
        original_sessions, original_sources = {}, {}
        for symbol in union[day]:
            try:
                chunk = next(chunks)
            except StopIteration:
                raise AccountInputsRefusal("missing_price_chunk") from None
            session, source = eb._read_price(chunk, day, symbol)
            for expected in pins:
                original = expected[(day, symbol)]
                for field in ("data_vintage", "row_linkage_sha256", "input_rows"):
                    _require(source[field] == original[field], f"price_source_{field}_changed")
                if "raw_chunk_metadata" in original:
                    _require(eb.cc._canonical(source["raw_chunk_metadata"])
                             == eb.cc._canonical(original["raw_chunk_metadata"]), "price_source_metadata_changed")
            total_rows += source["input_rows"]
            _require(total_rows <= eb.cc.RESOURCES["maximum_rows"], "price_row_budget_exceeded")
            evidence.append(source)
            if include_context:
                original_sessions[symbol] = session
                original_sources[symbol] = source
            if symbol in retained:
                minute_bars.update({(symbol, at): values for at, values in session.minutes.items()})
                open_prices.update({(symbol, at): value for at, value in eb._opening_prices(chunk["rows"]).items()})
        if day_index == len(days) - 1:
            try:
                next(chunks)
            except StopIteration:
                pass
            else:
                raise AccountInputsRefusal("extra_price_chunk")
        projected = {"session": day, "session_open": eb.cc.session_open(day), "session_close": eb.cc.session_close(day),
               "minute_bars": minute_bars, "open_prices": open_prices, "sources": evidence,
               "retained_price_symbols": sorted(retained), "consumed_target_chunks": len(evidence),
               "consumed_price_rows": sum(source["input_rows"] for source in evidence),
               "original_union_complete": day_index == len(days) - 1}
        if include_context:
            projected.update(original_sessions=original_sessions, original_sources=original_sources)
        yield projected
        # The consumer owns any returned day. No earlier raw/derived day is cached.
        del session, chunk, minute_bars, open_prices, evidence, original_sessions, original_sources, projected
