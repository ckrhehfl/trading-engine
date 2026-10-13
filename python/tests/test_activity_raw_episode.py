"""Only fabricated source strings; no input files, DB, network or raw producer."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from decimal import Decimal, localcontext
from pathlib import Path
import socket
import sqlite3

import pytest

from research import activity_raw_episode as module

RETRIEVED = "2026-10-10T00:00:00+00:00"
PIN = "a" * 64
ITEM = dict(service="stk_bydd_trd", bas_dd="20210527", targets=["003550", "383800"])


def row(code="003550", **changes):
    return dict(ISU_CD=code, BAS_DD="20210527", TDD_OPNPRC="+00100.00", TDD_HGPRC="110", TDD_LWPRC="90",
        TDD_CLSPRC="105", ACC_TRDVOL="9,007,199,254,740,993", ACC_TRDVAL="123456789012345678901234567890.125", **changes)


def decode(value):
    return module.extract_raw_episode_targets([value], {**ITEM, "targets": ["003550"]}, RETRIEVED, PIN)[0]


def test_exact_raw_texts_large_amounts_metadata_and_immutable_output_without_io(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail("decoder attempted IO")
    for owner, name in ((Path, "open"), (sqlite3, "connect"), (socket, "create_connection")):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    value = row(); before = deepcopy(value)
    with localcontext() as context:
        context.prec = 3
        result = decode(value)
    assert value == before and result.state == result.status == "observed"
    assert dict(result.original_texts)["TDD_OPNPRC"] == "+00100.00"
    assert dict(result.canonical_decimal_strings)["TDD_OPNPRC"] == "100"
    assert result.volume == Decimal("9007199254740993") and result.turnover == Decimal("123456789012345678901234567890.125")
    assert result.observation_date == "2021-05-27" and result.row_position == 0 and result.row_positions == (0,)
    assert result.available_at is result.source_public_available_at is result.modeled_available_at is result.is_final is None
    assert result.availability_assumption == "not_applied_by_raw_decoder"
    assert result.source == "KRX OpenAPI stk_bydd_trd" and result.data_vintage == "current_retrieval_sha256:" + PIN
    assert set(result.provenance) >= {"observation_date", "available_at", "retrieved_at", "source", "is_final", "data_vintage", "evidence_level"}
    assert (result.price_unit, result.volume_unit, result.turnover_unit) == ("KRW_per_raw_share", "raw_shares", "KRW")
    assert result.source_truth_certified is result.historical_publication_certified is False
    assert all(not isinstance(getattr(result, field.name), float) for field in fields(result))
    with pytest.raises(FrozenInstanceError): result.close = Decimal("1")
    metadata = result.provenance; metadata["source"] = "changed"
    assert result.provenance["source"] == "KRX OpenAPI stk_bydd_trd"
    assert "MKTCAP" not in dict(result.original_texts) and not hasattr(result, "typed_values")
    assert module.validate_raw_observation(result) is result


@pytest.mark.parametrize("activity,state", [("1", "locked"), ("0", "frozen")])
def test_positive_flat_legacy_proxy_never_claims_halt(activity, state):
    value = row()
    value.update({field: "100" for field in module.RAW_FIELDS[:4]})
    value.update(ACC_TRDVOL=activity, ACC_TRDVAL=activity)
    result = decode(value)
    assert result.status == "observed" and result.state == state and result.close == 100
    assert result.market_state_cause == "unknown"
    assert module.validate_raw_observation(result) is result


def test_observed_all_zero_source_bar_keeps_zero_texts_but_no_prices():
    value = row(); value.update({field: "0.00" for field in module.RAW_FIELDS})
    result = decode(value)
    assert result.status == "observed" and result.state == "observed_no_trade_zero_prices"
    assert all(getattr(result, name) is None for name in ("open", "high", "low", "close"))
    assert result.volume == result.turnover == 0 and result.market_state_cause == "unknown"
    assert dict(result.original_texts) == dict.fromkeys(module.RAW_FIELDS, "0.00")
    assert dict(result.canonical_decimal_strings) == dict.fromkeys(module.RAW_FIELDS, "0")
    assert module.validate_raw_observation(result) is result


@pytest.mark.parametrize("field", module.RAW_FIELDS)
@pytest.mark.parametrize("invalid", [None, "NULL", "", "-", "-1", "-0", "NaN", "Infinity", "1e3", "1,00", " 1", 1.5, 1, True])
def test_invalid_source_numbers_are_unresolved_not_zero_or_float(field, invalid):
    value = row(); value[field] = invalid
    result = decode(value)
    assert result.status == result.state == "unresolved" and result.observation_date is None and result.issues
    assert all(getattr(result, name) is None for name in ("open", "high", "low", "close", "volume", "turnover"))
    assert dict(result.canonical_decimal_strings)[field] is None
    assert dict(result.original_texts)[field] == (invalid if type(invalid) is str else None)
    assert module.validate_raw_observation(result) is result


@pytest.mark.parametrize("changes", [dict(TDD_OPNPRC="0"), dict(TDD_HGPRC="99"), dict(TDD_LWPRC="106"),
    dict(ACC_TRDVOL="0"), dict(ACC_TRDVAL="0"), dict(ACC_TRDVOL="0", ACC_TRDVAL="0"),
    {**dict.fromkeys(module.RAW_FIELDS[:4], "0"), "ACC_TRDVOL": "1"}])
def test_mixed_zero_or_inconsistent_bar_is_unresolved(changes):
    value = row(); value.update(changes)
    result = decode(value)
    assert result.state == "unresolved" and result.close is None and result.issues
    assert module.validate_raw_observation(result) is result


def test_absent_duplicate_mismatched_date_and_target_order_are_preserved():
    values = [row("383800"), row(), row()]
    first, second = module.extract_raw_episode_targets(values, ITEM, RETRIEVED, PIN)
    assert first.code == "003550" and first.state == "unresolved" and first.issues == ("duplicate_target",)
    assert first.row_position is None and first.row_positions == (1, 2) and first.observation_date is None
    assert second.code == "383800" and second.row_position == 0 and second.status == "observed"
    absent = module.extract_raw_episode_targets([], ITEM, RETRIEVED, PIN)
    assert all(value.issues == ("target_absent_cause_unknown",) and value.observation_date is None for value in absent)
    value = row(); value["BAS_DD"] = "20210526"
    mismatch = decode(value)
    assert mismatch.returned_bas_dd == "20210526" and mismatch.observation_date is None
    assert mismatch.issues == ("observation_date_mismatch",)
    value["BAS_DD"] = "20210527"
    direct = module.decode_raw_episode_row(value, code="383800", bas_dd="20210527", retrieved_at=RETRIEVED,
        response_sha256=PIN, row_position=5)
    assert direct.issues == ("issue_code_mismatch",) and direct.close is None
    for result in (first, second, *absent, mismatch, direct):
        assert module.validate_raw_observation(result) is result


@pytest.mark.parametrize("mutation", ["code", "duplicate", "service", "date", "retrieval", "pin", "extra", "row"])
def test_invalid_request_or_provenance_contract_raises_without_io(mutation):
    item, retrieved, pin, rows = deepcopy(ITEM), RETRIEVED, PIN, [row()]
    if mutation == "code": item["targets"] = ["000001"]
    elif mutation == "duplicate": item["targets"] = ["003550", "003550"]
    elif mutation == "service": item["service"] = "stk_isu_base_info"
    elif mutation == "date": item["bas_dd"] = "20210230"
    elif mutation == "retrieval": retrieved = "2026-10-10T00:00:00"
    elif mutation == "pin": pin = "unknown"
    elif mutation == "extra": item["limit"] = 1
    else: rows = [None]
    with pytest.raises(ValueError): module.extract_raw_episode_targets(rows, item, retrieved, pin)


@pytest.mark.parametrize("changes", [dict(close=Decimal("0")), dict(close=105.0), dict(close=Decimal("NaN")),
    dict(volume=9007199254740993), dict(row_position=True), dict(row_positions=(True,)), dict(state="locked"),
    dict(available_at=RETRIEVED), dict(is_final=True), dict(price_unit="adjusted"), dict(source_truth_certified=0),
    dict(historical_publication_certified=True), dict(issues=[]), dict(original_texts=[]),
    dict(canonical_decimal_strings=tuple((field, "1") for field in module.RAW_FIELDS))])
def test_forged_dataclass_does_not_bypass_source_text_recomputation(changes):
    with pytest.raises(ValueError): module.validate_raw_observation(replace(decode(row()), **changes))


def test_forged_unresolved_selection_cannot_supply_price_or_false_positions():
    duplicate, absent = module.extract_raw_episode_targets([row(), row()], ITEM, RETRIEVED, PIN)
    for original, changes in ((duplicate, dict(close=Decimal("100"))), (duplicate, dict(row_positions=(0, 0))),
                              (absent, dict(row_positions=(0,))), (duplicate, dict(issues=("target_absent_cause_unknown",)))):
        with pytest.raises(ValueError): module.validate_raw_observation(replace(original, **changes))
