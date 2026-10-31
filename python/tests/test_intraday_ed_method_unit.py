"""Invented-only downstream contracts; no full bridge or actual qualification."""
from dataclasses import fields, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
import gzip
import io
import json
from pathlib import Path
from types import SimpleNamespace as Box
import weakref

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_comparison as comparison
from research import intraday_ed_method_unit as unit
from research import intraday_ed_models as models
from research import intraday_ed_replay as replay
from research import intraday_ed_snapshot as snapshot
from research import intraday_ed_sources as sources
from research.intraday_campaign_ml_labels import fixed_calendar


ROOT = Path(__file__).resolve().parents[2]


def checkpoint(_stage):
    pass


def calendar():
    days, day = [], date(2025, 9, 4)
    while len(days) < 257:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return fixed_calendar(days)


def row(index=100, *, unknown=False, parent=True):
    day = calendar().source_reference_sessions[index]
    opening = datetime.fromisoformat(day + "T09:00:00+09:00")
    candidate = None if not parent else replay.EDCandidate("invented-" + day, "KRX:000001", opening,
        opening + timedelta(minutes=390), opening + timedelta(minutes=32), opening + timedelta(minutes=35),
        opening + timedelta(minutes=95), Fraction(90))
    return comparison.PolicyOpportunity("S1", "KRX:000001", day,
        "parent_identity_unknown" if unknown else "parent" if parent else "known_no_parent",
        opening if unknown else None, candidate, parent and not unknown,
        (0.25,) * 191 + (1.0, 0.0, 0.0, 0.0, 1.0) + (0.0,) * 10 if parent and not unknown else None,
        ("NO_EVENT",), "declined", "accepted", None, Fraction(1), Fraction(100))


def prices(opportunity):
    candidate = opportunity.candidate
    return {(candidate.symbol, candidate.entry_at + timedelta(minutes=i)): replay.Minute(candidate.symbol,
        candidate.entry_at + timedelta(minutes=i), candidate.entry_at + timedelta(minutes=i + 3),
        (Decimal(101 if i == 60 else 100),) * 4, Decimal(100)) for i in range(61)}


def reference_fixture(*, invented=False, raw=None):
    specification = (ROOT / unit.producer.SPECIFICATION_PATH).read_bytes()
    names = tuple(sorted(set(unit.MINIMUM_PUBLIC_SOURCES) | {"var/invented-ed-entry.py",
        "python/research/experiment_log.py", "python/research/research_budget.py"}))
    hashes = {name: "a" * 64 for name in names}
    hashes[unit.producer.SPECIFICATION_PATH] = wire.digest(specification)
    value = {"unit_id": "ED-H1-SNAPSHOT", "status": "completed", "verification_complete": True,
        "invented_only": invented, "full_bridge": True, "local_pipe_test": False,
        "resource_profile": wire.ED_H1_600.id, "input_contract": wire.ED_INPUTS_CONTRACT,
        "geometry": unit.producer.FULL_GEOMETRY, "candidate_counts": unit.producer.CANDIDATE_BOUNDS,
        "source_hashes": hashes, "local_identity": {"runtime_sha256": "b" * 64},
        "remote_identity": {"runtime_sha256": "e" * 64},
        "output_sha256": "c" * 64, "output_bytes": 123,
        "independent_verification": {"verified": True, "full_geometry": True, "invented_only": invented,
            "all_original_coordinates_verified": True, "sampled_scientific_rows": False,
            "record_counts": {"session": 257, "coordinate": 10280}, "decoded_sha256": "d" * 64,
            "input_manifest_sha256": "f" * 64, "compressed_bytes": 123}}
    if raw is None:
        raw = wire.encode(value)
    reference = unit.resolve_snapshot_reference(raw, publication_sha256=wire.digest(raw),
        expected_output="/invented-evidence/prior/h1-snapshot.jsonl.gz", invented_only=invented)
    return specification, names, hashes, reference, value


