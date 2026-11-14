"""Invented EL dataset source, predecessor, shard, failure and fixture gates."""

from collections import Counter
from contextlib import contextmanager
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_dataset_run as run
from research import intraday_event_fixtures as fixtures
from research import intraday_event_support_run as support_run


ROOT = Path(__file__).absolute().parents[2]
MERGE, REVIEWED, TREE, SUPPORT_PIN = "1" * 40, "2" * 40, "3" * 40, "e" * 64
USED = {"worker_cpu_seconds": 10, "helper_cpu_seconds": 1, "wall_seconds": 20}


def spec():
    return json.loads((ROOT / run.SPEC).read_bytes())


def invented_manifest():
    return {"session_indices": [0, 32], "observed_maximum_names_on_day": 23, "counts": {"coordinates": 658,
        "scenario_coordinates": {"S1": 640, "S2": 640},
        "events_by_family": dict.fromkeys(("A_DOWNFAIL", "B_ORL30", "C_FIRSTPULLBACK"), 1),
        "frames_by_state": dict.fromkeys(("CONFIRMED", "INVALIDATED", "EXPIRED_NO_CONFIRMATION",
                                          "EXPIRED_NO_PULLBACK", "UNKNOWN_GAP"), 1),
        "path_status": dict.fromkeys(("known", "unknown", "unknown_intrabar"), 1),
        "immediate_status": dict.fromkeys(("known", "no_fill", "unknown", "no_entry"), 1),
        "held_checkpoint_status": {"free_alive": 1}, "held_target_status": {"known": 1},
        "counterfactual_books": 2, "held_queries": 1}}


def test_shards_partition_every_calendar_session_once_with_maximum_first_qualification():
    assert [i for start, end in run.SHARDS for i in range(start, end)] == list(range(257))
    assert max(end - start for start, end in run.SHARDS) == 32
    assert run.SHARDS[-1] == (256, 257)
    assert run.attempt_name("actual1", 0) == "actual1-s00"
    assert run.attempt_name("actual2", 8) == "actual2-s08"
    for base, shard in (("actual4", 0), ("actual1", True), ("actual1", 9)):
        with pytest.raises(ValueError, match="bounded shard"):
            run.attempt_name(base, shard)


@pytest.mark.parametrize("key,value", [
    ("id", "EL-SUPPORT-1"), ("model_fits_enabled", True), ("orders_enabled", True),
    ("shard_sessions", 31), ("session_shards", [[0, 32]]),
    ("qualification_session_indices", [32, 64]), ("qualification_full_source_geometry", False),
    ("source_registration_sha256", "0" * 64), ("scientific_contract", {}),
])
def test_registered_contract_cannot_silently_change_sampling_science_or_scope(key, value):
    value_spec = spec()
    run.validate_spec(value_spec)
    value_spec[key] = value
    with pytest.raises(ValueError, match="registered scope|scientific contract"):
        run.validate_spec(value_spec)


def test_exact_scientific_contract_binds_the_admitted_dataset_implementation():
    value = spec()
    run.verify_contract(value)
    value["scientific_contract"]["wait_minutes"] = 4
    with pytest.raises(ValueError, match="scientific implementation"):
        run.verify_contract(value)


@pytest.fixture
def reviewed_source(monkeypatch, tmp_path):
    raw = {run.SPEC: (ROOT / run.SPEC).read_bytes(), run.ORIGINAL: (ROOT / run.ORIGINAL).read_bytes(),
           "python/research/__init__.py": b"", run.OWN: b'"""Invented closed entry point."""\n'}
    for name, value in raw.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    def git(*args):
        if args[0] == "show":
            return f'{MERGE}\n{TREE}\n{"4" * 40} {REVIEWED}\n'.encode()
        if args[0] == "rev-parse":
            return (TREE + "\n").encode()
        if args[0] in ("merge-base", "status"):
            return b""
        assert args[0] == "ls-tree"
        return b"".join(("100644 blob " + sha1(f"blob {len(value)}\0".encode() + value,
            usedforsecurity=False).hexdigest() + "\t" + name).encode() + b"\0" for name, value in raw.items())
    monkeypatch.setattr(run, "REPO", tmp_path)
    monkeypatch.setattr(run, "ROOTS", ())
    monkeypatch.setattr(run, "_git", git)
    monkeypatch.setattr(run, "sys", SimpleNamespace(modules={}, meta_path=[]))
    return tmp_path, raw


