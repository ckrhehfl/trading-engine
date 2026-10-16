"""Fabricated policy bytes only; these dates, ISINs and hashes are not evidence."""
from copy import deepcopy

import pytest

from research import activity_cohort_return_policy as module
from research.activity_preflight import digest, encoded


def synthetic_policy():
    return dict(schema=module.SCHEMA, mode=module.MODE, study="BS-v1", approved_on="2026-10-16",
        automatic_subscription_rights_cash="not_modeled", actual_investment_return=False, promotion_allowed=False,
        events=[dict(event_id=event_id, code=code, kind=module.KIND, isin="KR7" + code + "000",
            start=f"{year}-04-03", end=f"{year}-04-06", evidence_ref="synthetic-" + code,
            raw_sha256="d" * 64, review_sha256="e" * 64, coordinate="synthetic-source:certificate-section")
            for event_id, (code, year) in module.EVENT_IDENTITIES.items()])


def synthetic_policy_v2():
    value = synthetic_policy()
    value.update(schema=module.SCHEMA_V2, automatic_stock_dividend_assets="not_modeled")
    for row in value["events"]:
        row["isin"] = module.V2_ISINS[row["code"]]
    value["events"].append(dict(event_id=module.STOCK_DIVIDEND_EVENT_ID, code="006800",
        kind=module.STOCK_DIVIDEND_KIND, isin=module.V2_ISINS["006800"], start="2026-03-17", end="2026-03-17",
        date_role=module.STOCK_DIVIDEND_DATE_ROLE, evidence_ref="synthetic-006800", raw_sha256="a" * 64,
        review_sha256="b" * 64, coordinate="synthetic:disclosed record date; delivery and entitlement unverified"))
    return value


def test_absent_policy_keeps_old_output_empty_and_valid_policy_is_pure(monkeypatch):
    assert module.decode_return_policy(None) is None and module.policy_report(None) == {}
    value = synthetic_policy()
    before = deepcopy(value)
    monkeypatch.setattr("builtins.open", lambda *a, **k: pytest.fail("unexpected policy IO"))
    body = encoded(value)
    assert module.decode_return_policy(body) == value == before
    assert module.policy_report(body) == dict(return_measure=module.MODE, return_policy_sha256=digest(body),
        automatic_subscription_rights_cash="not_modeled", actual_investment_return=False,
        unmodeled_rights_codes=["003670", "005070", "006400"],
        unmodeled_rights_event_ids=sorted(module.EVENT_IDENTITIES), promotion_allowed=False)


@pytest.mark.parametrize("change", ["mode", "study", "approval", "cash", "actual", "promotion", "extra", "missing",
    "duplicate", "fourth", "fourth_code", "future_id", "future_year", "kind", "isin", "reverse", "date", "raw", "review", "ref", "coordinate"])
def test_policy_refuses_scope_or_claim_expansion(change):
    value = synthetic_policy()
    row = value["events"][0]
    if change == "mode": value["mode"] = "total_return"
    elif change == "study": value["study"] = "BR-v1"
    elif change == "approval": value["approved_on"] = "2026-10-17"
    elif change == "cash": value["automatic_subscription_rights_cash"] = "zero"
    elif change == "actual": value["actual_investment_return"] = 0
    elif change == "promotion": value["promotion_allowed"] = True
    elif change == "extra": value["future_events_allowed"] = True
    elif change == "missing": value["events"].pop()
    elif change == "duplicate": value["events"][1] = deepcopy(row)
    elif change == "fourth": value["events"].append(deepcopy(row))
    elif change == "fourth_code": row["code"] = "005930"
    elif change == "future_id": row["event_id"] = "cosmo-2028-subscription-rights"
    elif change == "future_year": row["start"], row["end"] = "2028-04-03", "2028-04-06"
    elif change == "kind": row["kind"] = "voluntary_offer"
    elif change == "isin": row["isin"] = "unknown"
    elif change == "reverse": row["start"], row["end"] = row["end"], row["start"]
    elif change == "date": row["start"] = "20230403"
    elif change == "raw": row["raw_sha256"] = "missing"
    elif change == "review": row["review_sha256"] = "missing"
    elif change == "ref": row["evidence_ref"] = ""
    else: row["coordinate"] = " source "
    with pytest.raises(ValueError): module.policy_report(encoded(value))


def test_policy_original_bytes_and_duplicate_json_fields_are_not_replaced():
    value = synthetic_policy()
    compact, spaced = encoded(value), encoded(value) + b" "
    assert module.policy_report(compact)["return_policy_sha256"] != module.policy_report(spaced)["return_policy_sha256"]
    with pytest.raises(ValueError): module.decode_return_policy(value)
    with pytest.raises(ValueError): module.decode_return_policy(b'{"mode":1,"mode":2}')


def test_v2_is_exactly_the_named_four_assets_with_explicit_unknown_stock_dividend():
    policy = synthetic_policy_v2()
    body = encoded(policy)
    assert module.decode_return_policy(body) == policy
    result = module.policy_report(body)
    assert result == {**module.declared_policy_report(module.SCHEMA, digest(body)),
        "return_policy_schema": module.SCHEMA_V2, "automatic_stock_dividend_assets": "not_modeled",
        "unmodeled_asset_codes": ["003670", "005070", "006400", "006800"],
        "unmodeled_asset_event_ids": sorted(module.V2_EVENT_IDENTITIES)}
    assert module.attribution_codes(body) == ("003670", "005070", "006400", "006800")
    assert module.attribution_codes(None) == module.attribution_codes(encoded(synthetic_policy())) == (
        "003670", "005070", "006400")


@pytest.mark.parametrize("change", ["schema_downgrade", "schema_upgrade_without_event", "asset_zero", "missing_asset_flag",
    "missing_event", "fifth_event", "duplicate", "kind", "code", "isin", "certificate_isin", "year", "date", "interval",
    "date_role", "missing_date_role", "raw", "review", "coordinate", "unknown_event", "extra_field"])
def test_v2_does_not_expand_to_other_assets_or_claim_record_date_as_delivery(change):
    value = synthetic_policy_v2()
    row = value["events"][-1]
    if change == "schema_downgrade": value["schema"] = module.SCHEMA
    elif change == "schema_upgrade_without_event":
        value = synthetic_policy(); value.update(schema=module.SCHEMA_V2, automatic_stock_dividend_assets="not_modeled")
    elif change == "asset_zero": value["automatic_stock_dividend_assets"] = "zero"
    elif change == "missing_asset_flag": value.pop("automatic_stock_dividend_assets")
    elif change == "missing_event": value["events"].pop()
    elif change == "fifth_event": value["events"].append(deepcopy(row))
    elif change == "duplicate": value["events"][0] = deepcopy(row)
    elif change == "kind": row["kind"] = module.KIND
    elif change == "code": row["code"] = "005930"
    elif change == "isin": row["isin"] = "KR7006800001"
    elif change == "certificate_isin": value["events"][0]["isin"] = "KR7005070001"
    elif change == "year": row["start"] = row["end"] = "2025-03-17"
    elif change == "date": row["start"] = row["end"] = "2026-03-18"
    elif change == "interval": row["end"] = "2026-03-18"
    elif change == "date_role": row["date_role"] = "verified_delivery"
    elif change == "missing_date_role": row.pop("date_role")
    elif change in ("raw", "review"): row[change + "_sha256"] = "missing"
    elif change == "coordinate": row["coordinate"] = ""
    elif change == "unknown_event": row["event_id"] = "mirae-2027-stock-dividend"
    else: row["all_future_events_allowed"] = True
    with pytest.raises(ValueError): module.policy_report(encoded(value))