def plan_for(reference, names):
    parent, local, remote = Path("/invented-private"), Path("/invented-local/repo"), Path("/invented-remote/repo")
    return bridge.UnitPlan("ED-H1-RULES", unit.producer.SPECIFICATION_PATH, names,
        wire.digest(wire.encode(list(names))),
        (bridge.InputPin("ED-SNAPSHOT", reference.output_path, reference.output_sha256, unit.MAXIMUM),),
        reference.decoded_sha256, wire.encode({"symbol": "KRX:*", "interval": "1m",
            "start": "2025-09-04", "end": "2026-09-23"}), wire.encode(unit.producer.FULL_GEOMETRY),
        wire.encode(unit.producer.CANDIDATE_BOUNDS), "var/invented-ed-entry.py", local, remote,
        parent / "cache/actual", parent / "outputs/actual", parent / "diagnostics/actual",
        parent / "canonical/runs.jsonl", parent / "canonical/unresolved.json",
        (parent / "canonical/research.lock", parent / "outputs/campaign.lock"), parent / "cache",
        parent / "outputs", parent / "diagnostics", unit.OUTPUT_FILENAMES["ED-H1-RULES"], unit.MAXIMUM,
        ("ssh", "-i", "/invented-unused/key", "invented-unused.invalid"),
        ("python3", "-I", "-S", str(remote / "var/invented-ed-entry.py"), "coordinator"),
        input_contract=wire.ED_SNAPSHOT_CONTRACT, resource_profile=wire.ED_H1_600.id,
        actual_runner_ready=True)


def hooks_fixture(tmp_path):
    spec, names, hashes, actual, _ = reference_fixture()
    _, _, _, invented, _ = reference_fixture(invented=True)
    plan = plan_for(actual, names)
    config = {"invented_only": True, "source_hashes": hashes,
        "local_identity": {"runtime_sha256": "b" * 64}}
    hooks = unit.method_hooks(plan, spec, actual, invented_only=True,
        source_manifest_sha256=wire.digest(wire.encode(hashes)), runtime_sha256="b" * 64,
        invented_reference=invented, invented_source=tmp_path / "prior-invented.gz")
    return spec, plan, config, hooks, actual, invented