def test_new_spec_and_original_registration_are_in_exact_reviewed_closure(reviewed_source):
    _, raw = reviewed_source
    _, _, identity = run.freeze(MERGE, REVIEWED, TREE)
    assert identity["registration_sha256"] == sha256(raw[run.SPEC]).hexdigest()
    assert identity["parents_sha256"] == run.ORIGINAL_SHA256
    assert identity["sources"][run.OWN] == sha256(raw[run.OWN]).hexdigest()
    assert len(run.sys.meta_path) == 1


def test_dirty_source_is_refused_even_when_git_status_is_clean(reviewed_source):
    root, _ = reviewed_source
    (root / run.SPEC).write_bytes(b'{"changed":true}\n')
    with pytest.raises(ValueError, match="reviewed blob differs"):
        run.freeze(MERGE, REVIEWED, TREE)


@pytest.mark.parametrize("alias", ["symbolic", "hard"])
def test_reviewed_source_aliases_are_refused_before_import_admission(reviewed_source, alias):
    root, _ = reviewed_source
    path = root / run.SPEC
    target = root / "elsewhere.json"
    path.rename(target)
    if alias == "symbolic":
        path.symlink_to(target)
    else:
        os.link(target, path)
    with pytest.raises(ValueError, match="unaliased source|source bound"):
        run.freeze(MERGE, REVIEWED, TREE)
    assert not run.sys.meta_path


def test_dynamic_project_import_cannot_escape_the_reviewed_closure():
    finder = run.ReviewedModules({"research": ("python/research/__init__.py", b"")})
    assert finder.find_spec("json") is None
    assert finder.find_spec("research").submodule_search_locations is not None
    with pytest.raises(ValueError, match="Unregistered EL project import"):
        finder.find_spec("research.invented_unregistered_plugin")


def test_runtime_requires_the_exact_system_interpreter_before_hashing(monkeypatch):
    monkeypatch.setattr(run, "sys", SimpleNamespace(executable="/tmp/invented-other-python"))
    monkeypatch.setattr(bridge, "runtime_sha", lambda: pytest.fail("unexpected interpreter hashed"))
    with pytest.raises(ValueError, match="pinned local interpreter"):
        run.runtime_pin()


@pytest.mark.parametrize("worker,helper,wall", [(1491, 0, 0), (1000, 800, 0), (0, 0, 3581)])
def test_producer_plus_auditor_share_one_resource_budget(worker, helper, wall):
    run.verify_usage(USED, USED)
    with pytest.raises(ValueError, match="combined resource envelope"):
        run.verify_usage(USED, {"worker_cpu_seconds": worker,
                              "helper_cpu_seconds": helper, "wall_seconds": wall})


def test_saved_roster_and_every_pin_use_the_assembler_streaming_bound(monkeypatch, tmp_path):
    from research import intraday_event_dataset as dataset
    manifest = {"files": {name: {"filename": name, "sha256": "a" * 64, "bytes": 2, "rows": 1}
                          for name in dataset.FILES},
                "manifest_file": {"filename": "dataset-manifest.json", "sha256": "a" * 64, "bytes": 2, "rows": 1}}
    calls = []
    def digest(path, maximum, checkpoint):
        calls.append(path.name)
        assert maximum == dataset.MAX_FILE
        return "a" * 64, 2
    monkeypatch.setattr(wire, "file_digest", digest)
    run.verify_dataset_files(tmp_path, manifest)
    assert set(calls) == set(dataset.FILES) | {"dataset-manifest.json"}
    manifest["files"].pop(dataset.FILES[0])
    with pytest.raises(ValueError, match="output roster"):
        run.verify_dataset_files(tmp_path, manifest)


def test_predecessor_pin_precedes_its_audit_and_requires_the_original_actual_release(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "NATIVE", tmp_path)
    path = tmp_path / "output/EL-SUPPORT-1-actual1/audit.json"
    path.parent.mkdir(parents=True)
    identity = {"merge": run.SUPPORT["merge"], "reviewed": run.SUPPORT["reviewed"], "attempt": "actual1",
                "invented": False, "parents_sha256": run.ORIGINAL_SHA256}
    raw = wire.encode({"identity": identity})
    path.write_bytes(raw)
    pin = sha256(raw).hexdigest()
    calls = []
    def audited(got):
        assert got == identity
        calls.append(got)
        return {}, pin
    monkeypatch.setattr(support_run, "audited", audited)
    with pytest.raises(ValueError, match="predecessor audit pin"):
        run.verify_support_predecessor("0" * 64)
    assert not calls
    assert run.verify_support_predecessor(pin) == pin and len(calls) == 1
    identity["invented"] = True
    raw = wire.encode({"identity": identity})
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="predecessor actual source"):
        run.verify_support_predecessor(sha256(raw).hexdigest())


