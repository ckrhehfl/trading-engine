"""Invented raw minutes through both feature and policy reconstruction."""
from fractions import Fraction

import pytest

from research import intraday_campaign_feature_verifier as feature_oracle
from research import intraday_campaign_features as features
from research import intraday_campaign_policy_selection as producer
from research import intraday_campaign_policy_selection_verifier as policy_oracle
from test_intraday_campaign_features import fixture


@pytest.mark.parametrize('policy', ['P04', 'P05', 'P06', 'P07', 'P08'])
@pytest.mark.parametrize('setup', ['EA-A', 'EA-B'])
@pytest.mark.parametrize('peers', [4, 5])
@pytest.mark.parametrize('volume', [None, Fraction(0), Fraction(2)])
def test_raw_feature_and_policy_oracles_agree_without_outcomes(policy, setup, peers, volume):
    original = fixture(peers)
    original['setup_id'] = setup
    prepared = features.prepare_features(**original)
    assert feature_oracle.verify_features(prepared, **original)['verified'] is True
    selection = producer.select_policy(policy, prepared, volume_value=volume)
    receipt = policy_oracle.verify_selection(policy, prepared, volume_value=volume, selection=selection)
    assert receipt['verified'] is True
    assert receipt['raw_feature_reconstruction_verified'] is False
    assert receipt['volume_availability_reauthenticated'] is False
    changed = dict(selection, state='rejected' if selection['state'] != 'rejected' else 'selected')
    with pytest.raises(policy_oracle.SelectionVerificationRefusal):
        policy_oracle.verify_selection(policy, prepared, volume_value=volume, selection=changed)
