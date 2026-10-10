"""BL source joins use only hand-built responses; no saved market input is read."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from research import activity_normal_sources as module
from research import activity_preflight as saved
from research.activity_timing import SessionLagPolicy
from test_krx_formation_audit import rows_for_day


DAY = saved.formation.DATES[0]
CALENDAR = (date(2019, 3, 26), date(2019, 3, 27), date(2019, 3, 28),
            date(2019, 4, 2), date(2019, 4, 3), date(2019, 4, 4))


def formation_rows():
    rows = rows_for_day(DAY)
    below = deepcopy(rows[saved.probe.SERVICES[0]][0])
    below.update(ISU_CD="333333", TDD_CLSPRC="1", MKTCAP="100000000")
    basic = deepcopy(rows[saved.probe.SERVICES[2]][0])
    basic.update(ISU_CD="KR7333333003", ISU_SRT_CD="333333")
    rows[saved.probe.SERVICES[0]].append(below)
    rows[saved.probe.SERVICES[2]].append(basic)
    for code, (_, market, group, share, section) in module.NEGATIVE_TYPES.items():
        index = 0 if market == "KOSPI" else 1
        trade = deepcopy(rows[saved.probe.SERVICES[index]][0])
        trade.update(ISU_CD=code, SECT_TP_NM=section)
        basic = deepcopy(rows[saved.probe.SERVICES[index + 2]][0])
        basic.update(ISU_CD=f"KR7{code}003", ISU_SRT_CD=code, SECUGRP_NM=group,
                     KIND_STKCERT_TP_NM=share, SECT_TP_NM=section)
        rows[saved.probe.SERVICES[index]].append(trade)
        rows[saved.probe.SERVICES[index + 2]].append(basic)
    return rows


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Small test geometry; the production receipt/matrix helper is reused elsewhere."""
    monkeypatch.setattr(saved.formation, "DATES", (DAY,))
    monkeypatch.setattr(saved.liquidity, "WINDOW_SESSIONS", 3)
    monkeypatch.setattr(module, "POTENTIAL_WINDOWS", 1)
    totals = {"trade_rows": 7, "cap_pass_rows": 6, "cap_below_rows": 1,
              "cap_pass_common_label_rows": 4}
    monkeypatch.setattr(saved, "AQ_TOTALS", totals)
    declared = {role: {"root": str(tmp_path / role)} for role in ("aq", "ar_original", "ar_recovery")}
    store, raw_rows = {}, {}
    aq_matrix = [{"bas_dd": DAY, "service": service} for service in saved.probe.SERVICES]
    ar_matrix = [{"bas_dd": day.strftime("%Y%m%d"), "service": service}
                 for day in CALENDAR[:3] for service in saved.probe.SERVICES[:2]]
    receipts = {"aq_matrix": aq_matrix, "ar_matrix": ar_matrix,
                "aq": {"observations": []}, "ar_original": {"observations": []},
                "ar_recovery": {"observations": []}}
    formation = formation_rows()

    def publish(role, number, item, rows, aq):
        raw = saved.encoded({"OutBlock_1": rows})
        summary = (saved.formation._summarize if aq else saved.formation.summarize_trading_day)(
            rows, item["service"], item["bas_dd"])
        summary.update(response_sha256=saved.digest(raw), at="2026-10-07T08:31:00+00:00")
        receipts[role]["observations"].append(summary)
        width = 2 if aq else 4
        path = Path(declared[role]["root"]) / (
            f"response-{number:0{width}d}-{item['service']}-{item['bas_dd']}.json")
        store[path] = raw
        raw_rows[path] = rows

    for index, item in enumerate(aq_matrix):
        publish("aq", index + 1, item, formation[item["service"]], True)
    audit = saved.formation.audit_day(DAY, formation)
    receipts["aq"].update(days=[audit], **{key: audit[key] for key in totals if key != "trade_rows"})
    for index, item in enumerate(ar_matrix):
        rows = rows_for_day(item["bas_dd"])[item["service"]]
        if item["service"] == saved.probe.SERVICES[0]:
            rows[0].update(TDD_OPNPRC="50000", TDD_HGPRC="50000", TDD_LWPRC="50000",
                           ACC_TRDVOL="1", ACC_TRDVAL=("12,345,678,901", "0", "10000000000")[index // 2])
        publish("ar_original", index + 1, item, rows, False)
    output = tmp_path / "output"
    output.mkdir()
    reads = []

    def reader(path):
        assert (output / "raw-source-read-scope.json").exists()
        reads.append(path)
        return store[path]

    monkeypatch.setattr(module.cache, "cache_receipts", lambda *args: receipts)
    inputs = saved.PinnedInputs(reader)
    args = (declared, inputs, [{"formation": DAY}], CALENDAR, {(DAY, "111111")}, output)
    return args, receipts, store, raw_rows, reads


def test_complete_population_literal_controls_and_exact_separate_liquidity(harness):
    """Preserve complete source joins and the distinct basic/cap/liquidity coordinates."""
    args, receipts, _, _, reads = harness
    result = module.load_sources(*args)
    assert len(result["population"]) == 7
    assert sum(row["cap_pass"] for row in result["population"]) == 6
    assert len(result["negative_type_controls"]) == 4  # No presumed historical count.
    assert {row["code"] for row in result["negative_type_controls"]} == set(module.NEGATIVE_TYPES)
    for row in result["population"]:
        assert row["actual_classification_known_on"] is None and row["historical_version_unknown"] is True
        for field in ("capitalization_availability", "classification_availability", "trade_section_availability"):
            metadata = row[field]
            assert metadata["observation_date"] == CALENDAR[3].isoformat()
            assert metadata["available_at"] == SessionLagPolicy(1).selection_at(CALENDAR, 3).isoformat()
            assert metadata["retrieved_at"] == "2026-10-07T08:31:00+00:00"
            assert metadata["source_public_available_at"] is None
            assert metadata["evidence_level"] == "assumed" and metadata["is_final"] is None
    source = result["potential_sources"][DAY, "111111"]
    assert source["isin"] == "KR7111111003" and source["listing_date"] == date(2000, 1, 4)
    assert source["capitalization"].value == Decimal("5000000000000")
    assert source["capitalization"].public_available_at is None
    values = source["liquidity"]
    assert set(values) == set(CALENDAR[:3])
    assert values[CALENDAR[0]].turnover == Decimal("12345678901")
    assert values[CALENDAR[1]].state == "frozen" and values[CALENDAR[1]].turnover == 0
    assert values[CALENDAR[2]].state == "observed"
    metadata = values[CALENDAR[0]].availability
    assert metadata.retrieved_at == datetime.fromisoformat("2026-10-07T08:31:00+00:00")
    assert metadata.available_at == SessionLagPolicy(1).selection_at(CALENDAR, 0)
    assert metadata.source_public_available_at is None and metadata.evidence_level == "assumed"
    assert "ACC_TRDVAL" in metadata.source
    assert source["source_provenance"]["historical_version_unknown"] is True
    provenance = source["krx_metadata_provenance"]
    assert provenance == {"formation": DAY, "code": "111111", "source_observation_position": 3,
                          "source_response_sha256": receipts["aq"]["observations"][2]["response_sha256"],
                          "source_row_position": 1, "source_service": "stk_isu_base_info"}
    by_code = {row["code"]: row for row in result["population"]}
    assert {code: (by_code[code]["krx_metadata_provenance"]["source_observation_position"],
                   by_code[code]["krx_metadata_provenance"]["source_row_position"])
            for code in ("111111", "333333", "222222", "950160")} == {
                "111111": (3, 1), "333333": (3, 2), "222222": (4, 1), "950160": (4, 2)}
    # Capitalization and liquidity retain their existing, separate conventions.
    assert source["source_provenance"]["capitalization"]["source_observation_position"] == 0
    assert source["source_provenance"]["capitalization"]["source_row_position"] == 0
    assert source["source_provenance"]["liquidity_rows"][0]["logical_number"] == 1
    assert source["source_provenance"]["liquidity_rows"][0]["source_row_position"] == 0
    assert len(reads) == len(args[1].manifest) == 10
    assert all(entry["read_status"] == "hash_verified" for entry in args[1].manifest)
    scope = saved.strict_json((args[-1] / "raw-source-read-scope.json").read_bytes())
    assert saved.digest(saved.encoded(scope)) == result["raw_source_read_scope_sha256"]
    assert len(scope["aq_raw_responses"]) == 4 and len(scope["ar_raw_responses"]) == 6
    assert scope["liquidity_sessions_by_formation"][DAY] == [d.strftime("%Y%m%d") for d in CALENDAR[:3]]
    assert {item["path"] for item in scope["aq_raw_responses"] + scope["ar_raw_responses"]} == set(map(str, reads))
    assert not any("KIS" in str(row) for row in source["source_provenance"]["liquidity_rows"])


@pytest.mark.parametrize("formation_number,basic_observations", [(1, (3, 4)), (13, (51, 52))])
def test_basic_metadata_matches_independent_one_based_ax_coordinates(formation_number, basic_observations):
    """Literal AX coordinate expectations cover two markets and basic row order."""
    day = saved.formation.DATES[formation_number - 1]
    observed = datetime.strptime(day, "%Y%m%d").date()
    calendar = tuple(observed + timedelta(days=offset) for offset in range(3))
    rows = rows_for_day(day)
    for market, second_code in enumerate(("333333", "444444")):
        trade_service, basic_service = saved.probe.SERVICES[market], saved.probe.SERVICES[market + 2]
        trade, basic = deepcopy(rows[trade_service][0]), deepcopy(rows[basic_service][0])
        trade.update(ISU_CD=second_code, TDD_CLSPRC="1", MKTCAP="100000000")
        basic.update(ISU_CD=f"KR7{second_code}003", ISU_SRT_CD=second_code)
        rows[trade_service].append(trade)
        rows[basic_service].append(basic)
        rows[trade_service].reverse()  # Basic row coordinates must not follow trading row order.
    offset = (formation_number - 1) * 4
    positions = dict(zip(saved.probe.SERVICES, range(offset, offset + 4), strict=True))
    observations = {service: {"response_sha256": f"{index + 1:064x}",
                              "at": "2026-10-07T08:31:00+00:00"}
                    for service, index in positions.items()}
    population = {row["code"]: row for row in module._formation_rows(
        day, rows, observations, positions, calendar)}
    expected = {"111111": (basic_observations[0], 1), "333333": (basic_observations[0], 2),
                "222222": (basic_observations[1], 1), "444444": (basic_observations[1], 2)}
    for code, (observation, row_position) in expected.items():
        provenance = population[code]["krx_metadata_provenance"]
        assert (provenance["source_observation_position"], provenance["source_row_position"]) == (
            observation, row_position)
        assert provenance["source_response_sha256"] == f"{observation:064x}"
    for market, code in enumerate(("111111", "222222")):
        capital = population[code]["krx_capitalization_provenance"]
        assert capital["source_observation_position"] == offset + market
        assert capital["source_row_position"] == 1


@pytest.mark.parametrize("code,field,value", [
    ("005387", "share_class", "보통주"), ("005935", "security_group", "투자회사"),
    ("088980", "security_group", "주권"), ("950160", "basic_section", "중견기업부"),
    ("950160", "trade_section", "중견기업부"), ("999999", "share_class", "구형우선주"),
    ("999999", "security_group", "unknown"), ("999999", "basic_section", "외국기업(소속부없음)"),
])
def test_unknown_or_conflicting_type_is_an_error_not_an_exclusion(code, field, value):
    known = module.NEGATIVE_TYPES.get(code, (None, "KOSPI", "주권", "보통주", ""))
    _, market, group, share, section = known
    labels = {"market": market, "security_group": group, "share_class": share,
              "basic_section": section, "trade_section": section}
    labels[field] = value
    with pytest.raises(ValueError, match="source (labels|type)"):
        module._negative_type(code, labels)


def test_raw_hash_failure_preserves_started_manifest_and_scope(harness):
    args, _, store, _, reads = harness
    first = next(iter(store))
    store[first] = b"changed synthetic bytes"
    with pytest.raises(ValueError, match="hash mismatch"):
        module.load_sources(*args)
    assert reads == [first]
    assert args[1].manifest[-1]["read_status"] == "hash_mismatch"
    assert (args[-1] / "raw-source-read-scope.json").exists()


def test_complete_source_cap_arithmetic_is_audited(harness):
    args, _, _, raw_rows, _ = harness
    path = next(iter(raw_rows))
    raw_rows[path][0]["MKTCAP"] = "1"
    # Re-pin a schema-valid body, so the independent cap/share relation is tested.
    body = saved.encoded({"OutBlock_1": raw_rows[path]})
    harness[2][path] = body
    observation = saved.formation._summarize(raw_rows[path], "stk_bydd_trd", DAY)
    observation.update(response_sha256=saved.digest(body), at="2026-10-07T08:31:00+00:00")
    harness[1]["aq"]["observations"][0] = observation
    with pytest.raises(saved.probe.ProbeRefusal, match="capitalization_mismatch"):
        module.load_sources(*args)


def test_no_cap_or_type_exclusion_precedes_both_arms_availability_guard(harness, monkeypatch):
    original = module._metadata
    classifications = []

    def late(*args):
        metadata = original(*args)
        return replace(metadata, available_at=SessionLagPolicy(2).selection_at(CALENDAR, 3))

    monkeypatch.setattr(module, "_metadata", late)
    monkeypatch.setattr(module, "_negative_type", lambda *args: classifications.append(args))
    with pytest.raises(ValueError, match="available_at is later than selection_at"):
        module.load_sources(*harness[0])
    assert classifications == []
    assert len(harness[-1]) == 4  # Whole formation validated, no liquidity opened.


def test_nonintegral_capitalization_fails_even_for_an_otherwise_below_cap_row(harness):
    args, receipts, store, raw_rows, _ = harness
    path = next(iter(raw_rows))
    raw_rows[path][1].update(MKTCAP="100000000.1", TDD_CLSPRC="1.000000001")
    body = saved.encoded({"OutBlock_1": raw_rows[path]})
    store[path] = body
    observation = saved.formation._summarize(raw_rows[path], "stk_bydd_trd", DAY)
    observation.update(response_sha256=saved.digest(body), at="2026-10-07T08:31:00+00:00")
    receipts["aq"]["observations"][0] = observation
    with pytest.raises(ValueError, match="nonintegral source capitalization"):
        module.load_sources(*args)


def test_duplicate_basic_identifier_is_rejected_before_join_dict_can_overwrite(harness):
    args, receipts, store, raw_rows, _ = harness
    item = receipts["aq_matrix"][2]
    path = Path(args[0]["aq"]["root"]) / f"response-03-stk_isu_base_info-{DAY}.json"
    raw_rows[path].append(deepcopy(raw_rows[path][0]))
    body = saved.encoded({"OutBlock_1": raw_rows[path]})
    store[path] = body
    observation = saved.formation._summarize(raw_rows[path], item["service"], DAY)
    observation.update(response_sha256=saved.digest(body), at="2026-10-07T08:31:00+00:00", schema_valid=True)
    receipts["aq"]["observations"][2] = observation
    with pytest.raises(ValueError, match="saved raw observation re-audit mismatch"):
        module.load_sources(*args)


def test_missing_requested_liquidity_is_explicit_not_zero_or_kis_fallback(harness):
    args, receipts, store, raw_rows, _ = harness
    item = receipts["ar_matrix"][0]
    path = Path(args[0]["ar_original"]["root"]) / f"response-0001-stk_bydd_trd-{item['bas_dd']}.json"
    raw_rows[path][0]["ISU_CD"] = "444444"
    body = saved.encoded({"OutBlock_1": raw_rows[path]})
    store[path] = body
    observation = saved.formation.summarize_trading_day(raw_rows[path], item["service"], item["bas_dd"])
    observation.update(response_sha256=saved.digest(body), at="2026-10-07T08:31:00+00:00")
    receipts["ar_original"]["observations"][0] = observation
    with pytest.raises(ValueError, match="missing potential source liquidity rows: 111111:20190326"):
        module.load_sources(*args)


def test_exclusive_scope_refuses_retry_before_more_raw_bodies(harness):
    args, _, _, _, reads = harness
    module.load_sources(*args)
    before = len(reads)
    with pytest.raises(FileExistsError):
        module.load_sources(*args)
    assert len(reads) == before
