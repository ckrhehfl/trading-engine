"""Invented receipts only: bounded EI support handoff and mutation refusals."""

from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_predecessor as predecessor


def _sample(role, pid):
    parent = wire.parent_limits(role, profile=wire.ORDINARY300)
    return {"role": role, "pid": pid, "limits": wire.ORDINARY300.role_limits()[role], "parent_limits": parent,
            "resource_profile": wire.ORDINARY300.id, "cpu_self_seconds": 1.0, "address_space_bytes": 1024,
            "helpers": {"cpu_seconds": 0, "count": 0, "readbacks": []},
            "readback": {"rlimit_cpu": [parent["cpu_seconds"]] * 2, "rlimit_as": [parent["address_space_bytes"]] * 2,
                         "nice": 10, "affinity": [0], "oom_score_adj": "1000"}}


def _invent(root, monkeypatch, mutate=lambda *_: None):
    monkeypatch.setattr(predecessor, "NATIVE", root)
    inputs = tuple(bridge.InputPin(name, f"/invented/not-opened/{name}", str(index + 1) * 64, maximum)
                   for index, (name, maximum) in enumerate(wire.EI_SUPPORT_INPUT_BOUNDS))
    sources = {bridge.EI_SUPPORT_SPECIFICATION_PATH: "4" * 64, bridge.EI_SUPPORT_RUNTIME: "5" * 64,
               bridge.EI_SUPPORT_RECEIPT_AUDITOR: "6" * 64}
    source_sha = wire.digest(wire.encode(sources))
    pin = {"schema": predecessor._SCHEMA, "unit_id": bridge.EI_SUPPORT_UNIT_ID,
           "registration": bridge.EI_SUPPORT_SPECIFICATION_PATH, "qualification_attempt": predecessor.QUALIFICATION,
           "actual_attempt": "actual1", "merge_commit": "a" * 40, "reviewed_commit": "b" * 40, "tree": "c" * 40,
           "registration_sha256": sources[bridge.EI_SUPPORT_SPECIFICATION_PATH]}
    identity = {"source_manifest_sha256": source_sha, "runtime_sha256": "7" * 64, "machine_sha256": "8" * 64,
                "merge_commit": pin["merge_commit"], "tree": pin["tree"], "execution_host": bridge.LOCAL_HOST,
                "resource_profile": wire.ORDINARY300.id}
    modes = {"local_coordinator": True, "local_execution_root": str(root), "execution_host": bridge.LOCAL_HOST}
    aggregate = wire.aggregate_local_roles({role: _sample(role, index + 10)
        for index, role in enumerate(("worker", "coordinator", "controller"))}, profile=wire.ORDINARY300)
    paths = {}

    def save(name, path, value, *, raw=False):
        mutate(name, value)
        data = value if raw else wire.encode(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        paths[name] = path
        return wire.digest(data)

    for mode, attempt in (("qualification", predecessor.QUALIFICATION), ("actual", "actual1")):
        invented = mode == "qualification"
        leaf = bridge.EI_SUPPORT_UNIT_ID + ("-invented-" if invented else "-") + attempt
        cache, output, diagnostics = (root / key / leaf for key in ("cache", "output", "diagnostics"))
        directory = root / "launches" / bridge.EI_SUPPORT_UNIT_ID / attempt
        journal = output / "invented-experiments.jsonl" if invented else root / "canonical" / "experiments.jsonl"
        launch = {**modes, "unit_id": bridge.EI_SUPPORT_UNIT_ID, "attempt": attempt, "invented_only": invented,
            "local_pipe_test": False, "sessions": predecessor.FULL["sessions"], "wall_seconds": wire.ORDINARY300.wall_seconds,
            "automatic_retries": 0, "resource_profile": wire.ORDINARY300.id, "cache": str(cache), "output": str(output),
            "diagnostics": str(diagnostics), "journal": str(journal), "source_hashes": deepcopy(sources),
            "merge_commit": pin["merge_commit"], "reviewed_commit": pin["reviewed_commit"], "tree": pin["tree"],
            "local_identity": deepcopy(identity), "remote_identity": deepcopy(identity),
            "deadline_epoch": datetime(2031, 1, 1, 1, tzinfo=timezone.utc).timestamp()}
        if not invented:
            launch["qualification"] = {"output": str(root / "output" / (bridge.EI_SUPPORT_UNIT_ID + "-invented-" + predecessor.QUALIFICATION)),
                "publication_sha256": pin["qualification"]["publication_sha256"], "audit_sha256": pin["qualification"]["audit_sha256"]}
        launch_sha = save(mode + ".launch", directory / "launch.json", launch)
        save(mode + ".launch_identity", directory / "launch-identity.json", {"launch_sha256": launch_sha,
            "runtime_sha256": sources[bridge.EI_SUPPORT_RUNTIME], "merge": pin["merge_commit"],
            "reviewed": pin["reviewed_commit"], "tree": pin["tree"], "mode": mode, "invented_only": invented,
            "qualifies_actual_by_this_record": False})
        manifest = [item.metadata(index + 10) for index, item in enumerate(inputs)]
        if invented:
            manifest = [{**item, "original_path": f"/invented/qualification/{item['name']}"} for item in manifest]
        raw_audit = {"schema": "ED-raw-byte-cache-audit-v1", "verified": True, "full_eof_verified": True,
            "raw_keys": predecessor.FULL["cc_chunks"] + predecessor.FULL["cx_chunks"], "cache_sha256": "9" * 64,
            "archives": {kind: {"kind": kind, "compressed_sha256": item["sha256"], "decoded_sha256": "d" * 64,
                "expected_chunks": predecessor.FULL[kind.lower() + "_chunks"], "expected_rows": predecessor.FULL[kind.lower() + "_rows"],
                "expected_daily_rows": predecessor.FULL["cx_daily_rows"] if kind == "CX" else 0}
                for kind, item in zip(("CC", "CX"), manifest[1:], strict=True)}}
        proof = {"verified": True, "schema": bridge.EI_SUPPORT_OUTPUT_SCHEMA, "full_eof_verified": True,
            "all_original_coordinates_verified": True, "invented_only": invented, "full_geometry": True,
            "historical_availability_certified": False, "input_manifest": manifest,
            "input_manifest_sha256": wire.digest(wire.encode(manifest)), "raw_byte_audit": raw_audit,
            "summary_sha256": "e" * 64, "support_totals": {"invented_only": True}}
        mutate(mode + ".proof", proof)
        artifact = b"Invented census bytes; intentionally no semantic replay.\n"
        output_sha = save(mode + ".artifact", output / bridge.EI_SUPPORT_OUTPUT_FILENAME, artifact, raw=True)
        run_id = "00000000-0000-0000-0000-00000000000" + ("1" if invented else "2")
        result = {"output_sha256": output_sha, "output_bytes": len(artifact), "verification_complete": True,
                  "independent_verification": proof, "aggregate_budget": deepcopy(aggregate), "automatic_retries": 0}
        common = {"record_type": "discovery_trial", "study_id": bridge.EI_SUPPORT_UNIT_ID, "run_id": run_id,
            "specification_sha256": pin["registration_sha256"], "window": predecessor.WINDOW, "code_version": pin["merge_commit"],
            "promotion_allowed": False, "promotion_trial_increment": 0, "parameters": {**modes, "invented_only": invented,
                "attempt": attempt, "source_manifest_sha256": source_sha, "resource_profile": wire.ORDINARY300.id,
                "automatic_retries": 0, "input_contract": wire.EI_SUPPORT_INPUT_CONTRACT}}
        records = [{**common, "status": "started", "logged_at": "2031-01-01T00:00:00+00:00"},
                   {**common, "status": "completed", "logged_at": "2031-01-01T00:00:10+00:00", "result": result}]
        mutate(mode + ".journal", records)
        lines = [(json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode() for row in records]
        preceding = b"" if invented else b'{"preserved":"prior unrelated run"}\n'
        start, end = len(preceding), len(preceding) + sum(map(len, lines))
        save(mode + ".journal_bytes", journal, preceding + b"".join(lines), raw=True)
        publication = {**modes, "status": "completed", "unit_id": bridge.EI_SUPPORT_UNIT_ID, "resource_profile": wire.ORDINARY300.id,
            "invented_only": invented, "full_bridge": True, "local_pipe_test": False, "input_contract": wire.EI_SUPPORT_INPUT_CONTRACT,
            "invented_journal": str(journal) if invented else None,
            **{key: deepcopy(launch[key]) for key in ("source_hashes", "local_identity", "remote_identity")},
            **{key: value for key, value in result.items() if key != "automatic_retries"},
            "geometry": dict(predecessor.FULL), "candidate_counts": {clock: {"original": predecessor.FULL["targets_per_scenario"]} for clock in ("S1", "S2")},
            "run_id": run_id, "canonical_offsets": [start, end], "terminal_record_sha256": wire.digest(lines[-1]),
            "coordinator_after_terminal": deepcopy(aggregate["samples"]["coordinator"])}
        publication_sha = save(mode + ".publication", output / "publication.json", publication)
        audit = {"schema": "eh-support-original-receipt-audit-v1", "status": "PASS", "invented_only": invented,
            "science_reexecuted": False, "unit_id": bridge.EI_SUPPORT_UNIT_ID, "attempt": attempt,
            "publication_sha256": publication_sha, "source_manifest_sha256": source_sha, "output_sha256": output_sha,
            "merge_commit": pin["merge_commit"], "tree": pin["tree"], "runtime_sha256": identity["runtime_sha256"],
            "auditor_source_sha256": sources[bridge.EI_SUPPORT_RECEIPT_AUDITOR]}
        audit_sha = save(mode + ".audit", directory / "original-receipt-audit.json", audit)
        process = {**modes, "status": "completed_ack_received", "canonical_truth": "local WSL journal only", "automatic_retries": 0,
            "terminal": {"kind": "COMPLETED", "status": "completed", "run_id": run_id, "output_sha256": output_sha,
                "record_sha256": wire.digest(lines[-1]), "publication_sha256": publication_sha},
            "controller": deepcopy(aggregate["samples"]["controller"]),
            "children": {role: {"pid": aggregate["samples"][role]["pid"], "returncode": 0, "cpu_self_seconds": 1.0,
                "peak_rss_kib": 1, "measurement": "wait4 dedicated child/descendants; diagnostic only"} for role in ("worker", "coordinator")}}
        process_sha = save(mode + ".process", diagnostics / "process-receipt.json", process)
        pin[mode] = {"launch_sha256": launch_sha, "publication_sha256": publication_sha, "audit_sha256": audit_sha,
            "output_sha256": output_sha, "output_bytes": len(artifact), "source_manifest_sha256": source_sha,
            "runtime_sha256": identity["runtime_sha256"], "auditor_source_sha256": sources[bridge.EI_SUPPORT_RECEIPT_AUDITOR],
            "terminal_record_sha256": wire.digest(lines[-1]), "canonical_offsets": [start, end], "process_receipt_sha256": process_sha, "run_id": run_id}
    return pin, inputs, paths


def test_verifies_two_published_hashes_without_opening_market_inputs_or_old_markers(tmp_path, monkeypatch):
    pin, inputs, paths = _invent(tmp_path, monkeypatch)
    old = tmp_path / "canonical" / "eh-support-unresolved.json"
    old.write_bytes(b"preserved unrelated EH failure")
    reads, checkpoints = [], []
    original = wire.regular

    def observed(path):
        reads.append(path)
        assert str(path) not in {item.original_path for item in inputs}
        return original(path)

    monkeypatch.setattr(wire, "regular", observed)
    proof = predecessor.validate_support_predecessor(pin, tmp_path, inputs, checkpoints.append)
    assert proof["verified"] and proof["output_hash_verified"] and proof["science_reexecuted"] is False
    assert proof["actual_input_manifest"] == [item.metadata(index + 10) for index, item in enumerate(inputs)]
    assert paths["qualification.artifact"] in reads and paths["actual.artifact"] in reads
    assert checkpoints.count("file_hash") == 2 and checkpoints[-1] == "EI_predecessor_complete"
    assert old.read_bytes() == b"preserved unrelated EH failure"


@pytest.mark.parametrize("mutation", ("attempt", "extra", "git", "sha", "mode_runtime", "offset", "output_bound", "run_id", "root", "inputs"))
def test_pin_and_native_contract_fail_before_receipt_reads(tmp_path, monkeypatch, mutation):
    pin, inputs, _ = _invent(tmp_path, monkeypatch)
    native = tmp_path
    if mutation == "attempt":
        pin["qualification_attempt"] = "qualification-v2"
    elif mutation == "extra":
        pin["ready"] = True
    elif mutation == "git":
        pin["reviewed_commit"] = "not-a-commit"
    elif mutation == "sha":
        pin["actual"]["audit_sha256"] = "z" * 64
    elif mutation == "mode_runtime":
        pin["actual"]["runtime_sha256"] = "0" * 64
    elif mutation == "offset":
        pin["qualification"]["canonical_offsets"][0] = 1
    elif mutation == "output_bound":
        pin["actual"]["output_bytes"] = 33 * wire.MIB
    elif mutation == "run_id":
        pin["actual"]["run_id"] = "not-a-run"
    elif mutation == "root":
        native = tmp_path / "other"
    else:
        inputs = tuple(reversed(inputs))
    monkeypatch.setattr(bridge, "bounded_read", lambda *_: pytest.fail("read before structural refusal"))
    with pytest.raises(ValueError):
        predecessor.validate_support_predecessor(pin, native, inputs, lambda _: None)


@pytest.mark.parametrize("name,mutator", (
    ("actual.launch", lambda value: value.update(reviewed_commit="f" * 40)),
    ("actual.launch", lambda value: value["qualification"].update(audit_sha256="0" * 64)),
    ("qualification.launch_identity", lambda value: value.update(qualifies_actual_by_this_record=True)),
    ("actual.publication", lambda value: value.update(full_bridge=False)),
    ("actual.publication", lambda value: value.update(resource_profile="ef-extended")),
    ("actual.publication", lambda value: value["geometry"].update(sessions=1)),
    ("actual.publication", lambda value: value["candidate_counts"]["S1"].update(original=1)),
    ("actual.publication", lambda value: value["aggregate_budget"].update(cpu_seconds_counted_once=0)),
    ("actual.audit", lambda value: value.update(status="FAIL")),
    ("actual.audit", lambda value: value.update(science_reexecuted=True)),
    ("actual.audit", lambda value: value.update(auditor_source_sha256="0" * 64)),
    ("actual.proof", lambda value: value.update(full_eof_verified=False)),
    ("actual.proof", lambda value: value["raw_byte_audit"].update(raw_keys=1)),
    ("actual.proof", lambda value: value["raw_byte_audit"]["archives"]["CX"].update(expected_daily_rows=0)),
    ("actual.proof", lambda value: value["input_manifest"][0].update(size=17 * wire.MIB)),
    ("actual.process", lambda value: value["children"]["worker"].update(returncode=1)),
    ("actual.process", lambda value: value["children"]["coordinator"].update(pid=999)),
    ("actual.process", lambda value: value["controller"].update(cpu_self_seconds=999)),
    ("actual.journal", lambda value: value[0].update(status="completed")),
    ("actual.journal", lambda value: value[1].update(code_version="f" * 40)),
    ("actual.journal", lambda value: value[1].update(logged_at="2031-01-01T02:00:00+00:00")),
))
def test_resealed_metadata_semantic_mutations_refuse(tmp_path, monkeypatch, name, mutator):
    def mutate(key, value):
        if key == name:
            mutator(value)

    pin, inputs, _ = _invent(tmp_path, monkeypatch, mutate)
    with pytest.raises(ValueError):
        predecessor.validate_support_predecessor(pin, tmp_path, inputs, lambda _: None)


@pytest.mark.parametrize("name", ("qualification.launch", "actual.publication", "actual.audit", "actual.process", "actual.journal_bytes", "actual.artifact"))
def test_original_bytes_changed_after_audit_refuse(tmp_path, monkeypatch, name):
    pin, inputs, paths = _invent(tmp_path, monkeypatch)
    path = paths[name]
    raw = bytearray(path.read_bytes())
    if name == "actual.journal_bytes":
        raw[pin["actual"]["canonical_offsets"][1] - 3] ^= 1
    else:
        raw.extend(b"changed")
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        predecessor.validate_support_predecessor(pin, tmp_path, inputs, lambda _: None)


def test_later_unrelated_journal_append_does_not_change_pinned_completed_span(tmp_path, monkeypatch):
    pin, inputs, paths = _invent(tmp_path, monkeypatch)
    journal = paths["actual.journal_bytes"]
    journal.write_bytes(journal.read_bytes() + b'{"preserved":"later unrelated run"}\n')
    assert predecessor.validate_support_predecessor(pin, tmp_path, inputs, lambda _: None)["verified"]


@pytest.mark.parametrize("field,value", (("original_path", "/invented/different/CB"), ("sha256", "0" * 64),
                                       ("maximum", 512 * wire.MIB), ("size", 0)))
def test_resealed_original_input_identity_and_bounds_refuse(tmp_path, monkeypatch, field, value):
    def mutate(name, proof):
        if name == "actual.proof":
            proof["input_manifest"][0][field] = value
            proof["input_manifest_sha256"] = wire.digest(wire.encode(proof["input_manifest"]))

    pin, inputs, _ = _invent(tmp_path, monkeypatch, mutate)
    with pytest.raises(ValueError):
        predecessor.validate_support_predecessor(pin, tmp_path, inputs, lambda _: None)


@pytest.mark.parametrize("location", ("global", "out_unresolved", "out_failure", "worker", "process"))
def test_ei_markers_block_before_receipt_reads(tmp_path, monkeypatch, location):
    pin, inputs, paths = _invent(tmp_path, monkeypatch)
    out = paths["actual.publication"].parent
    markers = {"global": tmp_path / "canonical" / bridge.EI_SUPPORT_MARKER,
               "out_unresolved": out / "unresolved.json", "out_failure": out / "bridge-failure.json",
               "worker": tmp_path / "cache" / (bridge.EI_SUPPORT_UNIT_ID + "-actual1") / "worker-failure.json",
               "process": paths["actual.process"].parent / "process-failure.json"}
    markers[location].parent.mkdir(parents=True, exist_ok=True)
    markers[location].write_bytes(b"invented failure")
    monkeypatch.setattr(bridge, "bounded_read", lambda *_: pytest.fail("read before marker refusal"))
    with pytest.raises(ValueError, match="marker"):
        predecessor.validate_support_predecessor(pin, tmp_path, inputs, lambda _: None)


@pytest.mark.parametrize("mutation", ("marker", "receipt", "output"))
def test_post_validation_mutations_refuse(tmp_path, monkeypatch, mutation):
    pin, inputs, paths = _invent(tmp_path, monkeypatch)

    def checkpoint(stage):
        if stage != "EI_predecessor_complete":
            return
        path = (tmp_path / "canonical" / bridge.EI_SUPPORT_MARKER if mutation == "marker" else
                paths["actual.audit" if mutation == "receipt" else "qualification.artifact"])
        path.write_bytes(b"invented concurrent change")

    with pytest.raises(ValueError):
        predecessor.validate_support_predecessor(pin, tmp_path, inputs, checkpoint)


def test_symlinked_metadata_and_oversized_receipt_refuse(tmp_path, monkeypatch):
    pin, inputs, paths = _invent(tmp_path, monkeypatch)
    original = paths["actual.audit"]
    raw = original.read_bytes()
    original.unlink()
    original.symlink_to(paths["qualification.audit"])
    with pytest.raises(ValueError, match="symlink"):
        predecessor.validate_support_predecessor(pin, tmp_path, inputs, lambda _: None)
    original.unlink()
    original.write_bytes(raw + b" " * 65536)
    with pytest.raises(ValueError, match="bounded"):
        predecessor.validate_support_predecessor(pin, tmp_path, inputs, lambda _: None)
