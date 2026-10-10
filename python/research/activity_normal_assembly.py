"""Pure BL connection of a bounded BK decision to the unchanged normal screen.

Inputs are supplied by the separately registered reader. This module performs
no IO and cannot establish original-source truth, historical publication, a
portfolio selection, price/action coverage or a performance result. A BK
acceptance remains an inferred historical classification, not an original-
vintage certificate. KIS activity and official KRX liquidity remain separate.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import re

from research.activity_accounting import IdentityPeriod
from research.activity_screen import (
    PublishedValue, ScreenObservation, SyntheticListing, _observation, _published, screen_synthetic,
)
from research.activity_timing import SessionLagPolicy


def _day(value: object, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO date") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be an ISO date")
    return parsed


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be explicit nonempty text")
    return value


def _json(value: object) -> object:
    """Retain exact Decimal strings and every supplied provenance field."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def _window(
    accepted_record: dict, *, formation: date, code: str, isin: str,
) -> dict:
    if (not isinstance(accepted_record, dict) or accepted_record.get("code") != code
            or accepted_record.get("decision") != "accepted"
            or accepted_record.get("continuity_evidence_level") != "inferred"):
        raise ValueError("BK exact-code accepted inferred decision is required")
    windows = accepted_record.get("required_windows")
    if not isinstance(windows, list) or any(not isinstance(row, dict) for row in windows):
        raise ValueError("BK required_windows must be explicit records")
    same_formation = [row for row in windows if row.get("formation") == formation.isoformat()]
    if len(same_formation) != 1:
        raise ValueError("BK requires one unique formation/code/ISIN window")
    row = same_formation[0]
    if (row.get("window_id") != f"{formation:%Y%m%d}/{code}" or row.get("isin") != isin
            or ("code" in row and row["code"] != code)):
        raise ValueError("BK formation/code/ISIN identity mismatch")
    provenance = row.get("krx_metadata_provenance")
    if (not isinstance(provenance, dict) or provenance.get("code") != code
            or provenance.get("formation") != formation.strftime("%Y%m%d")):
        raise ValueError("BK dated KRX identity provenance mismatch")
    return row


def accepted_period(
    accepted_record: dict, *, formation: date, code: str, isin: str,
    required_dates: tuple[date, ...],
) -> IdentityPeriod:
    """Connect one exact BK window; never broaden its adjudicated boundaries."""
    if (type(formation) is not date or not required_dates
            or any(type(day) is not date for day in required_dates)
            or tuple(sorted(set(required_dates))) != required_dates
            or required_dates[-1] != formation):
        raise ValueError("required activity dates must be unique, sorted and end at formation")
    _text(code, "code")
    _text(isin, "ISIN")
    window = _window(accepted_record, formation=formation, code=code, isin=isin)
    source_dates = window.get("required_dates")
    if (not isinstance(source_dates, list)
            or tuple(_day(day, "BK required date") for day in source_dates) != required_dates):
        raise ValueError("BK required dates differ from the exact consumed activity dates")
    start = _day(window.get("effective_start_for_this_adjudication"), "BK bounded start")
    end = _day(window.get("effective_end_exclusive"), "BK bounded end")
    if start != required_dates[0] or end != formation + timedelta(days=1):
        raise ValueError("BK bounded interval must exactly span oldest through formation")
    known_on = _day(accepted_record.get("known_on_conservative_candidate"), "BK known_on")
    if known_on > formation or window.get("known_before_formation") is not True:
        raise ValueError("BK positive classification evidence must be known by formation")
    raw = accepted_record.get("raw_sha256")
    readable = accepted_record.get("readable_sha256")
    if any(not isinstance(pin, str) or re.fullmatch(r"[0-9a-f]{64}", pin) is None
           for pin in (raw, readable)):
        raise ValueError("BK raw/readable evidence pins are required")
    receipt = _text(accepted_record.get("receipt"), "BK receipt")
    _text(accepted_record.get("source_url"), "BK source URL")
    _text(accepted_record.get("rationale"), "BK decision rationale")
    passages = accepted_record.get("passages")
    if not isinstance(passages, list) or not passages:
        raise ValueError("BK positive raw/readable passages are required")
    for passage in passages:
        if (not isinstance(passage, dict)
                or type(passage.get("readable_line")) is not int
                or passage["readable_line"] < 1
                or passage.get("literal_text_present_in_raw_html") is not True):
            raise ValueError("BK positive raw/readable passage coordinates are required")
        _text(passage.get("text"), "BK passage text")
    return IdentityPeriod(code, start, end, known_on, True,
                          f"BK bounded inferred decision {receipt}; raw={raw}; readable={readable}")


def _reasons(candidate: dict | None) -> list[str]:
    if candidate is None:
        return ["formation_frozen"]
    reasons = []
    if candidate["classification"] != "operating_common":
        reasons.append("classification_excluded")
    if candidate["size_pass"] is not True:
        reasons.append("capitalization_below_floor")
    for key in ("absolute_liquidity", "operating_baseline"):
        if candidate[key] != "pass":
            reasons.append(f"{key}_{candidate[key]}")
    if candidate["activity_pass"] is not True:
        reasons.append("activity_predicate_failed")
    return reasons


