"""Invented saved failed receipts only; no SSH, API, DB or actual raw inputs."""
import importlib
import json
from pathlib import Path

import pytest

from research import intraday_ed_disclosure_acquisition as acquisition
from research import intraday_ed_disclosure_repair as repair
from research import intraday_ed_disclosure_unit as original
from research import intraday_ed_disclosures as listing


def configuration(invented=True):
    config = {"invented_only": invented, "output": str(repair.EVIDENCE /
        "intraday-ed-disclosure-saved-repair-profile-v1") if invented else str(repair.ACTUAL_OUTPUT),
        "merge_commit": "a" * 40, "reviewed_commit": "b" * 40, "tree": "c" * 40,
        "source_hashes": {name: "d" * 64 for name in repair.SOURCE_NAMES},
        "controller_sources": {name: "e" * 64 for name in repair.CONTROLLERS},
        "attempt": "ED-disclosure-saved-repair-" + ("v1" if invented else "actual-v1"),
        "deadline_epoch": 1234567890}
    if not invented:
        config["qualification"] = {"remote_output": str(repair.EVIDENCE /
            "intraday-ed-disclosure-saved-repair-profile-v1"), "remote_publication_sha256": "f" * 64}
    return config


def specification():
    return json.loads((Path(__file__).resolve().parents[2] / repair.SPEC).read_bytes())


def budget_receipt():
    caps = repair.LIMITS["worker"]
    return {"status": "complete", "violations": [], "scope": "single_process_including_threads",
        "limits": {**caps, "wall_seconds": 1200, "nice": 10},
        "readback": {"rlimit_cpu": [caps["cpu_seconds"]] * 2,
            "rlimit_as": [caps["address_space_bytes"]] * 2, "nice": 10},
        "absolute": {"cpu_process_seconds": 1., "cpu_user_seconds": .5, "cpu_system_seconds": .5,
            "address_space_bytes": 20 * repair.wire.MIB}, "delta": {"wall_seconds": 1.}}


def test_import_has_no_io(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("import attempted IO")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", refused)
        patch.setattr(Path, "resolve", refused)
        patch.setattr(Path, "read_bytes", refused)
        patch.setattr(repair.bridge.subprocess, "Popen", refused)
        importlib.reload(repair)


def test_registration_freezes_failed_origin_and_separate_closures():
    spec = repair.contract(repair.wire.encode(specification()))
    assert len(repair.SOURCE_NAMES) == 17
    assert set(repair.SOURCE_NAMES) - set(original.SOURCE_NAMES) == {repair.SPEC,
        "python/research/intraday_ed_disclosure_repair.py", "python/tests/test_intraday_ed_disclosure_repair.py"}
    assert len(spec["origin"]["source_hashes"]) == 14
    assert len(spec["origin"]["artifacts"]) == 78
    assert spec["origin"]["canonical_offsets"] is None
    assert spec["origin"]["origin_manifest_sha256"] == "224e4291314748c0c0413b3b5e90cff48a7c14eaa9a9f58cc0588eea1c2bda1a"
    assert sum(role["cpu_seconds"] for role in repair.LIMITS.values()) == 250
    assert sum(role["address_space_bytes"] for role in repair.LIMITS.values()) == 480 * repair.wire.MIB
    assert spec["actual_runner_ready"] is True
    repair.contract(repair.wire.encode(spec), actual=True)
    # A not-ready registration never permits actual execution, even with pins.
    spec["actual_runner_ready"] = False
    with pytest.raises(ValueError, match="not registered ready"):
        repair.contract(repair.wire.encode(spec), actual=True)
    spec["actual_runner_ready"] = True
    repair.contract(repair.wire.encode(spec), actual=True)
    spec["origin"]["artifacts"] = None
    with pytest.raises(ValueError, match="artifact pins"):
        repair.contract(repair.wire.encode(spec), actual=True)


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(invented_only=1),
    lambda c: c.update(output=str(repair.ORIGIN_OUTPUT)),
    lambda c: c.update(attempt="retry"),
    lambda c: c.update(resume=True),
    lambda c: c.update(deadline_epoch=float("inf")),
    lambda c: c["source_hashes"].pop(repair.SPEC),
    lambda c: c["source_hashes"].update({"unregistered.py": "f" * 64}),
    lambda c: c["controller_sources"].update({"var/ed-disclosure-launch.py": "f" * 64}),
])
def test_config_refuses_old_path_source_retry_or_unbounded_config(mutate):
    config = configuration()
    repair.validate_config(config)
    mutate(config)
    with pytest.raises(ValueError):
        repair.validate_config(config)


@pytest.mark.parametrize("mutate", [
    lambda o: o.update(directory="/tmp/other"),
    lambda o: o.update(source_hashes={}),
    lambda o: o.update(runtime_sha256="f" * 64),
    lambda o: o.update(origin_manifest_sha256=None),
    lambda o: o["artifacts"].update({"../raw.html": "f" * 64}),
    lambda o: o["artifacts"].pop("registration.json"),
])
def test_origin_contract_refuses_unpinned_or_expanded_inputs(mutate):
    origin = specification()["origin"]
    mutate(origin)
    with pytest.raises(ValueError):
        repair.validate_origin(origin, ready=True)


