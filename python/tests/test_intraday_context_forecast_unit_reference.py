"""Saved-bundle corruption and streaming arithmetic checks on invented data."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_forecast_unit as unit
from research import intraday_context_forecast_unit_reference as audit
from test_intraday_context_forecast_unit import _build, adapters, tiny_calendar


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    directory = tmp_path_factory.mktemp("ei-saved-audit")
    source = _build(directory / "input")
    path = directory / "forecast.bundle"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(unit, "fixed_calendar", tiny_calendar)
        result = unit.compare_snapshot(source.bundle, path, scenario="S1", expected_members=source.members,
            expected_summary=source.summary, universe=source.universe, checkpoint=lambda _: None, adapters=adapters([]))
    return source, path, result


@pytest.fixture
def calendar_patch(monkeypatch):
    monkeypatch.setattr(audit, "fixed_calendar", tiny_calendar)


def verify(completed, directory, **changes):
    source, path, result = completed
    directory.mkdir(mode=0o700)
    arguments = {"expected_sha256": result.seal.sha256, "expected_bytes": result.seal.size,
                 "expected_members": source.members, "expected_summary": source.summary, "universe": source.universe,
                 "scenario": "S1", "checkpoint": lambda _: None, "expected_adapter_mode": "injected_diagnostic"}
    return audit.audit_forecast_bundle(path, source.bundle, directory, **(arguments | changes))


def mutate_bundle(completed, path, *, header_change=None, member_change=None, trailer=b""):
    source, original, result = completed
    raw = original.read_bytes()
    first, body = raw.split(b"\n", 1)
    header = json.loads(first)
    members, offset = [], 0
    for index, record in enumerate(header["folds"]):
        size = record["seal"]["size"]
        member = body[offset:offset + size]
        offset += size
        if member_change is not None and index == 0:
            rows = [json.loads(line) for line in member.splitlines()]
            member_change(rows)
            member = b"".join(wire.encode(row) for row in rows)
            record["seal"] = {**record["seal"], "size": len(member), "sha256": wire.digest(member)}
        members.append(member)
    if header_change is not None:
        header_change(header)
    raw = wire.encode(header) + b"".join(members) + trailer
    path.write_bytes(raw)
    path.chmod(0o400)
    changed = replace(result, seal=replace(result.seal, sha256=wire.digest(raw), size=len(raw)))
    return source, path, changed


def test_saved_audit_recounts_exact_summary_without_producer_or_model_calls(tmp_path, completed, calendar_patch, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("saved audit called producer science or inference")
    for name in ("compare_snapshot", "forecast_fold", "score_fold", "combine_fold_reports", "_original", "_feature_stream"):
        monkeypatch.setattr(unit, name, forbidden)
    result = verify(completed, tmp_path / "audit")
    assert result["verified"] and result["full_eof_verified"] and result["original_roster_verified"]
    assert result["producer_summary"] == completed[2].summary
    assert result["independent_saved_bundle_audit"] and result["learned_fit_count"] == 20
    assert result["original_evaluation_coordinates"] == 100 and result["folds"] == 5
    assert result["maximum_absolute_difference"] < 1e-12
    assert all(result[name] is False for name in ("model_refit", "model_inference_reexecuted",
        "numeric_query_projection_reexecuted", "worker_execution_order_reexecuted", "original_snapshot_science_reexecuted"))
    assert not list((tmp_path / "audit").iterdir())


def test_importing_auditor_does_not_load_numpy_or_fit_modules():
    root = Path(__file__).resolve().parents[1]
    code = (f"import sys; sys.path.insert(0, {str(root)!r}); "
            "import research.intraday_context_forecast_unit_reference; "
            "assert 'numpy' not in sys.modules; assert 'lightgbm' not in sys.modules; "
            "assert 'research.intraday_context_sequence' not in sys.modules")
    subprocess.run([sys.executable, "-I", "-S", "-c", code], check=True, capture_output=True, timeout=15)


def test_tiny_saved_audit_fits_existing_64mib_12cpu_boundary(tmp_path, completed):
    source, path, result = completed
    directory = tmp_path / "bounded"
    directory.mkdir(mode=0o700)
    parameters = {"source": str(source.bundle), "forecast": str(path), "directory": str(directory),
        "sha256": result.seal.sha256, "size": result.seal.size, "members": source.members, "summary": source.summary,
        "sessions": source.universe.sessions, "universe_sha256": source.universe.raw_sha256,
        "targets": [[row.scenario, row.symbol, row.session, row.rank, row.stored_row_count, row.state, row.metadata.hex()]
                    for row in source.universe.targets]}
    config = tmp_path / "invented-parameters.json"
    config.write_bytes(wire.encode(parameters))
    root = Path(__file__).resolve().parents[1]
    code = """
