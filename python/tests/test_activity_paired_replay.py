"""Pure paired integration over fabricated producer outputs, without evidence IO."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3
import weakref

import pytest

from research import activity_paired_replay as PAIRED
from research import activity_holding_preflight as producer
from research import activity_preflight as saved
from research import activity_price_parity as prices
from research.activity_exit_bounds import provisional_exit_bounds
from research.activity_holding_inputs import _FINGERPRINT_SCOPE, _provenance
from research.activity_partition_selection import selections_from_bl
from test_activity_partition_selection import package


def complete_synthetic_upstream(data, bj):
    """Add real upstream boundary fields and conservative BJ window geometry.

    The existing connected producer fixture deliberately tests only the BJ upper
    bound. This extension supplies BJ's full output contract before fixture byte
    pins and the independent BL receipt are registered.
    """
    bl = data["result.json"]
    bl.update(database_access=False, D1_completed_studies=0, D2_completed_studies=0)
    calendar = [date.fromisoformat(day) for day in data["read-scope.json"]["calendar_dates"]]
    ending = calendar.index(date.fromisoformat(bj["evaluation_end"]))
    schedule = {row["formation"]: row["arms"] for row in bl["paired_schedule"]}
    windows, union = [], {}
    for row in sorted(data["normal-input-audit.json"], key=lambda row: (row["formation"], row["code"])):
        formation = date.fromisoformat(row["formation"]).strftime("%Y%m%d")
        arms = []
        for arm in schedule[formation]:
            start = calendar.index(date.fromisoformat(arm["execution_on"]))
            days = calendar[start:ending + 1]
            arms.append({
                "session_lag": arm["session_lag"], "possible_entry_on": arm["execution_on"],
                "selection_at": arm["selection_at"], "scheduled_due_on": arm["due_on"],
                "investigation_start": days[0].isoformat(), "investigation_end": days[-1].isoformat(),
                "investigation_sessions": len(days),
            })
            union.setdefault(row["code"], set()).update(days)
        windows.append({"formation": formation, "code": row["code"], "arms": arms})
    bj["possible_windows"] = windows
    bj["requested_code_dates"] = {
        code: [day.isoformat() for day in sorted(days)] for code, days in sorted(union.items())
    }
    bj["summary"] = {
        "potential_windows": len(windows), "potential_codes": len(union),
        "arm_windows": {"D1": len(windows), "D2": len(windows)},
        "required_unique_code_dates": sum(map(len, union.values())),
    }


def synthetic_scan(path, plan, inputs, scope_pin, *, mode):
    """Supply one fabricated typed scan in memory using existing encoding helpers."""
    calendar = tuple(date.fromisoformat(day) for day in plan["calendar_dates"])
    requested = plan["intersection_code_dates"]
    fingerprint = dict(schema=prices.SCHEMA + ":typed-scan-v1", calendar_dates=plan["calendar_dates"],
        read_scope_sha256=scope_pin, intersection_code_dates=requested,
        panel=[[["integer", "1"], ["text", "20190102"], ["text", "20260918"]]], progress=[], rows=[])
    raw_rows = {}
    for code, days in requested.items():
        progress = (days[0].replace("-", ""), days[-1].replace("-", ""), len(days), 0,
                    "done", "2026-10-09T00:00:00+00:00")
        fingerprint["progress"].append([code, [[prices._typed(value) for value in progress]]])
        for iso in days:
            index = calendar.index(date.fromisoformat(iso))
            raw = [iso.replace("-", ""), "200" if index == 62 else "100", "201", "99", "100", "100", "10"]
            if ((mode == "absent_entry" and index == 61) or (mode == "deferred_exit" and index == 91)
                    or (mode == "pending_final_cash" and index > 65)):
                raw = None
            elif (mode == "frozen_entry" and index == 61) or (mode == "terminal_pending" and index >= 91):
                raw = [raw[0], "100", "100", "100", "100", "100", "0"]
            elif mode == "unresolved" and index == 65:
                raw[4] = None
            typed = "absent" if raw is None else [prices._typed(value) for value in raw]
            fingerprint["rows"].append([code, iso, typed])
            raw_rows[code, iso.replace("-", "")] = raw
    quote = saved.digest(saved.encoded(fingerprint))
    received = "2026-10-09T00:00:00+00:00"
    rows = {}
    for (code, compact), raw in raw_rows.items():
        day = date.fromisoformat(compact)
        rows[code, compact] = dict(values=None if raw is None else dict(zip(prices.FIELDS, raw[1:])),
            typed_values=None if raw is None else [prices._typed(value) for value in raw],
            provenance=_provenance(day, calendar, received, quote))
    inputs.manifest.append(dict(role="scan_database", path=str(path), mode="ro", columns=list(prices.READ_COLUMNS),
        read_scope_sha256=scope_pin, read_status="snapshot_consumed", typed_snapshot_sha256=quote,
        fetched_row_count=sum(row is not None for row in raw_rows.values()), snapshot_received_at=received))
    return dict(rows=rows, fingerprint=fingerprint, typed_snapshot_sha256=quote, snapshot_received_at=received,
        fingerprint_scope=_FINGERPRINT_SCOPE, whole_database_hash=False, BI_snapshot_equality_checked=False,
        progress_counts_recomputed_from_prices=False)


def fixture(monkeypatch, *, mode="ordinary", cash=False, late=False):
    """Reuse producer-shaped source proofs and the real BM audit encoder, with IO stubbed.

    BL wrapper fields follow test_activity_holding_preflight.fixture; that helper
    itself creates a DB, so it is deliberately not invoked. The complete proof
    population is reused from package(), not rebuilt from holding quotes.
    """
    result, population, normal, calendar, parameters = package()
    calendar += (calendar[-1] + timedelta(days=1), calendar[-1] + timedelta(days=2))
    parameters.update(threshold=3.0, slippage_bps_per_side=5.0, commission_bps_per_side=1.77, slots=1, seed=20261005)
    if cash:
        for arm in normal[0]["arms"].values():
            arm["candidate"]["operating_baseline"] = "fail"
            arm.update(selected=False, exclusion_reasons=["operating_baseline_fail"])
        for arm in result["arms"].values():
            arm.update(signals=[], signal_windows=0)
        result["summary"].update(signal_windows={"D1": 0, "D2": 0}, paired_signal_union=0)
    good_selections = {arm: selections_from_bl(result, population, normal, calendar, parameters, arm)
                       for arm in ("D1", "D2")}
    if late:
        # Deliberately repinned invalid source metadata for the connection boundary.
        normal[0]["inputs"]["activity"][calendar[60].isoformat()]["availability"]["available_at"] = (
            good_selections["D1"][0].selection_at + timedelta(seconds=1)).isoformat()
    bi = dict(path="/synthetic/bi.json", sha256="1" * 64)
    bl_spec = dict(schema="activity-normal-input-assembly-v1", study_id="activity-normal-input-assembly-v1",
        mode="discovery_normal_input_assembly", promotion_allowed=False, reference_specification=saved.REFERENCE,
        window=saved.WINDOW, parameters=parameters, inputs={"bi_result": bi})
    manifest = [dict(role="bi_result", **bi, expected_sha256=bi["sha256"], bytes=12, read_status="hash_verified")]
    bl_source = dict(files=[dict(path="configs/research/discovery/activity-normal-input-assembly-v1.json",
        bytes=len(saved.encoded(bl_spec)), sha256=saved.digest(saved.encoded(bl_spec)))], code_version="a" * 40,
        runtime_dependencies="stdlib_and_manifested_local_Python_only")
    activity = "3" * 64
    result.update(input_manifest=manifest, activity_input=dict(source_sha256=bi["sha256"],
        upstream_dataset_sha256=activity, typed_OHLC_fingerprint_recomputed=False))
    scope = dict(schema=bl_spec["schema"], calendar_dates=[day.isoformat() for day in calendar],
        restored_activity_source=bi, upstream_dataset_sha256=activity, typed_OHLC_fingerprint_recomputed=False,
        database_access=False, returns_computed=False, requested_code_dates={"000001": [calendar[60].isoformat()]})
    restored = dict(upstream_result_sha256=bi["sha256"], upstream_dataset_sha256=activity,
        typed_OHLC_fingerprint_recomputed=False,
        restored_observations_by_code={"000001": {calendar[60].isoformat(): {"state": "observed"}}})
    bl = dict(zip(producer.BL_FILES, (result, bl_spec, bl_source, manifest, scope,
        {"aq_raw_responses": [], "ar_raw_responses": []}, population, normal, restored)))
    bj = dict(schema="activity-price-action-scope-v1", status="conservative_original_issue_scope_planned",
        scope_kind="upper_bound_for_BI_potential_subset_only",
        interval_policy="every index session from possible entry through fixed evaluation end inclusive",
        evaluation_end=parameters["end"], calendar_dates=[day.strftime("%Y%m%d") for day in calendar],
        paired_schedule=result["paired_schedule"], returns_computed=False, holding_selection_executed=False,
        promotion_allowed=False, database_access=False)
    complete_synthetic_upstream(bl, bj)
    result.update(source_manifest_sha256=saved.digest(saved.encoded(bl_source)),
        specification_sha256=saved.digest(saved.encoded(bl_spec)),
        audit_sha256={name: saved.digest(saved.encoded(bl[name])) for name in producer.BL_FILES[4:]})
    bl_bytes = {name: saved.encoded(value) for name, value in bl.items()}
    hashes = {name: saved.digest(payload) for name, payload in bl_bytes.items()}
    receipt = dict(verified=True, scope=producer.RECEIPT_SCOPE, file_sha256=hashes, summary=result["summary"],
        package_sha256=saved.digest(producer._canonical([[name, hashes[name]] for name in producer.BL_FILES])),
        **{name: False for name in producer.RECEIPT_FALSE})
    memory, declarations = {}, {}
    for role, value in {**{role: bl_bytes[name] for role, name in producer.BL_ROLES.items()},
                        "bl_verification": saved.encoded(receipt), "bj_scope": saved.encoded(bj)}.items():
        name = producer.BL_ROLES.get(role, role + ".json")
        path = Path("/synthetic/evidence") / name
        memory[path] = value
        declarations[role] = dict(path=str(path), sha256=saved.digest(value))
    declarations["scan_database"] = dict(path="/synthetic/scan.db", mode="ro", columns=list(prices.READ_COLUMNS))
    spec = dict(schema=producer.SCHEMA, study_id=producer.SCHEMA, mode="discovery_holding_price_diagnostic",
        promotion_allowed=False, window=saved.WINDOW, reference_specification=saved.REFERENCE,
        parameters=parameters, inputs=declarations)
    captures = {}
    with monkeypatch.context() as build:
        build.setattr(saved, "write_exclusive", lambda path, raw: captures.__setitem__(path.name, raw))
        build.setattr(prices, "load_scan", lambda *args: synthetic_scan(*args, mode=mode))
        if late:
            build.setattr(producer, "selections_from_bl", lambda *args: good_selections[args[-1]])
        inputs = saved.PinnedInputs(memory.__getitem__)
        bm_result = producer.evaluate_inputs(spec, inputs, Path("/synthetic/output"))
    captures["specification.json"] = saved.encoded(spec)
    sources = dict(files=[dict(path="configs/research/discovery/activity-holding-price-preflight-v1.json",
        sha256=saved.digest(captures["specification.json"]), bytes=len(captures["specification.json"]))],
        runtime_dependencies="stdlib_and_manifested_local_Python_only", code_version="b" * 40)
    captures["source-manifest.json"] = saved.encoded(sources)
    captures["input-manifest.json"] = saved.encoded(inputs.manifest)
    bm_result.update(source_manifest_sha256=saved.digest(captures["source-manifest.json"]),
                     specification_sha256=saved.digest(captures["specification.json"]))
    captures["result.json"] = saved.encoded(bm_result)
    external = {"bl-result.json": bl_bytes["result.json"], "bl-read-scope.json": bl_bytes["read-scope.json"],
                "bj-result.json": saved.encoded(bj)}
    # A fabricated independent receipt is a package-join input, not a claim
    # that this unit fixture independently certified itself.
    bm_hashes = {name: saved.digest(captures[name]) for name in PAIRED.BM_FILES}
    bm_receipt = dict(verified=True, scope="BM_new_output_typed_rows_scope_and_internal_lineage_only",
        file_sha256=bm_hashes, package_sha256=saved.digest(producer._canonical(
            [[name, bm_hashes[name]] for name in PAIRED.BM_FILES])),
        evidence_sha256={name: saved.digest(value) for name, value in external.items()},
        activity_snapshot_sha256=bm_result["activity_snapshot_sha256"],
        quote_snapshot_sha256=bm_result["quote_snapshot_sha256"], summary=bm_result["summary"],
        D1_completed_studies=0, D2_completed_studies=0,
        original_source_truth_verified=False, database_transaction_verified=False,
        original_BL_verification_receipt_reread=False, whole_database_hash_verified=False,
        BI_snapshot_equality_checked=False, historical_vintage_certified=False,
        price_basis_certified=False, event_coverage_certified=False, returns_computed=False,
        original_source_inputs_opened=False, database_access=False, api_access=False)
    requested = saved.strict_json(captures["read-scope.json"])["requested_code_dates"]
    windows, refs = [], {}
    for code, days in requested.items():
        isin = next(signal["isin"] for signal in result["arms"]["D1"]["signals"] if signal["code"] == code)
        windows.append(dict(code=code, isin=isin, start=days[0], end=days[-1], review_state="reviewed_no_event",
                            event_ids=[], evidence_refs=[code]))
        refs[code] = dict(code=code, isin=isin, start=days[0], end=days[-1], raw_sha256="d" * 64,
            review_sha256="e" * 64, coordinate="synthetic-review:section-1", reviewed_on=days[-1],
            finding="confirmed_no_compulsory_action", event_ids=[])
    coverage = dict(schema="activity-reviewed-actions-v1", status="reviewed_complete", windows=windows,
        events=[], noops=[], unresolved=[], activity_snapshot_sha256=activity,
        quote_snapshot_sha256=bm_result["quote_snapshot_sha256"], required_scope_sha256=bm_result["read_scope_sha256"],
        calendar_sha256=saved.digest(producer._canonical([day.isoformat() for day in calendar])))
    bases = {}
    if mode == "pending_final_cash":
        code, isin = windows[0]["code"], windows[0]["isin"]
        last, record, paid = calendar[65].isoformat(), calendar[66].isoformat(), (calendar[-1] + timedelta(days=30)).isoformat()
        coverage["events"] = [dict(event_id="final-1", kind="final_cash_payment", status="verified_paid",
            code=code, isin=isin, last_trading_on=last, record_on=record, paid_on=paid,
            net_cash_per_raw_share="25.1250", basis_id="last", evidence_ref="paid")]
        windows[0].update(review_state="reviewed_supported_events", event_ids=["final-1"])
        refs[code].update(finding="confirmed_supported_events_complete", event_ids=["final-1"])
        refs["paid"] = dict(refs[code], start=paid, end=paid, reviewed_on=paid, finding="verified_final_cash_payment")
        refs["last"] = dict(refs[code], start=last, end=last, finding="verified_last_trading_basis", event_ids=[])
        bases["last"] = dict(code=code, isin=isin, session=last, role="last_trading", raw_close="100",
            adjusted_close="100", quote_snapshot_sha256=bm_result["quote_snapshot_sha256"], evidence_ref="last")
    coverage_raw, evidence_raw = saved.encoded(coverage), saved.encoded(dict(references=refs, bases=bases))
    bl_package = dict(files=bl_bytes, verification=saved.encoded(receipt), bj_scope=saved.encoded(bj))
    bm_package = dict(files=captures, verification=saved.encoded(bm_receipt))
    pins = {**{role: hashes[name] for role, name in producer.BL_ROLES.items()},
            **{role: saved.digest(captures[name]) for role, name in PAIRED.BM_ROLES.items()},
            "bl_verification": saved.digest(bl_package["verification"]), "bj_scope": saved.digest(bl_package["bj_scope"]),
            "bm_verification": saved.digest(bm_package["verification"]), "reviewed_action_coverage": saved.digest(coverage_raw),
            "basis_evidence": saved.digest(evidence_raw)}
    return [bl_package, bm_package, coverage_raw, evidence_raw], parameters, pins, calendar


def run(inputs, parameters, pins):
    return PAIRED.paired_replay(*inputs, parameters=parameters, expected_pins=pins)


def repin_bm(inputs, pins):
    """Refresh synthetic outer links so semantic negative checks exceed stale hashes."""
    payloads = inputs[1]["files"]
    result = saved.strict_json(payloads["result.json"])
    for field, name in (("specification_sha256", "specification.json"),
                        ("source_manifest_sha256", "source-manifest.json"),
                        ("read_scope_sha256", "read-scope.json"),
                        ("holding_input_audit_sha256", "holding-input-audit.json")):
        result[field] = saved.digest(payloads[name])
    payloads["result.json"] = saved.encoded(result)
    hashes = {name: saved.digest(payloads[name]) for name in PAIRED.BM_FILES}
    receipt = saved.strict_json(inputs[1]["verification"])
    receipt.update(file_sha256=hashes,
        package_sha256=saved.digest(producer._canonical([[name, hashes[name]] for name in PAIRED.BM_FILES])))
    inputs[1]["verification"] = saved.encoded(receipt)
    pins.update({role: hashes[name] for role, name in PAIRED.BM_ROLES.items()})
    pins["bm_verification"] = saved.digest(inputs[1]["verification"])


def test_connected_existing_proofs_audit_actions_both_books_and_report_without_io(monkeypatch):
    inputs, parameters, pins, calendar = fixture(monkeypatch)
    before = deepcopy((inputs, parameters, pins))
    monkeypatch.setattr("builtins.open", lambda *a, **kw: pytest.fail("file IO"))
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("DB IO"))
    monkeypatch.setattr(prices, "load_scan", lambda *a, **kw: pytest.fail("new quote scan"))
    calls, replay = [], PAIRED.replay_synthetic

    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return replay(*args, **kwargs)

    monkeypatch.setattr(PAIRED, "replay_synthetic", capture)
    value = run(inputs, parameters, pins)
    saved.encoded(value)
    assert (inputs, parameters, pins) == before
    assert value["initial_nav"] == value["report"]["initial_nav"] == "1"
    assert value["activity_snapshot_sha256"] == "3" * 64 != value["quote_snapshot_sha256"]
    assert calls[0][0][2] is calls[1][0][2] is parameters
    assert calls[0][1]["initial_book"] is calls[1][1]["initial_book"]
    assert calls[0][1]["initial_book"].as_of == calendar[60]
    assert all(call[1]["dataset_sha256"] == value["quote_snapshot_sha256"] for call in calls)
    assert calls[0][1]["events"] is calls[1][1]["events"] == ()
    assert value["selections"]["D1"][0]["source_population_count"] == 6
    assert len(calls[0][0][1]) == 1  # holding panel never reconstructs the six-row source partition
    assert len(value["required_code_dates"]["000001"]) == 69
    one, two = value["report"]["arms"]["D1"], value["report"]["arms"]["D2"]
    assert Decimal(one["path"][0]["session_return"]) < 0
    assert Decimal(two["path"][0]["session_return"]) == 0
    assert one["total_return"] != two["total_return"]
    assert one["filled_entries"] == two["filled_entries"] == one["ordinary_closed_trades"] == 1
    assert value["report"]["signal_overlap"]["rate"] == "0"
    assert value["actual_study_completed"] is value["promotion_allowed"] is False


@pytest.mark.parametrize("mode", ["absent_entry", "frozen_entry", "deferred_exit", "terminal_pending"])
def test_existing_missing_frozen_fill_and_deferred_exit_semantics(monkeypatch, mode):
    inputs, parameters, pins, _ = fixture(monkeypatch, mode=mode)
    value = run(inputs, parameters, pins)
    one, two = value["report"]["arms"]["D1"], value["report"]["arms"]["D2"]
    if mode in ("absent_entry", "frozen_entry"):
        assert one["filled_entries"] == 0 and two["filled_entries"] == 1
        assert value["report"]["target_overlap"]["rate"] == "0"
        assert dict(value["replays"]["D1"]["diagnostics"])["unfilled_entries"] == 1
    elif mode == "deferred_exit":
        assert one["ordinary_closed_trades"] == two["ordinary_closed_trades"] == 1
        assert ["delayed_exits", 1] in value["replays"]["D1"]["diagnostics"]
    else:
        assert one["ordinary_closed_trades"] == two["ordinary_closed_trades"] == 0
        assert one["terminal_open_lots"] == two["terminal_open_lots"] == 1


def test_cash_empty_union_retains_no_quote_hash_and_no_event_inputs(monkeypatch):
    inputs, parameters, pins, _ = fixture(monkeypatch, cash=True)
    value = run(inputs, parameters, pins)
    assert value["required_code_dates"] == {} and value["quote_snapshot_sha256"] is None
    assert value["report"]["signal_overlap"]["rate"] is None
    assert all(Decimal(arm["total_return"]) == 0 and arm["terminal_open_lots"] == 0
               for arm in value["report"]["arms"].values())


def test_production_decoder_keeps_verified_payment_after_horizon_pending(monkeypatch):
    inputs, parameters, pins, calendar = fixture(monkeypatch, mode="pending_final_cash")
    value = run(inputs, parameters, pins)
    assert value["reviewed_action_coverage"] == saved.strict_json(inputs[2])
    assert value["reviewed_action_coverage"]["events"][0]["paid_on"] > calendar[-1].isoformat()
    for arm in ("D1", "D2"):
        result = value["replays"][arm]
        assert result["closed_trades"] == 0 and len(result["books"][-1]["lots"]) == 1
        assert result["books"][-1]["applied_event_ids"] == []
        assert Decimal(result["books"][-1]["cash"]) == 0
    assert value["event_coverage_certified"] is False


@pytest.mark.parametrize("mode,finding", [("ordinary", "inferred_no_compulsory_action"),
                                          ("pending_final_cash", "inferred_supported_events_complete")])
def test_inferred_complete_finding_and_original_pin_are_preserved_without_upgrade(monkeypatch, mode, finding):
    inputs, parameters, pins, _ = fixture(monkeypatch, mode=mode)
    evidence = saved.strict_json(inputs[3])
    evidence["references"]["000001"]["finding"] = finding
    inputs[3] = saved.encoded(evidence)
    pins["basis_evidence"] = saved.digest(inputs[3])
    value = run(inputs, parameters, pins)
    assert value["reviewed_action_evidence"] == evidence
    assert value["reviewed_action_evidence"]["references"]["000001"]["finding"] == finding
    assert value["input_sha256"]["basis_evidence"] == saved.digest(inputs[3])
    assert value["event_coverage_certified"] is value["source_truth_certified"] is False


def test_assumed_unknown_completeness_is_not_inferred_reviewed_coverage(monkeypatch):
    inputs, parameters, pins, _ = fixture(monkeypatch)
    evidence = saved.strict_json(inputs[3])
    evidence["references"]["000001"]["finding"] = "assumed_no_compulsory_action"
    inputs[3] = saved.encoded(evidence)
    pins["basis_evidence"] = saved.digest(inputs[3])
    with pytest.raises(ValueError, match="finding"):
        run(inputs, parameters, pins)


@pytest.mark.parametrize("value", [5, True, "5.0", 6.0])
def test_frozen_reference_parameters_keep_exact_types_at_own_bm_boundary(monkeypatch, value):
    inputs, parameters, pins, _ = fixture(monkeypatch)
    parameters["slippage_bps_per_side"] = value
    with pytest.raises(ValueError):
        run(inputs, parameters, pins)


@pytest.mark.parametrize("mode,late", [("unresolved", False), ("ordinary", True)])
def test_unresolved_observed_prices_and_repinned_late_selection_stop_connection(monkeypatch, mode, late):
    inputs, parameters, pins, _ = fixture(monkeypatch, mode=mode, late=late)
    with pytest.raises(ValueError):
        run(inputs, parameters, pins)


@pytest.mark.parametrize("corruption", ["byte_pin", "BM_receipt", "upstream_link", "source_link", "parameters", "action_gap",
                                        "declared_BL_pin", "consumed_manifest", "scope_union", "activity_quote_alias", "action_quote"])
def test_invalid_packages_parameters_and_action_declarations_fail_before_replay(monkeypatch, corruption):
    inputs, parameters, pins, _ = fixture(monkeypatch)
    if corruption == "byte_pin":
        pins["bl_result"] = "f" * 64
    elif corruption == "parameters":
        parameters["slippage_bps_per_side"] = 6.0
    elif corruption == "action_gap":
        coverage = saved.strict_json(inputs[2])
        coverage["windows"] = []
        inputs[2] = saved.encoded(coverage)
        pins["reviewed_action_coverage"] = saved.digest(inputs[2])
    elif corruption == "action_quote":
        coverage = saved.strict_json(inputs[2])
        coverage["quote_snapshot_sha256"] = coverage["activity_snapshot_sha256"]
        inputs[2] = saved.encoded(coverage)
        pins["reviewed_action_coverage"] = saved.digest(inputs[2])
    elif corruption == "declared_BL_pin":
        spec = saved.strict_json(inputs[1]["files"]["specification.json"])
        spec["inputs"]["bl_result"]["sha256"] = "f" * 64
        inputs[1]["files"]["specification.json"] = saved.encoded(spec)
        source = saved.strict_json(inputs[1]["files"]["source-manifest.json"])
        source["files"][0].update(sha256=saved.digest(inputs[1]["files"]["specification.json"]),
                                  bytes=len(inputs[1]["files"]["specification.json"]))
        inputs[1]["files"]["source-manifest.json"] = saved.encoded(source)
        repin_bm(inputs, pins)
    elif corruption in ("consumed_manifest", "scope_union"):
        scope = saved.strict_json(inputs[1]["files"]["read-scope.json"])
        if corruption == "consumed_manifest":
            scope["input_manifest_before_database"][0]["bytes"] += 1
        else:
            scope["requested_code_dates"]["000001"].pop()
        inputs[1]["files"]["read-scope.json"] = saved.encoded(scope)
        repin_bm(inputs, pins)
    elif corruption == "activity_quote_alias":
        result = saved.strict_json(inputs[1]["files"]["result.json"])
        result["quote_snapshot_sha256"] = result["activity_snapshot_sha256"]
        inputs[1]["files"]["result.json"] = saved.encoded(result)
        repin_bm(inputs, pins)
    else:
        receipt = saved.strict_json(inputs[1]["verification"])
        if corruption == "BM_receipt":
            receipt["original_source_truth_verified"] = True
        elif corruption == "upstream_link":
            receipt["evidence_sha256"]["bl-result.json"] = "f" * 64
        else:
            source = saved.strict_json(inputs[1]["files"]["source-manifest.json"])
            source["files"][0]["sha256"] = "f" * 64
            inputs[1]["files"]["source-manifest.json"] = saved.encoded(source)
            repin_bm(inputs, pins)
            receipt = saved.strict_json(inputs[1]["verification"])
        inputs[1]["verification"] = saved.encoded(receipt)
        pins["bm_verification"] = saved.digest(inputs[1]["verification"])
    monkeypatch.setattr(PAIRED, "replay_synthetic", lambda *a, **kw: pytest.fail("invalid input reached replay"))
    with pytest.raises(ValueError):
        run(inputs, parameters, pins)


def test_successor_scope_never_silently_appends_series_under_original_quote_hash(monkeypatch):
    inputs, parameters, pins, _ = fixture(monkeypatch)
    observed = []

    def declared_successor(*args, **kwargs):
        # Isolate the connection boundary; real decoder stock-event tests are separate.
        observed.append(kwargs["quote_snapshot_sha256"])
        return (), {"000099": (parameters["end"],)}

    monkeypatch.setattr(PAIRED, "replay_synthetic", lambda *a, **kw: pytest.fail("successor snapshot was invented"))
    monkeypatch.setattr(PAIRED, "reviewed_actions", declared_successor)
    with pytest.raises(ValueError, match="separately registered coherent"):
        PAIRED.paired_replay(*inputs, parameters=parameters, expected_pins=pins)
    assert observed == [saved.strict_json(inputs[1]["files"]["result.json"])["quote_snapshot_sha256"]]



def test_prepare_inputs_shares_full_original_scope_without_action_inputs_or_replay(monkeypatch):
    """Conditional preflight preparation has exactly 18 package inputs and no book."""
    inputs, parameters, pins, calendar = fixture(monkeypatch)
    package_pins = {role: pins[role] for role in PAIRED.PACKAGE_ROLES}
    assert len(package_pins) == 18
    before = deepcopy((inputs, parameters, pins))
    monkeypatch.setattr("builtins.open", lambda *a, **kw: pytest.fail("file IO"))
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("DB IO"))
    monkeypatch.setattr(PAIRED, "reviewed_actions", lambda *a, **kw: pytest.fail("action review during preparation"))
    monkeypatch.setattr(PAIRED, "replay_synthetic", lambda *a, **kw: pytest.fail("returns during preparation"))
    prepared = PAIRED.prepare_inputs(inputs[0], inputs[1], parameters=parameters, expected_pins=package_pins)
    assert prepared.calendar == calendar
    assert prepared.parameters is parameters
    assert prepared.input_sha256 == package_pins
    assert prepared.read_scope_sha256 == pins["bm_read_scope"]
    assert prepared.quote_snapshot_sha256 == prepared.holding_inputs.quote_snapshot_sha256
    assert prepared.activity_snapshot_sha256 == prepared.holding_inputs.activity_snapshot_sha256
    assert prepared.selections_d1[0].source_population_count == 6
    assert len(prepared.required_code_dates["000001"]) == 69
    assert prepared.expected_windows == (
        dict(code="000001", isin="KR7000001000", start=calendar[61].isoformat(), end=parameters["end"]),
        dict(code="000001", isin="KR7000001000", start=calendar[62].isoformat(), end=parameters["end"]),
    )
    assert (inputs, parameters, pins) == before
    with pytest.raises(FrozenInstanceError):
        prepared.quote_snapshot_sha256 = "f" * 64


@pytest.mark.parametrize("change", ["missing", "action_extra"])
def test_prepare_inputs_rejects_incomplete_or_action_pin_roles(monkeypatch, change):
    """The price-only consumer cannot accidentally require coverage source inputs."""
    inputs, parameters, pins, _ = fixture(monkeypatch)
    package_pins = {role: pins[role] for role in PAIRED.PACKAGE_ROLES}
    if change == "missing":
        package_pins.pop("bm_verification")
    else:
        package_pins["reviewed_action_coverage"] = pins["reviewed_action_coverage"]
    with pytest.raises(ValueError, match="exact registered"):
        PAIRED.prepare_inputs(inputs[0], inputs[1], parameters, package_pins)


def test_production_schema_does_not_claim_actual_study_completion(monkeypatch):
    inputs, parameters, pins, _ = fixture(monkeypatch, cash=True)
    value = run(inputs, parameters, pins)
    assert value["schema"] == "activity-paired-replay-v1"
    assert value["status"] == "pure_paired_replay"
    assert value["actual_study_completed"] is False


def test_large_decoded_bl_tree_is_released_before_bm_audit_decode(monkeypatch):
    """Check reference lifetime synthetically, without measuring actual inputs."""
    inputs, parameters, pins, _ = fixture(monkeypatch)
    restored_raw = inputs[0]["files"]["restored-activity-audit.json"]
    bm_raw = inputs[1]["files"]["result.json"]
    decode, leases, checked = saved.strict_json, [], []

    class LifetimeMarker:
        """A weak marker retained only by the decoded synthetic BL tree."""

    def tracked_decode(payload):
        if payload == bm_raw:
            assert leases and all(lease() is None for lease in leases)
            checked.append(True)
        value = decode(payload)
        if payload == restored_raw:
            marker = LifetimeMarker()
            value["synthetic_reference_lifetime_only"] = marker
            leases.append(weakref.ref(marker))
        return value

    monkeypatch.setattr(saved, "strict_json", tracked_decode)
    prepared = PAIRED.prepare_inputs(inputs[0], inputs[1], parameters,
        {role: pins[role] for role in PAIRED.PACKAGE_ROLES})
    assert checked == [True]
    assert len(prepared.required_code_dates["000001"]) == 69


def conditional_case(monkeypatch, *, mode="ordinary", finding="confirmed_no_compulsory_action"):
    """Build a separately pinned price projection over the unchanged full package."""
    inputs, parameters, pins, calendar = fixture(monkeypatch, mode=mode)
    prepared = PAIRED.prepare_inputs(inputs[0], inputs[1], parameters,
        {role: pins[role] for role in PAIRED.PACKAGE_ROLES})
    identities = {row["code"]: row["isin"] for row in prepared.expected_windows}
    plan = provisional_exit_bounds(prepared.selections_d1, prepared.selections_d2, prepared.holding_inputs,
        parameters, prepared.required_code_dates, identities, input_sha256=prepared.input_sha256)
    if mode != "pending_final_cash":
        coverage, evidence = saved.strict_json(inputs[2]), saved.strict_json(inputs[3])
        coverage.update(required_scope_sha256=plan["child_scope_sha256"], windows=[])
        refs = {}
        for index, window in enumerate(plan["child_windows"]):
            ref = f"conditional-{index}"
            refs[ref] = dict(evidence["references"][window["code"]], **window, finding=finding)
            coverage["windows"].append(dict(window, review_state="reviewed_no_event", event_ids=[], evidence_refs=[ref]))
        evidence["references"] = refs
        inputs[2], inputs[3] = saved.encoded(coverage), saved.encoded(evidence)
        pins.update(reviewed_action_coverage=saved.digest(inputs[2]), basis_evidence=saved.digest(inputs[3]))
    plan_raw = saved.encoded(plan)
    pins[PAIRED.CONDITIONAL_ROLE] = saved.digest(plan_raw)
    return inputs, parameters, pins, calendar, plan_raw


@pytest.mark.parametrize("finding", ["confirmed_no_compulsory_action", "inferred_no_compulsory_action"])
def test_recomputed_conditional_no_event_windows_keep_full_quote_snapshot_and_same_books(monkeypatch, finding):
    inputs, parameters, pins, _, plan_raw = conditional_case(monkeypatch, finding=finding)
    quote = saved.strict_json(inputs[1]["files"]["result.json"])["quote_snapshot_sha256"]
    value = PAIRED.paired_replay(*inputs, parameters=parameters, expected_pins=pins,
                                conditional_exit_scope=plan_raw)
    plan = value["conditional_exit_scope"]
    assert value["conditional_scope_used"] is True
    assert value["read_scope_sha256"] == pins["bm_read_scope"] != value["action_scope_sha256"]
    assert value["action_scope_sha256"] == plan["child_scope_sha256"]
    assert value["quote_snapshot_sha256"] == quote == plan["quote_snapshot_sha256"]
    assert len(value["required_code_dates"]["000001"]) == 69 > len(plan["child_code_dates"]["000001"])
    assert {row["finding"] for row in value["reviewed_action_evidence"]["references"].values()} == {finding}
    full_inputs, full_parameters, full_pins, _ = fixture(monkeypatch)
    assert value["report"] == run(full_inputs, full_parameters, full_pins)["report"]


def test_conditional_supported_pending_action_retains_original_full_scope(monkeypatch):
    inputs, parameters, pins, _, plan_raw = conditional_case(monkeypatch, mode="pending_final_cash")
    value = PAIRED.paired_replay(*inputs, parameters=parameters, expected_pins=pins,
                                conditional_exit_scope=plan_raw)
    assert value["conditional_scope_used"] is False
    assert value["action_scope_sha256"] == value["read_scope_sha256"] == pins["bm_read_scope"]
    assert all(window["end"] == parameters["end"] for window in value["reviewed_action_coverage"]["windows"])
    assert all(value["report"]["arms"][arm]["terminal_open_lots"] == 1 for arm in ("D1", "D2"))


def test_conditional_cash_union_stays_vacuous_without_quote_identity(monkeypatch):
    inputs, parameters, pins, _ = fixture(monkeypatch, cash=True)
    prepared = PAIRED.prepare_inputs(inputs[0], inputs[1], parameters,
        {role: pins[role] for role in PAIRED.PACKAGE_ROLES})
    plan = provisional_exit_bounds(prepared.selections_d1, prepared.selections_d2, prepared.holding_inputs,
        parameters, {}, {}, input_sha256=prepared.input_sha256)
    coverage = saved.strict_json(inputs[2])
    coverage["required_scope_sha256"] = plan["child_scope_sha256"]
    inputs[2] = saved.encoded(coverage)
    plan_raw = saved.encoded(plan)
    pins.update(reviewed_action_coverage=saved.digest(inputs[2]), conditional_exit_scope=saved.digest(plan_raw))
    value = PAIRED.paired_replay(*inputs, parameters=parameters, expected_pins=pins,
                                conditional_exit_scope=plan_raw)
    assert value["conditional_scope_used"] is True
    assert value["quote_snapshot_sha256"] is plan["quote_snapshot_sha256"] is None
    assert plan["entries"] == plan["child_windows"] == []
    assert value["reviewed_action_evidence"] == {"references": {}, "bases": {}}
    assert all(value["report"]["arms"][arm]["total_return"] == "0" for arm in ("D1", "D2"))


@pytest.mark.parametrize("corruption", ["pin", "truncated_proof", "assumed_finding", "coverage_gap", "scope_alias"])
def test_conditional_child_scope_requires_recomputation_and_reviewed_no_event(monkeypatch, corruption):
    inputs, parameters, pins, calendar, plan_raw = conditional_case(monkeypatch)
    if corruption == "pin":
        pins[PAIRED.CONDITIONAL_ROLE] = "f" * 64
    elif corruption == "truncated_proof":
        plan = saved.strict_json(plan_raw)
        plan["child_code_dates"]["000001"].pop()
        plan["child_windows"][0]["end"] = calendar[90].isoformat()
        plan["child_scope_sha256"] = saved.digest(saved.encoded(
            {key: value for key, value in plan.items() if key != "child_scope_sha256"}))
        plan_raw = saved.encoded(plan)
        pins[PAIRED.CONDITIONAL_ROLE] = saved.digest(plan_raw)
    else:
        coverage, evidence = saved.strict_json(inputs[2]), saved.strict_json(inputs[3])
        if corruption == "assumed_finding":
            next(iter(evidence["references"].values()))["finding"] = "assumed_no_compulsory_action"
        elif corruption == "coverage_gap":
            coverage["windows"][0]["end"] = calendar[90].isoformat()
        else:
            coverage["required_scope_sha256"] = pins["bm_read_scope"]
        inputs[2], inputs[3] = saved.encoded(coverage), saved.encoded(evidence)
        pins.update(reviewed_action_coverage=saved.digest(inputs[2]), basis_evidence=saved.digest(inputs[3]))
    monkeypatch.setattr(PAIRED, "replay_synthetic", lambda *a, **kw: pytest.fail("invalid child proof reached returns"))
    with pytest.raises(ValueError):
        PAIRED.paired_replay(*inputs, parameters=parameters, expected_pins=pins, conditional_exit_scope=plan_raw)

