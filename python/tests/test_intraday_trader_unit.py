"""Source-only factory and small invented EE hooks; no market data or network."""
from copy import deepcopy
from dataclasses import replace
import gzip
import json
from pathlib import Path
import shutil

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_trader_unit as unit


ROOT = Path(__file__).resolve().parents[2]


def source_bytes():
    return (ROOT / unit.SPECIFICATION_PATH).read_bytes(), (ROOT / unit.SCENARIO_PATH).read_bytes()


def plan_for(raw):
    spec = json.loads(raw)
    source = spec["actual_source_preparation"]
    names = tuple(sorted(set(unit.MINIMUM_PUBLIC_SOURCES) | {
        "python/research/__init__.py","python/research/experiment_log.py",
        "python/research/research_budget.py","var/invented-ee-entry.py"}))
    private, local = Path("/invented-private"), Path("/invented-worktree")
    return bridge.UnitPlan(unit_id=bridge.EE_UNIT_ID,specification_path=unit.SPECIFICATION_PATH,
        source_names=names,source_roster_sha256=wire.digest(wire.encode(list(names))),input_pins=unit._pins(spec),
        decoded_stream_sha256=unit.decoded_stream_pin(source["decoded_archives"]["CC"],source["decoded_archives"]["CX"]),
        window=wire.encode({"symbol":"KRX:*","interval":"1m","start":"2025-09-04","end":"2026-09-23"}),
        full_geometry=wire.encode(unit.FULL_GEOMETRY),candidate_bounds=wire.encode(unit.CANDIDATE_BOUNDS),
        private_entrypoint="var/invented-ee-entry.py",local_worktree=local,remote_worktree=local,
        actual_cache=private/"cache"/"actual",actual_output=private/"output"/"actual",
        actual_diagnostics=private/"diagnostics"/"actual",canonical_journal=private/"canonical"/"runs.jsonl",
        global_marker=private/"canonical"/"unresolved.json",
        research_locks=(private/"canonical"/"research.lock",private/"output"/"campaign.lock"),
        invented_cache_parent=private/"cache",invented_output_parent=private/"output",
        invented_diagnostics_parent=private/"diagnostics",output_filename=unit.OUTPUT_FILENAME,
        output_max_bytes=512*wire.MIB,ssh_argv=(),remote_prefix=(),
        input_contract=wire.EE_INPUT_CONTRACT,resource_profile=wire.ORDINARY300.id,
        actual_runner_ready=spec["actual_runner_ready"],local_coordinator=True,local_execution_root=private)


def hook_fixture(raw=None, scenario=None, plan_change=None):
    original_raw,original_scenario = source_bytes()
    raw = original_raw if raw is None else raw
    scenario = original_scenario if scenario is None else scenario
    plan = plan_for(raw)
    if plan_change:
        plan = replace(plan,**plan_change)
    hashes = {name:"a"*64 for name in plan.source_names}
    hashes[unit.SPECIFICATION_PATH],hashes[unit.SCENARIO_PATH] = wire.digest(raw),wire.digest(scenario)
    config = {"invented_only":True,"source_hashes":hashes,"local_identity":{"runtime_sha256":"b"*64}}
    hooks = unit.primary_hooks(plan,raw,scenario,invented_only=True,
        source_manifest_sha256=wire.digest(wire.encode(hashes)),runtime_sha256="b"*64)
    return raw,scenario,plan,config,hooks


def test_factory_and_load_are_source_only_and_keep_ordinary_profile(monkeypatch):
    raw,scenario,plan,config,_ = hook_fixture()
    def inaccessible(*args,**kwargs):
        raise AssertionError("source-only factory attempted evidence access")
    with monkeypatch.context() as patch:
        for name in ("stat","lstat","open"):
            patch.setattr(Path,name,inaccessible)
        patch.setattr(wire,"file_digest",inaccessible)
        patch.setattr(bridge,"bounded_read",inaccessible)
        hooks = unit.primary_hooks(plan,raw,scenario,invented_only=True,
            source_manifest_sha256=wire.digest(wire.encode(config["source_hashes"])),runtime_sha256="b"*64)
        assert hooks.load_plan(config) is plan
        assert plan.resource_profile == wire.ORDINARY300.id
        assert plan.actual_runner_ready is True


