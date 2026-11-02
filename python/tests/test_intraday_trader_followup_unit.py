"""Closed metadata binding and invented-only mechanism-one hook regressions."""
from copy import deepcopy
from dataclasses import replace
import json
import gzip
from pathlib import Path

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_trader_followup_unit as unit
from research import intraday_trader_unit as primary
import test_intraday_trader_unit as base_fixture


ROOT = Path(__file__).resolve().parents[2]


def fixture(spec_change=None, *, actual=False):
    raw = (ROOT / unit.SPECIFICATION_PATH).read_bytes()
    original, scenario = base_fixture.source_bytes()
    spec = json.loads(raw)
    if spec_change is not None:
        spec_change(spec)
        raw = wire.encode(spec)
    plan = base_fixture.plan_for(original)
    root = plan.local_execution_root
    leaf = unit.UNIT_ID + "-actual1"
    names = tuple(sorted(set(plan.source_names) | set(unit.MINIMUM_PUBLIC_SOURCES)))
    plan = replace(plan, unit_id=unit.UNIT_ID, specification_path=unit.SPECIFICATION_PATH,
        source_names=names, source_roster_sha256=wire.digest(wire.encode(list(names))),
        candidate_bounds=wire.encode(unit.CANDIDATE_BOUNDS), actual_runner_ready=spec["actual_runner_ready"],
        actual_cache=root/"cache"/leaf, actual_output=root/"output"/leaf,
        actual_diagnostics=root/"diagnostics"/leaf, canonical_journal=root/"canonical"/"experiments.jsonl",
        global_marker=root/"canonical"/"ee-unresolved.json",
        research_locks=(root/"canonical"/"intraday-research.lock",root/"output"/"ee-campaign.lock"))
    hashes = {name:"a"*64 for name in names}
    hashes.update({unit.SPECIFICATION_PATH:wire.digest(raw),primary.SPECIFICATION_PATH:wire.digest(original),
                   primary.SCENARIO_PATH:wire.digest(scenario)})
    config = {"unit_id":unit.UNIT_ID,"invented_only":not actual,"attempt":"actual1","sessions":257,
        "source_hashes":hashes,"local_identity":{"runtime_sha256":"b"*64}}
    return raw, original, scenario, plan, config


def hooks(values, *, actual=False, plan=None):
    raw, original, scenario, default_plan, config = values
    return unit.mechanism_hooks(default_plan if plan is None else plan,raw,original,scenario,
        invented_only=not actual,source_manifest_sha256=wire.digest(wire.encode(config["source_hashes"])),
        runtime_sha256="b"*64)


def test_factory_is_source_only_and_science_is_deferred(monkeypatch):
    values = fixture()
    def inaccessible(*args, **kwargs):
        raise AssertionError("metadata hook touched evidence or science")
    for name in ("stat","lstat","open"):
        monkeypatch.setattr(Path,name,inaccessible)
    monkeypatch.setattr(wire,"file_digest",inaccessible)
    monkeypatch.setattr(primary,"_compute",inaccessible)
    monkeypatch.setattr(primary,"_make_invented",inaccessible)
    assert hooks(values).load_plan(values[-1]) is values[-2]


def test_unready_registration_does_not_open_actual_before_readiness():
    def closed(spec):
        spec["actual_runner_ready"] = spec["actual_execution_authorized_by_this_file"] = False
    values = fixture(closed)
    with pytest.raises(ValueError,match="not registered ready"):
        hooks(values,actual=True)


@pytest.mark.parametrize("field",["actual_cache","actual_output","actual_diagnostics","canonical_journal",
    "global_marker","research_locks","unit_id","specification_path","resource_profile"])
def test_new_unit_cannot_reuse_spent_primary_paths_or_another_profile(field):
    values = fixture()
    plan = values[-2]
    old = base_fixture.plan_for(values[1])
    value = old.research_locks if field == "research_locks" else getattr(old,field)
    if field == "resource_profile":
        value = wire.ED_H1_600.id
    with pytest.raises(ValueError,match="fixed plan"):
        hooks(values,plan=replace(plan,**{field:value}))


@pytest.mark.parametrize("name",[unit.SPECIFICATION_PATH,primary.SPECIFICATION_PATH,primary.SCENARIO_PATH,
    "extra.py","runtime","unit","mode"])
