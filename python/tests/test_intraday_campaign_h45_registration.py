"""Prevent drift from the authorized original hypotheses and shared limits."""
import json
from fractions import Fraction
from pathlib import Path

from research import intraday_campaign_h45_account as producer
from research import intraday_campaign_wsl_transport as wire


DIRECTORY = Path(__file__).resolve().parents[2] / 'configs/research/discovery'


def test_original_hypotheses_and_separate_single_accounts_are_preserved():
    spec = json.loads((DIRECTORY / 'intraday-campaign-h45-accounts-v1.json').read_bytes())
    original = json.loads((DIRECTORY / 'intraday-autonomous-round1-v1.json').read_bytes())
    assert set(spec['units']) == set(producer.UNITS) == {'P11', 'P12'}
    for policy, number in (('P11', 4), ('P12', 5)):
        definition = original['hypotheses'][number - 1]
        current, unit = spec['detectors'][policy], spec['units'][policy]
        assert current['hypothesis_id'] == definition['id'] == unit['hypothesis_id']
        assert current['initial_minutes'] == definition['initial_minutes']
        assert unit['account_mode'] == producer.UNITS[policy]['mode']
        assert unit['arms'] == [producer.UNITS[policy]['arm']]
        assert current['structural_stop'] is None
        assert spec['statistics']['own_label_planning_count_by_unit'][policy] == 24
        if number == 4:
            for key in ('lookback_bars', 'minimum_trigger_end_minutes'):
                assert current[key] == definition[key]
            assert Fraction(current['compression_ratio']) == Fraction(definition['compression_ratio'])
        else:
            assert current['entry_minutes_before_close'] == definition['entry_minutes_before_close']
            assert current['exit_minutes_before_close'] == definition['exit_minutes_before_close']
            assert current['decision_minutes_before_close'] == (
                definition['entry_minutes_before_close'] + definition['decision_minutes_before_entry'])
    assert spec['account']['no_intrabar_barriers'] is True
    assert spec['statistics']['new_threshold_search'] is False
    assert spec['statistics']['fits'] == 0
    assert spec['statistics']['promotion_allowed'] is False


def test_original_pins_geometry_and_no_outcome_admission_remain():
    spec = json.loads((DIRECTORY / 'intraday-campaign-h45-accounts-v1.json').read_bytes())
    previous = json.loads((DIRECTORY / 'intraday-campaign-context-policy-comparison-v1.json').read_bytes())
    for name in ('EA-A', 'EA-B', 'CC'):
        assert spec['inputs'][name] == previous['inputs'][name]
        if name != 'CC':
            assert spec['candidate_bounds'][name] == previous['candidate_bounds'][name]
    assert spec['geometry'] == previous['geometry']
    assert spec['inputs']['fourth_derived_input'] is False
    assert spec['inputs']['new_source_acquisition'] is False
    assert spec['inputs']['outcome_or_completeness_admits_candidates'] is False
    assert spec['population']['outcome_or_completeness_admits_candidates'] is False
    assert spec['candidate_bounds']['original_hypothesis'] == {
        timing: spec['geometry']['targets_per_policy'] for timing in ('D+1', 'D+2')}


def test_entire_units_keep_the_ordinary_shared_resource_contract():
    spec = json.loads((DIRECTORY / 'intraday-campaign-h45-accounts-v1.json').read_bytes())
    execution = spec['execution']
    assert execution['resource_profile'] == wire.ORDINARY300.id
    assert execution['shared_cpu_seconds'] == wire.ORDINARY300.cpu_seconds
    assert execution['shared_address_space_bytes'] == wire.ORDINARY300.address_space_bytes
    assert execution['role_cpu_seconds'] == {
        role: limits['cpu_seconds'] for role, limits in wire.ORDINARY300.role_limits().items()}
    assert execution['role_address_space_bytes'] == {
        role: limits['address_space_bytes'] for role, limits in wire.ORDINARY300.role_limits().items()}
    assert execution['helper_reservations_within_roles'] == wire.ORDINARY300.helper_limits()
    assert execution['concurrent_units'] == 1
    assert execution['automatic_actual_retries'] == 0
    assert execution['operator_core_CPU600_scope_inherited'] is False
