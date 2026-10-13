"""Pure origin-specific action bounds; immutable full quote requirements remain.

This proof changes evidence priority, never accounting, selection or execution.
Only isolated compulsory spin-offs receive component candidates. Unsupported
chains and eligibility paths retain original-end coverage under the existing
decoder/replay. A candidate is neither an actual exit nor a source certificate.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
import json

from research.activity_book import CompulsorySpinOff, CompulsoryStockExchange, FinalCashPayment
from research.activity_exit_bounds import validate_exit_bounds
from research.activity_holding_inputs import HoldingInputs
from research.activity_partition_selection import PartitionSelection
from research.activity_preflight import digest, encoded, require, strict_json
from research.activity_quote_extension import CompositeHoldingInputs, _same, _validated, extend_holding_quotes
from research.activity_raw_episode import TARGET_CODES
from research.activity_raw_quote_view import RawEpisodeQuoteInputs, validate_raw_quote_view
from research.activity_reviewed_actions import ReviewedActionTerms, _day, _identity, _value, reviewed_action_terms


SCHEMA = "activity-component-lifetime-bounds-v1"
RAW_SCHEMA = "activity-raw-component-lifetime-bounds-v1"
WRAPPER_SCHEMA = "activity-component-exit-scope-v1"
ORIGIN_FIELDS = ("formation_on", "arm", "code", "isin", "entry_on", "due_on")


def _windows(spans):
    identities, intervals = {}, {}
    for code, isin, start, end in spans:
        require(code not in identities or identities[code] == isin, "component code reuse unsupported")
        identities[code] = isin
        intervals.setdefault(code, []).append((start, end))
    result = []
    for code, rows in sorted(intervals.items()):
        merged = []
        for start, end in sorted(rows):
            if merged and start <= merged[-1][1] + timedelta(days=1):
                merged[-1] = merged[-1][0], max(end, merged[-1][1])
            else:
                merged.append((start, end))
        result.extend(dict(code=code, isin=identities[code], start=str(start), end=str(end))
                      for start, end in merged)
    return result


def _cells(windows):
    return {(row["code"], row["isin"], ordinal) for row in windows
            for ordinal in range(_day(row["start"]).toordinal(), _day(row["end"]).toordinal() + 1)}


def _terms(terms, plan, quotes):
    """Re-run shared term checks, including forged frozen dataclass projections."""
    require(type(terms) is ReviewedActionTerms and digest(terms.canonical_json) == terms.terms_sha256,
            "canonical reviewed terms required")
    canonical = strict_json(terms.canonical_json)
    full = [dict(code=row["code"], isin=row["isin"], start=row["entry_on"], end=row["original_end_on"])
            for row in plan["entries"]]
    require(_cells(canonical["original_windows"]) == _cells(full), "terms must retain full original origins")
    coverage = dict(schema="activity-reviewed-actions-v1", status="reviewed_complete",
        activity_snapshot_sha256=quotes.activity_snapshot_sha256, quote_snapshot_sha256=quotes.quote_snapshot_sha256,
        calendar_sha256=plan["calendar_sha256"], required_scope_sha256=plan["original_read_scope_sha256"],
        events=[row["declaration"] for row in canonical["events"]], windows=[], noops=[], unresolved=[])
    fresh = reviewed_action_terms(coverage, canonical["basis_evidence"],
        expected_windows=tuple(canonical["original_windows"]), calendar=quotes.calendar,
        activity_snapshot_sha256=quotes.activity_snapshot_sha256, quote_snapshot_sha256=quotes.quote_snapshot_sha256)
    require(fresh.canonical_json == terms.canonical_json and _value(fresh) == _value(terms),
            "reviewed terms projection disagrees with shared decoder")
    return fresh, canonical


def _candidate(quotes, observations: dict, code: str, due: date | None, available: date, end: date):
    if due is None:
        return None, "due_outside_calendar"
    for index, day in enumerate(quotes.calendar):
        if not max(due, available) <= day <= end:
            continue
        observation = observations.get((code, day))
        if observation is None:
            return None, "unrequested_quote"
        if type(quotes) is RawEpisodeQuoteInputs and code in TARGET_CODES:
            if observation.issues == ("target_absent_cause_unknown",) or observation.state == "observed_no_trade_zero_prices":
                continue
            if observation.status != "observed":
                return None, "unresolved_observed"
            if observation.state == "frozen":
                continue
            series = quotes.panel[code]
            if observation.issues or observation.open is None or series.opens[index] != observation.open:
                return None, "unresolved_observed"
            if observation.open > 0:
                return day, "locked" if observation.state == "locked" else "observed"
            continue
        if observation.typed_values is None:
            continue
        series = quotes.panel[code]
        if observation.issues or any(getattr(series, name)[index] is None
                                    for name in ("opens", "closes", "turnover", "frozen", "locked")):
            return None, "unresolved_observed"
        if series.observed[index] and series.opens[index] > 0 and series.frozen[index] is False:
            return day, "locked" if series.locked[index] else "observed"
    return None, "no_usable_opening"


def _lifetime(event):
    if isinstance(event, FinalCashPayment):
        return event.distribution.code, event.distribution.last_trading_on, event.distribution.paid_on
    return (event.old_basis.code,
            event.last_eligible_entry_on if isinstance(event, CompulsorySpinOff) else event.effective_on,
            event.effective_on)


def component_exit_bounds(
    ordinary_exit_scope: dict, selections_d1: tuple[PartitionSelection, ...],
    selections_d2: tuple[PartitionSelection, ...], original: HoldingInputs, parameters: Mapping,
    original_required_code_dates: Mapping[str, Sequence[str]], issue_isins: Mapping[str, str], *,
    input_sha256: Mapping[str, str], quotes: HoldingInputs | CompositeHoldingInputs | RawEpisodeQuoteInputs,
    terms: ReviewedActionTerms, unresolved_priority_intervals: tuple[dict, ...] = (),
) -> dict:
    """Recompute every potential origin and preserve the exact FULL quote union.

    Hash DAG: original pins -> ordinary proof; canonical linked terms + composite
    snapshot + ordinary proof + unresolved priority intervals -> component hash.
    Final interval coverage bytes are absent from this DAG and must reference
    its resulting hash. A separate logged caller must establish actual use gates.
    """
    plan = validate_exit_bounds(ordinary_exit_scope, selections_d1, selections_d2, original, parameters,
        original_required_code_dates, issue_isins, input_sha256=input_sha256)
    raw_quotes = type(quotes) is RawEpisodeQuoteInputs
    if raw_quotes:
        quotes = validate_raw_quote_view(quotes)
        require(_same(quotes.base.original, original), "raw view must retain immutable original quotes")
        supplied = frozenset(quotes.base.extension.requested_coordinates)
    elif type(quotes) is CompositeHoldingInputs:
        require(_same(quotes.original, original)
                and _same(quotes, extend_holding_quotes(original, quotes.extension)), "immutable composite quotes required")
        supplied = frozenset(quotes.extension.requested_coordinates)
    else:
        require(type(quotes) is HoldingInputs and _same(quotes, original), "immutable original quotes required")
        if quotes.requested_coordinates:
            _validated(quotes)
        supplied = frozenset()
    terms, canonical = _terms(terms, plan, quotes)
    extra = {(code, _day(day)) for code, days in terms.successor_required for day in days}
    extra.update((row["code"], _day(row["session"])) for row in canonical["basis_evidence"]["bases"].values())
    extra.difference_update(original.requested_coordinates)
    require(supplied == extra, "FULL extension must retain exact original successor and basis union")
    if raw_quotes:
        observations = {(row.code, row.observation_date): row for row in quotes.base.observations if row.code not in TARGET_CODES}
        observations.update({(row.code, date.fromisoformat(row.requested_bas_dd)): row
                             for row in quotes.raw_observations})
    else:
        observations = {(row.code, row.observation_date): row for row in quotes.observations}
    positions = {day: index for index, day in enumerate(quotes.calendar)}
    for row in canonical["basis_evidence"]["bases"].values():
        key = row["code"], _day(row["session"])
        observation = observations.get(key)
        if raw_quotes and key[0] in TARGET_CODES:
            require(observation is not None and observation.status == "observed" and not observation.issues
                    and observation.close is not None and Decimal(row["raw_close"]) == Decimal(row["adjusted_close"])
                    == observation.close == quotes.panel[key[0]].closes[positions[key[1]]],
                    "raw event basis must be equal raw/raw requested resolved raw close")
            continue
        require(observation is not None and observation.typed_values is not None and not observation.issues
                and quotes.panel[key[0]].closes[positions[key[1]]] == Decimal(row["adjusted_close"]),
                "event basis must match a requested resolved composite quote")
    require(type(unresolved_priority_intervals) is tuple, "immutable unresolved priority intervals required")
    for row in unresolved_priority_intervals:
        require(type(row) is dict and set(row) == {"code", "isin", "start", "end"},
                "exact unresolved priority interval required")
        _identity(row)
        require(_day(row["start"]) <= _day(row["end"]), "reversed unresolved priority interval")
    identities = dict(issue_isins)
    for row in canonical["events"]:
        raw = row["declaration"]
        for code, isin in ((_identity(raw)),) + (() if "successor_code" not in raw else
                ((raw["successor_code"], raw["successor_isin"]),)):
            require(code not in identities or identities[code] == isin, "event/origin identity conflict")
            identities[code] = isin
    for row in unresolved_priority_intervals:
        require(identities.get(row["code"]) == row["isin"], "unresolved priority identity mismatch")

    entries, spans, spin_spans, keys = [], [], {}, set()
    for original_row in plan["entries"]:
        origin = {name: original_row[name] for name in ORIGIN_FIELDS}
        origin_sha = digest(encoded(origin))
        require(origin_sha not in keys, "duplicate potential origin")
        keys.add(origin_sha)
        entry, end = _day(origin["entry_on"]), _day(original_row["original_end_on"])
        due = _day(origin["due_on"]) if origin["due_on"] else None
        endpoint = _day(original_row["end_on"])
        raw_origin = raw_quotes and origin["code"] in TARGET_CODES
        if raw_origin:
            endpoint = end
        relevant = [event for event in terms.events if _lifetime(event)[0] == origin["code"]
                    and entry < _lifetime(event)[2] and _lifetime(event)[1] <= endpoint]
        require(len(relevant) <= 1, "unsupported full-issue origin graph")
        event = relevant[0] if relevant else None
        mode, reason, event_id = "ordinary_child", "ordinary_price_candidate", None
        components = [dict(code=origin["code"], isin=origin["isin"], available_on=str(entry),
                           candidate_on=original_row["candidate_on"], candidate_state=original_row["candidate_state"])]
        if raw_origin:
            components[0].update(candidate_on=None, candidate_state="raw_ordinary_candidate_not_proven")
            if event is None:
                mode, reason = "original_end", "raw_ordinary_candidate_not_proven"
        if event is not None:
            mode, reason, endpoint = "original_end", "unsupported_non_spin_event", end
            if isinstance(event, CompulsorySpinOff):
                event_id, components = event.event_id, []
                for code, available in ((event.old_basis.code, event.retained_available_on),
                                        (event.new_basis.code, event.new_available_on)):
                    candidate, state = _candidate(quotes, observations, code, due, available, end)
                    components.append(dict(code=code, isin=identities[code], available_on=str(available),
                        candidate_on=str(candidate) if candidate else None, candidate_state=state))
                if entry > event.last_eligible_entry_on:
                    reason = "unsupported_post_cutoff_holding_if_filled"
                elif entry > event.old_basis.session:
                    reason = "unsupported_entry_after_old_basis_if_filled"
                elif all(row["candidate_on"] for row in components):
                    endpoint = max(_day(row["candidate_on"]) for row in components)
                    mode, reason = "component_candidate", "both_components_have_price_candidates"
                else:
                    reason = "pending_or_unresolved_component"
                candidate = original_row["candidate_on"]
                if raw_origin:
                    # After legal effect, pending components use their own
                    # availability; do not inspect a not-yet-delivered bar.
                    candidate, state = _candidate(quotes, observations, origin["code"], due, entry,
                                                  min(end, event.effective_on - timedelta(days=1)))
                    candidate = str(candidate) if candidate else None
                    if candidate is None and state in ("unrequested_quote", "unresolved_observed"):
                        mode, reason, endpoint = "original_end", "unresolved_raw_origin_sale_path", end
                if candidate and event.last_eligible_entry_on < _day(candidate) < event.effective_on:
                    mode, reason, endpoint = "original_end", "unsupported_pre_effective_sale_if_filled", end
                if any(other is not event and _lifetime(other)[0] in {row["code"] for row in components}
                       and entry <= _lifetime(other)[1] <= endpoint for other in terms.events):
                    mode, reason, endpoint = "original_end", "unsupported_followup_event_full_path", end
        if raw_origin and event is not None and (origin["code"], entry) not in observations:
            mode, reason, endpoint = "original_end", "raw_origin_entry_unrequested", end
        if any(any(row["code"] == part["code"] and row["isin"] == part["isin"] for part in components)
               and _day(row["start"]) <= endpoint and entry <= _day(row["end"])
               for row in unresolved_priority_intervals):
            mode, reason, endpoint = "original_end", "unresolved_origin_interval", end
        spans.append((origin["code"], origin["isin"], entry, endpoint))
        if event_id and event.effective_on <= endpoint:
            span = event.new_basis.code, identities[event.new_basis.code], event.effective_on, endpoint
            spans.append(span)
            spin_spans.setdefault(event_id, []).append(span)
        entries.append(dict(origin=origin, origin_sha256=origin_sha, event_id=event_id, mode=mode, reason=reason,
            end_on=str(endpoint), original_end_on=str(end), ordinary_candidate_on=original_row["candidate_on"],
            components=components, last_eligible_entry_on=str(event.last_eligible_entry_on) if event_id else None,
            record_on=str(event.record_on) if event_id else None, effective_on=str(event.effective_on) if event_id else None))

    # Existing unsupported graph semantics retain full propagation. Direct spin
    # successors use eligible origins only, so later parent acquisitions cannot
    # extend an earlier allotment. A downstream spin has no direct origin proof.
    delivered = set()
    for event in terms.events:
        if isinstance(event, FinalCashPayment):
            continue
        if isinstance(event, CompulsoryStockExchange) or event.old_basis.code in delivered:
            rows = _windows(spans)
            for row in rows:
                start, end = max(_day(row["start"]), event.effective_on), _day(row["end"])
                if row["code"] == event.old_basis.code and start <= end:
                    span = event.new_basis.code, identities[event.new_basis.code], start, end
                    spans.append(span)
                    if isinstance(event, CompulsorySpinOff):
                        spin_spans.setdefault(event.event_id, []).append(span)
        delivered.add(event.new_basis.code)
    spin_windows = [dict(event_id=event_id, **row) for event_id, rows in sorted(spin_spans.items())
                    for row in _windows(rows)]
    result = dict(schema=RAW_SCHEMA if raw_quotes else SCHEMA, status="provisional", ordinary_scope_sha256=plan["child_scope_sha256"],
        original_input_sha256=plan["input_sha256"], original_read_scope_sha256=plan["original_read_scope_sha256"],
        calendar_sha256=plan["calendar_sha256"], parameters_sha256=plan["parameters_sha256"],
        original_quote_snapshot_sha256=original.quote_snapshot_sha256, quote_snapshot_sha256=quotes.quote_snapshot_sha256,
        terms_sha256=terms.terms_sha256, unresolved_priority_intervals=_value(unresolved_priority_intervals),
        full_extension_code_dates=[[code, str(day)] for code, day in sorted(supplied)],
        entries=entries, windows=_windows(spans), spin_off_windows=spin_windows,
        books_computed=False, returns_computed=False, action_coverage_certified=False, actual_exits_certified=False)
    if raw_quotes:
        result.update(raw_source_artifact_sha256=quotes.source_artifact_sha256, raw_unit_plan_sha256=quotes.unit_plan_sha256,
                      base_quote_snapshot_sha256=quotes.base.quote_snapshot_sha256)
    result["component_scope_sha256"] = digest(encoded(result))
    return result


def validate_component_exit_bounds(proof: dict, *args, **kwargs) -> dict:
    recomputed = component_exit_bounds(*args, **kwargs)
    require(type(proof) is dict and encoded(proof) == encoded(recomputed),
            "component proof differs from immutable inputs")
    return recomputed


def verify_component_inventory(replays: Mapping, proof: dict,
                               events: tuple[CompulsorySpinOff | CompulsoryStockExchange | FinalCashPayment, ...]) -> None:
    """Inspect actual lots after the existing replay, never demand unfilled lots.

    Runtime lot IDs lack formation/ISIN. Ambiguous arm/code/entry origins fail.
    The existing deterministic event IDs permit lineage checks for full fallback
    paths too; this is an identity invariant, not another accounting engine.
    """
    states, origin_keys = {}, set()
    for row in proof["entries"]:
        origin = row["origin"]
        entered = _day(origin["entry_on"])
        lot_id = f"{origin['code']}:{entered}"
        key = origin["arm"], lot_id
        require(key not in origin_keys, "ambiguous runtime potential origin")
        origin_keys.add(key)
        active = [dict(lot_id=lot_id, code=origin["code"], investment_id=None, available_on=entered,
                       start=entered, end=None, applied=(), candidate=row["ordinary_candidate_on"]
                       if row["mode"] == "ordinary_child" else None)]
        history = []
        for event in events:
            next_active = []
            for state in active:
                code, _, on = _lifetime(event)
                if state["code"] != code or entered >= on:
                    next_active.append(state)
                    continue
                if isinstance(event, FinalCashPayment):
                    state["end"] = on
                    history.append(state)
                    continue
                state["end"] = on
                history.append(state)
                applied = state["applied"] + (event.event_id,)
                if isinstance(event, CompulsoryStockExchange):
                    next_active.append(dict(state, code=event.new_basis.code, available_on=event.available_on,
                        start=on, end=None, applied=applied, candidate=None))
                    continue
                identity = json.dumps((event.event_id, state["lot_id"]), ensure_ascii=True, separators=(",", ":"))
                group = state["investment_id"] or "spin-off-investment:" + identity
                for code, available, component_id in ((event.old_basis.code, event.retained_available_on, state["lot_id"]),
                        (event.new_basis.code, event.new_available_on, "spin-off-component:" + identity)):
                    candidate = None
                    if row["event_id"] == event.event_id and row["reason"] in (
                            "both_components_have_price_candidates", "pending_or_unresolved_component"):
                        candidate = next(part["candidate_on"] for part in row["components"] if part["code"] == code)
                    next_active.append(dict(lot_id=component_id, code=code, investment_id=group, available_on=available,
                                            start=on, end=None, applied=applied, candidate=candidate))
            active = next_active
        for state in history + active:
            runtime = origin["arm"], state["lot_id"], state["code"], state["investment_id"]
            states.setdefault(runtime, []).append((row, state))
    for arm, replay in replays.items():
        for book in replay.books:
            for lot in book.lots:
                matches = [(row, state) for row, state in states.get((arm, lot.lot_id, lot.code, lot.investment_id), ())
                           if state["start"] <= book.as_of and (state["end"] is None or book.as_of < state["end"])]
                require(len(matches) == 1, "inventory lacks exact origin/component lineage")
                row, state = matches[0]
                require(str(lot.entered_on) == row["origin"]["entry_on"] and str(lot.due_on) == row["origin"]["due_on"]
                        and lot.available_on == state["available_on"] and lot.dataset_sha256 == proof["quote_snapshot_sha256"]
                        and set(state["applied"]) <= set(book.applied_event_ids), "inventory origin/due/availability/snapshot mismatch")
                require(state["candidate"] is None or book.as_of < _day(state["candidate"]),
                        "component survives its candidate session close")
                if row["mode"] == "component_candidate":
                    require(book.as_of < _day(row["end_on"]), "investment survives its group endpoint")