def test_config_cannot_change_source_science_unit_or_runtime(name):
    values = fixture()
    hook = hooks(values)
    config = deepcopy(values[-1])
    if name == "runtime": config["local_identity"]["runtime_sha256"] = "c"*64
    elif name == "unit": config["unit_id"] = bridge.EE_UNIT_ID
    elif name == "mode": config["invented_only"] = False
    else: config["source_hashes"][name] = "c"*64
    with pytest.raises(ValueError,match="hook/source/runtime"):
        hook.load_plan(config)


@pytest.mark.parametrize("attempt,sessions",[("actual2",257),("profile-v1",257),(True,257),
    (None,257),("actual1",True),("actual1",256),("actual1",258)])
def test_even_ready_metadata_cannot_authorize_an_actual_retry_or_changed_geometry(monkeypatch,attempt,sessions):
    def ready(spec):
        spec["actual_runner_ready"] = spec["actual_execution_authorized_by_this_file"] = True
    values = fixture(ready,actual=True)
    hook = hooks(values,actual=True)
    assert hook.load_plan(values[-1]) is values[-2]
    config = {**values[-1],"attempt":attempt,"sessions":sessions}
    monkeypatch.setattr(Path,"stat",lambda *a,**k: (_ for _ in ()).throw(AssertionError("evidence accessed")))
    with pytest.raises(ValueError,match="fixed actual1 and full257"):
        hook.load_plan(config)


@pytest.mark.parametrize("path,value",[
    (("policies",0,"risk_multiple"),True),(("policies",0,"risk_multiple"),3),
    (("peer_contract","window_minutes"),30),(("peer_contract","minimum_complete_peer_windows"),True),
    (("peer_contract","minimum_complete_peer_windows"),4),(("peer_contract","maximum_other_members"),19),
    (("peer_contract","comparison"),"greater_or_equal"),
    (("peer_contract","mean_threshold"),{"numerator":"0.0","denominator":"1"}),
    (("source_preparation_reference","sha256"),"c"*64),
    (("source_preparation_reference","path"),"other.json"),
    (("scenario_reference","sha256"),"c"*64)])
def test_wrong_registration_constants_or_source_references_refuse(path,value):
    def change(spec):
        selected = spec
        for key in path[:-1]: selected = selected[key]
        selected[path[-1]] = value
    values = fixture(change)
    with pytest.raises(ValueError):
        hooks(values)


def test_small_invented_hook_uses_closed_profile_and_independent_saved_verification(tmp_path):
    values = fixture()
    hook = hooks(values)
    generated = tmp_path/"generated"
    generated.mkdir(mode=0o700)
    envelope = hook.make_invented(generated,1,lambda _:None)
    work = tmp_path/"worker"
    (work/"received").mkdir(parents=True)
    work.chmod(0o700)
    for item in envelope.manifest:
        (work/"received"/item.name).write_bytes((generated/item.name).read_bytes())
    receipt = hook.compute_verify(work,envelope,lambda _:None)
    proof = json.loads(receipt.independent_verification)
    assert proof["verified"] and proof["full_eof_verified"] and proof["invented_only"]
    assert proof["record_counts"] == {"session":21,"targets":{"S1":20,"S2":20}}
    assert tuple(json.loads(receipt.candidate_counts)["S1"]) == tuple(sorted(unit.POLICIES))
    assert proof["schema"] == json.loads(values[0])["stored_output_preparation"]["schema"]


def test_integrated_invented_profile_retains_peer_sign_zero_and_subquorum(tmp_path):
    values = fixture()
    hook = hooks(values)
    generated = tmp_path/"generated"
    generated.mkdir(mode=0o700)
    envelope = hook.make_invented(generated,4,lambda _:None)
    work = tmp_path/"worker"
    (work/"received").mkdir(parents=True)
    work.chmod(0o700)
    for item in envelope.manifest:
        (work/"received"/item.name).write_bytes((generated/item.name).read_bytes())
    receipt = hook.compute_verify(work,envelope,lambda _:None)
    assert json.loads(receipt.independent_verification)["verified"]
    found = set()
    with gzip.open(work/unit.OUTPUT_FILENAME,"rb") as stream:
        for raw in stream:
            row = json.loads(raw)
            if row["kind"] != "session": continue
            context = row["payload"]["peer_context"]
            if context is None: continue
            if context["state"] == "unknown": found.add("unknown")
            else:
                numerator = context["mean"]["fraction"][0]
                found.add("positive" if numerator > 0 else "negative" if numerator < 0 else "zero")
    assert found == {"positive","negative","zero","unknown"}