def assemble_window(
    calendar: tuple[date, ...], formation: date, code: str, isin: str,
    listing_date: date, observations: Mapping[date, ScreenObservation],
    capitalization: PublishedValue, liquidity: Mapping[date, ScreenObservation],
    accepted_record: dict, params: dict,
) -> dict:
    """Return both fixed timing arms for one already partitioned possible window.

    ``selected`` means passing this window's normal-screen predicates only; it
    is not a portfolio target or filled entry. The reader must preserve the
    full source population and all other proof/control partitions separately.
    Listing availability uses the formation source's conservative assumption;
    it does not claim an original listing-notice publication timestamp.
    """
    SessionLagPolicy._calendar(calendar)
    if type(calendar) is not tuple or type(formation) is not date or formation not in calendar:
        raise ValueError("an explicit formation in the immutable calendar is required")
    index = calendar.index(formation)
    if index < 60:
        raise ValueError("calendar cannot supply the fixed preceding 60-session liquidity window")
    if not isinstance(observations, Mapping) or not observations:
        raise ValueError("the exact window's activity observations are required")
    if any(type(day) is not date for day in observations):
        raise ValueError("activity observation keys must be dates")
    required_dates = tuple(sorted(observations))
    if required_dates[0] not in calendar:
        raise ValueError("activity interval begins outside the supplied calendar")
    if required_dates != calendar[calendar.index(required_dates[0]):index + 1]:
        raise ValueError("activity dates must exactly cover the oldest-through-formation interval")
    period = accepted_period(accepted_record, formation=formation, code=code, isin=isin,
                             required_dates=required_dates)
    if type(listing_date) is not date or listing_date > required_dates[0]:
        raise ValueError("source listing bound must precede every required activity observation")
    if not isinstance(capitalization, PublishedValue):
        raise ValueError("a separate official capitalization input is required")
    liquidity_dates = calendar[index - 60:index]
    if (not isinstance(liquidity, Mapping) or any(type(day) is not date for day in liquidity)
            or tuple(sorted(liquidity)) != liquidity_dates):
        raise ValueError("official liquidity must contain exactly the preceding 60 sessions")
    if (not isinstance(params, dict) or isinstance(params.get("threshold"), bool)
            or not isinstance(params.get("threshold"), (int, Decimal))):
        raise ValueError("activity threshold must be an exact integer or Decimal")
    listing = SyntheticListing(listing_date, formation)
    arms = {}
    for lag in (1, 2):
        policy = SessionLagPolicy(lag)
        cutoff = policy.selection_at(calendar, index)
        # Every supplied consumed value retains provenance and satisfies each
        # cutoff, even if another failed predicate would remove the candidate.
        for source in (observations, liquidity):
            for day in source:
                _observation(source, day, cutoff, require_metadata=True)
        _published(capitalization, formation, cutoff, require_metadata=True)
        screen = screen_synthetic(
            calendar, index, params, {code: observations}, {code: liquidity},
            {code: capitalization}, (period,), {code: listing},
            liquidity_lookback=60, liquidity_floor=Decimal("10000000000"),
            capitalization_floor=Decimal("5000000000000"), decision_at=cutoff,
            timing_policy=policy,
        )
        candidate = _json(asdict(screen.selection.candidates[0])) if screen.selection.candidates else None
        diagnostic = (_json(asdict(screen.diagnostics[0])) if screen.diagnostics else {
            "code": code, "normal_dates": [], "skipped_frozen_dates": [],
            "liquidity_dates": [], "liquidity_zero_count": 0, "liquidity_frozen_count": 0,
            "activity_median": None, "activity_ratio": None, "liquidity_median": None,
        })
        reasons = _reasons(candidate)
        arms[f"D{lag}"] = {
            "session_lag": lag, "decision_on": screen.selection.decision_on.isoformat(),
            "selection_at": cutoff.isoformat(), "candidate": candidate,
            **diagnostic, "selected": not reasons, "exclusion_reasons": reasons,
            "every_consumed_input_available_by_selection": True,
        }
    comparable = ("candidate", "normal_dates", "skipped_frozen_dates", "liquidity_dates",
                  "liquidity_zero_count", "liquidity_frozen_count", "activity_median",
                  "activity_ratio", "liquidity_median", "selected", "exclusion_reasons")
    if any(arms["D1"][key] != arms["D2"][key] for key in comparable):
        raise ValueError("paired normal-screen inputs unexpectedly changed eligibility or dates")
    return {
        "formation": formation.isoformat(), "code": code, "isin": isin,
        "identity_period": _json(asdict(period)),
        "classification_evidence_level": "inferred",
        "original_vintage_certificate": False,
        "accepted_evidence": _json(deepcopy(accepted_record)),
        "listing": {
            **_json(asdict(listing)), "evidence_level": "assumed",
            "known_on_basis": "formation-source timing assumption; original publication unknown",
            "source_provenance": _json(deepcopy(_window(
                accepted_record, formation=formation, code=code, isin=isin,
            )["krx_metadata_provenance"])),
        },
        "inputs": {
            "activity": {day.isoformat(): _json(asdict(row)) for day, row in sorted(observations.items())},
            "official_liquidity": {day.isoformat(): _json(asdict(row)) for day, row in sorted(liquidity.items())},
            "official_capitalization": _json(asdict(capitalization)),
        },
        "arms": arms,
        "paired_eligibility_and_dates_equal": True,
        "selection_meaning": "normal-screen eligible signal only; no portfolio or filled entry",
    }