def test_actual_hook_is_refused_before_any_declared_source_access(monkeypatch):
    raw,scenario = source_bytes()
    closed = json.loads(raw)
    closed["actual_runner_ready"] = closed["actual_execution_authorized_by_this_file"] = False
    raw,scenario,plan,config,_ = hook_fixture(raw=wire.encode(closed),scenario=scenario)
    def inaccessible(*args,**kwargs):
        raise AssertionError("actual registration refusal must be source-only")
    monkeypatch.setattr(Path,"stat",inaccessible)
    with pytest.raises(ValueError,match="not registered ready"):
        unit.primary_hooks(plan,raw,scenario,invented_only=False,
            source_manifest_sha256=wire.digest(wire.encode(config["source_hashes"])),runtime_sha256="b"*64)


@pytest.mark.parametrize("change",["mode","registration","scenario","extra_source","runtime"])
def test_execution_config_cannot_switch_science_source_or_runtime(change):
    _,_,_,config,hooks = hook_fixture()
    modified = deepcopy(config)
    if change == "mode":
        modified["invented_only"] = False
    elif change == "runtime":
        modified["local_identity"]["runtime_sha256"] = "c"*64
    else:
        name = unit.SPECIFICATION_PATH if change == "registration" else unit.SCENARIO_PATH if change == "scenario" else "extra.py"
        modified["source_hashes"][name] = "c"*64
    with pytest.raises(ValueError,match="hook/source/runtime"):
        hooks.load_plan(modified)


@pytest.mark.parametrize("field,value",[
    ("unit_id","ED-H1-SNAPSHOT"),("specification_path",unit.SCENARIO_PATH),
    ("input_contract",wire.EC_INPUT_CONTRACT),("resource_profile",wire.CORE600.id),
    ("local_coordinator",False),("local_execution_root",None),("ssh_argv",("ssh",)),
    ("remote_prefix",("python3",)),("actual_runner_ready",False),
    ("output_filename","other.gz"),("output_max_bytes",1),
    ("full_geometry",wire.encode({"sessions":257})),("candidate_bounds",wire.encode({"any":5140}))])
def test_factory_rejects_plan_confusion(field,value):
    with pytest.raises(ValueError,match="fixed primary plan"):
        hook_fixture(plan_change={field:value})


@pytest.mark.parametrize("path,value",[
    (("campaign",),"ED"),(("actual_runner_ready",),0),
    (("scenario_specification_sha256",),"a"*64),
    (("lifecycle_preparation","resource_profile"),wire.CORE600.id),
    (("lifecycle_preparation","local_coordinator"),False),
    (("actual_source_preparation","geometry","cc_rows"),1002210),
    (("actual_source_preparation","window"),["2025-09-04","2026-11-01"]),
    (("actual_source_preparation","local_raw_cache","path"),"/mnt/c/raw-byte-chunks.sqlite"),
    (("actual_source_preparation","local_raw_cache","sha256"),"invalid"),
    (("stored_output_preparation","maximum_line_bytes"),1),
    (("live_or_paper_order_capable",),True)])
def test_invalid_registration_is_not_missing_data(path,value):
    raw,scenario = source_bytes()
    spec = json.loads(raw)
    parent = spec
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = value
    with pytest.raises(ValueError):
        unit.contract(wire.encode(spec),scenario)


def test_duplicate_registration_key_and_different_scenario_bytes_refused():
    raw,scenario = source_bytes()
    with pytest.raises(ValueError,match="duplicate"):
        unit.contract(raw.replace(b'"id":',b'"campaign":"EE","id":',1),scenario)
    with pytest.raises(ValueError,match="scenario bytes"):
        unit.contract(raw,scenario+b" ")


def test_ready_actual_factory_is_source_only_and_cannot_generate_replacement_inputs(monkeypatch):
    raw,scenario,plan,config,_ = hook_fixture()
    config["invented_only"] = False
    def inaccessible(*args,**kwargs):
        raise AssertionError("actual construction opened an evidence path")
    monkeypatch.setattr(Path,"stat",inaccessible)
    monkeypatch.setattr(Path,"open",inaccessible)
    hooks = unit.primary_hooks(plan,raw,scenario,invented_only=False,
        source_manifest_sha256=wire.digest(wire.encode(config["source_hashes"])),runtime_sha256="b"*64)
    assert hooks.load_plan(config) is plan
    with pytest.raises(ValueError,match="unavailable to actual"):
        hooks.make_invented(Path("/not-opened"),257,lambda _:None)


@pytest.mark.parametrize("sessions",[1,2,257])
def test_geometry_matches_original_exact_totals_at_full_scale(sessions):
    geometry = unit.invented_geometry(sessions)
    assert geometry["targets_per_scenario"] == 20*sessions
    assert geometry["union_coordinates"] == 20*sessions+min(sessions,18)
    if sessions == 257:
        assert geometry == unit.FULL_GEOMETRY
    assert max(geometry.values()) <= max(unit.FULL_GEOMETRY.values())


