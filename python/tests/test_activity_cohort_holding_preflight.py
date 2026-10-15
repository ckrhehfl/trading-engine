"""BS full-family holding scopes use fabricated normal audits and typed quotes."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import socket
import sqlite3
import weakref

import pytest

from research import activity_cohort_holding_preflight as module
from research import activity_cohort_normal_preflight as normal
from research import activity_cohort_scope_preflight as frontier
from research import activity_preflight as saved
from research.activity_cohort_selection import CohortSelection, activity_targets_fingerprint, population_fingerprint, rank_control_codes
from research.activity_control_assembly import assemble_control_window
from research.activity_control_selection import _rank
from research.activity_exit_bounds import validate_exit_bounds
from research.activity_holding_inputs import _FINGERPRINT_SCOPE
from research.activity_holding_restore import restore_holding_inputs
from research.activity_normal_assembly import _json
from research.activity_pair_calibration import PARAMETERS
from research.activity_timing import SessionLagPolicy
from test_activity_cohort_comparison import quotes as synthetic_quotes
from test_activity_normal_assembly import fixture as window_fixture


@pytest.mark.parametrize("value", [{}, {"한글": [0, -0.0, None, True, 10**40, "\\n\"\t"], "a": "Unicode: 가나다"},
    {"rows": [{"code": f"{index:06}", "value": "가" * 128, "typed": [["text", "0"], ["text", "1.2500"]]}
        for index in range(2048)]}])
def test_streamed_audit_matches_all_existing_canonical_bytes_and_hash(tmp_path, monkeypatch, value):
    expected = saved.encoded(value)
    monkeypatch.setattr(saved, "encoded", lambda *args: pytest.fail("stream writer rebuilt a complete canonical JSON"))
    path = tmp_path / "audit.json"
    assert module._write_audit(path, value) == saved.digest(expected)
    assert path.read_bytes() == expected
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("link", [False, True])
def test_streamed_audit_refuses_existing_file_or_symlink(tmp_path, link):
    target = tmp_path / "original.json"
    target.write_bytes(b"preserved")
    path = tmp_path / "audit.json"
    if link:
        path.symlink_to(target)
    else:
        path.write_bytes(b"preserved")
    with pytest.raises(FileExistsError):
        module._write_audit(path, {})
    assert target.read_bytes() == b"preserved"
    assert path.read_bytes() == b"preserved"


def test_streamed_audit_preserves_partial_output_without_returning_success(tmp_path, monkeypatch):
    class FailedEncoder:
        def __init__(self, **kwargs):
            assert kwargs == dict(ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)

        def iterencode(self, value):
            yield "x" * (64 * 1024)
            raise OSError("fabricated serialization failure")
    monkeypatch.setattr(module.json, "JSONEncoder", FailedEncoder)
    path = tmp_path / "audit.json"
    with pytest.raises(OSError, match="serialization failure"):
        module._write_audit(path, {})
    assert path.read_bytes() == b"x" * (64 * 1024)


def test_streamed_audit_rejects_nonfinite_json_values(tmp_path):
    with pytest.raises(ValueError):
        module._write_audit(tmp_path / "audit.json", {"bad": float("nan")})


@pytest.fixture(autouse=True)
def no_actual_network_database_or_normal_sources(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("actual network/database or normal-source reread forbidden")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(sqlite3, "connect", denied)
    monkeypatch.setattr(normal, "load_sources", denied)
    monkeypatch.setattr(normal, "restore_activity_failure", denied)


def case(ks=(2, 1)):
    end_index = 60 + 126 * len(ks) + 2
    calendar = tuple(date(2026, 9, 18) - timedelta(days=end_index - index) for index in range(end_index + 3))
    codes = tuple(f"{value:06d}" for value in range(1, 9))
    identities = tuple((code, "KR7" + code + "000") for code in codes)
    sample = window_fixture(frozen=0)
    original_dates = tuple(sorted(sample["observations"]))
    population = {"schema": "activity-normal-population-audit-v1", "availability_metadata": {}, "rows": []}
    scope = {"schema": "activity-control-scope-v1", "status": "complete_control_rank_metadata_planned",
        "formations": [], "paired_schedule": [], "population_partition": [], "activity_snapshot_sha256": "a" * 64}
    dispositions, audits, originals = [], [], {arm: [] for arm in module.ARMS}
    for index, k in zip(range(60, 60 + 126 * len(ks), 126), ks, strict=True):
        formation, compact = calendar[index], calendar[index].strftime("%Y%m%d")
        dates = calendar[index - 60:index + 1]
        cutoff = SessionLagPolicy(1, paired=True).selection_at(calendar, index)
        mapping = dict(zip(original_dates, dates, strict=True))
        cap = replace(sample["capitalization"], availability=replace(sample["capitalization"].availability,
            observation_date=formation, available_at=cutoff))
        metadata_key = f"m{index:04d}"
        population["availability_metadata"][metadata_key] = _json(asdict(cap.availability))
        arms = [{"session_lag": lag, "execution_on": SessionLagPolicy(lag, paired=True).execution_on(calendar, index).isoformat(),
            "selection_at": SessionLagPolicy(lag, paired=True).selection_at(calendar, index).isoformat()}
            for lag in (1, 2)]
        numeric = []
        for rank, code in enumerate(_rank(codes, formation, PARAMETERS["seed"]), 1):
            isin = dict(identities)[code]
            provenance = deepcopy(sample["accepted_record"]["required_windows"][0]["krx_metadata_provenance"])
            provenance.update(code=code, formation=compact)
            source = dict(formation=compact, code=code, isin=isin, capitalization="5000000000000", cap_pass=True,
                disposition="normal_window", listing_date=sample["listing_date"].isoformat(), krx_metadata_provenance=provenance,
                **{field: metadata_key for field in ("capitalization_availability", "classification_availability", "trade_section_availability")})
            population["rows"].append(source)
            partition = dict(formation=compact, code=code, isin=isin, prior_disposition="normal_window", control_disposition="numeric_possible")
            scope["population_partition"].append(partition)
            dispositions.append(dict(partition, normal_input_disposition="eligible", normal_input_exclusion_reasons=[]))
            observations = {mapping[day]: replace(row, availability=replace(row.availability, observation_date=mapping[day], available_at=cutoff))
                for day, row in sample["observations"].items()}
            liquidity = {mapping[day]: replace(row, availability=replace(row.availability, observation_date=mapping[day], available_at=cutoff))
                for day, row in sample["liquidity"].items()}
            record = deepcopy(sample["accepted_record"]); record.update(code=code)
            record["required_windows"][0].update(formation=formation.isoformat(), window_id=f"{compact}/{code}", isin=isin,
                required_dates=[day.isoformat() for day in dates], effective_start_for_this_adjudication=dates[0].isoformat(),
                effective_end_exclusive=(formation + timedelta(days=1)).isoformat(), krx_metadata_provenance=provenance)
            audit = assemble_control_window(calendar, formation, code, isin, sample["listing_date"], observations,
                cap, liquidity, record, {**PARAMETERS, "threshold": Decimal("3.0")})
            audit.update(formation=formation.isoformat(), code=code, isin=isin, formation_source_provenance={"synthetic": True})
            audits.append(audit)
            numeric.append(dict(formation=compact, code=code, isin=isin, rank=rank,
                required_dates=[day.isoformat() for day in dates], normal_dates=[day.isoformat() for day in dates[:-1]],
                frozen_dates=[], listing_date=source["listing_date"], krx_metadata_provenance=provenance,
                activity_reason="numeric_possible", first_required_observation=dates[0].isoformat(),
                normal_candidate_start=dates[0].isoformat(), formation_state="observed",
                selection_at={f"D{arm['session_lag']}": arm["selection_at"] for arm in arms}, source_metadata_refs={"synthetic": "b" * 64}))
        scope["formations"].append(dict(formation=compact, arms=deepcopy(arms), ranked_numeric_possible=numeric))
        scope["paired_schedule"].append(dict(formation=compact, arms=deepcopy(arms)))
        targets = tuple(_rank(codes, formation, PARAMETERS["seed"])[:k])
        rows = sorted((row for row in dispositions if row["formation"] == compact), key=lambda row: row["code"])
        for arm, lag in (("D1", 1), ("D2", 2)):
            policy = SessionLagPolicy(lag, paired=True)
            decision, selected = policy.execution_on(calendar, index), policy.selection_at(calendar, index)
            originals[arm].append(CohortSelection("ACTIVITY", arm, formation, decision, selected, PARAMETERS["seed"], targets,
                activity_targets_fingerprint(arm=arm, formation_on=formation, decision_on=decision, selection_at=selected,
                    targets=targets, seed=PARAMETERS["seed"]), identities, len(identities), population_fingerprint(identities),
                saved.digest(saved.encoded(rows)), ()))
    for name in ("rows",): population[name].sort(key=lambda row: (row["formation"], row["code"]))
    scope["population_partition"].sort(key=lambda row: (row["formation"], row["code"]))
    dispositions.sort(key=lambda row: (row["formation"], row["code"]))
    audits.sort(key=lambda row: (row["formation"], row["code"]))
    scope["summary"] = {"source_rows": len(population["rows"]), "numeric_possible_windows": len(dispositions)}
    originals = {arm: tuple(values) for arm, values in originals.items()}
    initial = frontier.plan_frontiers(scope, population, dispositions, audits, originals, deepcopy(PARAMETERS))
    delta = {"dispositions": dispositions, "normal_input_audit": audits, "new_normal_exclusions": []}
    updated = normal.updated_frontier(scope, population, initial, delta, deepcopy(PARAMETERS))
    return dict(calendar=calendar, original_scope=scope, population=population, initial_scope=initial,
        dispositions=dispositions, normal_audits=audits, updated=updated, exclusions=[], parameters=deepcopy(PARAMETERS))


def test_full_family_union_is_independent_and_preserves_original_k_and_end():
    value = case(); before = deepcopy(value)
    result = module.plan_holding_inputs(**value)
    assert value == before and len(result["declarations"]) == 24
    assert {row.group_id for row in result["declarations"]} == {"ACTIVITY", *module.CONTROL_IDS}
    for row in result["declarations"]:
        original = next(day for day in value["initial_scope"]["formations"] if day["formation"] == str(row.formation_on))["ACTIVITY"][row.arm]
        assert row.target_ceiling == original["target_ceiling"]
        assert row.activity_targets == tuple(original["target_codes"])
        assert row.activity_targets_sha256 == original["activity_targets_sha256"]
        assert set(row.chosen_codes) <= set(result["requested_code_dates"])
    for left, right in zip(result["proof_selections"]["D1"], result["proof_selections"]["D2"], strict=True):
        assert left.eligible_codes == right.eligible_codes
        assert set(left.eligible_codes) == {code for row in result["declarations"] if row.formation_on == left.formation_on for code in row.chosen_codes}
    assert all(days[-1] == PARAMETERS["end"] for days in result["requested_code_dates"].values())
    assert sum(len(row.chosen_codes) for row in result["declarations"]) > sum(len(row.eligible_codes) for rows in result["proof_selections"].values() for row in rows)


def test_fourteen_formations_build_all_168_declarations_without_old_nominee_counts():
    value = case(ks=(1,) * 14)
    result = module.plan_holding_inputs(**value)
    assert len(result["declarations"]) == 6 * 14 * 2 == 168
    assert all(len(result["proof_selections"][arm]) == 14 for arm in module.ARMS)
    assert all(row.target_ceiling == 1 for row in result["declarations"])
    assert all(row.source_population_count == 8 for row in result["declarations"])
    assert all(days[-1] == PARAMETERS["end"] for days in result["requested_code_dates"].values())


def test_original_lag_specific_zero_ceiling_stays_zero_while_proof_union_remains_paired():
    value = case()
    formation = value["calendar"][60]
    target = value["initial_scope"]["formations"][0]["ACTIVITY"]["D2"]
    target.update(target_codes=[], target_ceiling=0, assigned_capital_fraction={"numerator": 0, "denominator": 20})
    target["activity_targets_sha256"] = activity_targets_fingerprint(arm="D2", formation_on=formation,
        decision_on=date.fromisoformat(target["decision_on"]), selection_at=SessionLagPolicy(2, paired=True).selection_at(value["calendar"], 60),
        targets=(), seed=PARAMETERS["seed"])
    value["updated"] = normal.updated_frontier(value["original_scope"], value["population"], value["initial_scope"],
        {"dispositions": value["dispositions"], "normal_input_audit": value["normal_audits"], "new_normal_exclusions": []}, value["parameters"])
    result = module.plan_holding_inputs(**value)
    rows = [row for row in result["declarations"] if row.arm == "D2" and row.formation_on == formation]
    assert len(rows) == 6 and all(row.target_ceiling == 0 and row.chosen_codes == () for row in rows)
    left, right = result["proof_selections"]["D1"][0], result["proof_selections"]["D2"][0]
    assert left.eligible_codes == right.eligible_codes and left.eligible_codes


@pytest.mark.parametrize("change", ["missing_population", "source_identity", "missing_audit", "audit_state", "control_predicate", "cutoff", "unknown_prefix", "frontier", "activity_targets", "reordered_partition"])
def test_invalid_complete_prefix_or_metadata_refuses_before_price_reader(change):
    value = case()
    if change == "missing_population": value["population"]["rows"].pop()
    elif change == "source_identity": value["population"]["rows"][0]["isin"] = "KR7000999000"
    elif change == "missing_audit": value["normal_audits"].pop()
    elif change == "audit_state": value["dispositions"][0]["normal_input_disposition"] = "excluded"
    elif change == "control_predicate":
        audit = value["normal_audits"][0]
        for arm in module.ARMS:
            audit["control_arms"][arm].update(eligible=False, exclusion_reasons=["activity_predicate_failed"])
        value["dispositions"][0].update(normal_input_disposition="excluded", normal_input_exclusion_reasons=["activity_predicate_failed"])
    elif change == "cutoff": value["normal_audits"][0]["control_arms"]["D2"]["selection_at"] = "2026-01-01T08:30:00+09:00"
    elif change == "unknown_prefix":
        formation = value["calendar"][60]
        code = value["updated"]["formations"][0]["controls"]["C01"]["D1"]["target_codes"][0]
        value["normal_audits"] = [row for row in value["normal_audits"] if (row["formation"], row["code"]) != (str(formation), code)]
        row = next(row for row in value["dispositions"] if row["formation"] == formation.strftime("%Y%m%d") and row["code"] == code)
        row.update(normal_input_disposition="unresolved", normal_input_exclusion_reasons=None)
        value["updated"] = normal.updated_frontier(value["original_scope"], value["population"], value["initial_scope"],
            {"dispositions": value["dispositions"], "normal_input_audit": value["normal_audits"], "new_normal_exclusions": []}, value["parameters"])
    elif change == "frontier": value["updated"]["formations"][0]["controls"]["C01"]["D1"]["target_codes"] = []
    elif change == "activity_targets": value["initial_scope"]["formations"][0]["ACTIVITY"]["D1"]["target_codes"] = []
    else: value["dispositions"].reverse()
    with pytest.raises((ValueError, KeyError)):
        module.plan_holding_inputs(**value)


def test_unknown_tail_can_remain_and_zero_k_requires_no_quotes():
    value = case(ks=(0, 0))
    value["normal_audits"] = []
    for row in value["dispositions"]:
        row.update(normal_input_disposition="unresolved", normal_input_exclusion_reasons=None)
    value["updated"] = normal.updated_frontier(value["original_scope"], value["population"], value["initial_scope"],
        {"dispositions": value["dispositions"], "normal_input_audit": [], "new_normal_exclusions": []}, value["parameters"])
    result = module.plan_holding_inputs(**value)
    assert len(result["declarations"]) == 24 and result["requested_code_dates"] == result["issue_isins"] == {}
    assert all(not row.chosen_codes for row in result["declarations"])
    assert value["updated"]["summary"]["normal_disposition_counts"] == {"unresolved": 16}


def test_nonzero_family_preserves_unknown_beyond_every_required_prefix():
    value = case()
    previous = module.plan_holding_inputs(**value)
    formation = value["calendar"][186]
    chosen = {code for row in previous["declarations"] if row.formation_on == formation for code in row.chosen_codes}
    code = next(row["code"] for row in value["dispositions"] if row["formation"] == formation.strftime("%Y%m%d") and row["code"] not in chosen)
    value["normal_audits"] = [row for row in value["normal_audits"] if (row["formation"], row["code"]) != (str(formation), code)]
    row = next(row for row in value["dispositions"] if row["formation"] == formation.strftime("%Y%m%d") and row["code"] == code)
    row.update(normal_input_disposition="unresolved", normal_input_exclusion_reasons=None)
    value["updated"] = normal.updated_frontier(value["original_scope"], value["population"], value["initial_scope"],
        {"dispositions": value["dispositions"], "normal_input_audit": value["normal_audits"], "new_normal_exclusions": []}, value["parameters"])
    result = module.plan_holding_inputs(**value)
    assert [row.chosen_codes for row in result["declarations"]] == [row.chosen_codes for row in previous["declarations"]]
    assert value["updated"]["complete_target_prefix"] is True and value["updated"]["summary"]["normal_disposition_counts"]["unresolved"] == 1


def test_same_quote_code_cannot_bind_different_isins_across_formations():
    value = case()
    original = module.plan_holding_inputs(**value)
    early, late = value["calendar"][60], value["calendar"][186]
    early_codes = {code for row in original["declarations"] if row.formation_on == early for code in row.chosen_codes}
    late_codes = {code for row in original["declarations"] if row.formation_on == late for code in row.chosen_codes}
    code = sorted(early_codes & late_codes)[0]
    isin = "ZZ7" + code + "000"
    compact = late.strftime("%Y%m%d")
    for rows in (value["population"]["rows"], value["dispositions"], value["original_scope"]["population_partition"],
                 value["original_scope"]["formations"][1]["ranked_numeric_possible"]):
        next(row for row in rows if row["formation"] == compact and row["code"] == code)["isin"] = isin
    audit = next(row for row in value["normal_audits"] if row["formation"] == str(late) and row["code"] == code)
    audit["isin"] = audit["activity_screen"]["isin"] = isin
    audit["activity_screen"]["accepted_evidence"]["required_windows"][0]["isin"] = isin
    identities = tuple(sorted((row["code"], row["isin"]) for row in value["population"]["rows"] if row["formation"] == compact))
    for arm in module.ARMS:
        value["initial_scope"]["formations"][1]["ACTIVITY"][arm]["source_population_sha256"] = population_fingerprint(identities)
    value["updated"] = normal.updated_frontier(value["original_scope"], value["population"], value["initial_scope"],
        {"dispositions": value["dispositions"], "normal_input_audit": value["normal_audits"], "new_normal_exclusions": []}, value["parameters"])
    with pytest.raises(ValueError, match="issue-code reuse"):
        module.plan_holding_inputs(**value)


def package(tmp_path, monkeypatch, *, ks=(2, 1)):
    value = case(ks)
    context_values = {"br_scope_result": value["original_scope"], "br_population_audit": value["population"],
        "calendar_manifest": {"synthetic": True}, "bi_result": {"scan_snapshot": {"dataset_sha256": "a" * 64}}}
    fixed = deepcopy(module.FIXED_INPUTS)
    for role, body in context_values.items():
        fixed[role]["sha256"] = saved.digest(saved.encoded(body))
    monkeypatch.setattr(module, "FIXED_INPUTS", fixed)
    monkeypatch.setattr(normal, "FIXED_INPUTS", {**deepcopy(normal.FIXED_INPUTS), **deepcopy(fixed)})
    spec = dict(schema=module.SCHEMA, study_id=module.SCHEMA, mode=module.MODE, promotion_allowed=False,
        comparison_power_prerequisite_met=False, power_design_passed=False, window=deepcopy(saved.WINDOW),
        reference_specification=saved.REFERENCE, parameters=deepcopy(PARAMETERS), controls=list(module.CONTROL_IDS), design=frontier.DESIGN)
    normal_spec = dict(schema=normal.SCHEMA, study_id=normal.SCHEMA, mode=normal.MODE, promotion_allowed=False,
        comparison_power_prerequisite_met=False, power_design_passed=False, window=deepcopy(saved.WINDOW),
        reference_specification=saved.REFERENCE, parameters=deepcopy(PARAMETERS), controls=list(module.CONTROL_IDS), design=frontier.DESIGN,
        inputs={**deepcopy(normal.FIXED_INPUTS), **{role: {"path": str(tmp_path / (role + ".json")), "sha256": "c" * 64} for role in normal.NEW_ROLES}})
    normal_spec["inputs"]["bs_scope_result"]["sha256"] = saved.digest(saved.encoded(value["initial_scope"]))
    spec_pin = saved.digest(saved.encoded(normal_spec))
    source = dict(code_version="d" * 40, files=[{"path": normal.SOURCE_PATH, "sha256": "e" * 64},
        {"path": module.NORMAL_SPEC_PATH, "sha256": spec_pin}])
    data = {"specification.json": normal_spec, "source-manifest.json": source, "input-manifest.json": [],
        "resource-manifest.json": {"synthetic": True}, "normal-read-scope.json": {"upstream_dataset_sha256": "a" * 64,
            "restored_activity_source": deepcopy(module.FIXED_INPUTS["bi_result"])}, "raw-source-read-scope.json": {"synthetic": True},
        "normal-input-delta.json": deepcopy(value["normal_audits"]), "normal-input-audit.json": deepcopy(value["normal_audits"]),
        "control-dispositions.json": deepcopy(value["dispositions"]), "normal-exclusions.json": [],
        "restored-activity-proof-audit.json": {"upstream_dataset_sha256": "a" * 64}, "frontier.json": deepcopy(value["updated"])}
    hashes = {name: saved.digest(saved.encoded(body)) for name, body in data.items()}
    result = dict(schema=normal.SCHEMA, status="cohort_normal_inputs_and_frontier_assembled", summary={"synthetic": True},
        independent_output_verification="pending", input_sha256={role: row["sha256"] for role, row in normal_spec["inputs"].items() if "path" in row},
        artifact_sha256={name: hashes[name] for name in module.NORMAL_FILES[7:]}, activity_snapshot_sha256="a" * 64,
        specification_sha256=hashes["specification.json"], source_manifest_sha256=hashes["source-manifest.json"],
        input_manifest_sha256=hashes["input-manifest.json"], resource_manifest_sha256=hashes["resource-manifest.json"],
        normal_read_scope_sha256=hashes["normal-read-scope.json"], raw_source_read_scope_sha256=hashes["raw-source-read-scope.json"],
        typed_OHLC_fingerprint_recomputed=False, classification_evidence_level="inferred", historical_publication_evidence_level="assumed",
        new_source_requests=0, automatic_retries=0, D1_completed_studies=0, D2_completed_studies=0, **dict.fromkeys(normal.FALSE_FLAGS, False))
    data["result.json"] = result; hashes["result.json"] = saved.digest(saved.encoded(result))
    input_pins = {}
    for role, row in normal_spec["inputs"].items():
        if "path" in row: input_pins[role] = row["sha256"]
        else:
            for part in ("report", "started", "ledger"): input_pins[role + ":" + part] = row[part + "_sha256"]
    receipt = dict(schema="bs-cohort-normal-independent-verification-v1", status="verified", verified=True,
        independent_output_verification="verified", file_sha256=deepcopy(hashes), input_sha256=input_pins,
        producer_code_version="d" * 40, expected_commit="d" * 40, producer_sha256="e" * 64, summary=deepcopy(result["summary"]),
        D1_completed_studies=0, D2_completed_studies=0, automatic_retries=0, performance_studies_added=0,
        **dict.fromkeys(module.VERIFY_TRUE, True), **dict.fromkeys(module.VERIFY_FALSE, False))
    declared = {**deepcopy(module.FIXED_INPUTS), **{role: {"path": str(tmp_path / "normal" / name), "sha256": hashes[name]}
        for role, name in module.NORMAL_ROLES.items()}, "bs_normal_verification": {"path": str(tmp_path / "verification.json"),
            "sha256": saved.digest(saved.encoded(receipt))}, "bs_initial_scope_result": deepcopy(normal_spec["inputs"]["bs_scope_result"]),
        "scan_database": {"path": str(tmp_path / "scan.db"), "mode": "ro", "columns": list(module.prices.READ_COLUMNS)}}
    spec["inputs"] = declared
    bodies = {declared[role]["path"]: saved.encoded(data[name]) for role, name in module.NORMAL_ROLES.items()}
    bodies.update({declared["bs_normal_verification"]["path"]: saved.encoded(receipt),
        declared["bs_initial_scope_result"]["path"]: saved.encoded(value["initial_scope"]),
        **{declared[role]["path"]: saved.encoded(body) for role, body in context_values.items()}})
    return value, spec, data, hashes, receipt, bodies


def scan_from_quotes(quotes):
    assert quotes.fingerprint_json is not None
    rows = {}
    for observation in quotes.observations:
        typed = None if observation.typed_values is None else [list(value) for value in observation.typed_values]
        values = None if typed is None else {field: value if kind in ("text", "null") else {"sqlite_storage": [kind, value]}
            for field, (kind, value) in zip(("open", "high", "low", "close", "volume", "turnover"), typed[1:])}
        rows[observation.code, observation.observation_date.strftime("%Y%m%d")] = dict(values=values,
            typed_values=typed, provenance=saved.strict_json(observation.provenance_json))
    return dict(rows=rows, fingerprint=saved.strict_json(quotes.fingerprint_json), typed_snapshot_sha256=quotes.quote_snapshot_sha256,
        snapshot_received_at=quotes.snapshot_received_at, fingerprint_scope=_FINGERPRINT_SCOPE,
        whole_database_hash=False, BI_snapshot_equality_checked=False, progress_counts_recomputed_from_prices=False)


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    value, spec, data, hashes, receipt, bodies = package(tmp_path, monkeypatch)
    monkeypatch.setattr(saved, "calendar_schedule", lambda manifest, parameters:
        ([day.strftime("%Y%m%d") for day in value["calendar"]], {}, deepcopy(value["original_scope"]["paired_schedule"])))
    output = tmp_path / "output"; output.mkdir(mode=0o700)
    reads = []
    def reader(path):
        assert str(path) in bodies, "unregistered source read"
        reads.append(str(path))
        return bodies[str(path)]
    return value, spec, data, hashes, receipt, bodies, output, saved.PinnedInputs(reader), reads


@pytest.mark.parametrize("zero", [False, True])
def test_verified_package_and_full_scope_persist_before_single_scan(pipeline, monkeypatch, zero):
    value, spec, data, hashes, receipt, bodies, output, inputs, reads = pipeline
    if zero:
        value, spec, data, hashes, receipt, bodies = package(output.parent, monkeypatch, ks=(0, 0))
        monkeypatch.setattr(saved, "calendar_schedule", lambda manifest, parameters:
            ([day.strftime("%Y%m%d") for day in value["calendar"]], {}, deepcopy(value["original_scope"]["paired_schedule"])))
        inputs = saved.PinnedInputs(lambda path: bodies[str(path)])
    scans = []
    def loader(path, plan, pinned, scope_sha):
        assert not zero
        assert path == Path(spec["inputs"]["scan_database"]["path"])
        assert all((output / name).exists() for name in ("cohort-declarations.json", "proof-selections.json", "read-scope.json"))
        scope = saved.strict_json((output / "read-scope.json").read_bytes())
        assert saved.digest(saved.encoded(scope)) == scope_sha
        assert plan == {"calendar_dates": scope["calendar_dates"], "intersection_code_dates": scope["requested_code_dates"]}
        assert all(row["read_status"] == "hash_verified" for row in pinned.manifest)
        assert {row["role"] for row in pinned.manifest} == set(spec["inputs"]) - {"scan_database"}
        scans.append(plan)
        quotes = synthetic_quotes(value["calendar"], scope={code: tuple(date.fromisoformat(day) for day in days)
            for code, days in plan["intersection_code_dates"].items()}, scope_pin=scope_sha)
        return scan_from_quotes(quotes)
    result = module.evaluate_inputs(spec, inputs, output, scan_loader=loader)
    assert len(scans) == (0 if zero else 1)
    assert result["database_access"] is (not zero) and result["books_replayed"] is result["returns_computed"] is False
    assert result["normal_sources_reread"] is result["normal_predicates_reexecuted"] is result["api_access"] is False
    assert result["control_targets_frozen"] is result["complete_control_prefix_proved"] is True
    assert result["actual_input_reuse_readiness_established"] is result["compulsory_successor_closure_complete"] is False
    assert result["summary"]["cohort_declarations"] == 24 and result["summary"]["formation_count"] == 2
    scope = saved.strict_json((output / "read-scope.json").read_bytes())
    audit = saved.strict_json((output / "holding-input-audit.json").read_bytes())
    restored = restore_holding_inputs(audit, scope, quote_snapshot_sha256=result["quote_snapshot_sha256"],
        activity_snapshot_sha256=result["activity_snapshot_sha256"], read_scope_sha256=result["read_scope_sha256"])
    prepared = module.plan_holding_inputs(**value)
    proof = saved.strict_json((output / "provisional-exit-scope.json").read_bytes())
    assert validate_exit_bounds(proof, prepared["proof_selections"]["D1"], prepared["proof_selections"]["D2"], restored,
        PARAMETERS, prepared["requested_code_dates"], prepared["issue_isins"], input_sha256=result["input_sha256"]) == proof
    assert len(proof["entries"]) == result["summary"]["union_potential_origins"]
    if zero:
        assert result["quote_snapshot_sha256"] is None and not proof["entries"] and not restored.requested_coordinates


def test_typed_missing_unresolved_frozen_and_locked_rows_stay_in_original_scope(pipeline):
    value, spec, data, hashes, receipt, bodies, output, inputs, reads = pipeline
    def loader(path, plan, pinned, scope_sha):
        scope = {code: tuple(date.fromisoformat(day) for day in days) for code, days in plan["intersection_code_dates"].items()}
        code, days = next(iter(scope.items()))
        changes = {(code, days[0]): None, (code, days[1]): (days[1].strftime("%Y%m%d"), "100", "100", "100", "100", "0", "0"),
            (code, days[2]): (days[2].strftime("%Y%m%d"), "100", "100", "100", "100", "100", "1"),
            (code, days[3]): (days[3].strftime("%Y%m%d"), "bad", "101", "99", "100", "100", "10")}
        return scan_from_quotes(synthetic_quotes(value["calendar"], scope=scope, changes=changes, scope_pin=scope_sha))
    result = module.evaluate_inputs(spec, inputs, output, scan_loader=loader)
    audit = saved.strict_json((output / "holding-input-audit.json").read_bytes())
    assert audit["summary"]["state_counts"]["absent_requested"] == 1
    assert audit["summary"]["state_counts"]["frozen"] == audit["summary"]["state_counts"]["locked"] == audit["summary"]["state_counts"]["unresolved_observed"] == 1
    assert all(days[-1] == PARAMETERS["end"] for days in audit["requested_code_dates"].values())
    assert audit["summary"]["requested_code_dates"] == sum(map(len, audit["requested_code_dates"].values()))
    assert result["original_quote_requests_preserved"] is True
    declarations = saved.strict_json((output / "cohort-declarations.json").read_bytes())
    expected = module.plan_holding_inputs(**value)["declarations"]
    assert [(row["group_id"], row["arm"], row["formation_on"], row["activity_targets"], row["resolved_prefix"])
        for row in declarations] == [(row.group_id, row.arm, str(row.formation_on), list(row.activity_targets), [list(item) for item in row.resolved_prefix])
        for row in expected]


def test_checked_unused_normal_bodies_and_bi_are_released_before_population_decode(pipeline):
    value, spec, data, hashes, receipt, bodies, output, inputs, reads = pipeline
    watched = {"bs_normal_input_manifest", "bs_normal_resource_manifest", "bs_normal_normal_read_scope",
        "bs_normal_raw_source_read_scope", "bs_normal_normal_input_delta", "bs_normal_restored_activity_proof_audit",
        "bi_result"}
    refs = {}
    class TrackedDict(dict):
        pass
    class TrackedList(list):
        pass
    class TrackedInputs(saved.PinnedInputs):
        def json(self, path, pin, role):
            if role == "br_population_audit":
                assert set(refs) == watched
                assert all(reference() is None for reference in refs.values())
            result = super().json(path, pin, role)
            if role in watched:
                result = TrackedDict(result) if isinstance(result, dict) else TrackedList(result)
                refs[role] = weakref.ref(result)
            return result
    def loader(path, plan, pinned, scope_sha):
        return scan_from_quotes(synthetic_quotes(value["calendar"],
            scope={code: tuple(date.fromisoformat(day) for day in days)
                for code, days in plan["intersection_code_dates"].items()}, scope_pin=scope_sha))
    tracked = TrackedInputs(lambda path: bodies[str(path)])
    result = module.evaluate_inputs(spec, tracked, output, scan_loader=loader)
    assert result["control_targets_frozen"] is True
    assert [row["role"] for row in tracked.manifest] == [*module.NORMAL_ROLES,
        "bs_normal_verification", *module.FIXED_INPUTS, "bs_initial_scope_result"]


@pytest.mark.parametrize("change", ["verified", "file_pin", "input_pin", "commit", "producer", "summary", "power", "counts", "predicate", "source_flag", "manifest_link", "body"])
def test_invalid_normal_receipt_or_changed_body_never_reaches_database(pipeline, change):
    value, spec, data, hashes, receipt, bodies, output, inputs, reads = pipeline
    if change == "verified": receipt["verified"] = False
    elif change == "file_pin": receipt["file_sha256"]["frontier.json"] = "f" * 64
    elif change == "input_pin": receipt["input_sha256"]["br_population_audit"] = "f" * 64
    elif change == "commit": receipt["expected_commit"] = "f" * 40
    elif change == "producer": receipt["producer_sha256"] = "f" * 64
    elif change == "summary": receipt["summary"] = {"changed": True}
    elif change == "power": data["result.json"]["power_design_passed"] = True
    elif change == "counts": receipt["D1_completed_studies"] = 1
    elif change == "predicate": receipt["normal_predicates_recomputed"] = False
    elif change == "source_flag": receipt["source_truth_certified"] = True
    elif change == "manifest_link": data["result.json"]["input_manifest_sha256"] = "f" * 64
    else:
        bodies[spec["inputs"]["bs_normal_frontier"]["path"]] += b" "
    if change != "body":
        for role, name in module.NORMAL_ROLES.items():
            bodies[spec["inputs"][role]["path"]] = saved.encoded(data[name])
            spec["inputs"][role]["sha256"] = saved.digest(bodies[spec["inputs"][role]["path"]])
        if change in ("power", "manifest_link"):
            # Re-sign the changed result in the externally bound receipt, so
            # these cases must reach the semantic flag/link checks.
            receipt["file_sha256"]["result.json"] = spec["inputs"]["bs_normal_result"]["sha256"]
        bodies[spec["inputs"]["bs_normal_verification"]["path"]] = saved.encoded(receipt)
        spec["inputs"]["bs_normal_verification"]["sha256"] = saved.digest(saved.encoded(receipt))
    with pytest.raises(ValueError):
        module.evaluate_inputs(spec, inputs, output, scan_loader=lambda *args: pytest.fail("invalid normal package reached scan"))
    assert not (output / "read-scope.json").exists()


@pytest.mark.parametrize("change", ["cost", "power", "controls", "extra_input", "scan_write", "scan_columns", "alias", "package_path"])
def test_fixed_specification_and_exact_package_boundary(tmp_path, monkeypatch, change):
    value, spec, data, hashes, receipt, bodies = package(tmp_path, monkeypatch)
    module.validate_spec(spec, {"parameters": deepcopy(PARAMETERS)})
    if change == "cost": spec["parameters"]["slippage_bps_per_side"] = 5
    elif change == "power": spec["power_design_passed"] = True
    elif change == "controls": spec["controls"].pop()
    elif change == "extra_input": spec["inputs"]["original_activity_book"] = {"path": "/private/forbidden", "sha256": "f" * 64}
    elif change == "scan_write": spec["inputs"]["scan_database"]["mode"] = "rw"
    elif change == "scan_columns": spec["inputs"]["scan_database"]["columns"].append("forbidden")
    elif change == "alias": spec["inputs"]["bs_initial_scope_result"]["path"] = spec["inputs"]["bi_result"]["path"]
    else: spec["inputs"]["bs_normal_frontier"]["path"] = str(tmp_path / "outside" / "frontier.json")
    with pytest.raises(ValueError):
        module.validate_spec(spec, {"parameters": deepcopy(PARAMETERS)})


def statuses(path):
    return [saved.strict_json(raw)["status"] for raw in path.read_bytes().splitlines()]


def logging_case(tmp_path, monkeypatch):
    root, private, evidence = (tmp_path / name for name in ("repo", "private", "evidence"))
    root.mkdir(); private.mkdir(mode=0o700); evidence.mkdir(mode=0o700)
    path, raw = evidence / "population.json", b"{}"
    spec = dict(schema=module.SCHEMA, study_id=module.SCHEMA, mode=module.MODE, promotion_allowed=False,
        comparison_power_prerequisite_met=False, power_design_passed=False, window=deepcopy(saved.WINDOW),
        reference_specification=saved.REFERENCE, parameters=deepcopy(PARAMETERS), controls=list(module.CONTROL_IDS), design=frontier.DESIGN,
        inputs={"br_population_audit": {"path": str(path), "sha256": saved.digest(raw)}})
    sources = {"code_version": "a" * 40, "files": [{"path": module.SOURCE_PATH, "sha256": "a" * 64}]}
    monkeypatch.chdir(root); monkeypatch.setattr(module, "__file__", str(root / module.SOURCE_PATH))
    def freeze(*args, **kwargs):
        assert kwargs["spec_validator"] is module.validate_spec
        return deepcopy(sources), saved.encoded(spec), deepcopy(spec)
    def evaluator(spec, inputs, output):
        inputs.json(path, saved.digest(raw), "br_population_audit")
        return dict(schema=module.SCHEMA, status="synthetic_holding_inputs_audited", summary={"synthetic": True},
            D1_completed_studies=0, D2_completed_studies=0, **dict.fromkeys(module.FALSE_FLAGS, False))
    return root, private / "run", tmp_path / "runs.jsonl", path, raw, freeze, evaluator


def test_durable_logged_start_precedes_any_read_and_no_performance_trial(tmp_path, monkeypatch):
    root, output, runs, path, raw, freeze, evaluator = logging_case(tmp_path, monkeypatch)
    def reader(target):
        assert target == path and statuses(runs) == ["started"]
        assert (output / "source-manifest.json").exists() and (output / "specification.json").exists()
        return raw
    record = module.run_preflight(root, root / "spec.json", output, runs, reader=reader, freeze=freeze, evaluator=evaluator)
    assert statuses(runs) == ["started", "completed"]
    rows = [saved.strict_json(row) for row in runs.read_bytes().splitlines()]
    assert all(row["promotion_allowed"] is False and row["promotion_trial_increment"] == 0 for row in rows)
    result = saved.strict_json((output / "result.json").read_bytes())
    assert result["D1_completed_studies"] == result["D2_completed_studies"] == 0
    assert result["books_replayed"] is result["returns_computed"] is False
    assert record["result"]["result_sha256"] == saved.digest((output / "result.json").read_bytes())
    assert output.stat().st_mode & 0o777 == 0o700 and all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())


@pytest.mark.parametrize("failure", ["dirty", "alias", "hash", "runtime", "scan"])
def test_failure_preserves_started_manifests_scope_and_refuses_retry(tmp_path, monkeypatch, failure):
    root, output, runs, path, raw, freeze, evaluator = logging_case(tmp_path, monkeypatch)
    calls = 0
    def freezer(*args, **kwargs):
        nonlocal calls
        calls += 1
        if failure == "dirty": raise ValueError("source dirty")
        source, body, spec = freeze(*args, **kwargs)
        if failure == "runtime" and calls == 2: source["code_version"] = "b" * 40
        return source, body, spec
    def evaluate(spec, inputs, output):
        value = evaluator(spec, inputs, output)
        if failure == "scan":
            saved.write_exclusive(output / "read-scope.json", saved.encoded({"synthetic_scope_persisted_before_scan": True}))
            inputs.manifest.append({"role": "scan_database", "read_status": "started", "mode": "ro"})
            raise ValueError("synthetic scan stopped")
        return value
    if failure == "alias": runs = path
    with pytest.raises(ValueError):
        module.run_preflight(root, root / "spec.json", output, runs,
            reader=lambda target: b"changed" if failure == "hash" else raw, freeze=freezer, evaluator=evaluate)
    if failure in ("dirty", "alias"):
        assert not output.exists() and not runs.exists()
    else:
        assert statuses(runs) == ["started", "failed"] and not (output / "result.json").exists()
        assert all((output / name).exists() for name in ("failure.json", "input-manifest.json", "resource-manifest.json"))
        preserved = saved.strict_json((output / "failure.json").read_bytes())
        assert preserved["partial_package_preserved"] is True and preserved["automatic_retries"] == 0
        assert preserved["books_replayed"] is preserved["returns_computed"] is False
        if failure == "scan": assert (output / "read-scope.json").exists()
        with pytest.raises(ValueError, match="already exists"):
            module.run_preflight(root, root / "spec.json", output, runs, reader=lambda target: raw, freeze=freeze, evaluator=evaluator)
        assert statuses(runs) == ["started", "failed"]
