"""Closed source binding and invented-only completion-hook integration."""
from copy import deepcopy
from dataclasses import replace
import gzip
import json
from pathlib import Path

import pytest

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_trader_completion_unit as unit
from research import intraday_trader_unit as primary
import test_intraday_trader_unit as base_fixture


ROOT = Path(__file__).resolve().parents[2]


def fixture(name=unit.REENTRY, *, ready=False, actual=False, pair=None):
    path, _, _, filename = unit.UNITS[name]
    spec = json.loads((ROOT/path).read_bytes())
    spec['actual_runner_ready'] = spec['actual_execution_authorized_by_this_file'] = ready
    if ready:
        if name == unit.JOINT:
            spec['selected_pair'] = list(unit.INVENTED_PAIR if pair is None else pair)
            spec['invented_pair'] = list(spec['selected_pair'])
            spec['selection_evidence'] = {'mode':'audited_spent_discovery_same_source_fixed_rule','audit_sha256':'c'*64}
    raw = wire.encode(spec)
    original, scenario = base_fixture.source_bytes()
    plan = base_fixture.plan_for(original)
    root, leaf = plan.local_execution_root, name+'-actual1'
    names = tuple(sorted(set(plan.source_names) | set(unit.MINIMUM_PUBLIC_SOURCES)))
    plan = replace(plan, unit_id=name, specification_path=path, source_names=names,
        source_roster_sha256=wire.digest(wire.encode(list(names))), candidate_bounds=wire.encode(unit.bounds(name)),
        output_filename=filename, actual_runner_ready=spec['actual_runner_ready'],
        actual_cache=root/'cache'/leaf, actual_output=root/'output'/leaf, actual_diagnostics=root/'diagnostics'/leaf,
        canonical_journal=root/'canonical'/'experiments.jsonl', global_marker=root/'canonical'/'ee-unresolved.json',
        research_locks=(root/'canonical'/'intraday-research.lock',root/'output'/'ee-campaign.lock'))
    hashes = {key:'a'*64 for key in names}
    hashes.update({path:wire.digest(raw),primary.SPECIFICATION_PATH:wire.digest(original),primary.SCENARIO_PATH:wire.digest(scenario)})
    config = {'unit_id':name,'invented_only':not actual,'attempt':'actual1','sessions':257,
              'source_hashes':hashes,'local_identity':{'runtime_sha256':'b'*64}}
    return raw, original, scenario, plan, config


def hooks(values, *, actual=False, plan=None):
    raw, original, scenario, initial, config = values
    return unit.completion_hooks(initial if plan is None else plan,raw,original,scenario,
        invented_only=not actual,source_manifest_sha256=wire.digest(wire.encode(config['source_hashes'])),runtime_sha256='b'*64)


@pytest.mark.parametrize('name',unit.UNITS)
def test_hook_factory_only_reads_supplied_source_bytes(monkeypatch,name):
    values = fixture(name)
    def forbidden(*args,**kwargs):
        raise AssertionError('source-only hook opened science/evidence')
    for method in ('open','stat','lstat'):
        monkeypatch.setattr(Path,method,forbidden)
    monkeypatch.setattr(wire,'file_digest',forbidden)
    monkeypatch.setattr(primary,'original_inputs',forbidden)
    monkeypatch.setattr(primary,'_make_invented',forbidden)
    assert hooks(values).load_plan(values[-1]) is values[-2]


@pytest.mark.parametrize('name',unit.UNITS)
def test_actual_readiness_remains_a_separate_source_gate(name):
    with pytest.raises(ValueError,match='not registered ready|source-frozen audited'):
        hooks(fixture(name),actual=True)


@pytest.mark.parametrize('name',unit.UNITS)
@pytest.mark.parametrize('field',['actual_cache','actual_output','actual_diagnostics','unit_id','specification_path','resource_profile'])
def test_spent_paths_other_unit_or_resource_exception_are_rejected(name,field):
    values = fixture(name)
    old = base_fixture.plan_for(values[1])
    changed = wire.ED_H1_600.id if field == 'resource_profile' else getattr(old,field)
    with pytest.raises(ValueError,match='fixed plan|closed completion'):
        hooks(values,plan=replace(values[-2],**{field:changed}))


@pytest.mark.parametrize('name',unit.UNITS)
@pytest.mark.parametrize('attempt,sessions',[('actual2',257),('profile-v1',257),(True,257),('actual1',True),('actual1',256)])
def test_ready_source_does_not_authorize_retry_or_partial_actual(name,attempt,sessions):
    values = fixture(name,ready=True,actual=True)
    hook = hooks(values,actual=True)
    assert hook.load_plan(values[-1]) is values[-2]
    with pytest.raises(ValueError,match='fixed actual1/full257'):
        hook.load_plan({**values[-1],'attempt':attempt,'sessions':sessions})