@pytest.mark.parametrize("group,state", [("events_by_family", "A_DOWNFAIL"), ("events_by_family", "B_ORL30"),
    ("events_by_family", "C_FIRSTPULLBACK"), ("frames_by_state", "UNKNOWN_GAP"),
    ("path_status", "unknown_intrabar"), ("immediate_status", "no_fill"), ("immediate_status", "unknown"),
    ("held_checkpoint_status", "free_alive"), ("held_target_status", "known")])
def test_qualification_cannot_pass_with_zero_events_or_unexercised_labels(group, state):
    manifest = invented_manifest()
    run.qualification_coverage(manifest)
    manifest["counts"][group][state] = 0
    with pytest.raises(ValueError, match="did not exercise"):
        run.qualification_coverage(manifest)


def test_qualification_must_compute_the_full_maximum_shard():
    manifest = invented_manifest()
    manifest["counts"]["coordinates"] = 640
    with pytest.raises(ValueError, match="maximum thirty-two-session"):
        run.qualification_coverage(manifest)
    manifest = invented_manifest()
    manifest["observed_maximum_names_on_day"] = 22
    with pytest.raises(ValueError, match="maximum thirty-two-session"):
        run.qualification_coverage(manifest)


def test_recovery_binds_exact_failed_release_journal_and_empty_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "NATIVE", tmp_path)
    slot = tmp_path / "output/EL-DATASET-1-actual1-s00"
    slot.mkdir(parents=True)
    marker = tmp_path / "canonical/EL-DATASET-1-actual1-s00-unresolved.json"
    marker.parent.mkdir()
    registration = "a" * 64
    identity = {**run.FAILED_RELEASE, "attempt": "actual1-s00", "invented": False, "shard": 0,
        "session_indices": [0, 32], "registration_sha256": registration, "support_audit_sha256": SUPPORT_PIN,
        "parents_sha256": run.ORIGINAL_SHA256, "qualification_sha256": "b" * 64}
    marker.write_bytes(wire.encode(identity))
    started = {"record_type": "discovery_trial", "study_id": run.ORIGINAL_UNIT, "run_id": "invented-run",
        "parameters": identity, "promotion_allowed": False, "promotion_trial_increment": 0,
        "code_version": identity["merge"], "window": run.WINDOW, "specification_sha256": registration, "status": "started"}
    terminal = wire.encode({**started, "status": "failed", "error_type": "ValueError"})
    journal = wire.encode(started) + terminal
    (marker.parent / "experiments.jsonl").write_bytes(journal)
    args = (wire.digest(marker.read_bytes()), wire.digest(terminal), 0, len(journal), registration, SUPPORT_PIN)
    assert run.verify_failed_predecessor(*args)["terminal_sha256"] == args[1]
    with pytest.raises(ValueError, match="terminal failure pin"):
        run.verify_failed_predecessor(args[0], "0" * 64, *args[2:])
    (slot / "unexpected").touch()
    with pytest.raises(ValueError, match="unexpected output"):
        run.verify_failed_predecessor(*args)


@pytest.fixture
def local_run(monkeypatch, tmp_path):
    from research import intraday_event_dataset as dataset
    monkeypatch.setattr(run, "NATIVE", tmp_path)
    (tmp_path / "canonical").mkdir()
    (tmp_path / "output").mkdir()
    for relative in ("canonical/experiments.jsonl", "canonical/intraday-research.lock", "output/eh-support-campaign.lock"):
        (tmp_path / relative).write_bytes(b"")
    monkeypatch.setattr(run, "sys", SimpleNamespace(flags=SimpleNamespace(isolated=True, no_site=True), platform="linux"))
    real_path = run.Path
    monkeypatch.setattr(run, "Path", lambda value: SimpleNamespace(read_text=lambda: "invented microsoft kernel")
                        if value == "/proc/sys/kernel/osrelease" else real_path(value))
    for name in ("budget", "verify_contract"):
        monkeypatch.setattr(run, name, lambda *args: None)
    monkeypatch.setattr(run, "runtime_pin", lambda: "d" * 64)
    monkeypatch.setattr(run, "usage", lambda: dict(USED))
    monkeypatch.setattr(run, "audit_budget", lambda _: None)
    release = {"merge": MERGE, "reviewed": REVIEWED, "tree": TREE, "registration_sha256": "a" * 64,
               "parents_sha256": "b" * 64, "sources": {}}
    monkeypatch.setattr(run, "freeze", lambda *args: ({"window": run.WINDOW}, {}, dict(release)))
    monkeypatch.setattr(wire, "prohibit_ef_tasks", lambda: None)
    monkeypatch.setattr(bridge, "restrict_local_network", lambda: None)
    monkeypatch.setattr(run, "verify_support_predecessor", lambda pin: SUPPORT_PIN if pin == SUPPORT_PIN else pytest.fail("wrong support pin"))
    monkeypatch.setattr(run, "verify_failed_predecessor", lambda *_: {"terminal_sha256": "f" * 64})
    def qualified(identity):
        assert identity["invented"] is True and identity["shard"] is None and identity["session_indices"] == [0, 32]
        assert identity["support_audit_sha256"] == SUPPORT_PIN
        return {}, "c" * 64
    monkeypatch.setattr(run, "audited", qualified)
    monkeypatch.setattr(dataset, "build_dataset", lambda *args, **kwargs: {"session_indices": list(kwargs["session_indices"]), "invented_stub": True})
    return tmp_path


