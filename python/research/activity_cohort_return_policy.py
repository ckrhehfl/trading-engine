"""Pinned BS-only price-return conventions; no asset valuation or source IO.

V1 retains the three named certificates; v2 adds only the separately approved
Mirae2026 stock dividend. The disclosed record date is not certified delivery or
entitlement. Unmodeled assets remain unknown, never observed zero. Declarations
establish structural consistency, not source truth or investment total return.
"""
from __future__ import annotations

from datetime import date
import re

from research.activity_preflight import digest, require, strict_json


SCHEMA = "activity-cohort-return-policy-v1"
SCHEMA_V2 = "activity-cohort-return-policy-v2"
MODE = "price_return_exploratory"
KIND = "automatic_subscription_rights_certificate"
STOCK_DIVIDEND_KIND = "automatic_stock_dividend"
STOCK_DIVIDEND_EVENT_ID = "mirae-2026-stock-dividend"
STOCK_DIVIDEND_DATE_ROLE = "disclosed_record_date_only"
EVENT_IDENTITIES = {
    "cosmo-2023-subscription-rights": ("005070", 2023),
    "posco-future-m-2025-subscription-rights": ("003670", 2025),
    "samsung-sdi-2025-subscription-rights": ("006400", 2025),
}
V2_EVENT_IDENTITIES = {**EVENT_IDENTITIES, STOCK_DIVIDEND_EVENT_ID: ("006800", 2026)}
V2_ISINS = {"005070": "KR7005070008", "003670": "KR7003670007",
    "006400": "KR7006400006", "006800": "KR7006800007"}


def decode_return_policy(policy_json: bytes | None) -> dict | None:
    """Validate exact versioned events and their pinned inclusive evidence periods."""
    if policy_json is None:
        return None
    require(type(policy_json) is bytes, "original return policy bytes required")
    policy = strict_json(policy_json)
    require(type(policy) is dict, "exact BS return policy object required")
    v2 = policy.get("schema") == SCHEMA_V2
    require(set(policy) == {
        "schema", "mode", "study", "approved_on", "automatic_subscription_rights_cash",
        "actual_investment_return", "promotion_allowed", "events"} | ({"automatic_stock_dividend_assets"} if v2 else set()),
        "exact BS return policy fields required")
    require(policy["schema"] in (SCHEMA, SCHEMA_V2) and policy["mode"] == MODE and policy["study"] == "BS-v1"
        and policy["approved_on"] == "2026-10-16" and policy["automatic_subscription_rights_cash"] == "not_modeled"
        and policy["actual_investment_return"] is False and policy["promotion_allowed"] is False,
        "fixed human-approved BS price-return convention required")
    require(not v2 or policy["automatic_stock_dividend_assets"] == "not_modeled",
        "named stock-dividend shares and fractional cash remain unmodeled")
    identities = V2_EVENT_IDENTITIES if v2 else EVENT_IDENTITIES
    require(type(policy["events"]) is list and len(policy["events"]) == len(identities),
        "exact versioned named unmodeled asset events required")
    seen, references = set(), set()
    for row in policy["events"]:
        stock_dividend = type(row) is dict and row.get("event_id") == STOCK_DIVIDEND_EVENT_ID
        require(type(row) is dict and set(row) == {"event_id", "kind", "code", "isin", "start", "end",
            "evidence_ref", "raw_sha256", "review_sha256", "coordinate"} | ({"date_role"} if stock_dividend else set()),
            "exact pinned unmodeled asset fields required")
        event_id = row["event_id"]
        require(type(event_id) is str and event_id in identities and event_id not in seen,
            "unregistered or duplicate unmodeled asset event")
        code, year = identities[event_id]
        require(row["code"] == code and row["kind"] == (STOCK_DIVIDEND_KIND if stock_dividend else KIND)
            and type(row["isin"]) is str
            and re.fullmatch(r"[A-Z]{2}[0-9A-Z]{9}[0-9]", row["isin"]) is not None,
            "fixed unmodeled asset issue and kind required")
        require(not v2 or row["isin"] == V2_ISINS[code], "fixed v2 named issue ISIN required")
        for key in ("start", "end"):
            require(type(row[key]) is str, "canonical inclusive certificate date required")
            day = date.fromisoformat(row[key])
            require(str(day) == row[key] and day.year == year, "named historical certificate year required")
        require(row["start"] <= row["end"], "reversed certificate interval")
        require(not stock_dividend or (row["start"] == row["end"] == "2026-03-17"
            and row["date_role"] == STOCK_DIVIDEND_DATE_ROLE),
            "stock dividend uses only its disclosed record date, not proven delivery or entitlement")
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


def declared_policy_report(schema: str, policy_sha256: str) -> dict:
    """Label an exact registered version before reading its pinned bytes."""
    require(schema in (SCHEMA, SCHEMA_V2), "registered return policy version required")
    result = dict(return_measure=MODE, return_policy_sha256=policy_sha256,
        automatic_subscription_rights_cash="not_modeled", actual_investment_return=False,
        unmodeled_rights_codes=sorted(code for code, _ in EVENT_IDENTITIES.values()),
        unmodeled_rights_event_ids=sorted(EVENT_IDENTITIES), promotion_allowed=False)
    if schema == SCHEMA_V2:
        result.update(return_policy_schema=SCHEMA_V2, automatic_stock_dividend_assets="not_modeled",
            unmodeled_asset_codes=sorted(code for code, _ in V2_EVENT_IDENTITIES.values()),
            unmodeled_asset_event_ids=sorted(V2_EVENT_IDENTITIES))
    return result


def policy_report(policy_json: bytes | None) -> dict:
    """Leave legacy outputs untouched; label every supplied valid convention."""
    policy = decode_return_policy(policy_json)
    return {} if policy is None else declared_policy_report(policy["schema"], digest(policy_json))


def attribution_codes(policy_json: bytes | None) -> tuple[str, ...]:
    """Keep the original three-issuer diagnostic unless v2 is explicitly bound."""
    policy = decode_return_policy(policy_json)
    identities = V2_EVENT_IDENTITIES if policy is not None and policy["schema"] == SCHEMA_V2 else EVENT_IDENTITIES
    return tuple(sorted(code for code, _ in identities.values()))
