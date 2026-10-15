"""Price-free raw LG/LX scope for the complete BS potential-origin family.

This pure adapter accepts caller-pinned metadata, never market bodies. It keeps
every paired proof origin through its original horizon and describes possible
components without finding entitlement. Original response inventory entries are
only reuse candidates; their bodies, receipts and rows require later logged
verification. No old raw artifact is relabeled against the new base snapshot.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import re

from research.activity_cohort_connection import cohort_proof_selections
from research.activity_cohort_selection import CohortSelection
from research.activity_pair_calibration import PARAMETERS
from research.activity_paired_replay import _json_value
from research.activity_preflight import digest, encoded, require, _pin
from research.activity_raw_episode import TARGET_CODES
from research.activity_raw_quote_view import PLAN_SCHEMA, UNITS, calendar_sha256
from research.activity_timing import SessionLagPolicy


SCHEMA = "activity-cohort-raw-acquisition-plan-v1"
SERVICE = "stk_bydd_trd"
PIN_FIELDS = {"activity_snapshot_sha256", "original_quote_snapshot_sha256", "original_read_scope_sha256",
    "base_quote_snapshot_sha256", "calendar_sha256", "cohort_declarations_sha256", "raw_event_plan_sha256"}
EVENT_FIELDS = {"event_id", "kind", "code", "isin", "successor_code", "successor_isin", "old_basis_on",
    "last_eligible_entry_on", "record_on", "effective_on", "retained_available_on", "new_available_on", "evidence_sha256"}
CACHE_FIELDS = {"service", "bas_dd", "response_path", "response_sha256", "receipt_path", "receipt_sha256",
    "receipt_coordinate", "retrieved_at"}
FALSE_FLAGS = ("source_truth_certified", "source_contents_verified", "event_coverage_certified", "entitlement_certified",
    "price_basis_certified", "unit_bridge_certified", "historical_vintage_certified", "historical_publication_certified",
    "custody_delivery_certified", "isin_join_certified", "atomic_source_vintage_certified", "raw_input_reuse_certified",
    "cache_bodies_verified", "cache_receipts_verified", "raw_rows_verified", "actual_input_reuse_readiness_established",
    "unit_plan_ready", "component_proof_verified", "original_end_obligation_discharged", "episode_end_is_actual_exit",
    "adjusted_price_fallback_allowed", "missing_row_market_fallback_allowed", "actions_applied", "books_computed",
    "returns_computed", "actual_study_completed", "promotion_allowed", "database_access", "api_access",
    "credential_access", "raw_response_bodies_read")
ZERO_FIELDS = ("new_source_requests", "automatic_retries", "D1_completed_studies", "D2_completed_studies")


def _day(value: str) -> date:
    require(type(value) is str, "explicit canonical ISO date required")
    result = date.fromisoformat(value)
    require(str(result) == value, "canonical ISO date required")
    return result


def _text(value) -> bool:
    return type(value) is str and bool(value) and value == value.strip()


def _isin(value) -> bool:
    return type(value) is str and re.fullmatch(r"[A-Z]{2}[0-9A-Z]{9}[0-9]", value) is not None


def _events(raw_event_plan, identities):
    require(type(raw_event_plan) is tuple, "immutable caller-pinned raw event metadata required")
    events, ids = [], set()
    for event in raw_event_plan:
        require(type(event) is dict and set(event) == EVENT_FIELDS and event["kind"] == "compulsory_spin_off"
            and event["code"] == "003550" and event["successor_code"] == "383800"
            and _text(event["event_id"]) and event["event_id"] not in ids,
            "only explicit unique LG/LX compulsory-spin-off metadata is supported")
        _pin(event["evidence_sha256"])
        for code, isin in ((event["code"], event["isin"]), (event["successor_code"], event["successor_isin"])):
            require(_isin(isin) and (code not in identities or identities[code] == isin), "raw event/origin issue mismatch")
        dates = {name: _day(event[name]) for name in EVENT_FIELDS if name.endswith("_on")}
        require(dates["old_basis_on"] < dates["effective_on"]
            and dates["last_eligible_entry_on"] <= dates["record_on"] <= dates["effective_on"]
            <= min(dates["retained_available_on"], dates["new_available_on"]), "raw event date roles contradict")
        ids.add(event["event_id"])
        events.append((deepcopy(event), dates))
    require(len(events) <= 1, "repeated full-issue raw events are unsupported")
    return events


def _inventory(prior_responses, prior_inventory_sha256):
    require(type(prior_responses) is tuple, "immutable explicit original-response inventory required")
    if prior_inventory_sha256 is None:
        require(not prior_responses, "nonempty prior inventory needs its caller pin")
    else:
        _pin(prior_inventory_sha256)
        require(digest(encoded(prior_responses)) == prior_inventory_sha256, "prior inventory metadata pin mismatch")
    inventory = {}
    for row in prior_responses:
        require(type(row) is dict and set(row) == CACHE_FIELDS and row["service"] == SERVICE,
            "exact original KOSPI trading-response inventory required")
        compact = row["bas_dd"]
        require(type(compact) is str and re.fullmatch(r"[0-9]{8}", compact) is not None
            and date.fromisoformat(compact).strftime("%Y%m%d") == compact, "canonical original response date required")
        for name in ("response_sha256", "receipt_sha256"):
            _pin(row[name])
        for name in ("response_path", "receipt_path"):
            path = row[name]
            require(_text(path) and re.match(r"^(?:/|[A-Za-z]:[/\\])", path) is not None
                and ".." not in path.replace("\\", "/").split("/"), "explicit absolute retained evidence path required")
        require(row["response_path"] != row["receipt_path"] and _text(row["receipt_coordinate"])
            and type(row["retrieved_at"]) is str and datetime.fromisoformat(row["retrieved_at"]).utcoffset() is not None,
            "distinct response/receipt and aware original retrieval metadata required")
        key = row["service"], compact
        require(key not in inventory, "ambiguous original-response inventory vintage")
        inventory[key] = deepcopy(row)
    return inventory


def build_cohort_raw_plan(
    declarations: tuple[CohortSelection, ...], calendar: tuple[date, ...], raw_event_plan: tuple[dict, ...], *,
    parameters: dict, input_sha256: dict, prior_responses: tuple[dict, ...] = (),
    prior_inventory_sha256: str | None = None,
) -> dict:
    """Derive full-origin requirements and an existing-schema contiguous unit plan.

    Pins bind exact caller metadata and named snapshots, not their source truth.
    The caller must supply and verify the complete original calendar and both
    old-original/new-base quote identities. No price-state or filled-lot input
    can exclude an origin. A missing target episode yields no unit plan rather
    than an invented empty interval; source/proof/readiness remain false.
    """
    require(type(calendar) is tuple, "immutable complete original calendar required")
    SessionLagPolicy._calendar(calendar)
    require(type(parameters) is dict and encoded(parameters) == encoded(PARAMETERS), "unchanged canonical BS parameters required")
    require(type(input_sha256) is dict and set(input_sha256) == PIN_FIELDS, "exact original/new-base metadata pins required")
    for value in input_sha256.values():
        _pin(value)
    require(input_sha256["calendar_sha256"] == calendar_sha256(calendar), "complete original calendar pin mismatch")
    require(input_sha256["cohort_declarations_sha256"] == digest(encoded(_json_value(declarations)))
        and input_sha256["raw_event_plan_sha256"] == digest(encoded(raw_event_plan)), "declaration/event metadata pin mismatch")
    require(input_sha256["original_quote_snapshot_sha256"] != input_sha256["base_quote_snapshot_sha256"],
        "new composite base must retain a distinct original quote identity")
    require(calendar[0] >= date(2019, 1, 2), "reserved calendar is outside this planning boundary")
    selections = cohort_proof_selections(declarations, calendar, parameters)
    identities, chosen = {}, []
    for row in declarations:
        for code in row.chosen_codes:
            isin = dict(row.source_population)[code]
            require(type(code) is str and re.fullmatch(r"[0-9]{6}", code) is not None and _isin(isin)
                and (code not in identities or identities[code] == isin), "exact unique cohort issue identities required")
            identities[code] = isin
            chosen.append(dict(group_id=row.group_id, arm=row.arm, formation_on=str(row.formation_on),
                code=code, isin=isin, entry_on=str(row.decision_on)))
    events = _events(raw_event_plan, identities)
    inventory = _inventory(prior_responses, prior_inventory_sha256)
    end = _day(parameters["end"])
    positions = {value: index for index, value in enumerate(calendar)}
    all_origins, origins, required, action_spans, unresolved = [], [], {}, {}, []

    def add(code, first, last):
        required.setdefault(code, set()).update(value for value in calendar if first <= value <= last)

    def action_span(code, isin, first, last):
        action_spans.setdefault(code, []).append((first, last, isin))

    for arm, rows in selections.items():
        for selection in rows:
            entry = selection.decision_on
            due = calendar[positions[entry] + parameters["holding_sessions"]]
            for code in selection.eligible_codes:
                origin = dict(formation_on=str(selection.formation_on), arm=arm, code=code, isin=identities[code],
                    entry_on=str(entry), due_on=str(due), original_end_on=str(end))
                all_origins.append(origin)
                if code not in TARGET_CODES:
                    continue
                origins.append(deepcopy(origin))
                action_span(code, origin["isin"], entry, end)
                matched = [(event, dates) for event, dates in events
                    if code == event["code"] and entry < dates["effective_on"] <= end]
                if not matched:
                    add(code, entry, end)
                    if code == "003550" and not events:
                        unresolved.append(dict(origin=deepcopy(origin), reason="raw_event_metadata_not_supplied"))
                    continue
                event, dates = matched[0]
                action_span(event["successor_code"], event["successor_isin"], dates["effective_on"], end)
                add(event["successor_code"], dates["new_available_on"], end)
                if entry > dates["last_eligible_entry_on"] or entry > dates["old_basis_on"]:
                    add(code, entry, end)
                    unresolved.append(dict(origin=deepcopy(origin), event_id=event["event_id"],
                        reason="unsupported_raw_origin_eligibility_preserves_original_horizon"))
                else:
                    add(code, entry, dates["effective_on"] - timedelta(days=1))
                    add(code, dates["retained_available_on"], end)
                for target, on in ((code, dates["old_basis_on"]), (code, dates["retained_available_on"]),
                        (event["successor_code"], dates["new_available_on"])):
                    if on in positions:
                        required.setdefault(target, set()).add(on)
                    else:
                        unresolved.append(dict(origin=deepcopy(origin), code=target, session=str(on),
                            reason="raw_basis_anchor_outside_supplied_calendar"))
    minimum = {code: sorted(values) for code, values in sorted(required.items()) if values}
    envelope = {code: [day for day in calendar if values[0] <= day <= values[-1]] for code, values in minimum.items()}
    episodes = [dict(code=code, start=str(values[0]), end=str(values[-1])) for code, values in envelope.items()]
    unit = None
    if set(envelope) == TARGET_CODES:
        unit = dict(schema=PLAN_SCHEMA, activity_snapshot_sha256=input_sha256["activity_snapshot_sha256"],
            base_quote_snapshot_sha256=input_sha256["base_quote_snapshot_sha256"],
            calendar_sha256=input_sha256["calendar_sha256"], episodes=episodes, **UNITS)
    elif origins:
        unresolved.append(dict(reason="existing_raw_view_requires_both_nonempty_LG_LX_episodes"))
    days = sorted({day for values in envelope.values() for day in values})
    matrix, candidates, delta = [], [], []
    for day in days:
        request = dict(service=SERVICE, bas_dd=day.strftime("%Y%m%d"),
            targets=[code for code, values in envelope.items() if values[0] <= day <= values[-1]])
        matrix.append(request)
        previous = inventory.get((SERVICE, request["bas_dd"]))
        if previous is None:
            delta.append(deepcopy(request))
        else:
            candidates.append(dict(**previous, targets=list(request["targets"])))
    windows = []
    for code, spans in sorted(action_spans.items()):
        require(len({isin for _, _, isin in spans}) == 1, "raw action-priority identity conflict")
        windows.append(dict(code=code, isin=spans[0][2], start=str(min(first for first, _, _ in spans)),
            end=str(max(last for _, last, _ in spans))))
    project = lambda rows: {code: [str(day) for day in values] for code, values in rows.items()}
    acquisition = dict(schema=SCHEMA, status="price_free_full_family_potential_scope_only",
        input_sha256=deepcopy(input_sha256), parameters_sha256=digest(encoded(parameters)),
        prior_inventory_sha256=prior_inventory_sha256, raw_event_plan=deepcopy(raw_event_plan),
        calendar_dates=[str(day) for day in calendar], chosen_cohort_origins=chosen, potential_origins=all_origins,
        raw_potential_origins=origins, minimum_code_dates=project(minimum), requested_code_dates=project(envelope),
        action_priority_windows=windows, request_matrix=matrix, cached_response_candidates=candidates,
        uncached_request_candidates=delta, unresolved=unresolved,
        original_end_fallback_preserved=True, original_quote_requests_preserved=True,
        raw_scope_meaning="all possible original holdings and potential successors; no entitlement or actual exit finding",
        cache_scope_meaning="explicit original response body/service/date/receipt candidates only; no old raw artifact reuse",
        endpoint_meaning="original horizon; separate recomputed raw component proof required before refinement",
        counts=dict(cohort_declarations=len(declarations), chosen_cohort_origins=len(chosen), potential_origins=len(all_origins),
            raw_potential_origins=len(origins), minimum_raw_code_dates=sum(map(len, minimum.values())),
            raw_code_dates=sum(map(len, envelope.values())), query_days=len(days),
            cached_response_candidates=len(candidates), uncached_request_candidates=len(delta)),
        **dict.fromkeys(FALSE_FLAGS, False), **dict.fromkeys(ZERO_FIELDS, 0))
    return dict(unit_plan=unit, acquisition_plan=acquisition)