def invoke(monkeypatch, mode="actual", base="actual1", shard=0):
    monkeypatch.setattr(sys, "argv", ["invented-el-dataset", mode, "--merge", MERGE, "--reviewed", REVIEWED,
        "--tree", TREE, "--support-audit-sha256", SUPPORT_PIN, "--failed-marker-sha256", "a" * 64,
        "--failed-terminal-sha256", "b" * 64, "--failed-start", "0", "--failed-end", "42",
        "--attempt", base, "--shard", str(shard)])
    run.main()


def test_durable_started_binds_shard_qualification_and_support_before_any_original_read(local_run, monkeypatch):
    @contextmanager
    def inputs(slot, prior, invented, audit):
        assert not invented and not audit
        first = json.loads((local_run / "canonical/experiments.jsonl").read_bytes())
        assert first["status"] == "started"
        identity = first["parameters"]
        assert identity["shard"] == 0 and identity["session_indices"] == [0, 32]
        assert identity["attempt"] == "actual1-s00" and identity["qualification_sha256"] == "c" * 64
        assert identity["support_audit_sha256"] == SUPPORT_PIN
        assert identity["original_failed_attempt"]["terminal_sha256"] == "f" * 64
        assert run._doc(run._marker(identity["attempt"])) == identity
        yield object(), object()
    monkeypatch.setattr(run, "original_inputs", inputs)
    invoke(monkeypatch)
    done = run._doc(run._slot("actual1-s00") / "completion.json")
    assert run.publication(done["identity"])[0]["dataset"]["session_indices"] == [0, 32]
    with pytest.raises(ValueError, match="attempt already exists"):
        invoke(monkeypatch)


def test_next_shard_cannot_skip_a_missing_or_unaudited_predecessor(local_run, monkeypatch):
    monkeypatch.setattr(run, "original_inputs", lambda *args: pytest.fail("raw reader before predecessor audit"))
    with pytest.raises(ValueError, match="preceding shard is absent"):
        invoke(monkeypatch, shard=1)
    assert not run._slot("actual1-s01").exists()


def test_a_later_shard_path_blocks_first_shard_as_an_ambiguous_batch(local_run, monkeypatch):
    run._slot("actual1-s08").mkdir()
    monkeypatch.setattr(run, "original_inputs", lambda *args: pytest.fail("raw reader before order admission"))
    with pytest.raises(ValueError, match="later shard already exists"):
        invoke(monkeypatch)
    assert not run._slot("actual1-s00").exists()


def test_timeout_retry_has_new_shard_path_and_two_failures_close_same_implementation(local_run, monkeypatch):
    @contextmanager
    def inputs(*args):
        raise TimeoutError("invented worker timeout")
        yield
    monkeypatch.setattr(run, "original_inputs", inputs)
    for base in ("actual1", "actual2"):
        with pytest.raises(TimeoutError):
            invoke(monkeypatch, base=base)
        failed = run._doc(run._slot(run.attempt_name(base, 0)) / "timeout.json")
        run._failed(failed["identity"])
    with pytest.raises(ValueError, match="two consecutive failures"):
        invoke(monkeypatch, base="actual3")
    assert not run._slot("actual3-s00").exists()