def test_nested_invented_raw_keeps_full_scale_and_producer_shape():
    raw = repair.invented_listing("000001", 1)
    assert len(raw) == repair.wire.MIB
    assert raw.startswith(b"<div class='outer'><div><div class='pageInfo'>")
    parsed = listing.parse_listing_page(raw, listing.ListingRequest("000001",
        listing.WINDOW_START, listing.WINDOW_END, 1))
    assert len(parsed.rows) == 100 and parsed.total == 300 and parsed.pages == 3


@pytest.fixture
def native(monkeypatch, tmp_path):
    root, evidence, collector = (tmp_path / name for name in ("checkout", "evidence", "collector"))
    root.mkdir()
    evidence.mkdir()
    (collector / "runs").mkdir(parents=True)
    spec = specification()
    old_spec = json.loads((Path(__file__).resolve().parents[2] / original.SPEC).read_bytes())
    monkeypatch.setattr(repair, "__file__", str(root / "python/research/intraday_ed_disclosure_repair.py"))
    for module in (repair, original):
        monkeypatch.setattr(module, "REMOTE", root)
        monkeypatch.setattr(module, "EVIDENCE", evidence)
        monkeypatch.setattr(module, "COLLECTOR", collector)
    monkeypatch.setattr(repair, "ACTUAL_OUTPUT", evidence / "intraday-ed-disclosure-saved-repair-20261031-v1")
    monkeypatch.setattr(original, "ACTUAL_OUTPUT", evidence / "intraday-ed-disclosure-support-20261031-v1")
    monkeypatch.setattr(repair, "ORIGIN_OUTPUT", original.ACTUAL_OUTPUT)
    monkeypatch.setattr(repair, "contract", lambda raw, actual=False: json.loads(raw))
    monkeypatch.setattr(original, "contract", lambda raw, actual=False: json.loads(raw))
    monkeypatch.setattr(repair, "source_bundle", lambda _: {
        repair.SPEC: repair.wire.encode(spec), original.SPEC: repair.wire.encode(old_spec)})
    monkeypatch.setattr(repair, "verify_sources", lambda *args: None)
    monkeypatch.setattr(original, "fixed_environment", lambda: None)
    monkeypatch.setattr(repair.wire, "fixed_deadline", lambda _: None)
    monkeypatch.setattr(repair.bridge, "require_free_disk", lambda _: None)
    class Budget:
        def __init__(self, **caps):
            assert caps == repair.LIMITS["worker"]
        def checkpoint(self, stage):
            return {}
        def finish(self):
            return budget_receipt()
    class Git:
        def __init__(self, root, role):
            assert role == "coordinator"
            self.root = root
            self.receipt = {"cpu_seconds": 0., "count": 0, "readbacks": []}
        def git(self, *args):
            return "" if args[0] == "status" else original.COLLECTOR_COMMIT if self.root == collector else "a" * 40
    monkeypatch.setattr(repair, "ResearchBudget", Budget)
    monkeypatch.setattr(repair.bridge, "GitHelpers", Git)
    full = original.invented_listing
    def tiny(code, page):
        raw = full(code, 1)
        row = raw[raw.index(b"<tr>"):raw.index(b"</tr>") + 5]
        return (b"<div><div class='pageInfo'>[1/1] [" + "총 1건".encode() +
            b"]</div></div><table><tbody id='tbody'>" + row + b"</tbody></table>")
    monkeypatch.setattr(repair, "invented_listing", tiny)
    geometry = {"codes": 2, "pages_per_code": 1, "rows_per_page": 1, "requests": 2,
        "rows": 2, "maximum_raw_bytes": sum(len(tiny(f"{n:06d}", 1)) for n in range(1, 3))}
    monkeypatch.setattr(repair, "GEOMETRY", geometry)
    monkeypatch.setattr(original, "GEOMETRY", geometry)
    return spec, evidence, collector


def profile():
    result = repair.run_unit(configuration())
    out = Path(result["remote_output"])
    return result, out


def actual_fixture(spec, out):
    """Change only invented fixtures into a simulated original actual failure."""
    origin = json.loads((out / "origin-manifest.json").read_bytes())
    directory = repair.ORIGIN_OUTPUT
    directory.mkdir()
    for name in origin["artifacts"]:
        raw = (out / ("origin-" + name)).read_bytes()
        if name == "registration.json":
            saved = json.loads(raw)
            saved.update(invented_only=False, output=str(directory), attempt="ED-disclosure-actual-v1",
                qualification={"remote_output": str(repair.EVIDENCE / "intraday-ed-disclosure-profile-v1"),
                    "remote_publication_sha256": "f" * 64})
            raw = repair.wire.encode(saved)
        elif name == "canonical-failure-span.jsonl":
            records = [json.loads(line) for line in raw.splitlines()]
            for record in records:
                record["parameters"]["invented_only"] = False
            raw = b"".join(repair.wire.encode(record) for record in records)
        (directory / name).write_bytes(raw)
        origin["artifacts"][name] = repair.wire.digest(raw)
    spec["origin"] = origin
    return origin


