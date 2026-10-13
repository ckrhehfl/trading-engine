"""Price-free fixed LG/LX diagnostic geometry; no evidence IO or exit proof.

The supplied scope-review SHA is a declared frozen-input reference. Its original
bytes, cache bodies/receipts and actual source identities require caller checks.
Full two-market matrices below describe retained receipt ordinals only; planned
reads and new requests use the explicitly registered KOSPI service exclusively.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import re

from data import krx_formation_audit as formation, krx_openapi_probe as probe
from research.activity_raw_quote_view import PLAN_SCHEMA, UNITS

SCHEMA = "activity-raw-episode-acquisition-plan-v1"
SERVICE = "stk_bydd_trd"
ORIGINAL_END = "2026-09-18"
CALENDAR_END = "2026-09-23"
EPISODES = (("003550", "2021-04-16", "2021-10-22"), ("383800", "2021-05-27", "2021-10-22"))
BP_RESPONSES = {
    "20210428": (1, "1aab50e65bcd5353f7c45415b9a75f872f49196dae12343d3a77f394b34d130d"),
    "20210527": (2, "3ba4e2062b499122f2824983d214b83a2fe376d939fba948c65a12f425f110c4"),
}
FALSE_FLAGS = ("source_truth_certified", "event_coverage_certified", "price_basis_certified", "unit_bridge_certified",
    "historical_vintage_certified", "historical_publication_certified", "custody_delivery_certified", "isin_join_certified",
    "atomic_source_vintage_certified", "executable_basis_created", "actions_applied", "returns_computed", "books_computed",
    "actual_study_completed", "promotion_allowed", "database_access", "api_access", "raw_response_bodies_read")
ZERO_FIELDS = ("new_source_requests", "automatic_retries", "D1_completed_studies", "D2_completed_studies")
ORIGIN_FIELDS = ("formation_on", "arm", "code", "isin", "entry_on", "entry_state", "due_on", "due_state",
    "candidate_on", "candidate_state", "end_on", "original_end_on", "state", "mode", "reason", "apr28_original_horizon_eligible")


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _iso(value):
    _require(type(value) is str and date.fromisoformat(value).isoformat() == value, "canonical ISO session required")
    return value


def _pin(value):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "SHA-256 reference required")


def _calendar(values):
    _require(type(values) is list and bool(values) and all(_iso(value) for value in values)
        and values == sorted(set(values)), "ordered unique ISO calendar required")
    return values


def cache_geometry(calendar_dates: list[str], formation_dates: tuple[str, ...], lookback: int) -> dict:
    """Pure calendar union; explicit independent geometry supports synthetic tests."""
    calendar = _calendar(calendar_dates)
    _require(type(formation_dates) is tuple and bool(formation_dates) and type(lookback) is int and lookback > 0,
             "immutable formations and positive session lookback required")
    iso_formations = []
    for day in formation_dates:
        _require(type(day) is str and re.fullmatch(r"[0-9]{8}", day), "compact formation date required")
        iso_formations.append(date.fromisoformat(day).isoformat())
    _require(iso_formations == sorted(set(iso_formations)), "ordered unique formations required")
    positions = {day: index for index, day in enumerate(calendar)}
    histories = {}
    for compact, day in zip(formation_dates, iso_formations):
        _require(day in positions and positions[day] >= lookback, "formation lacks full preceding session window")
        histories[compact] = [value.replace("-", "") for value in calendar[positions[day] - lookback:positions[day]]]
    history_days = sorted({day for values in histories.values() for day in values})
    return dict(formation_dates=list(formation_dates), lookback_sessions=lookback, history_windows=histories,
        cached_dates=sorted(set(formation_dates) | set(history_days)),
        aq_matrix=[dict(service=service, bas_dd=day) for day in formation_dates for service in probe.SERVICES],
        ar_matrix=[dict(service=service, bas_dd=day) for day in history_days for service in probe.SERVICES[:2]])


def cached_request(day: str, geometry: dict) -> dict | None:
    """AQ, then AR, then preserved BP; no invented AQ/AR body hash."""
    _require(type(day) is str and re.fullmatch(r"[0-9]{8}", day), "compact request day required")
    _require(date.fromisoformat(day).strftime("%Y%m%d") == day, "canonical request day required")
    item = dict(service=SERVICE, bas_dd=day)
    if item in geometry["aq_matrix"]:
        logical = geometry["aq_matrix"].index(item) + 1
        role, number, width, pin = "aq", logical, 2, None
    elif item in geometry["ar_matrix"]:
        logical = geometry["ar_matrix"].index(item) + 1
        role, number, width, pin = ("ar_original" if logical <= 549 else "ar_recovery"), (logical if logical <= 549 else logical - 549), 4, None
    elif day in BP_RESPONSES:
        logical, pin = BP_RESPONSES[day]
        role, number, width = "bp_raw", logical, 2
    else:
        return None
    return dict(**item, role=role, logical_number=logical, number=number,
        filename=f"response-{logical:0{width}d}-{SERVICE}-{day}.json", response_sha256=pin,
        response_pin_requires_receipt=pin is None)


def _bounds(rows):
    result = dict(count=len(rows))
    for key in ("formation_on", "entry_on", "due_on", "candidate_on", "end_on", "original_end_on"):
        values = [row[key] for row in rows if row[key] is not None]
        result[key] = dict(first=min(values) if values else None, last=max(values) if values else None)
    return result


def _metadata(metadata):
    keys = {"schema", "status", "projection_sha256", "exit_scope_sha256", "projection_bytes", "exit_scope_bytes",
        "event_reference_on", "eligibility_definition", "potential_origins", "apr28_groups", "calendar_dates", "calendar_summary",
        "activity_snapshot_sha256", "original_quote_snapshot_sha256", "original_read_scope_sha256", "composite_quote_snapshot_sha256",
        "content_review_completed", "calendar_transported", "origin_count", *FALSE_FLAGS, *ZERO_FIELDS}
    _require(type(metadata) is dict and set(metadata) == keys and metadata["schema"] == "bq-raw-episode-scope-review-v1"
        and metadata["status"] == "planning_metadata_only" and metadata["event_reference_on"] == "2021-04-28"
        and metadata["eligibility_definition"] == "entry_on <= 2021-04-28 <= original_end_on; date geometry only, not book/event eligibility"
        and metadata["content_review_completed"] is False and metadata["calendar_transported"] is True
        and all(metadata[key] is False for key in FALSE_FLAGS)
        and all(type(metadata[key]) is int and metadata[key] == 0 for key in ZERO_FIELDS), "fixed planning-only scope review required")
    for key in ("projection_sha256", "exit_scope_sha256", "activity_snapshot_sha256", "original_quote_snapshot_sha256",
                "original_read_scope_sha256", "composite_quote_snapshot_sha256"):
        _pin(metadata[key])
    _require(all(type(metadata[key]) is int and 0 < metadata[key] <= 16 * 1024**2 for key in ("projection_bytes", "exit_scope_bytes")),
             "bounded source receipt lengths required")
    calendar = _calendar(metadata["calendar_dates"])
    _require(len(calendar) == 1898 and calendar[0] == "2019-01-02" and calendar[-1] == CALENDAR_END and ORIGINAL_END in calendar
        and metadata["calendar_summary"] == dict(count=len(calendar), first=calendar[0], last=calendar[-1])
        and type(metadata["calendar_summary"].get("count")) is int, "original fixed calendar geometry required")
    rows = metadata["potential_origins"]
    _require(type(rows) is list and len(rows) == 2 and type(metadata["origin_count"]) is int and metadata["origin_count"] == 2,
             "exact two LG potential origins required")
    expected = {"D1": ("2021-04-16", "2021-10-21"), "D2": ("2021-04-19", "2021-10-22")}
    arms, isins = [], []
    for row in rows:
        _require(type(row) is dict and set(row) == set(ORIGIN_FIELDS) and type(row["arm"]) is str and row["arm"] in expected,
                 "exact original potential-origin fields required")
        entry, due = expected[row["arm"]]
        _require(row["formation_on"] == "2021-04-15" and row["code"] == "003550" and row["entry_on"] == entry
            and row["due_on"] == due and row["original_end_on"] == ORIGINAL_END
            and row["mode"] == "potential_origin_original_horizon" and row["reason"] == row["state"]
            and row["apr28_original_horizon_eligible"] is True
            and row["isin"] == "KR7003550001", "fixed LG origin identity/dates required")
        for key in ("formation_on", "entry_on", "due_on", "end_on", "original_end_on"):
            _require(_iso(row[key]) in calendar, "origin endpoint outside original calendar")
        _require(row["candidate_on"] is None or _iso(row["candidate_on"]) in calendar, "candidate outside original calendar")
        _require(row["end_on"] == (row["candidate_on"] or ORIGINAL_END) and entry <= row["end_on"] <= ORIGINAL_END
            and all(type(row[key]) is str and bool(row[key]) for key in ("entry_state", "due_state", "state"))
            and (row["candidate_state"] is None or type(row["candidate_state"]) is str), "original potential-origin state mismatch")
        arms.append(row["arm"]); isins.append(row["isin"])
    expected_groups = dict(eligible=_bounds(rows), ineligible=_bounds([]))
    _require(sorted(arms) == ["D1", "D2"] and len(set(isins)) == 1
        and json.dumps(metadata["apr28_groups"], sort_keys=True, allow_nan=False)
            == json.dumps(expected_groups, sort_keys=True, allow_nan=False), "original LG group summary mismatch")
    return calendar, rows


def build_raw_episode_plan(metadata: dict, *, scope_review_sha256: str) -> dict:
    """Return separate unit and acquisition plans; no price or actual exit claim."""
    _pin(scope_review_sha256)
    calendar, origins = _metadata(metadata)
    geometry = cache_geometry(calendar, formation.DATES, 60)
    _require(len(formation.DATES) == 14 and len(geometry["cached_dates"]) == 854 and len(geometry["ar_matrix"]) == 1680
        and not set(formation.DATES) & {row["bas_dd"] for row in geometry["ar_matrix"]}, "fixed AQ/AR cache geometry changed")
    requested = {code: [day for day in calendar if start <= day <= end] for code, start, end in EPISODES}
    _require(all(days and days[0] == start and days[-1] == end for (_, start, end), days in zip(EPISODES, requested.values())),
             "raw diagnostic endpoints must be sessions")
    unit = dict(schema=PLAN_SCHEMA, activity_snapshot_sha256=metadata["activity_snapshot_sha256"],
        base_quote_snapshot_sha256=metadata["composite_quote_snapshot_sha256"],
        calendar_sha256=hashlib.sha256(json.dumps(calendar, separators=(",", ":")).encode()).hexdigest(),
        episodes=[dict(code=code, start=start, end=end) for code, start, end in EPISODES], **UNITS)
    days = sorted({day for values in requested.values() for day in values})
    matrix, cached, new = [], [], []
    for day in days:
        item = dict(service=SERVICE, bas_dd=day.replace("-", ""), targets=[code for code in sorted(requested) if day in requested[code]])
        matrix.append(item)
        previous = cached_request(item["bas_dd"], geometry)
        if previous is None:
            new.append(dict(item, targets=list(item["targets"])))
        else:
            cached.append(dict(**previous, targets=list(item["targets"])))
    counts = dict(calendar_sessions=len(calendar), potential_origins=len(origins), raw_unique_days=len(days),
        raw_code_dates=sum(map(len, requested.values())), cached_responses=len(cached), new_requests=len(new),
        cached_code_dates=sum(len(row["targets"]) for row in cached), new_code_dates=sum(len(row["targets"]) for row in new),
        cache_geometry_unique_days=len(geometry["cached_dates"]))
    counts.update({role + "_responses": sum(row["role"] == role for row in cached) for role in ("aq", "ar_original", "ar_recovery", "bp_raw")})
    acquisition = dict(schema=SCHEMA, status="fixed_price_free_diagnostic_plan", scope_review_sha256=scope_review_sha256,
        activity_snapshot_sha256=unit["activity_snapshot_sha256"], base_quote_snapshot_sha256=unit["base_quote_snapshot_sha256"],
        calendar_sha256=unit["calendar_sha256"], original_end_on=ORIGINAL_END, potential_origins=[dict(row) for row in origins],
        issue_identities=[dict(code=code, market="KOSPI", service=SERVICE) for code, _, _ in EPISODES],
        requested_code_dates=requested, unique_days=days, request_matrix=matrix, cached_requests=cached,
        new_request_matrix=new, cache_geometry=geometry, counts=counts, missing_row_market_fallback_allowed=False,
        episode_end_is_actual_exit=False, original_end_obligation_discharged=False,
        cache_bodies_verified=False, **dict.fromkeys(FALSE_FLAGS, False), **dict.fromkeys(ZERO_FIELDS, 0))
    return dict(unit_plan=unit, acquisition_plan=acquisition)
