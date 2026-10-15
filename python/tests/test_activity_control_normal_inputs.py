"""Pure BR reviewed-subset/full-population joins with synthetic source inputs."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, timedelta
from decimal import Decimal as D
from inspect import signature
import json

import pytest

from research.activity_control_normal_inputs import assemble_control_normal_inputs
from research.activity_normal_assembly import _json
from research import activity_normal_sources as sources
from research.activity_timing import SessionLagPolicy
from test_activity_normal_assembly import fixture as window_fixture
from test_activity_normal_sources import harness


def fixture():
    window = window_fixture(frozen=0)
    calendar = tuple(date(2020, 1, 1) + timedelta(days=index) for index in range(192))
    formation, compact = window["formation"], window["formation"].strftime("%Y%m%d")
    params = {"lookback": 60, "threshold": D("3.0"), "holding_sessions": 126,
              "end": calendar[188].isoformat(), "slots": 20, "seed": 20261005}
    arms = [{"session_lag": lag, "execution_on": SessionLagPolicy(lag, paired=True).execution_on(calendar, 60).isoformat(),
             "selection_at": SessionLagPolicy(lag, paired=True).selection_at(calendar, 60).isoformat()}
            for lag in (1, 2)]
    availability = _json(asdict(window["capitalization"].availability))
    rows, partition = [], []
    for code, prior, control in (("000001", "bi_failure", "numeric_possible"),
            ("000002", "bi_failure", "numeric_possible"), ("000003", "cap_below", "cap_below"),
            ("005935", "negative_type", "negative_type"), ("000004", "bh_failure", "bh_failure"),
            ("000005", "bi_failure", "formation_frozen")):
        provenance = deepcopy(window["accepted_record"]["required_windows"][0]["krx_metadata_provenance"])
        provenance["code"] = code
        isin = f"KR7{code}000"
        rows.append({"formation": compact, "code": code, "isin": isin, "disposition": prior,
            "capitalization": "5000000000000" if prior != "cap_below" else "1",
            "listing_date": window["listing_date"].isoformat(), "krx_metadata_provenance": provenance,
            "capitalization_availability": "m0000", "classification_availability": "m0000",
            "trade_section_availability": "m0000", "unabridged_source_field": f"retained:{code}"})
        partition.append({"formation": compact, "code": code, "isin": isin,
                          "prior_disposition": prior, "control_disposition": control})
    possible = [{"formation": compact, "code": row["code"], "isin": row["isin"], "rank": rank,
        "required_dates": [day.isoformat() for day in calendar[:61]],
        "normal_dates": [day.isoformat() for day in calendar[:60]], "frozen_dates": [],
        "listing_date": row["listing_date"], "krx_metadata_provenance": deepcopy(row["krx_metadata_provenance"])}
        for rank, row in enumerate(rows[:2], 1)]
    scope = {"schema": "activity-control-scope-v1", "status": "complete_control_rank_metadata_planned",
        "paired_schedule": [{"formation": compact, "arms": deepcopy(arms)}],
        "formations": [{"formation": compact, "arms": deepcopy(arms), "ranked_numeric_possible": possible}],
        "population_partition": partition, "summary": {"source_rows": len(rows), "numeric_possible_windows": 2}}
    source = {"isin": window["isin"], "listing_date": window["listing_date"],
        "capitalization": window["capitalization"], "liquidity": window["liquidity"],
        "krx_metadata_provenance": deepcopy(rows[0]["krx_metadata_provenance"]),
        "source_provenance": {"source_raw_capitalization": "5000000000000", "original_publication": None}}
    observations = deepcopy(window["observations"])
    observations[formation] = replace(observations[formation], turnover=D(1))
    return {"calendar": calendar, "scope": scope,
        "population_audit": {"schema": "activity-normal-population-audit-v1", "rows": rows,
                             "availability_metadata": {"m0000": availability}},
        "accepted_records": [window["accepted_record"]], "observations": {window["code"]: observations},
        "potential_sources": {(compact, window["code"]): source}, "params": params}


@pytest.mark.parametrize("baseline", [D(10), D(0)])
def test_reviewed_subset_keeps_whole_population_unknowns_and_original_activity_failures(baseline):
    inputs = fixture()
    for day, row in inputs["observations"]["000001"].items():
        if day < inputs["calendar"][60]:
            inputs["observations"]["000001"][day] = replace(row, turnover=baseline)
    before = deepcopy(inputs)
    result = assemble_control_normal_inputs(**inputs)
    assert inputs == before and result["population_audit"] == inputs["population_audit"]
    assert result["summary"] == {"source_rows": 6, "numeric_possible_windows": 2,
        "reviewed_normal_windows": 1, "unreviewed_numeric_possible_windows": 1}
    by_code = {row["code"]: row for row in result["dispositions"]}
    assert by_code["000001"]["normal_input_disposition"] == "eligible"
    assert by_code["000001"]["control_disposition"] == "numeric_possible"
    assert by_code["000002"]["normal_input_disposition"] == "unresolved"
    assert by_code["000002"]["normal_input_exclusion_reasons"] is None
    for code in ("000003", "005935", "000004", "000005"):
        assert by_code[code]["normal_input_disposition"] == "excluded"
        assert by_code[code]["normal_input_exclusion_reasons"] == [by_code[code]["control_disposition"]]
    audit = result["normal_input_audit"][0]
    assert audit["formation_source_provenance"] == inputs["potential_sources"][("20200301", "000001")]["source_provenance"]
    for name in ("D1", "D2"):
        original = audit["activity_screen"]["arms"][name]
        assert original["candidate"]["activity_pass"] is original["selected"] is False
        assert original["activity_median"] == str(baseline)
        assert audit["control_arms"][name]["eligible"] is True
    assert all(result[name] is False for name in ("complete_control_prefix_proved", "compulsory_successor_closure_complete",
                                                "holding_selection_executed", "returns_computed", "promotion_allowed"))
    assert result["classification_evidence_level"] == "inferred"
    assert result["historical_publication_evidence_level"] == "assumed"
    json.dumps(result, allow_nan=False)
    inputs["population_audit"]["rows"][0]["unabridged_source_field"] = "changed later"
    assert result["population_audit"]["rows"][0]["unabridged_source_field"] == "retained:000001"


def test_reviewing_later_candidate_does_not_skip_or_exclude_earlier_unknown():
    inputs = fixture()
    record = inputs["accepted_records"][0]
    record["code"] = "000002"
    window = record["required_windows"][0]
    window.update(window_id="20200301/000002", isin="KR7000002000")
    window["krx_metadata_provenance"]["code"] = "000002"
    inputs["observations"]["000002"] = inputs["observations"].pop("000001")
    source = inputs["potential_sources"].pop(("20200301", "000001"))
    source["isin"] = "KR7000002000"
    source["krx_metadata_provenance"]["code"] = "000002"
    inputs["potential_sources"][("20200301", "000002")] = source
    result = assemble_control_normal_inputs(**inputs)
    first = next(row for row in result["dispositions"] if row["code"] == "000001")
    assert first["normal_input_disposition"] == "unresolved"
    assert first["normal_input_exclusion_reasons"] is None
    assert result["complete_control_prefix_proved"] is False


def test_zero_reviewed_windows_keeps_all_numeric_candidates_unresolved():
    inputs = fixture()
    inputs["accepted_records"], inputs["potential_sources"] = [], {}
    result = assemble_control_normal_inputs(**inputs)
    assert result["normal_input_audit"] == []
    assert result["summary"]["unreviewed_numeric_possible_windows"] == 2


def test_one_positive_record_connects_multiple_exact_formation_windows_without_splicing():
    inputs = fixture()
    calendar = tuple(date(2020, 1, 1) + timedelta(days=index) for index in range(318))
    inputs["calendar"], inputs["params"]["end"] = calendar, calendar[314].isoformat()
    formation, compact = calendar[186], calendar[186].strftime("%Y%m%d")
    cutoff = SessionLagPolicy(1).selection_at(calendar, 186)
    first_source = inputs["potential_sources"][("20200301", "000001")]
    source = deepcopy(first_source)
    source["krx_metadata_provenance"]["formation"] = compact
    source["capitalization"] = replace(source["capitalization"], availability=replace(
        source["capitalization"].availability, observation_date=formation, available_at=cutoff))
    sample = next(iter(inputs["observations"]["000001"].values()))
    for day in calendar[126:187]:
        inputs["observations"]["000001"][day] = replace(sample, turnover=D(1) if day == formation else D(10),
            availability=replace(sample.availability, observation_date=day, available_at=cutoff))
    sample_liquidity = next(iter(first_source["liquidity"].values()))
    source["liquidity"] = {day: replace(sample_liquidity,
        availability=replace(sample_liquidity.availability, observation_date=day, available_at=cutoff))
        for day in calendar[126:186]}
    inputs["potential_sources"][(compact, "000001")] = source
    window = deepcopy(inputs["accepted_records"][0]["required_windows"][0])
    window.update(formation=formation.isoformat(), window_id=f"{compact}/000001",
        effective_start_for_this_adjudication=calendar[126].isoformat(),
        effective_end_exclusive=calendar[187].isoformat(),
        required_dates=[day.isoformat() for day in calendar[126:187]],
        krx_metadata_provenance=deepcopy(source["krx_metadata_provenance"]))
    inputs["accepted_records"][0]["required_windows"].append(window)
    population = inputs["population_audit"]
    population["availability_metadata"]["m0001"] = _json(asdict(source["capitalization"].availability))
    added_rows = deepcopy(population["rows"])
    for row in added_rows:
        row["formation"], row["krx_metadata_provenance"]["formation"] = compact, compact
        for field in ("capitalization_availability", "classification_availability", "trade_section_availability"):
            row[field] = "m0001"
    population["rows"].extend(added_rows)
    scope = inputs["scope"]
    added_partition = deepcopy(scope["population_partition"])
    for row in added_partition:
        row["formation"] = compact
    scope["population_partition"].extend(added_partition)
    arms = [{"session_lag": lag, "execution_on": SessionLagPolicy(lag).execution_on(calendar, 186).isoformat(),
             "selection_at": SessionLagPolicy(lag).selection_at(calendar, 186).isoformat()} for lag in (1, 2)]
    added_formation = deepcopy(scope["formations"][0])
    added_formation.update(formation=compact, arms=arms)
    for row in added_formation["ranked_numeric_possible"]:
        row.update(formation=compact, required_dates=window["required_dates"],
            normal_dates=[day.isoformat() for day in calendar[126:186]])
        row["krx_metadata_provenance"]["formation"] = compact
    scope["formations"].append(added_formation)
    scope["paired_schedule"].append({"formation": compact, "arms": deepcopy(arms)})
    scope["summary"].update(source_rows=12, numeric_possible_windows=4)
    result = assemble_control_normal_inputs(**inputs)
    assert result["summary"]["reviewed_normal_windows"] == 2
    assert [row["activity_screen"]["identity_period"]["start"] for row in result["normal_input_audit"]] == [
        calendar[0].isoformat(), calendar[126].isoformat()]
    assert all(row["code"] == "000001" for row in result["normal_input_audit"])


def test_reviewed_liquidity_failure_remains_an_exclusion():
    inputs = fixture()
    source = inputs["potential_sources"][("20200301", "000001")]
    source["liquidity"] = {day: replace(row, turnover=D(1)) for day, row in source["liquidity"].items()}
    result = assemble_control_normal_inputs(**inputs)
    row = next(row for row in result["dispositions"] if row["code"] == "000001")
    assert row["normal_input_disposition"] == "excluded"
    assert row["normal_input_exclusion_reasons"] == ["absolute_liquidity_fail"]
    assert result["normal_input_audit"][0]["activity_screen"]["arms"]["D1"]["exclusion_reasons"] == [
        "absolute_liquidity_fail", "activity_predicate_failed"]


@pytest.mark.parametrize("corruption", ["population_drop", "population_duplicate", "population_future",
    "partition_isin", "partition_disposition", "missing_formation", "cutoff", "scope_pool_drop", "rank",
    "metadata_isin", "metadata_provenance", "duplicate_record", "unaccepted", "accepted_future",
    "accepted_dates", "source_missing", "source_extra", "source_isin", "source_provenance", "source_cap",
    "normal_dates", "activity_missing", "late_activity", "late_excluded_metadata", "params"])
def test_source_population_scope_positive_subset_and_timing_contradictions_fail_closed(corruption):
    inputs = fixture()
    scope, population = inputs["scope"], inputs["population_audit"]
    record = inputs["accepted_records"][0]
    window = record["required_windows"][0]
    source = inputs["potential_sources"][("20200301", "000001")]
    candidate = scope["formations"][0]["ranked_numeric_possible"][0]
    if corruption == "population_drop":
        population["rows"].pop()
    elif corruption == "population_duplicate":
        population["rows"].append(deepcopy(population["rows"][0]))
    elif corruption == "population_future":
        population["rows"][0]["formation"] = "20260919"
    elif corruption == "partition_isin":
        scope["population_partition"][0]["isin"] = "KR7000002000"
    elif corruption == "partition_disposition":
        scope["population_partition"][0]["prior_disposition"] = "cap_below"
    elif corruption == "missing_formation":
        scope["formations"] = []
    elif corruption == "cutoff":
        scope["formations"][0]["arms"][0]["selection_at"] = "2099-01-01T08:30:00+09:00"
    elif corruption == "scope_pool_drop":
        scope["formations"][0]["ranked_numeric_possible"].pop()
    elif corruption == "rank":
        candidate["rank"] = 3
    elif corruption == "metadata_isin":
        candidate["isin"] = "KR7000002000"
    elif corruption == "metadata_provenance":
        candidate["krx_metadata_provenance"]["source_row_position"] += 1
    elif corruption == "duplicate_record":
        inputs["accepted_records"].append(deepcopy(record))
    elif corruption == "unaccepted":
        record["decision"] = "unresolved"
    elif corruption == "accepted_future":
        window["formation"] = inputs["calendar"][61].isoformat()
    elif corruption == "accepted_dates":
        window["required_dates"].append(inputs["calendar"][61].isoformat())
    elif corruption == "source_missing":
        inputs["potential_sources"].clear()
    elif corruption == "source_extra":
        inputs["potential_sources"][("20200302", "000001")] = deepcopy(source)
    elif corruption == "source_isin":
        source["isin"] = "KR7000002000"
    elif corruption == "source_provenance":
        source["krx_metadata_provenance"]["source_row_position"] += 1
    elif corruption == "source_cap":
        source["capitalization"] = replace(source["capitalization"], value=D(1))
    elif corruption == "normal_dates":
        candidate["normal_dates"].pop()
    elif corruption == "activity_missing":
        inputs["observations"]["000001"].pop(inputs["calendar"][0])
    elif corruption == "late_activity":
        day = inputs["calendar"][0]
        row = inputs["observations"]["000001"][day]
        inputs["observations"]["000001"][day] = replace(row,
            availability=replace(row.availability, available_at=row.availability.available_at + timedelta(seconds=1)))
    elif corruption == "late_excluded_metadata":
        population["availability_metadata"]["m0000"]["available_at"] = "2099-01-01T08:30:00+09:00"
    else:
        inputs["params"]["threshold"] = D(0)
    with pytest.raises(ValueError):
        assemble_control_normal_inputs(**inputs)


def test_saved_source_loader_keeps_literal_default_36(harness):
    assert signature(sources.load_sources).parameters["expected_window_count"].default == 36
    with pytest.raises(ValueError, match="fixed source population geometry"):
        sources.load_sources(*harness[0])
    assert harness[-1] == []


@pytest.mark.parametrize("count", [0, -1, True, 1.0, "1", None])
def test_saved_source_loader_requires_exact_positive_declared_count_before_reads(harness, count):
    with pytest.raises(ValueError, match="positive integer"):
        sources.load_sources(*harness[0], expected_window_count=count)
    assert harness[-1] == []