@pytest.mark.parametrize('name',unit.UNITS)
@pytest.mark.parametrize('change',['source','runtime','mode','unit'])
def test_exact_source_runtime_mode_and_unit_are_bound(name,change):
    values = fixture(name)
    hook = hooks(values)
    config = deepcopy(values[-1])
    if change == 'source': config['source_hashes'][unit.UNITS[name][0]] = 'c'*64
    if change == 'runtime': config['local_identity']['runtime_sha256'] = 'c'*64
    if change == 'mode': config['invented_only'] = False
    if change == 'unit': config['unit_id'] = bridge.EE_UNIT_ID
    with pytest.raises(ValueError,match='hook/source/runtime'):
        hook.load_plan(config)


def test_joint_placeholder_cannot_become_an_actual_selection():
    values = fixture(unit.JOINT,ready=True,actual=True)
    raw, original, scenario, _, _ = values
    spec = json.loads(raw)
    spec['selected_pair'] = None
    with pytest.raises(ValueError,match='source-frozen audited'):
        unit.contract(unit.JOINT,wire.encode(spec),original,scenario,actual=True)
    spec['selected_pair'] = list(unit.INVENTED_PAIR)
    spec['selection_evidence']['audit_sha256'] = None
    with pytest.raises(ValueError,match='source-frozen audited'):
        unit.contract(unit.JOINT,wire.encode(spec),original,scenario,actual=True)


def test_ready_joint_must_qualify_the_same_selected_policy_pair(monkeypatch):
    pair = (unit.FAMILY_A[0], unit.FAMILY_B[1])
    values = fixture(unit.JOINT,ready=True,pair=pair)
    raw, original, scenario, _, _ = values
    spec = json.loads(raw)
    spec['invented_pair'] = list(unit.INVENTED_PAIR)
    with pytest.raises(ValueError,match='invented/actual pair differs'):
        unit.contract(unit.JOINT,wire.encode(spec),original,scenario)
    captured = []
    sentinel = object()
    def generation(*args,profile):
        captured.append(profile)
        return sentinel
    monkeypatch.setattr(primary,'_make_invented',generation)
    assert hooks(values).make_invented(Path('/invented-not-opened'),1,lambda _:None) is sentinel
    assert captured == ['mechanism1']


@pytest.mark.parametrize('name',unit.UNITS)
def test_small_original_three_hook_rebuilds_saved_output(tmp_path,name):
    values = fixture(name)
    hook = hooks(values)
    generated = tmp_path/'generated'
    generated.mkdir(mode=0o700)
    envelope = hook.make_invented(generated,1,lambda _:None)
    work = tmp_path/'worker'
    (work/'received').mkdir(parents=True)
    work.chmod(0o700)
    for item in envelope.manifest:
        (work/'received'/item.name).write_bytes((generated/item.name).read_bytes())
    receipt = hook.compute_verify(work,envelope,lambda _:None)
    proof = json.loads(receipt.independent_verification)
    assert proof['verified'] and proof['full_eof_verified'] and proof['invented_only']
    assert proof['schema'] == unit.UNITS[name][2]
    assert proof['raw_byte_audit']['verified'] and proof['raw_byte_audit']['raw_keys'] == 42
    counts = proof['record_counts']
    assert counts['targets'] == {'S1':20,'S2':20}
    assert counts.get('session',counts.get('union_coordinates')) == 21


def test_reentry_profile_contains_causal_second_attempts_and_distinct_terminals(tmp_path):
    values = fixture()
    hook = hooks(values)
    generated = tmp_path/'generated'
    generated.mkdir(mode=0o700)
    envelope = hook.make_invented(generated,4,lambda _:None)
    work = tmp_path/'worker'
    (work/'received').mkdir(parents=True)
    work.chmod(0o700)
    for item in envelope.manifest:
        (work/'received'/item.name).write_bytes((generated/item.name).read_bytes())
    hook.compute_verify(work,envelope,lambda _:None)
    seen, second_candidates = set(), set()
    with gzip.open(work/primary.OUTPUT_FILENAME,'rb') as stream:
        for raw in stream:
            row = json.loads(raw)
            if row['kind'] != 'session': continue
            for family in row['payload']['result']['families']:
                reset = family['reset']
                if reset['state'] == 'supported':
                    seen.add(reset['previous']['kind'])
                else:
                    seen.add(reset['state'])
                if family['second'] is not None and family['second']['detection']['candidate'] is not None:
                    second_candidates.add(family['family'])
    assert {'closed','cancelled','unknown','unsupported'} <= seen
    assert second_candidates == {'EE-A','EE-B'}