import resource, sys
resource.setrlimit(resource.RLIMIT_AS, (64 * 1024**2, 64 * 1024**2))
resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
sys.path.insert(0, sys.argv[1])
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from research import intraday_context_forecast_unit_reference as audit
from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_ed_sources import TargetCoordinate, UniverseCalendar
p = json.loads(Path(sys.argv[2]).read_bytes())
days = tuple(p['sessions'])
def invented_calendar(sessions):
    assert sessions == days
    folds = tuple(ChronologicalFold(i, (0, i), (i, i+1), days[:i], days[i:i+1],
        datetime.fromisoformat(days[i]).replace(hour=9, tzinfo=timezone(timedelta(hours=9)))) for i in range(1, 6))
    return SimpleNamespace(folds=folds, evaluation_reference_sessions=days[1:])
audit.fixed_calendar = invented_calendar
universe = UniverseCalendar(days, tuple(TargetCoordinate(*row[:-1], bytes.fromhex(row[-1])) for row in p['targets']),
                            p['universe_sha256'])
proof = audit.audit_forecast_bundle(Path(p['forecast']), Path(p['source']), Path(p['directory']),
    expected_sha256=p['sha256'], expected_bytes=p['size'], expected_members=p['members'], expected_summary=p['summary'],
    universe=universe, scenario='S1', checkpoint=lambda _: None, expected_adapter_mode='injected_diagnostic')
