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
