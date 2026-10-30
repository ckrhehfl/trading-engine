"""Fixed source registration only; no input/evidence path is inspected."""
import json
from pathlib import Path

from research import intraday_campaign_policy_comparison as comparison
from research import intraday_campaign_wsl_transport as wire


PATH = Path(__file__).resolve().parents[2] / 'configs/research/discovery/intraday-campaign-context-policy-comparison-v1.json'


def test_fixed_units_match_source_and_explicit_scenario_geometry():
    registration = json.loads(PATH.read_bytes())
    assert set(registration['units']) == set(comparison.UNIT_DEFINITIONS)
    for policy, definition in comparison.UNIT_DEFINITIONS.items():
        unit = registration['units'][policy]
        assert unit['account_mode'] == definition['mode']
        assert tuple(unit['setups']) == tuple(definition['setups'])
        assert tuple(unit['arms']) == tuple(definition['arms'])
        assert unit['unit_id'] == f'intraday-ec-context-{policy.lower()}-20261031-v1'
        assert len(unit['arms']) * 2 * 2 * 2 == (32 if policy == 'P06' else 16)
    assert registration['conditions']['P06']['interaction_cells'] == ['neither', 'volume', 'market', 'both']
    assert registration['conditions']['P06']['common_support_required_for_all_four_arms'] is True


def test_ordinary_shared_budget_does_not_inherit_core_exception():
    registration = json.loads(PATH.read_bytes())
    execution = registration['execution']
    assert execution['resource_profile'] == wire.ORDINARY300.id
    assert execution['shared_cpu_seconds'] == wire.ORDINARY300.cpu_seconds
    assert execution['shared_address_space_bytes'] == wire.ORDINARY300.address_space_bytes
    assert execution['role_cpu_seconds'] == {role: cap['cpu_seconds'] for role, cap in wire.ORDINARY300.role_limits().items()}
    assert execution['role_address_space_bytes'] == {role: cap['address_space_bytes'] for role, cap in wire.ORDINARY300.role_limits().items()}
    assert execution['helper_reservations_within_roles'] == wire.ORDINARY300.helper_limits()
    assert execution['automatic_actual_retries'] == 0
    assert execution['concurrent_units'] == 1
    assert execution['operator_core_CPU600_scope_inherited'] is False


def test_original_inputs_and_scientific_boundaries_are_explicit():
    registration = json.loads(PATH.read_bytes())
    prior = json.loads((PATH.parent / 'intraday-campaign-context-input-readiness-v1.json').read_bytes())
    for name in ('EA-A', 'EA-B', 'CC'):
        assert registration['inputs'][name] == prior['inputs'][name]
    assert registration['geometry'] == prior['geometry']
    assert registration['candidate_bounds'] == prior['candidate_bounds']
    assert registration['inputs']['fourth_derived_input'] is False
    assert registration['inputs']['outcome_or_completeness_admits_candidates'] is False
    assert registration['features']['feature_imputation'] is False
    assert registration['features']['entry_OPEN_as_feature'] is False
    assert registration['conditions']['P08']['unknown_is_not_cash'] is True
    assert registration['statistics']['strategy_pass'] is False
    assert registration['statistics']['promotion_allowed'] is False
    assert type(registration['actual_runner_ready']) is bool