assert proof['verified'] and 'numpy' not in sys.modules
print('bounded invented audit verified')
"""
    run = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(root), str(config)],
                         check=True, capture_output=True, text=True, timeout=25)
    assert run.stdout.strip() == "bounded invented audit verified"


@pytest.mark.parametrize("change", ["pin", "bytes", "members", "summary", "universe", "scenario", "mode"])
def test_external_receipt_pins_and_original_population_are_required(tmp_path, completed, calendar_patch, change):
    source = completed[0]
    members, summary = deepcopy(source.members), deepcopy(source.summary)
    updates = {}
    if change == "pin":
        updates["expected_sha256"] = "0" * 64
    elif change == "bytes":
        updates["expected_bytes"] = completed[2].seal.size + 1
    elif change == "members":
        members["features"]["sha256"] = "0" * 64
        updates["expected_members"] = members
    elif change == "summary":
        summary["original_coordinates"] -= 1
        updates["expected_summary"] = summary
    elif change == "universe":
        updates["universe"] = replace(source.universe, targets=source.universe.targets[:-1])
    elif change == "scenario":
        updates["scenario"] = "S2"
    else:
        updates["expected_adapter_mode"] = "fixed_native"
    with pytest.raises(ValueError):
        verify(completed, tmp_path / "audit", **updates)


@pytest.mark.parametrize("change", ["fold_order", "test_dates", "model_hash", "support", "score", "combined",
                                    "io", "extra", "scope", "trailing"])
def test_resealed_header_mutations_are_rejected(tmp_path, completed, calendar_patch, change):
    def mutate(header):
        if change == "fold_order":
            header["folds"].reverse()
        elif change == "test_dates":
            header["folds"][0]["test_sessions"] = header["folds"][1]["test_sessions"]
        elif change == "model_hash":
            header["folds"][0]["models"][0]["training_roster_sha256"] = "0" * 64
        elif change == "support":
            header["folds"][0]["training_support"]["supported_joint_questions"] += 1
        elif change == "score":
            header["folds"][0]["score"]["groups"][0]["counts"]["first_brier"][0] += 0.1
        elif change == "combined":
            header["date_weighted_comparisons"][0]["candidates"] += 1
        elif change == "io":
            header["io"]["label_question_requests"]["training"] += 1
        elif change == "extra":
            header["unexpected"] = True
        elif change == "scope":
            header["contains_economic_returns"] = True
    changed = mutate_bundle(completed, tmp_path / "bad.bundle", header_change=mutate,
                            trailer=b"extra\n" if change == "trailing" else b"")
    with pytest.raises(ValueError):
        verify(changed, tmp_path / "audit")


@pytest.mark.parametrize("change", ["question", "missing", "extra", "frame", "probability", "artifact", "pair", "proof", "footer"])
def test_resealed_member_mutations_are_rejected(tmp_path, completed, calendar_patch, change):
    def mutate(rows):
        if change == "question":
            rows[6]["predictions"][0][0] = "0" * 64
        elif change == "missing":
            rows[6]["predictions"].pop()
        elif change == "extra":
            rows[6]["predictions"].append(rows[6]["predictions"][0])
        elif change == "frame":
            rows[6]["original"]["frames"][0][3] = "unknown"
        elif change == "probability":
            rows[6]["predictions"][0][1][2][2] = [0.2] * 10
        elif change == "artifact":
            rows[2]["artifact"]["kind"] = "changed"
        elif change == "pair":
            rows[2]["pair_sha256"] = "0" * 64
        elif change == "proof":
            rows[4]["independent_verification"]["query_ids"].reverse()
        else:
            rows[-1]["records"] += 1
    changed = mutate_bundle(completed, tmp_path / "bad.bundle", member_change=mutate)
    with pytest.raises(ValueError):
        verify(changed, tmp_path / "audit")


def test_zero_support_saved_audit_does_not_invent_fits_or_outcomes(tmp_path, calendar_patch, monkeypatch):
    monkeypatch.setattr(unit, "fixed_calendar", tiny_calendar)
    source = _build(tmp_path / "empty-input", absent=True)
    path = tmp_path / "empty.bundle"
    result = unit.compare_snapshot(source.bundle, path, scenario="S1", expected_members=source.members,
        expected_summary=source.summary, universe=source.universe, checkpoint=lambda _: None, adapters=adapters([]))
    verified = verify((source, path, result), tmp_path / "audit")
    assert verified["producer_summary"] == result.summary
    assert verified["learned_fit_count"] == verified["empirical_fit_count"] == 0
    assert verified["original_evaluation_questions"] == 0


def test_native_saved_artifacts_are_audited_without_reinference(tmp_path, calendar_patch, monkeypatch):
    pytest.importorskip("numpy")
    pytest.importorskip("lightgbm")
    monkeypatch.setattr(unit, "fixed_calendar", tiny_calendar)
    source = _build(tmp_path / "native-input")
    path = tmp_path / "native.bundle"
    result = unit.compare_snapshot(source.bundle, path, scenario="S1", expected_members=source.members,
        expected_summary=source.summary, universe=source.universe, checkpoint=lambda _: None)
    from research import intraday_context_lightgbm as tree
    from research import intraday_context_sequence as sequence
    from research import intraday_context_sequence_reference as reference
    for module, names in ((tree, ("fit_lightgbm", "predict_lightgbm")),
                          (sequence, ("fit_sequence", "predict_sequence")),
                          (reference, ("verify_sequence_probabilities", "reference_sequence_probabilities"))):
        for name in names:
            monkeypatch.setattr(module, name, lambda *a, **kw: pytest.fail("auditor reran a model"))
    verified = verify((source, path, result), tmp_path / "audit", expected_adapter_mode="fixed_native")
    assert verified["producer_summary"] == result.summary and not verified["model_inference_reexecuted"]