def test_reference_resolution_is_metadata_only_and_preserves_producer_binding(monkeypatch):
    spec, names, hashes, actual, _ = reference_fixture()
    _, _, _, invented, _ = reference_fixture(invented=True)
    plan = plan_for(actual, names)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("source-only reference construction accessed evidence")
    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(Path, "lstat", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(wire, "file_digest", forbidden)
    hooks = unit.method_hooks(plan, spec, actual, invented_only=True,
        source_manifest_sha256=wire.digest(wire.encode(hashes)), runtime_sha256="b" * 64,
        invented_reference=invented, invented_source=Path("/invented-local/prior.gz"))
    config = {"invented_only": True, "source_hashes": hashes,
        "local_identity": {"runtime_sha256": "b" * 64}, "compute_verify": forbidden}
    assert hooks.load_plan(config) is plan
    binding = bridge.canonical(actual.producer_binding)
    assert binding["caller"] == "ED-H1-SNAPSHOT" and binding["input_manifest_sha256"] == "f" * 64
    assert binding["input_manifest_sha256"] != wire.digest(wire.encode([plan.input_pins[0].metadata(123)]))


@pytest.mark.parametrize("change", ["pin", "invented", "failed", "partial", "local", "geometry",
    "sample", "calendar", "runtime", "source", "output_size", "decoded"])
def test_partial_or_unbound_snapshot_publication_is_not_adopted(change):
    _, _, _, _, value = reference_fixture()
    if change == "pin":
        raw = wire.encode(value)
        with pytest.raises(ValueError):
            unit.resolve_snapshot_reference(raw, publication_sha256="0" * 64,
                expected_output="/invented-evidence/prior/h1-snapshot.jsonl.gz", invented_only=False)
        return
    if change == "invented":
        value["invented_only"] = True
    elif change == "failed":
        value["status"] = "failed"
    elif change == "partial":
        value["verification_complete"] = False
    elif change == "local":
        value["local_pipe_test"] = True
    elif change == "geometry":
        value["geometry"] = {**value["geometry"], "sessions": 2}
    elif change == "sample":
        value["independent_verification"]["sampled_scientific_rows"] = True
    elif change == "calendar":
        value["independent_verification"]["record_counts"]["coordinate"] -= 1
    elif change == "runtime":
        value["local_identity"]["runtime_sha256"] = None
    elif change == "source":
        value["source_hashes"].pop(unit.producer.SPECIFICATION_PATH)
    elif change == "output_size":
        value["output_bytes"] += 1
    else:
        value["independent_verification"]["decoded_sha256"] = None
    raw = wire.encode(value)
    with pytest.raises(ValueError):
        unit.resolve_snapshot_reference(raw, publication_sha256=wire.digest(raw),
            expected_output="/invented-evidence/prior/h1-snapshot.jsonl.gz", invented_only=False)


def test_derived_invented_copy_does_not_substitute_actual_or_tiny_source(tmp_path):
    _, _, _, hooks, _, _ = hooks_fixture(tmp_path)
    target = tmp_path / "generated"
    target.mkdir()
    with pytest.raises(ValueError, match="full invented"):
        hooks.make_invented(target, 2, checkpoint)
    spec, names, hashes, actual, _ = reference_fixture()
    with pytest.raises(ValueError, match="cannot copy actual"):
        unit.method_hooks(plan_for(actual, names), spec, actual, invented_only=True,
            source_manifest_sha256=wire.digest(wire.encode(hashes)), runtime_sha256="b" * 64,
            invented_reference=actual, invented_source=tmp_path / "anything")


def test_conditional_gate_and_actual_runtime_cannot_be_opened_with_boolean(tmp_path):
    spec, plan, config, _, actual, invented = hooks_fixture(tmp_path)
    changed = replace(plan, unit_id="ED-H1-LIGHTGBM-ABLATION",
        output_filename=unit.OUTPUT_FILENAMES["ED-H1-LIGHTGBM-ABLATION"])
    with pytest.raises(ValueError, match="conditional ablation gate"):
        unit.method_hooks(changed, spec, actual, invented_only=True,
            source_manifest_sha256=wire.digest(wire.encode(config["source_hashes"])), runtime_sha256="b" * 64,
            invented_reference=invented, invented_source=tmp_path / "anything", conditional_gate=True)
    with pytest.raises(ValueError, match="not registered ready"):
        draft = json.loads(spec)
        draft["numbers_frozen_for_execution"] = False
        unit.method_hooks(plan, wire.encode(draft), actual, invented_only=False,
            source_manifest_sha256=wire.digest(wire.encode(config["source_hashes"])), runtime_sha256="b" * 64)


def test_independent_admission_reads_no_future_labels_and_detects_selection_mutation():
    opportunity = row()
    plan = comparison.plan_rule(calendar(), (opportunity,), scenario="S1", without_filing=True)
    raw = Box(coordinate=Box(scenario="S1", session=opportunity.session, symbol=opportunity.symbol),
        parent_scan=Box(status="parent"), candidate=opportunity.candidate, common_support=True,
        window_inputs=Box(static_exact=(("target_minus_peer_30m_return", Fraction(1)),
            ("last5_share_volume_ratio", Fraction(2)))), opening_context=Box(context=Box(state="KNOWN_NO_WHITELISTED_LIST_EVENT")),
        label=object())
    assert unit._verify_admission(plan, (raw,))["test_labels_read"] is False
    bad = replace(plan, decisions=(replace(plan.decisions[0], status="decline"),))
    with pytest.raises(ValueError, match="admission differs"):
        unit._verify_admission(bad, (raw,))


def test_missing_training_is_unavailable_even_on_zero_parent_days(monkeypatch):
    opportunity = row(parent=False)
    monkeypatch.setattr(models.numeric, "_libraries", lambda *_args: pytest.fail("unavailable fit imported native code"))
    binding = models.EDModelBinding("a" * 64, "b" * 64, "c" * 64, "d" * 64, 1, "S1")
    plan = comparison.plan_lightgbm(calendar(), (opportunity,), scenario="S1", binding=binding, checkpoint=checkpoint)
    opening = datetime.fromisoformat(opportunity.session + "T09:00:00+09:00")
    session = replay.EDSession(opening, opening + timedelta(minutes=390))
    results = list(unit._day_accounts(plan, session, {}, {}, {}))
    assert len(plan.fits) == 5 and all(fit.status == "unavailable" for _view, fit, _batch, _proof in plan.fits)
    assert len(results) == 8
    assert all(result.snapshots[-1].full_nav is None for _key, result, _proof, _prior in results)
    assert all(proof["producer_state_used_as_input"] is False for _key, _result, proof, _prior in results)


def test_full_and_conditional_carry_and_cash_replay_are_independently_verified():
    opportunity = row()
    gap = replace(row(unknown=True, parent=False), symbol="KRX:000002")
    next_day = row(101, parent=False)
    plan = comparison.plan_rule(calendar(), (opportunity, gap, next_day), scenario="S1", without_filing=True)
    session = replay.EDSession(opportunity.candidate.opening, opportunity.candidate.closing)
    carries, oracles = {}, {}
    outcomes = list(unit._day_accounts(plan, session, prices(opportunity), carries, oracles))
    full = next(result for key, result, _, _ in outcomes if key[2:] == (33, unit.DOMAINS[0], False))
    key, conditional, receipt, prior = next(item for item in outcomes if item[0][2:] == (33, unit.DOMAINS[1], False))
    assert full.snapshots[-1].full_nav is None and conditional.snapshots[-1].full_nav is not None
    assert receipt["verified"] and not receipt["producer_state_used_as_input"]
    summary = unit._account_day_summary(key, conditional, session, prior)
    assert summary["entries"] == 1 and summary["entry_cost_to_date"] > 0
    assert summary["same_quantity_gross_nav"] - summary["day_close_nav"] == summary["entry_cost_to_date"]
    assert summary["notional_minutes_observed"] > 0 and not summary["fresh_days_stitched"]
    assert all(carry.domain == key[3] for key, carry in carries.items())
    assert len(oracles) == 8
    next_open = datetime.fromisoformat(next_day.session + "T09:00:00+09:00")
    resumed = list(unit._day_accounts(plan, replay.EDSession(next_open, next_open + timedelta(minutes=390)),
        {}, carries, oracles))
    full_next = next(result for key, result, _, _ in resumed if key[2:] == (33, unit.DOMAINS[0], False))
    conditional_next = next(result for key, result, _, _ in resumed if key[2:] == (33, unit.DOMAINS[1], False))
    fresh_next = next(result for key, result, _, _ in resumed if key[2:] == (33, unit.DOMAINS[1], True))
    assert full_next.snapshots[-1].full_nav is None
    assert conditional_next.snapshots[-1].full_nav == conditional.snapshots[-1].full_nav
    assert fresh_next.snapshots[-1].full_nav == replay.cash.INITIAL_CAPITAL
    assert all(oracle.verified_days == 2 for oracle in oracles.values())


def test_lossless_streaming_report_rechecks_durable_footer_and_external_pins(tmp_path):
    path = tmp_path / "invented-report.jsonl.gz"
    with path.open("wb") as stream:
        report = unit._Report(stream, unit.MAXIMUM)
        report.add("header", schema=unit.SCHEMA, qualifies_actual_by_itself=False)
        report.add("exact", decimal=Decimal("1.2300"), fraction=Fraction(7, 11), float64=-0.0)
        pin, size, decoded_pin, counts = report.close()
    verified = unit._verify_report(path, pin, decoded_pin, counts, checkpoint)
    assert verified["full_eof_verified"] and path.stat().st_size == size
    records = [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]
    assert records[0]["qualifies_actual_by_itself"] is False
    assert records[1]["decimal"] == {"decimal": "1.2300"}
    assert records[1]["fraction"] == {"fraction": [7, 11]}
    assert records[1]["float64"] == {"float64": "-0x0.0p+0"}
    with pytest.raises(ValueError, match="stored identity"):
        unit._verify_report(path, "0" * 64, decoded_pin, counts, checkpoint)
    limited = unit._Report(io.BytesIO(), 20)
    limited.add("header", schema=unit.SCHEMA)
    with pytest.raises(ValueError, match="exceeded"):
        limited.close()


def serialized_policy_fixture(scenario="S1"):
    """Invented serialization objects, with no native fit or qualification."""
    training, testing = row(1), row(100)
    train = models.EDTrainingRow("invented-training-" + scenario, training.symbol, training.session,
        training.candidate.decision_at, training.features, training.event_types, Fraction(1, 100),
        "known_traded", training.candidate.cap_exit_at, training.candidate.cap_exit_at + timedelta(minutes=3))
    inference = models.EDInferenceRow(testing.candidate.parent_id, testing.symbol, testing.session,
        testing.candidate.decision_at, testing.features, testing.event_types)
    view = models.EDFoldView(calendar().source_reference_sessions, calendar().folds[0], scenario,
        models.FEATURE_NAMES, (train,), (inference,), "unavailable", "invented_serialization_only",
        {"training_candidates": 1, "test_candidates": 1}, (), {"NO_EVENT": 1})
    # Opaque invented payload exercises retained-artifact lifetime/serialization;
    # no caller may treat this object as an authenticated fitted native model.
    artifact = models.EDModelArtifact({"invented_serialization_only": True, "model": "never fitted"})
    fit = models.EDFitResult("ready", None, artifact, ())
    prediction = models.EDPrediction(testing.candidate.parent_id, "known", None, (0.5).hex(), True, (), False)
    batch = models.EDPredictionBatch(1, scenario, "ready", None, calendar().folds[0].test_sessions, (), (prediction,))
    unavailable = (replace(view, fold=calendar().folds[1], training=(), testing=()),
        models.EDFitResult("unavailable", "invented_empty", None, calendar().folds[1].test_sessions),
        models.EDPredictionBatch(2, scenario, "unavailable", "invented_empty",
            calendar().folds[1].test_sessions, calendar().folds[1].test_sessions, ()),
        {"verified": True, "invented_serialization_only": True})
    decision = comparison.PolicyDecision(scenario, testing.symbol, testing.session, "accept", "invented",
        replay.EDAction(testing.candidate.decision_at, "accept", "invented", testing.candidate),
        testing.candidate.parent_id, testing.event_types)
    policy = comparison.PolicyPlan(comparison.LEARNER, scenario, calendar().evaluation_reference_sessions,
        (decision,), calendar().folds[1].test_sessions,
        ((view, fit, batch, {"verified": True, "invented_serialization_only": True}), unavailable))
    return policy


def test_full_fold_report_bytes_and_admission_survive_release_of_training_views_and_artifacts():
    policy = serialized_policy_fixture()
    references = [weakref.ref(policy.fits[0][0]), weakref.ref(policy.fits[0][0].training[0]),
                  weakref.ref(policy.fits[0][0].testing[0]), weakref.ref(policy.fits[0][1].artifact)]
    prediction_reference = weakref.ref(policy.fits[0][2].rows[0])
    def original_record_order(report, original):
        for view, fit, batch, verification in original.fits:
            report.add("model_fold", arm=original.arm, scenario=original.scenario,
                fold_view={field.name: getattr(view, field.name) for field in fields(view)
                    if field.name not in ("training", "testing")},
                fit=fit, predictions=batch, independent_verification=verification)
            for training in view.training:
                report.add("model_training_row", arm=original.arm, scenario=original.scenario,
                    fold=view.fold.index, row=training)
            for testing in view.testing:
                report.add("model_inference_row", arm=original.arm, scenario=original.scenario,
                    fold=view.fold.index, row=testing)
        for decision in original.decisions:
            report.add("decision", arm=original.arm, scenario=original.scenario, decision=decision)
    legacy_bytes, compact_bytes = io.BytesIO(), io.BytesIO()
    original, updated = unit._Report(legacy_bytes, unit.MAXIMUM), unit._Report(compact_bytes, unit.MAXIMUM)
    original.add("header", schema=unit.SCHEMA)
    updated.add("header", schema=unit.SCHEMA)
    original_record_order(original, policy)
    lean, predictions, counts = unit._write_policy_records(updated, policy)
    assert original.close() == updated.close()
    assert legacy_bytes.getvalue() == compact_bytes.getvalue()
    assert counts == {"model_fit_slots": 2, "model_fits_performed": 1, "unavailable_fit_slots": 1}
    assert lean == replace(policy, fits=()) and lean.decisions is policy.decisions
    opportunity = row(100)
    raw = Box(coordinate=Box(scenario="S1", session=opportunity.session, symbol=opportunity.symbol),
        parent_scan=Box(status="parent"), common_support=True, candidate=opportunity.candidate,
        label=object())
    assert unit._verify_admission(lean, (raw,), predictions=predictions) == unit._verify_admission(policy, (raw,))
    assert comparison.unit_summary(calendar(), (opportunity,), lean) == comparison.unit_summary(
        calendar(), (opportunity,), policy)
    assert unit._concentration(calendar(), (opportunity,), lean) == unit._concentration(
        calendar(), (opportunity,), policy)
    changed = {key: replace(value, prediction_hex=(-0.5).hex(), accepted=False) for key, value in predictions.items()}
    with pytest.raises(ValueError, match="policy admission differs"):
        unit._verify_admission(lean, (raw,), predictions=changed)
    del policy
    unit.gc.collect()
    assert all(reference() is None for reference in references)
    assert prediction_reference() is predictions[opportunity.candidate.parent_id]


def test_compute_collects_fold_cycles_and_releases_serialized_payloads_before_second_pass(tmp_path, monkeypatch):
    """Only lifetime/order is exercised; no numeric backend or snapshot claim."""
    from research import intraday_ed_numerical
    spec, names, _hashes, reference, _publication = reference_fixture(invented=True)
    directory = tmp_path / "invented-worker"
    received = directory / "received"
    received.mkdir(parents=True)
    raw = b"invented admission-only input; reader is mocked for this lifetime test\n"
    (received / "ED-SNAPSHOT").write_bytes(raw)
    reference = replace(reference, output_sha256=wire.digest(raw), output_bytes=len(raw))
    plan = replace(plan_for(reference, names), unit_id="ED-H1-LIGHTGBM",
                   output_filename=unit.OUTPUT_FILENAMES["ED-H1-LIGHTGBM"])
    item = bridge.InputFile("ED-SNAPSHOT", reference.output_path, reference.output_sha256,
        unit.MAXIMUM, reference.output_bytes)
    envelope = bridge.InputEnvelope((item,), reference.decoded_sha256, plan.full_geometry, wire.ED_SNAPSHOT_CONTRACT)
    monkeypatch.setattr(unit, "_first_pass", lambda *args: ({"invented_header": True}, calendar(), ()))
    monkeypatch.setattr(unit, "_premean_plans", lambda *args: {"invented_lifetime_test": True})
    monkeypatch.setattr(intraday_ed_numerical, "activate", lambda *args: {"invented_mock": True})
    monkeypatch.setattr(unit, "_records", lambda *args: pytest.fail("second pass began before release checkpoint"))
    references, events = [], []
    real_collect = unit.gc.collect
    def collect():
        events.append("collect")
        return real_collect()
    monkeypatch.setattr(unit.gc, "collect", collect)
    class Cycle:
        pass
    def planned(_calendar, _rows, *, scenario, checkpoint, **kwargs):
        cycle = Cycle()
        cycle.self = cycle
        reference = weakref.ref(cycle)
        del cycle
        checkpoint("ED-after-fold-1")
        assert reference() is None
        policy = serialized_policy_fixture(scenario)
        references.extend((weakref.ref(policy.fits[0][0]), weakref.ref(policy.fits[0][1].artifact)))
        return policy
    monkeypatch.setattr(comparison, "plan_lightgbm", planned)
    class ReachedRelease(Exception):
        pass
    def observed(stage):
        if stage == "ED-after-fold-1":
            assert events[-1] == "collect"
            events.append(stage)
        elif stage == "ED-after-model-storage-release":
            assert events[-1] == "collect"
            assert len(references) == 4 and all(reference() is None for reference in references)
            raise ReachedRelease
    with pytest.raises(ReachedRelease):
        unit._compute(directory, envelope, plan, reference, bridge.canonical(reference.producer_binding),
            unit.producer.contract(spec), observed)
    assert events.count("collect") == 3


def test_paired_accounts_freeze_all157_dates_and_unknowns_before_conditional_mean():
    dates = calendar().evaluation_reference_sessions
    accounts = {}
    for arm, gain in ((comparison.RULE, 100), (comparison.CONTROL, 50)):
        days = []
        for index, day in enumerate(dates):
            opening = replay.cash.INITIAL_CAPITAL + index * gain
            known = not (arm == comparison.RULE and index >= 10)
            days.append({"session": day, "day_open_nav": opening if known else None,
                "day_close_nav": opening + gain if known else None,
                "identified_day_return": Fraction(gain, opening) if known else None,
                "notional_minutes_observed": Fraction(1000), "overlap_minutes_observed": 0,
                "entries": 1, "entry_reason_counts": {"filled": 1},
                "first_unknown_at": None if known else "invented unknown"})
        accounts[arm, "S1", 33, unit.DOMAINS[0], False] = days
    records = list(unit._paired_accounts(calendar(), accounts))
    assert records[0][0] == "paired_account_plan"
    assert records[0][1]["before_mean_sample_plan"]["known_labels"] == 10
    assert sum(kind == "paired_account_day" for kind, _ in records) == 157
    summary = records[-1][1]
    assert summary["unknown_pair_sessions"] == 147
    assert summary["continuous_terminal_net_increment"] is None
    assert summary["identified_intersection_mean_same_budget_day_net_change_difference"] == Fraction(
        50, replay.cash.INITIAL_CAPITAL)
    assert not summary["fresh_days_stitched"] and not summary["conditional_subset_mean_is_full_population_claim"]


def test_tiny_empty_source_rules_pipeline_two_eofs_accounts_summaries_and_seal(tmp_path, monkeypatch):
    """Fixed calendar only: zero raw minutes, no bridge or full qualification.

    The sole mocked trust boundary is an invented audited reader returning no
    chunks. Real snapshot framing/decoders, policy planning, independent account
    oracles, independent result summaries and output verification all execute.
    """
    spec, names, hashes, preliminary, publication = reference_fixture(invented=True)
    days = unit.producer._invented_calendar(257)
    symbols = tuple(f"KRX:{index:06d}" for index in range(1, 21))
    targets = tuple(sources.TargetCoordinate(scenario, symbol, day, rank, None,
        "no_stored_rows_unknown_reason", b'{"invented_empty_source":true}')
        for scenario in ("S1", "S2") for day in days
        for rank, symbol in enumerate(symbols, 1))
    universe = sources.UniverseCalendar(days, targets, "1" * 64)
    codes = tuple(symbol.removeprefix("KRX:") for symbol in symbols)
    filings = sources.FilingSource(codes, (), codes, (), date.fromisoformat(days[0]),
        date.fromisoformat(days[-1]), "2" * 64)

    class EmptyInventedReader(sources.AuditedChunkReader):
        # This fixture declaration is not a real cache/source audit receipt.
        receipt = {"schema": "ED-raw-byte-cache-audit-v1", "verified": True,
            "full_eof_verified": True, "invented_empty_source": True,
            "raw_rows": 0, "qualifies_actual_by_itself": False}

        def __init__(self):
            pass

        def load_raw_chunk(self, _kind, _symbol, _day):
            return None

    directory = tmp_path / "invented-worker"
    received = directory / "received"
    received.mkdir(parents=True)
    source = received / "ED-SNAPSHOT"
    with source.open("wb") as stream:
        digest = snapshot.write_snapshot(snapshot.snapshot_records(universe, EmptyInventedReader(), filings,
            binding=bridge.canonical(preliminary.producer_binding), checkpoint=checkpoint), stream, checkpoint)
    source.chmod(0o400)
    publication["output_sha256"], publication["output_bytes"] = digest.compressed_sha256, digest.compressed_bytes
    publication["independent_verification"].update(decoded_sha256=digest.decoded_sha256,
        compressed_bytes=digest.compressed_bytes)
    raw_publication = wire.encode(publication)
    reference = unit.resolve_snapshot_reference(raw_publication, publication_sha256=wire.digest(raw_publication),
        expected_output=preliminary.output_path, invented_only=True)
    plan = plan_for(reference, names)
    manifest = (bridge.InputFile("ED-SNAPSHOT", "/invented-original/ED-SNAPSHOT", digest.compressed_sha256,
        unit.MAXIMUM, digest.compressed_bytes),)
    envelope = bridge.InputEnvelope(manifest, digest.decoded_sha256, plan.full_geometry, wire.ED_SNAPSHOT_CONTRACT)
    binding = {"registration_sha256": wire.digest(spec), "source_sha256": wire.digest(wire.encode(hashes)),
        "runtime_sha256": "b" * 64, "input_manifest_sha256": wire.digest(wire.encode(
            [item.metadata() for item in manifest])), "caller": "ED-H1-RULES"}
    original_reader, eof_passes = snapshot.read_snapshot, []

    def counted_reader(*args, **kwargs):
        count = 0
        for record in original_reader(*args, **kwargs):
            count += 1
            yield record
        eof_passes.append(count)  # Reached only after actual gzip/hash EOF checks.

    monkeypatch.setattr(snapshot, "read_snapshot", counted_reader)
    monkeypatch.setattr(models.numeric, "_libraries", lambda *_args: pytest.fail("rule pipeline imported native backend"))
    receipt = unit._compute(directory, envelope, plan, reference, binding,
        unit.producer.contract(spec), checkpoint)
    verification = bridge.canonical(receipt.independent_verification)
    assert eof_passes == [1 + 257 + 10280 + 1] * 2
    assert bridge.canonical(receipt.candidate_counts) == {"S1": 5140, "S2": 5140}
    assert verification["verified"] and verification["all_account_days_verified"] == 157
    assert verification["policy_summaries_independently_verified"]
    assert verification["account_summaries_independently_verified"]
    assert verification["paired_accounts_independently_verified"]
    assert verification["scientific_report_complete"] and not verification["qualifies_actual_by_itself"]
    assert verification["model_fits_performed"] == 0
    output = directory / plan.output_filename
    assert output.stat().st_mode & 0o777 == 0o400
    assert wire.file_digest(source, unit.MAXIMUM, checkpoint) == (digest.compressed_sha256, digest.compressed_bytes)
    records = [json.loads(line) for line in gzip.decompress(output.read_bytes()).splitlines()]
    assert records[0]["invented_only"] and not records[0]["qualifies_actual_by_itself"]
    assert records[-1]["kind"] == "footer"
    assert records[-1]["record_counts"]["account_day"] == 157 * 32
    assert sum(record["kind"] == "paired_account_summary" for record in records) == 16
    full = next(record for record in records if record["kind"] == "account_summary"
        and record["key"][3:] == [unit.DOMAINS[0], False])
    assert full["identified_days"] == 0 and full["continuous_terminal_net_return"] is None
    # A corrupt transfer under the same original pin must fail before output
    # creation; the valid body exists ONLY in received/, catching path drift.
    broken = tmp_path / "corrupt-worker"
    (broken / "received").mkdir(parents=True)
    changed = bytearray(source.read_bytes())
    changed[-1] ^= 1
    (broken / "received" / "ED-SNAPSHOT").write_bytes(changed)
    with pytest.raises(ValueError, match="received snapshot identity differs"):
        unit._compute(broken, envelope, plan, reference, binding, unit.producer.contract(spec), checkpoint)
    assert not (broken / plan.output_filename).exists()