@pytest.mark.parametrize("sessions",[0,True,258,1.0,None])
def test_invalid_invented_geometry_refused(sessions):
    with pytest.raises(ValueError):
        unit.invented_geometry(sessions)


def generated_fixture(tmp_path,sessions):
    raw,scenario,plan,config,hooks = hook_fixture()
    generated,work = tmp_path/"generated",tmp_path/"worker"
    generated.mkdir(mode=0o700)
    work.mkdir(mode=0o700)
    (work/"received").mkdir(mode=0o700)
    envelope = hooks.make_invented(generated,sessions,lambda _:None)
    for item in envelope.manifest:
        shutil.copyfile(generated/item.name,work/"received"/item.name)
        (work/"received"/item.name).chmod(0o400)
    return plan,hooks,envelope,work


@pytest.mark.parametrize("sessions",[1,2])
def test_invented_original_three_whole_compute_and_saved_verification(tmp_path,sessions):
    from research import intraday_trader_report as report
    plan,hooks,envelope,work = generated_fixture(tmp_path,sessions)
    assert [item.name for item in envelope.manifest] == ["CB","CC","CX"]
    receipt = hooks.compute_verify(work,envelope,lambda _:None)
    bridge.validate_result(receipt.payload(),envelope,plan)
    proof = json.loads(receipt.independent_verification)
    assert proof["verified"] and proof["full_eof_verified"] and proof["all_original_coordinates_verified"]
    assert proof["invented_only"] and not proof["qualifies_actual_by_itself"]
    assert proof["raw_byte_audit"]["raw_keys"] == 2*(20*sessions+min(sessions,18))
    assert proof["full_geometry"] is False
    assert (work/unit.OUTPUT_FILENAME).stat().st_mode & 0o777 == 0o400
    assert wire.file_digest(work/unit.OUTPUT_FILENAME,plan.output_max_bytes,lambda _:None) == (
        receipt.output_sha256,receipt.output_bytes)
    assert len(wire.encode({"kind":"RESULT","result":receipt.payload(),"file":{
        "name":plan.output_filename,"original_path":str(work/plan.output_filename),"sha256":receipt.output_sha256,
        "size":receipt.output_bytes,"maximum":plan.output_max_bytes}})) <= wire.HEADER_MAX
    with (work/unit.OUTPUT_FILENAME).open("rb") as stream:
        records = list(report.read_report(stream,expected_sha256=receipt.output_sha256,
            expected_decoded_sha256=proof["decoded_sha256"],checkpoint=lambda _:None))
    assert records[0]["kind"] == "header" and records[-1]["kind"] == "footer"
    assert any(count>0 for clock in json.loads(receipt.candidate_counts).values() for count in clock.values())


def test_invented_generator_exclusive_and_science_cases_are_not_flat_stubs(tmp_path):
    _,hooks,envelope,work = generated_fixture(tmp_path,1)
    for item in envelope.manifest:
        assert wire.file_digest(Path(item.original_path),item.maximum,lambda _:None) == (item.sha256,item.size)
    chunks = [json.loads(line) for line in gzip.decompress((tmp_path/"generated"/"CC").read_bytes()).splitlines()]
    assert sum(len(chunk["rows"]) for chunk in chunks) == unit.invented_geometry(1)["cc_rows"]
    assert len(chunks[0]["rows"]) == 300
    assert any(row["open"] == "invalid-invented-number" for chunk in chunks for row in chunk["rows"])
    assert any(len({b["open_time_ms"]-a["open_time_ms"] for a,b in zip(chunk["rows"],chunk["rows"][1:])})>1
               for chunk in chunks)
    with pytest.raises(FileExistsError):
        hooks.make_invented(tmp_path/"generated",1,lambda _:None)


@pytest.mark.parametrize("change",["payload","geometry","decoded","contract"])
def test_input_corruption_fails_before_output_or_false_zero(tmp_path,change):
    _,hooks,envelope,work = generated_fixture(tmp_path,1)
    if change == "payload":
        received = work/"received"/"CB"
        received.chmod(0o600)
        received.write_bytes(b"{}\n")
    elif change == "geometry":
        geometry = json.loads(envelope.geometry)
        geometry["cc_rows"] -= 1
        envelope = replace(envelope,geometry=wire.encode(geometry))
    elif change == "decoded":
        envelope = replace(envelope,stream_sha256="f"*64)
    else:
        envelope = replace(envelope,input_contract=wire.EC_INPUT_CONTRACT)
    with pytest.raises(ValueError):
        hooks.compute_verify(work,envelope,lambda _:None)
    assert not (work/unit.OUTPUT_FILENAME).exists()