def test_report_corruption_is_rejected_before_independent_raw_rebuild(local_run, monkeypatch):
    @contextmanager
    def inputs(*args):
        yield object(), object()
    monkeypatch.setattr(run, "original_inputs", inputs)
    invoke(monkeypatch)
    (run._slot("actual1-s00") / "report.json").write_bytes(b'{"changed":true}\n')
    monkeypatch.setattr(run, "original_inputs", lambda *args: pytest.fail("original re-opened before publication verification"))
    with pytest.raises(ValueError, match="report changed"):
        invoke(monkeypatch, mode="actual-audit")


def test_nontechnical_failure_is_durable_but_not_an_automatic_retry(local_run, monkeypatch):
    @contextmanager
    def inputs(*args):
        raise ValueError("invented scientific integrity failure")
        yield
    monkeypatch.setattr(run, "original_inputs", inputs)
    with pytest.raises(ValueError, match="scientific integrity"):
        invoke(monkeypatch)
    rows = [json.loads(row) for row in (local_run / "canonical/experiments.jsonl").read_bytes().splitlines()]
    assert [row["status"] for row in rows] == ["started", "failed"]
    assert rows[-1]["error_type"] == "ValueError"
    assert run._marker("actual1-s00").is_file()
    with pytest.raises(FileNotFoundError):
        invoke(monkeypatch, base="actual2")
    assert not run._slot("actual2-s00").exists()


def test_new_fixture_really_generates_all_registered_event_and_label_cases():
    from research import intraday_ed_sources as sources
    from research.intraday_event_inputs import adapt_session
    from research.intraday_event_scenarios import scan_scenarios
    from research.intraday_event_scenarios_reference import verify
    from research.intraday_event_features import chart_features, coalesce_frames
    from research.intraday_event_labels import prepare_query, build_targets
    day, symbol = "2025-09-04", "KRX:000001"
    events, states, paths, cash = Counter(), Counter(), Counter(), Counter()
    for case in range(len(fixtures.CASES)):
        price = fixtures.raw_chunk("CC", day, symbol, 195, case)
        volume = fixtures.raw_chunk("CX", day, symbol, 195, case)
        observed = adapt_session(sources.full_reference_session_inputs(symbol, day, price, volume))
        end = observed.session_length - 10
        series = f"invented:{case}"
        projection = scan_scenarios(observed.bars.values(), end_offset=end, session_length=end, series_id=series)
        verify(projection, observed.bars.values(), end_offset=end, session_length=end, series_id=series)
        events.update(frame.family for frame in projection.frames if frame.kind == "at_onset")
        states.update(frame.state for frame in projection.frames)
        for frame in coalesce_frames(projection.frames):
            if frame.decision_offset >= end:
                continue
            chart = chart_features(frame, observed.bars, session_length=end, volumes=observed.volumes)
            target = build_targets(prepare_query(frame, chart, session_length=end), observed.bars)
            paths[target.path.status] += 1
            cash[target.immediate.status] += 1
    assert set(events) == {"A_DOWNFAIL", "B_ORL30", "C_FIRSTPULLBACK"}
    assert {"CONFIRMED", "INVALIDATED", "EXPIRED_NO_CONFIRMATION", "EXPIRED_NO_PULLBACK", "UNKNOWN_GAP"} <= states.keys()
    assert {"known", "unknown", "unknown_intrabar"} <= paths.keys()
    assert {"known", "no_fill", "unknown", "no_entry"} <= cash.keys()


def test_invented_archive_geometry_and_decoded_pins_use_fresh_exclusive_paths(tmp_path):
    from research import intraday_ed_sources as sources
    directory = tmp_path / "invented"
    directory.mkdir(mode=0o700)
    made = fixtures.make_invented(directory, 1, lambda _: None)
    geometry = made["geometry"]
    raw = (directory / "CB").read_bytes()
    universe = sources.load_universe(raw, sha256(raw).hexdigest(), expected_sessions=1)
    assert len(universe.targets) == 40 and len({(t.session, t.symbol) for t in universe.targets}) == 21
    for kind in ("CC", "CX"):
        compressed, _ = wire.file_digest(directory / kind, 512 * run.MIB, lambda _: None)
        pin = sources.RawArchivePin(kind, compressed, made["decoded_archives"][kind],
            geometry[kind.lower() + "_chunks"], geometry[kind.lower() + "_rows"],
            geometry["cx_daily_rows"] if kind == "CX" else 0)
        assert len(list(sources.canonical_chunks(directory / kind, pin, lambda _: None))) == 21
    with pytest.raises(FileExistsError):
        fixtures.make_invented(directory, 1, lambda _: None)
