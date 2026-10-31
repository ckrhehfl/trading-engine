"""Invented source/native receipts only; no SSH, API, database or saved bodies."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research import intraday_ed_disclosure_acquisition as acquisition
from research import intraday_ed_disclosure_unit as unit
from research import intraday_ed_disclosures as listing


def configuration(*, invented=True):
    result = {"invented_only": invented,
        "output": str(unit.EVIDENCE / "intraday-ed-disclosure-profile-v1") if invented else str(unit.ACTUAL_OUTPUT),
        "merge_commit": "a" * 40, "reviewed_commit": "b" * 40, "tree": "c" * 40,
        "source_hashes": {name: "d" * 64 for name in unit.SOURCE_NAMES},
        "controller_sources": {"var/ed-disclosure-launch.py": "e" * 64,
                               "var/ed-inventory-runtime.py": "f" * 64},
        "attempt": "ED-disclosure-v1" if invented else "ED-disclosure-actual-v1", "deadline_epoch": 1234567890}
    if not invented:
        result["qualification"] = {"remote_output": str(unit.EVIDENCE / "intraday-ed-disclosure-profile-v1"),
                                   "remote_publication_sha256": "1" * 64}
    return result


def receipt(role="worker"):
    caps = unit.LIMITS[role]
    return {"status": "complete", "violations": [], "scope": "single_process_including_threads",
        "limits": {**caps, "wall_seconds": 1200, "nice": 10},
        "readback": {"rlimit_cpu": [caps["cpu_seconds"]] * 2,
                     "rlimit_as": [caps["address_space_bytes"]] * 2, "nice": 10},
        "absolute": {"cpu_process_seconds": 1., "cpu_user_seconds": .5, "cpu_system_seconds": .5,
                     "address_space_bytes": 20 * unit.wire.MIB}, "delta": {"wall_seconds": 1.}}


def specification():
    root = Path(__file__).resolve().parents[2]
    return json.loads((root / unit.SPEC).read_bytes())


def test_import_has_no_runtime_file_or_process_access(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("import attempted IO")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", refused)
        patch.setattr(Path, "read_bytes", refused)
        patch.setattr(Path, "resolve", refused)
        patch.setattr(unit.bridge.subprocess, "Popen", refused)
        importlib.reload(unit)


def test_fixed_roster_resources_and_no_private_source():
    assert len(unit.SOURCE_NAMES) == 14
    assert not any(name.startswith("var/") for name in unit.SOURCE_NAMES)
    assert sum(cap["cpu_seconds"] for cap in unit.LIMITS.values()) == 250
    assert sum(cap["address_space_bytes"] for cap in unit.LIMITS.values()) == 480 * unit.wire.MIB
    assert unit.runtime_contract()["unused_coordinator_reserve"] == {
        "cpu_seconds": 50, "address_space_bytes": 32 * unit.wire.MIB}
    assert unit.contract(unit.wire.encode(specification()))["campaign"] == "ED"


@pytest.mark.parametrize("mutation", [
    lambda c: c.update(invented_only="true"),
    lambda c: c.update(output="/tmp/alternate"),
    lambda c: c.update(attempt="replay"),
    lambda c: c["source_hashes"].pop(unit.SPEC),
    lambda c: c["source_hashes"].update({"private.py": "0" * 64}),
    lambda c: c["controller_sources"].update({"extra.py": "0" * 64}),
    lambda c: c.update(deadline_epoch=float("inf")),
    lambda c: c.update(tree="not-git"),
    lambda c: c.update(resume=True),
])
def test_config_refuses_scope_source_retry_or_path_changes(mutation):
    config = configuration()
    unit.validate_config(config)
    mutation(config)
    with pytest.raises(ValueError):
        unit.validate_config(config)


def test_actual_requires_separate_same_source_profile_and_registered_ready():
    config = configuration(invented=False)
    unit.validate_config(config)
    config.pop("qualification")
    with pytest.raises(ValueError):
        unit.validate_config(config)
    spec = specification()
    spec["actual_runner_ready"] = False
    with pytest.raises(ValueError):
        unit.contract(unit.wire.encode(spec), actual=True)
    spec.update(actual_runner_ready=True, public_runtime=unit.runtime_contract())
    unit.contract(unit.wire.encode(spec), actual=True)
    spec["public_runtime"] = {**unit.runtime_contract(), "hard_cpu_sum_seconds": 600}
    with pytest.raises(ValueError):
        unit.contract(unit.wire.encode(spec), actual=True)


def test_saved_metadata_pin_precedes_exact_symbol_projection(monkeypatch):
    raw = unit.wire.encode({"inventory": {"klines": {"rows": [
        {"namespace": "KRX", "symbol": f"KRX:{n:06d}"} for n in range(1, 19)
    ] + [{"namespace": "KRX-INDEX", "symbol": "KRX-INDEX:0001"}]}}})
    with pytest.raises(ValueError, match="pin"):
        unit.inventory_codes(raw)
    monkeypatch.setattr(unit, "INVENTORY_SHA", unit.wire.digest(raw))
    assert unit.inventory_codes(raw) == [f"{n:06d}" for n in range(1, 19)]
    bad = raw.replace(b"KRX:000018", b"KRX:ABCDEF")
    monkeypatch.setattr(unit, "INVENTORY_SHA", unit.wire.digest(bad))
    with pytest.raises(ValueError, match="projection"):
        unit.inventory_codes(bad)


def test_invented_shape_uses_all_three_pages_and_disjoint_filing_ids():
    pages = [listing.parse_listing_page(unit.invented_listing("000001", page),
             listing.ListingRequest("000001", listing.WINDOW_START, listing.WINDOW_END, page))
             for page in range(1, 4)]
    combined = listing.combine_listing_pages(pages)
    assert len(combined.rows) == 300
    assert all(page.total == 300 for page in pages)
    assert all(len(unit.invented_listing("000001", page)) == unit.wire.MIB for page in range(1, 4))
    assert unit.invented_listing("000001", 1) != unit.invented_listing("000002", 1)


def test_source_merge_proof_rejects_single_parent(monkeypatch, tmp_path):
    config = configuration()
    monkeypatch.setattr(unit.wire, "file_digest", lambda *args: ("d" * 64, 1))
    calls = []
    class Git:
        def git(self, *args):
            calls.append(args)
            return {("rev-parse", "HEAD"): "a" * 40,
                    ("rev-parse", "HEAD^{tree}"): "c" * 40,
                    ("status", "--porcelain", "--untracked-files=no"): "",
                    ("show", "-s", "--format=%P", "a" * 40): "b" * 40}[args]
    with pytest.raises(ValueError, match="normal reviewed"):
        unit.verify_sources(config, tmp_path, Git())
    assert not any(call[0] == "merge-base" for call in calls)


@pytest.mark.parametrize("bad", ["../raw.html", "subdir/raw.html", "/tmp/raw", "", ".hidden"])
def test_exclusive_artifact_names_are_flat(bad):
    with pytest.raises(ValueError):
        unit.artifact_name(bad)


def test_budget_rejects_extra_exception_priority_or_helper_allowance():
    valid = receipt()
    unit.validate_budget(valid)
    for mutation in (lambda r: r.update(operator_cpu_scope="EC-P01-P03-operator-2026-10-29"),
                     lambda r: r["readback"].update(nice=0),
                     lambda r: r["limits"].update(cpu_seconds=600)):
        changed = deepcopy(valid)
        mutation(changed)
        with pytest.raises(ValueError):
            unit.validate_budget(changed)
    unit.validate_helpers({"cpu_seconds": 1., "count": 1,
                           "readbacks": [{"cpu_seconds": 10, "address_space_bytes": 32 * unit.wire.MIB}]})
    with pytest.raises(ValueError):
        unit.validate_helpers({"cpu_seconds": 10., "count": 0, "readbacks": []})


@pytest.fixture
def native_unit(monkeypatch, tmp_path):
    """Keep real logger/exclusive output/acquisition; replace host-only boundaries."""
    root = tmp_path / "checkout"
    evidence = tmp_path / "evidence"
    collector = tmp_path / "collector"
    root.mkdir()
    evidence.mkdir()
    (collector / "runs").mkdir(parents=True)
    spec = specification()
    monkeypatch.setattr(unit, "__file__", str(root / "python/research/intraday_ed_disclosure_unit.py"))
    monkeypatch.setattr(unit, "REMOTE", root)
    monkeypatch.setattr(unit, "EVIDENCE", evidence)
    monkeypatch.setattr(unit, "COLLECTOR", collector)
    monkeypatch.setattr(unit, "ACTUAL_OUTPUT", evidence / "intraday-ed-disclosure-support-20261031-v1")
    monkeypatch.setattr(unit, "source_bundle", lambda _: {unit.SPEC: unit.wire.encode(spec)})
    monkeypatch.setattr(unit, "contract", lambda raw, actual=False: json.loads(raw))
    monkeypatch.setattr(unit, "verify_sources", lambda *args: None)
    monkeypatch.setattr(unit, "fixed_environment", lambda: None)
    monkeypatch.setattr(unit.wire, "fixed_deadline", lambda _: None)
    monkeypatch.setattr(unit.bridge, "require_free_disk", lambda _: None)
    class Budget:
        def __init__(self, **caps):
            assert caps == unit.LIMITS["worker"]
        def checkpoint(self, stage):
            return {}
        def finish(self):
            return receipt()
    class Git:
        def __init__(self, root, role):
            assert role == "coordinator"
            self.root = root
            self.receipt = {"cpu_seconds": 0., "count": 0, "readbacks": []}
        def git(self, *args):
            if args[0] == "status":
                return ""
            return unit.COLLECTOR_COMMIT if self.root == collector else "a" * 40
    monkeypatch.setattr(unit, "ResearchBudget", Budget)
    monkeypatch.setattr(unit.bridge, "GitHelpers", Git)
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    def advance(_timezone):
        nonlocal now
        now += timedelta(seconds=2)
        return now
    monkeypatch.setattr(unit, "datetime", SimpleNamespace(now=advance))
    # Tests use one tiny page; production's registered full geometry is unchanged.
    original = unit.invented_listing
    def tiny(code, page):
        raw = original(code, 1).decode()
        first = raw[raw.index("<tr>"):raw.index("</tr>") + 5]
        return ("<div class='pageInfo'>[1/1] [총 1건]</div><table><tbody id='tbody'>"
                + first + "</tbody></table>").encode()
    monkeypatch.setattr(unit, "invented_listing", tiny)
    monkeypatch.setattr(unit, "GEOMETRY", {"codes": 18, "requests": 18, "rows": 18,
        "pages_per_code": 1, "rows_per_page": 1,
        "maximum_raw_bytes": sum(len(tiny(f"{n:06d}", 1)) for n in range(1, 19))})
    return evidence, collector


def test_native_tiny_unit_durable_start_precedes_first_request_and_publishes(native_unit, monkeypatch):
    evidence, collector = native_unit
    config = configuration()
    original = unit.invented_listing
    def checked(code, page):
        rows = (Path(config["output"]) / "profile-experiments.jsonl").read_text().splitlines()
        assert len(rows) == 1 and json.loads(rows[0])["status"] == "started"
        assert not (collector / "runs/experiments.jsonl").exists()
        return original(code, page)
    monkeypatch.setattr(unit, "invented_listing", checked)
    result = unit.run_unit(config)
    out = Path(result["remote_output"])
    publication = json.loads((out / "publication.json").read_bytes())
    assert result["status"] == "completed"
    assert result["remote_publication_sha256"] == unit.wire.digest((out / "publication.json").read_bytes())
    assert not (evidence / "intraday-ed-disclosure-unresolved.json").exists()
    assert "listings" not in result
    assert publication["controller_sources"] == config["controller_sources"]
    reconstructed = acquisition.verify_saved_acquisition(lambda name: (out / name).read_bytes())
    assert reconstructed["complete_listing_rows"] == 18
    assert result["verified_support"] == publication["verified_support"] == reconstructed
    assert json.loads((out / "verification.json").read_bytes()) == reconstructed
    terminal = json.loads((out / "canonical-span.jsonl").read_text().splitlines()[1])
    assert terminal["result"]["verified_support"] == reconstructed
    monkeypatch.setattr(unit, "invented_listing", original)
    unit.qualify_actual({**configuration(invented=False), "qualification": {
        "remote_output": str(out), "remote_publication_sha256": result["remote_publication_sha256"]}}, lambda: None)
    with pytest.raises(FileExistsError):
        unit.run_unit(config)


def test_profile_qualification_rejects_shrunken_saved_raw_geometry(native_unit, monkeypatch):
    result = unit.run_unit(configuration())
    out = Path(result["remote_output"])
    publication = json.loads((out / "publication.json").read_bytes())
    raw_path = out / "attempt-001-raw.html"
    # A self-consistent altered producer/verifier package must still prove scale.
    # Replace only the independent reconstruction here to isolate that final gate.
    raw_path.write_bytes(raw_path.read_bytes().replace("제출인".encode(), b""))
    publication["artifacts"][raw_path.name] = unit.wire.digest(raw_path.read_bytes())
    changed = unit.wire.encode(publication)
    (out / "publication.json").write_bytes(changed)
    verified = json.loads((out / "verification.json").read_bytes())
    monkeypatch.setattr(acquisition, "verify_saved_acquisition", lambda read: verified)
    config = configuration(invented=False)
    config["qualification"] = {"remote_output": str(out), "remote_publication_sha256": unit.wire.digest(changed)}
    with pytest.raises(ValueError, match="raw fixture"):
        unit.qualify_actual(config, lambda: None)


def test_terminated_unit_preserves_failed_journal_marker_and_never_replays(native_unit, monkeypatch):
    evidence, _ = native_unit
    config = configuration()
    calls = []
    def failed(code, page):
        calls.append((code, page))
        raise OSError("invented transport failure")
    monkeypatch.setattr(unit, "invented_listing", failed)
    with pytest.raises(ValueError, match="geometry"):
        unit.run_unit(config)
    out = Path(config["output"])
    assert len(calls) == 1
    assert (out / "report.json").exists()
    assert (out / "publication-failure.json").exists()
    assert (evidence / "intraday-ed-disclosure-unresolved.json").exists()
    span = [json.loads(row) for row in (out / "canonical-failure-span.jsonl").read_text().splitlines()]
    assert [row["status"] for row in span] == ["started", "failed"]
    with pytest.raises(FileExistsError):
        unit.run_unit(config)
    assert len(calls) == 1
