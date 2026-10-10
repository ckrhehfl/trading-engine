"""Fabricated real upstream packages and private logged IO; actual evidence is never read."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import localcontext
from pathlib import Path
import socket
import sqlite3
import stat
from types import SimpleNamespace

import pytest

from research import activity_raw_component_preflight as module
from research import activity_paired_replay as paired
from research import activity_preflight as saved
from research import activity_quote_preflight as quote_producer
from research import activity_raw_quote_view as raw
from research.activity_exit_bounds import provisional_exit_bounds
from research.activity_holding_restore import restore_holding_inputs
from research.activity_quote_extension import extend_holding_quotes
from research.activity_raw_episode import RAW_FIELDS, TARGET_CODES, extract_raw_episode_targets
import test_activity_paired_extension as extension_fixture
from test_activity_paired_replay import synthetic_scan
from test_activity_quote_preflight import fixture as quote_fixture

SOURCE = Path(module.__file__).resolve()
LG, LX = "003550", "383800"


def fixture(tmp_path, monkeypatch, *, raw_end=129, states=None):
    """Use real package validation; the original synthetic issue stays unchanged.

    Its spin delivers raw LX. LG rows exercise the fixed two-issue raw view,
    while dedicated helper tests below cover raw parent date-role diagnostics.
    No original package pin, validator or prepare_inputs function is stubbed.
    """
    monkeypatch.setattr(extension_fixture, "B", LX)
    monkeypatch.setattr(extension_fixture, "BI", "KR7383800000")
    bundles, parameters, _, calendar, _, _ = extension_fixture.spin_fixture(monkeypatch)
    quote_spec, quote_store, plan, prepared = quote_fixture(monkeypatch)
    declarations, evidence = (saved.strict_json(value) for value in bundles[2:])
    event = declarations["events"][0]
    plan["events"] = [{name: event[name] for name in ("event_id", "kind", "code", "successor_code",
        "effective_on", "retained_available_on", "new_available_on")} |
        {"old_basis_on": evidence["bases"]["old"]["session"]}]
    plan_path = Path(quote_spec["inputs"]["coordinate_plan"]["path"])
    quote_store[plan_path] = saved.encoded(plan)
    quote_spec["inputs"]["coordinate_plan"]["sha256"] = saved.digest(quote_store[plan_path])
    quote_output = tmp_path / "synthetic-quote"
    quote_output.mkdir(mode=0o700)
    monkeypatch.setattr(quote_producer.prices, "load_scan", lambda *a: synthetic_scan(*a, mode="ordinary"))
    produced = quote_producer.evaluate_inputs(quote_spec, saved.PinnedInputs(quote_store.__getitem__), quote_output)
    envelope = saved.strict_json((quote_output / "quote-extension.json").read_bytes())
    extension = restore_holding_inputs(envelope["holding_input_audit"], envelope["read_scope"],
        quote_snapshot_sha256=produced["extension_quote_snapshot_sha256"], activity_snapshot_sha256=prepared.activity_snapshot_sha256,
        read_scope_sha256=produced["read_scope_sha256"])
    base = extend_holding_quotes(prepared.holding_inputs, extension)
    unit = dict(schema=raw.PLAN_SCHEMA, activity_snapshot_sha256=base.activity_snapshot_sha256,
        base_quote_snapshot_sha256=base.quote_snapshot_sha256, calendar_sha256=raw.calendar_sha256(calendar),
        episodes=[dict(code=LG, start=str(calendar[61]), end=str(calendar[raw_end])),
                  dict(code=LX, start=str(calendar[66]), end=str(calendar[raw_end]))], **raw.UNITS)
    observations = []
    for episode in unit["episodes"]:
        for index, day in enumerate(calendar):
            if not episode["start"] <= str(day) <= episode["end"]:
                continue
            code, opening = episode["code"], 200 if episode["code"] == LG else 300
            row = dict(ISU_CD=code, BAS_DD=day.strftime("%Y%m%d"), TDD_OPNPRC=str(opening),
                TDD_HGPRC=str(opening + 2), TDD_LWPRC=str(opening - 1), TDD_CLSPRC=str(opening + 1),
                ACC_TRDVOL="9007199254740993", ACC_TRDVAL="1000")
            state = (states or {}).get((code, index))
            if state == "zero": row.update(dict.fromkeys(RAW_FIELDS, "0"))
            elif state in ("locked", "frozen"):
                row.update(dict.fromkeys(RAW_FIELDS[:4], str(opening + 1)))
                if state == "frozen": row.update(ACC_TRDVOL="0", ACC_TRDVAL="0")
            elif state == "invalid": row["TDD_OPNPRC"] = "NULL"
            observations.extend(extract_raw_episode_targets([] if state == "absent" else [row],
                dict(service="stk_bydd_trd", bas_dd=row["BAS_DD"], targets=[code]), "2026-10-10T00:00:00+00:00", "9" * 64))
    unit_bytes = saved.encoded(unit)
    artifact = raw.encode_raw_artifact(base, tuple(observations), saved.digest(unit_bytes))
    view = raw.restore_raw_quote_view(base, artifact, saved.digest(artifact), unit_bytes, saved.digest(unit_bytes))
    declarations.update(windows=[], noops=[], unresolved=[], quote_snapshot_sha256=view.quote_snapshot_sha256,
                        required_scope_sha256=prepared.read_scope_sha256)
    evidence["references"] = {key: value for key, value in evidence["references"].items()
                              if key in ("event", "carry", "old", "retained", "new")}
    for row in evidence["bases"].values():
        row["quote_snapshot_sha256"] = view.quote_snapshot_sha256
        if row["code"] in TARGET_CODES:
            row.update(raw_close="301", adjusted_close="301")
    ordinary = provisional_exit_bounds(prepared.selections_d1, prepared.selections_d2, prepared.holding_inputs,
        parameters, prepared.required_code_dates, {row["code"]: row["isin"] for row in prepared.expected_windows},
        input_sha256=prepared.input_sha256)
    payloads = {**{role: bundles[0]["files"][name] for role, name in module.holding.BL_ROLES.items()},
        **{role: bundles[1]["files"][name] for role, name in module.BM_ROLES.items()},
        "bl_verification": bundles[0]["verification"], "bj_scope": bundles[0]["bj_scope"], "bm_verification": bundles[1]["verification"]}
    original_pins = {role: saved.digest(value) for role, value in payloads.items()}
    quote_receipt = dict(verified=True, scope="BP_new_output_typed_rows_exact_extension_scope_composite_and_publication_links_only",
        file_sha256={"quote-extension.json": saved.digest(saved.encoded(envelope))},
        reference_sha256={"bm-read-scope.json": original_pins["bm_read_scope"], "bm-verification.json": original_pins["bm_verification"]},
        activity_snapshot_sha256=base.activity_snapshot_sha256, original_quote_snapshot_sha256=base.original.quote_snapshot_sha256,
        extension_quote_snapshot_sha256=extension.quote_snapshot_sha256, composite_quote_snapshot_sha256=base.quote_snapshot_sha256,
        D1_completed_studies=0, D2_completed_studies=0,
        **dict.fromkeys(("database_access", "api_access", "returns_computed", "historical_vintage_certified", "price_basis_certified", "event_coverage_certified"), False))
    days = sorted({row.requested_bas_dd for row in observations})
    summary = dict(calendar_sessions=len(calendar), potential_origins=2, raw_unique_days=len(days), raw_code_dates=len(observations),
        cached_responses=0, new_requests=len(days), cached_code_dates=0, new_code_dates=len(observations),
        cache_geometry_unique_days=0, aq_responses=0, ar_original_responses=0, ar_recovery_responses=0, bp_raw_responses=0,
        observed_rows=sum(row.status == "observed" for row in observations), unresolved_rows=sum(row.status != "observed" for row in observations),
        no_trade_zero_price_rows=sum(row.state == "observed_no_trade_zero_prices" for row in observations))
    raw_receipt = dict(schema="bq-raw-episode-independent-verification-v1", verified=True, expected_commit="a" * 40,
        file_sha256={name: saved.digest(name.encode()) for name in module.RAW_FILES},
        publication_sha256={name: saved.digest(name.encode()) for name in ("receipt.json", "trial-receipts.jsonl")},
        activity_snapshot_sha256=base.activity_snapshot_sha256, base_quote_snapshot_sha256=base.quote_snapshot_sha256,
        calendar_sha256=raw.calendar_sha256(calendar), unit_plan_sha256=saved.digest(unit_bytes), acquisition_plan_sha256=saved.digest(b"plan"),
        response_dependency_pins={f"response-{number:04d}-stk_bydd_trd-{day}.json": dict(sha256="9" * 64, bytes=100) for number, day in enumerate(days, 1)},
        response_pin_source="root_frozen_new_response_manifest_before_body_reads", summary=summary,
        new_response_bodies_read=len(days), new_code_dates_verified=len(observations), cached_code_dates_structurally_verified=0,
        cached_source_bodies_read=0, upstream_inputs_read=0, new_response_row_extract_verified=True, raw_response_bodies_read=True,
        current_retrieval_only=True, resource_records_verified=True, durable_start_before_recorded_requests_verified=True,
        **dict.fromkeys(("credential_access", "database_access", "api_access", "cached_body_truth_independently_verified",
            "cached_row_positions_independently_verified", "missing_market_certifies_market_identity",
            "resource_enforcement_independently_verified", "source_files_reread", *module.RAW_FALSE), False),
        **dict.fromkeys(("new_source_requests", "automatic_retries", "D1_completed_studies", "D2_completed_studies"), 0))
    raw_receipt.update({name: saved.digest(name.encode()) for name in ("helper_sha256", "protocol_sha256", "producer_sha256",
        "caller_sha256", "credential_reader_sha256", "verification_registration_sha256", "scope_review_sha256")})
    raw_receipt["file_sha256"].update({"raw-episode-quotes.json": saved.digest(artifact), "unit-plan.json": saved.digest(unit_bytes),
        "acquisition-plan.json": raw_receipt["acquisition_plan_sha256"]})
    raw_receipt["specification_sha256"] = raw_receipt["file_sha256"]["specification.json"]
    documents = dict(ordinary_exit_scope=ordinary, quote_extension=envelope, quote_verification=quote_receipt,
        raw_episode_verification=raw_receipt, reviewed_terms=declarations, basis_evidence=evidence, unresolved_priority_intervals=[])
    payloads.update({role: saved.encoded(value) for role, value in documents.items()})
    payloads.update(raw_episode_quotes=artifact, raw_episode_unit_plan=unit_bytes)
    declared, store = {}, {}
    for role, filename in module.INPUT_FILES.items():
        directory = "bl" if role in module.holding.BL_ROLES else "bm" if role in module.BM_ROLES else role
        path, value = tmp_path / "evidence" / directory / filename, payloads[role]
        declared[role] = dict(path=str(path), sha256=saved.digest(value))
        store[path] = value
    spec = dict(schema=module.SCHEMA, study_id=module.SCHEMA, mode="discovery_raw_component_lifetime_scope", promotion_allowed=False,
        window=dict(saved.WINDOW), reference_specification=saved.REFERENCE, parameters=parameters, inputs=declared)
    module.validate_spec(spec, {"parameters": parameters})
    return spec, store, documents, prepared, view


def repin(spec, store, role, value):
    value = value if type(value) is bytes else saved.encoded(value)
    spec["inputs"][role]["sha256"] = saved.digest(value)
    store[Path(spec["inputs"][role]["path"])] = value


def statuses(path):
    return [saved.strict_json(line)["status"] for line in path.read_bytes().splitlines()]


def frozen(monkeypatch, spec, failure=None):
    root = SOURCE.parents[2]
    monkeypatch.chdir(root)
    sources = dict(files=[dict(path=module.SOURCE_PATH, sha256=saved.digest(SOURCE.read_bytes()))], code_version="a" * 40)
    if failure == "uncommitted": sources["files"] = []
    if failure == "source_pin": sources["files"][0]["sha256"] = "e" * 64
    calls = []
    def freeze(*a, **kw):
        assert kw["spec_validator"] is module.validate_spec
        module.validate_spec(spec, {"parameters": spec["parameters"]})
        calls.append(1)
        return {**sources, "code_version": "b" * 40 if failure == "drift" and len(calls) > 1 else "a" * 40}, saved.encoded(spec), spec
    monkeypatch.setattr(saved, "freeze_sources", freeze)
    return root, calls


def test_real_upstream_packages_one_combined_raw_proof_no_engine_or_extra_io(tmp_path, monkeypatch):
    spec, store, _, prepared, view = fixture(tmp_path, monkeypatch)
    before, calls = deepcopy((spec, store)), []
    def forbidden(*a, **kw): pytest.fail("unexpected DB/network/performance invocation")
    for owner, name in ((sqlite3, "connect"), (socket, "create_connection"), (paired, "paired_replay"), (paired, "replay_synthetic")):
        monkeypatch.setattr(owner, name, forbidden)
    original = module.component_exit_bounds
    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "component_exit_bounds", capture)
    output = tmp_path / "proof"
    output.mkdir(mode=0o700)
    inputs = saved.PinnedInputs(store.__getitem__)
    with localcontext() as context:
        context.prec = 4
        result = module.evaluate_inputs(spec, inputs, output)
    assert len(calls) == 1 and (spec, store) == before
    assert len(inputs.manifest) == 27 and all(row["read_status"] == "hash_verified" for row in inputs.manifest)
    wrapper = saved.strict_json((output / "component-exit-scope.json").read_bytes())
    terms = saved.strict_json((output / "canonical-terms.json").read_bytes())
    assert wrapper["schema"] == module.WRAPPER_SCHEMA and wrapper["component_scope"]["schema"] == "activity-raw-component-lifetime-bounds-v1"
    assert result["component_scope_sha256"] == wrapper["component_scope"]["component_scope_sha256"]
    assert result["terms_sha256"] == saved.digest((output / "canonical-terms.json").read_bytes())
    assert terms["status"] == "terms_only" and terms["final_coverage_verified"] is False
    assert terms["basis_evidence"]["bases"]["new"]["raw_close"] == terms["basis_evidence"]["bases"]["new"]["adjusted_close"] == "301"
    assert view.base.panel[LX].closes[66] == 100 and view.panel[LX].closes[66] == 301
    assert result["summary"]["entries_by_arm"] == {"D1": 1, "D2": 1} and result["summary"]["shortened_entries"] == 2
    assert result["summary"]["full_base_code_dates"] == len(view.base.requested_coordinates)
    assert result["original_input_sha256"] == prepared.input_sha256
    assert all(result[name] is False for name in module.FALSE_FLAGS)
    assert result["prior_cached_body_truth_independently_verified"] is False
    assert result["prior_independent_receipts_relied_on"] is True and result["D1_completed_studies"] == result["D2_completed_studies"] == 0


@pytest.mark.parametrize("mutation", ["upstream", "bp_pin", "bp_ref", "bp_cert", "raw_pin", "raw_files", "raw_base", "raw_calendar",
    "raw_spec", "raw_plan", "raw_publication", "raw_registration", "raw_commit", "raw_cert", "raw_cached_truth",
    "raw_studies", "raw_count", "raw_dependency", "raw_dependency_sha", "raw_dependency_day", "basis_half", "basis_adjusted", "basis_absent", "terms_windows", "terms_scope",
    "ordinary", "priority"])
def test_repinned_inputs_refuse_incoherent_lineage_or_units_before_outputs(tmp_path, monkeypatch, mutation):
    spec, store, documents, _, _ = fixture(tmp_path, monkeypatch)
    role, value = "raw_episode_verification", documents["raw_episode_verification"]
    if mutation == "upstream":
        role = "bl_verification"
        value = saved.strict_json(store[Path(spec["inputs"][role]["path"])])
        value["verified"] = False
    elif mutation.startswith("bp_"):
        role, value = "quote_verification", documents["quote_verification"]
        if mutation == "bp_pin": value["file_sha256"]["quote-extension.json"] = "f" * 64
        elif mutation == "bp_ref": value["reference_sha256"]["bm-read-scope.json"] = "f" * 64
        else: value["price_basis_certified"] = True
    elif mutation == "raw_pin": value["file_sha256"]["raw-episode-quotes.json"] = "f" * 64
    elif mutation == "raw_files": value["file_sha256"].pop("input-manifest.json")
    elif mutation == "raw_base": value["base_quote_snapshot_sha256"] = "f" * 64
    elif mutation == "raw_calendar": value["calendar_sha256"] = "f" * 64
    elif mutation == "raw_spec": value["specification_sha256"] = "f" * 64
    elif mutation == "raw_plan": value["unit_plan_sha256"] = "f" * 64
    elif mutation == "raw_publication": value["publication_sha256"].pop("receipt.json")
    elif mutation == "raw_registration": value["verification_registration_sha256"] = "bad"
    elif mutation == "raw_commit": value["expected_commit"] = "bad"
    elif mutation == "raw_cert": value["source_truth_certified"] = True
    elif mutation == "raw_cached_truth": value["cached_body_truth_independently_verified"] = True
    elif mutation == "raw_studies": value["D1_completed_studies"] = False
    elif mutation == "raw_count": value["summary"]["no_trade_zero_price_rows"] += 1
    elif mutation == "raw_dependency": next(iter(value["response_dependency_pins"].values()))["bytes"] = True
    elif mutation == "raw_dependency_sha": next(iter(value["response_dependency_pins"].values()))["sha256"] = "f" * 64
    elif mutation == "raw_dependency_day":
        dependencies = value["response_dependency_pins"]
        dependencies["response-9999-stk_bydd_trd-20991231.json"] = dependencies.pop(next(iter(dependencies)))
    elif mutation.startswith("basis_"):
        role, value = "basis_evidence", documents["basis_evidence"]
        if mutation == "basis_half": value["bases"]["new"]["raw_close"] = "150.5"
        elif mutation == "basis_adjusted": value["bases"]["new"].update(raw_close="100", adjusted_close="100")
        else: value["bases"]["new"]["session"] = "2020-01-01"
    elif mutation.startswith("terms_"):
        role, value = "reviewed_terms", documents["reviewed_terms"]
        if mutation == "terms_windows": value["windows"] = [{}]
        else: value["required_scope_sha256"] = "f" * 64
    elif mutation == "ordinary":
        role, value = "ordinary_exit_scope", documents["ordinary_exit_scope"]
        value["entries"][0]["end_on"] = "2020-01-01"
    else: role, value = "unresolved_priority_intervals", {}
    repin(spec, store, role, value)
    output = tmp_path / "proof"
    output.mkdir(mode=0o700)
    with pytest.raises(ValueError): module.evaluate_inputs(spec, saved.PinnedInputs(store.__getitem__), output)
    assert list(output.iterdir()) == []


def test_all_potential_raw_scope_must_precede_output_even_without_any_book(tmp_path, monkeypatch):
    spec, store, _, _, _ = fixture(tmp_path, monkeypatch, raw_end=90)
    output = tmp_path / "proof"
    output.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="potential raw scope"):
        module.evaluate_inputs(spec, saved.PinnedInputs(store.__getitem__), output)
    assert list(output.iterdir()) == []


def test_contradictory_later_mark_rejects_the_reviewed_old_role_before_proof(tmp_path, monkeypatch):
    spec, store, documents, _, view = fixture(tmp_path, monkeypatch)
    evidence = documents["basis_evidence"]
    earlier = str(view.calendar[63])
    evidence["bases"]["old"]["session"] = earlier
    evidence["references"]["old"].update(start=earlier, end=earlier)
    repin(spec, store, "basis_evidence", evidence)
    monkeypatch.setattr(module, "component_exit_bounds", lambda *a, **kw: pytest.fail("contradictory mark reached proof"))
    output = tmp_path / "proof"
    output.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="later observable pre-event mark"):
        module.evaluate_inputs(spec, saved.PinnedInputs(store.__getitem__), output)
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("state", ["positive", "locked", "zero", "absent", "invalid", "frozen", "unrequested"])
def test_raw_parent_old_mark_diagnostic_distinguishes_no_trade_absence_and_unknown(tmp_path, monkeypatch, state):
    spec, store, documents, prepared, view = fixture(tmp_path, monkeypatch, states={(LG, 64): state})
    old = raw.raw_price_basis(view, LG, view.calendar[63])
    # This diagnostic only consumes already restored rows, not a forged quote view.
    event = SimpleNamespace(old_basis=old, effective_on=view.calendar[65], event_id="raw-parent")
    monkeypatch.setattr(module, "CompulsorySpinOff", SimpleNamespace)
    if state == "unrequested":
        view = replace(view, raw_observations=tuple(row for row in view.raw_observations
            if (row.code, row.requested_bas_dd) != (LG, view.calendar[64].strftime("%Y%m%d"))))
    check = module._pre_effective_marks(SimpleNamespace(events=(event,)), view)[0]
    expected = "contradictory_later_observable" if state in ("positive", "locked") else "unresolved" if state in ("invalid", "unrequested") else "no_later_observable_in_retained_quotes"
    assert check["state"] == expected and check["basis_role_certified"] is False
    field = {"zero": "no_trade_zero_price_sessions", "absent": "absent_requested_sessions", "invalid": "invalid_sessions",
             "frozen": "frozen_sessions", "unrequested": "unrequested_sessions"}.get(state, "later_observable_sessions")
    assert check[field] == 1


@pytest.mark.parametrize("failure", [None, "pin", "reader", "proof", "drift", "uncommitted", "source_pin"])
def test_logged_start_before_27_reads_private_receipts_failure_and_no_retry(tmp_path, monkeypatch, failure):
    spec, store, _, _, _ = fixture(tmp_path, monkeypatch)
    root, freezes = frozen(monkeypatch, spec, failure)
    output, runs, reads = tmp_path / "proof", tmp_path / "runs.jsonl", []
    if failure == "pin": spec["inputs"]["reviewed_terms"]["sha256"] = "f" * 64
    def reader(path):
        assert statuses(runs) == ["started"]
        assert (output / "specification.json").exists() and (output / "source-manifest.json").exists()
        reads.append(path)
        if failure == "reader": raise OSError("private raw values never print")
        return store[path]
    if failure == "proof":
        def fail(*a, **kw): raise ValueError("private raw values never print")
        monkeypatch.setattr(module, "component_exit_bounds", fail)
    if failure:
        with pytest.raises((ValueError, OSError)):
            module.run_preflight(root, root / "synthetic-spec.json", output, runs, reader=reader)
        if failure in ("uncommitted", "source_pin"):
            assert not runs.exists() and not output.exists() and not reads
            return
        failure_bytes = (output / "failure.json").read_bytes()
        assert b"private raw" not in failure_bytes
        receipt = saved.strict_json(failure_bytes)
        assert receipt["partial_package_preserved"] is True and receipt["automatic_retries"] == receipt["D1_completed_studies"] == receipt["D2_completed_studies"] == 0
        assert saved.strict_json((output / "input-manifest.json").read_bytes()) == receipt["actual_inputs_attempted"]
        assert (output / "resource-manifest.json").exists() and not (output / "result.json").exists()
    else:
        record = module.run_preflight(root, root / "synthetic-spec.json", output, runs, reader=reader)
        assert reads == [Path(spec["inputs"][role]["path"]) for role in module.INPUT_FILES] and len(set(reads)) == 27
        assert len(freezes) == 2 and set(record["result"]["output_sha256"]) == set(module.OUTPUT_FILES)
        assert record["result"]["output_sha256"] == {path.name: saved.digest(path.read_bytes()) for path in output.iterdir()}
        assert stat.S_IMODE(output.stat().st_mode) == 0o700
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())
    assert statuses(runs) == ["started", "failed" if failure else "completed"]


@pytest.mark.parametrize("alias", ["input_log", "source_log", "input_output", "checkout_output", "output_log"])
def test_write_aliases_refused_before_start(tmp_path, monkeypatch, alias):
    spec, _, _, _, _ = fixture(tmp_path, monkeypatch)
    root, _ = frozen(monkeypatch, spec)
    output, runs = tmp_path / "proof", tmp_path / "runs.jsonl"
    if alias == "input_log": runs = Path(spec["inputs"]["raw_episode_quotes"]["path"])
    elif alias == "source_log": runs = SOURCE
    elif alias == "input_output": output = Path(spec["inputs"]["reviewed_terms"]["path"]).parent / "proof"
    elif alias == "checkout_output": output = root / "synthetic-proof"
    else: runs = output / "runs.jsonl"
    with pytest.raises(ValueError):
        module.run_preflight(root, root / "synthetic-spec.json", output, runs, reader=lambda p: pytest.fail("unexpected input read"))
    assert not output.exists()


def test_bounded_population_reader_and_existing_output_preserved(tmp_path, monkeypatch):
    spec, store, _, _, _ = fixture(tmp_path, monkeypatch)
    root, _ = frozen(monkeypatch, spec)
    population, other = [], []
    monkeypatch.setattr(module.holding, "_read_population", lambda p: population.append(p) or store[p])
    monkeypatch.setattr(saved, "read_private", lambda p: other.append(p) or store[p])
    output, runs = tmp_path / "proof", tmp_path / "runs.jsonl"
    module.run_preflight(root, root / "synthetic-spec.json", output, runs)
    assert population == [Path(spec["inputs"]["bl_population_audit"]["path"])] and len(other) == 26
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(FileExistsError):
        module.run_preflight(root, root / "synthetic-spec.json", output, runs, reader=lambda p: pytest.fail("unexpected reread"))
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}
    assert statuses(runs) == ["started", "completed", "started", "failed"]


@pytest.mark.parametrize("mutation", ["role", "parameter_type", "promotion", "window", "filename", "alias", "parent", "relative"])
def test_spec_keeps_exact_roles_reference_encoding_and_paths(tmp_path, monkeypatch, mutation):
    spec, _, _, _, _ = fixture(tmp_path, monkeypatch)
    reference = {"parameters": deepcopy(spec["parameters"])}
    if mutation == "role": spec["inputs"].pop("raw_episode_verification")
    elif mutation == "parameter_type": spec["parameters"]["threshold"] = 3
    elif mutation == "promotion": spec["promotion_allowed"] = True
    elif mutation == "window": spec["window"]["start"] = "2018-01-01"
    elif mutation == "filename": spec["inputs"]["raw_episode_unit_plan"]["path"] += ".wrong"
    elif mutation == "alias": spec["inputs"]["raw_episode_verification"]["path"] = spec["inputs"]["bm_verification"]["path"]
    elif mutation == "parent": spec["inputs"]["bm_read_scope"]["path"] = str(tmp_path / "other" / "read-scope.json")
    else: spec["inputs"]["reviewed_terms"]["path"] = "reviewed-terms.json"
    with pytest.raises(ValueError): module.validate_spec(spec, reference)


def test_role_ceiling_stops_before_json_decode_and_proof(tmp_path, monkeypatch):
    spec, store, _, _, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setitem(module.INPUT_MAX_BYTES, "bl_result", 1)
    reads = []
    def reader(path):
        reads.append(path)
        return store[path]
    output = tmp_path / "proof"
    output.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="fixed role ceiling"):
        module.evaluate_inputs(spec, saved.PinnedInputs(reader), output)
    assert reads == [Path(spec["inputs"]["bl_result"]["path"])] and list(output.iterdir()) == []


@pytest.mark.parametrize("failure", [False, True])
def test_cli_prints_only_completion_metadata_or_sanitized_failure(monkeypatch, capsys, failure):
    def run(*a, **kw):
        if failure: raise ValueError("private source raw prices")
        return {"result": dict(status="provisional_raw_component_scope_planned", D1_completed_studies=0)}
    monkeypatch.setattr(module, "run_preflight", run)
    assert module.main(["--spec", "/synthetic/spec", "--output-dir", "/synthetic/proof", "--runs-path", "/synthetic/runs"]) == int(failure)
    output = capsys.readouterr().out.encode()
    assert b"private source" not in output
    assert saved.strict_json(output)["status"] == ("failed" if failure else "provisional_raw_component_scope_planned")