def test_native_profile_saves_original_failure_and_qualifies_same_sources(native):
    result, out = profile()
    assert result["status"] == "completed" and result["verified_support"]["complete_listing_rows"] == 2
    saved = json.loads((out / "report.json").read_bytes())
    assert saved["origin_status"] == "failed" and saved["source_event_at"] is None
    assert saved["historical_completeness_certified"] is False
    old = [json.loads(line) for line in (out / "origin-canonical-failure-span.jsonl").read_bytes().splitlines()]
    assert [row["status"] for row in old] == ["started", "failed"]
    config = configuration(False)
    config["qualification"]["remote_publication_sha256"] = result["remote_publication_sha256"]
    repair.qualify_actual(config, lambda: None)
    config["controller_sources"]["var/ed-disclosure-repair-launch.py"] = "9" * 64
    with pytest.raises(ValueError, match="same-source"):
        repair.qualify_actual(config, lambda: None)
    with pytest.raises(FileExistsError):
        repair.run_unit(configuration())


def test_actual_only_reads_pinned_saved_inputs_after_durable_start_and_keeps_unknown(native, monkeypatch):
    spec, evidence, collector = native
    result, out = profile()
    origin = actual_fixture(spec, out)
    before = {name: (repair.ORIGIN_OUTPUT / name).read_bytes() for name in origin["artifacts"]}
    def refused(*args, **kwargs):
        raise AssertionError("actual attempted acquisition or producer parsing")
    monkeypatch.setattr(acquisition, "run_listing_acquisition", refused)
    monkeypatch.setattr(acquisition, "https_listing_transport", refused)
    monkeypatch.setattr(listing, "parse_listing_page", refused)
    real_read = repair.bridge.bounded_read
    reads = []
    def read(path, bound):
        if path.parent == repair.ORIGIN_OUTPUT:
            journal = (collector / "runs/experiments.jsonl").read_bytes().splitlines()
            assert len(journal) == 1 and json.loads(journal[0])["status"] == "started"
            reads.append(path.name)
        return real_read(path, bound)
    monkeypatch.setattr(repair.bridge, "bounded_read", read)
    config = configuration(False)
    config["qualification"]["remote_publication_sha256"] = result["remote_publication_sha256"]
    completed = repair.run_unit(config)
    assert completed["status"] == "completed" and set(reads) == set(origin["artifacts"])
    assert before == {name: (repair.ORIGIN_OUTPUT / name).read_bytes() for name in origin["artifacts"]}
    report = json.loads((Path(config["output"]) / "report.json").read_bytes())
    assert report["origin_status"] == "failed" and report["network_requests"] == 0 and report["producer_calls"] == 0
    assert report["available_at"] is None and report["historical_issuer_binding_certified"] is False
    assert not (evidence / "intraday-ed-disclosure-saved-repair-unresolved.json").exists()


def test_external_pin_failure_preserves_new_failed_span_and_prevents_retry(native):
    spec, evidence, collector = native
    result, out = profile()
    actual_fixture(spec, out)
    raw = repair.ORIGIN_OUTPUT / "attempt-001-raw.html"
    raw.write_bytes(raw.read_bytes() + b"<!--tampered-->")
    config = configuration(False)
    config["qualification"]["remote_publication_sha256"] = result["remote_publication_sha256"]
    with pytest.raises(ValueError, match="artifact hash"):
        repair.run_unit(config)
    new = Path(config["output"])
    span = [json.loads(line) for line in (new / "canonical-failure-span.jsonl").read_bytes().splitlines()]
    assert [row["status"] for row in span] == ["started", "failed"]
    assert (new / "publication-failure.json").exists() and not (new / "publication.json").exists()
    assert (evidence / "intraday-ed-disclosure-saved-repair-unresolved.json").exists()
    with pytest.raises(FileExistsError):
        repair.run_unit(config)
    assert len((collector / "runs/experiments.jsonl").read_bytes().splitlines()) == 2


def test_producer_projection_tampering_is_not_repaired_or_admitted(native):
    _, out = profile()
    origin = json.loads((out / "origin-manifest.json").read_bytes())
    raw = json.loads((out / "origin-report.json").read_bytes())
    raw["listings"][0]["rows"][0]["source_event_at"] = "2026-03-15T09:00:00+09:00"
    changed = repair.wire.encode(raw)
    origin["artifacts"]["report.json"] = repair.wire.digest(changed)
    def read(name):
        return changed if name == "report.json" else (out / ("origin-" + name)).read_bytes()
    with pytest.raises(acquisition.AcquisitionRefusal, match="projection mismatch"):
        repair.verify_origin(origin, read, lambda: None, invented=True)
