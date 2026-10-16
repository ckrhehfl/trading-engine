"""Pinned BS-only price-return convention; no rights valuation or source IO.

The three named historical certificates remain acknowledged assets whose cash
is not modeled. These declarations establish structural consistency, never
source truth, actual investment return, or permission to promote a strategy.
"""
from __future__ import annotations

from datetime import date
import re

from research.activity_preflight import digest, require, strict_json


SCHEMA = "activity-cohort-return-policy-v1"
MODE = "price_return_exploratory"
KIND = "automatic_subscription_rights_certificate"
EVENT_IDENTITIES = {
    "cosmo-2023-subscription-rights": ("005070", 2023),
    "posco-future-m-2025-subscription-rights": ("003670", 2025),
    "samsung-sdi-2025-subscription-rights": ("006400", 2025),
}


def decode_return_policy(policy_json: bytes | None) -> dict | None:
    """Validate exact named events and their pinned inclusive evidence periods."""
    if policy_json is None:
        return None
    require(type(policy_json) is bytes, "original return policy bytes required")
    policy = strict_json(policy_json)
    require(type(policy) is dict and set(policy) == {
        "schema", "mode", "study", "approved_on", "automatic_subscription_rights_cash",
        "actual_investment_return", "promotion_allowed", "events"}, "exact BS return policy fields required")
    require(policy["schema"] == SCHEMA and policy["mode"] == MODE and policy["study"] == "BS-v1"
        and policy["approved_on"] == "2026-10-16" and policy["automatic_subscription_rights_cash"] == "not_modeled"
        and policy["actual_investment_return"] is False and policy["promotion_allowed"] is False,
        "fixed human-approved BS price-return convention required")
    require(type(policy["events"]) is list and len(policy["events"]) == len(EVENT_IDENTITIES),
        "exactly three named automatic certificate events required")
    seen, references = set(), set()
    for row in policy["events"]:
        require(type(row) is dict and set(row) == {"event_id", "kind", "code", "isin", "start", "end",
            "evidence_ref", "raw_sha256", "review_sha256", "coordinate"}, "exact pinned certificate fields required")
        event_id = row["event_id"]
        require(type(event_id) is str and event_id in EVENT_IDENTITIES and event_id not in seen,
            "unregistered or duplicate certificate event")
        code, year = EVENT_IDENTITIES[event_id]
        require(row["code"] == code and row["kind"] == KIND and type(row["isin"]) is str
            and re.fullmatch(r"[A-Z]{2}[0-9A-Z]{9}[0-9]", row["isin"]) is not None,
            "fixed certificate issue and kind required")
        for key in ("start", "end"):
            require(type(row[key]) is str, "canonical inclusive certificate date required")
            day = date.fromisoformat(row[key])
            require(str(day) == row[key] and day.year == year, "named historical certificate year required")
        require(row["start"] <= row["end"], "reversed certificate interval")
        for key in ("raw_sha256", "review_sha256"):
            require(type(row[key]) is str and re.fullmatch(r"[0-9a-f]{64}", row[key]) is not None,
                "pinned certificate source and review required")
        for key in ("evidence_ref", "coordinate"):
            require(type(row[key]) is str and bool(row[key]) and row[key].strip() == row[key],
                "exact certificate evidence coordinate required")
        require(row["evidence_ref"] not in references, "distinct certificate evidence references required")
        seen.add(event_id)
        references.add(row["evidence_ref"])
    return policy


def policy_report(policy_json: bytes | None) -> dict:
    """Leave legacy outputs untouched; label every supplied valid convention."""
    policy = decode_return_policy(policy_json)
    if policy is None:
        return {}
    return dict(return_measure=MODE, return_policy_sha256=digest(policy_json),
        automatic_subscription_rights_cash="not_modeled", actual_investment_return=False,
        unmodeled_rights_codes=sorted(row["code"] for row in policy["events"]),
        unmodeled_rights_event_ids=sorted(row["event_id"] for row in policy["events"]), promotion_allowed=False)
