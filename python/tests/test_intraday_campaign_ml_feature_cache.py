"""One-day cache parity on invented component inputs; no archive or model fit."""
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from collections.abc import Mapping

import pytest

from research import intraday_campaign_features as features
from test_intraday_campaign_features import fixture, lose, SYMBOLS


ea, cc = features.ea, features.cc


def cache_for(data):
    return features.FeatureDayCache(opening=data['target'].opening,closing=data['target'].closing,
        sessions=data['sessions'],sources=data['sources'])


def cached(data,cache):
    return features.prepare_features(**{**data,'sessions':cache.sessions,'sources':cache.sources},day_cache=cache)


def canonical(value):
    if is_dataclass(value):
        return type(value).__name__,tuple((field.name,canonical(getattr(value,field.name))) for field in fields(value))
    if isinstance(value,Mapping):
        return tuple((key,canonical(item)) for key,item in value.items())
    if isinstance(value,(list,tuple)):
        return tuple(canonical(item) for item in value)
    return value


@pytest.mark.parametrize('setup',['EA-A','EA-B'])
@pytest.mark.parametrize('timing',['D+1','D+2'])
@pytest.mark.parametrize('minute',[75,80])
def test_exact_output_parity_across_targets_setups_timing_and_observation_end(setup,timing,minute):
    data=fixture()
    cache=cache_for(data)
    for symbol in SYMBOLS[:3]:
        target=replace(data['target'],symbol=symbol,policy=timing,
            price_source=features.eb._price_source(data['sources'][symbol]))
        variant={**data,'target':target,'setup_id':setup,
            'observed_end':target.opening+minute*cc.MINUTE,
            'decision_at':target.opening+(minute+2)*cc.MINUTE}
        expected=features.prepare_features(**variant)
        result=cached(variant,cache)
        assert result==expected
        assert canonical(result)==canonical(expected)
        assert type(result.windows[0]) is features.WindowObservation
        assert all(window.provenance.available_at<=result.decision_at for window in result.windows)


@pytest.mark.parametrize('issue',['missing','conflict','invalid_price','session_missing','invalid_ohlc','zero_range','quorum'])
def test_unknowns_zero_values_and_all_exact_provenance_match(issue):
    data=fixture()
    if issue in ('missing','conflict','invalid_price'):
        lose(data,SYMBOLS[1],10,issue)
        lose(data,SYMBOLS[2],50,issue)
    elif issue=='session_missing': data['sessions'][SYMBOLS[1]]=None
    elif issue=='invalid_ohlc':
        data['sessions'][SYMBOLS[0]].minutes[data['target'].opening+50*cc.MINUTE]=(Decimal('NaN'),)*4
    elif issue=='zero_range':
        for symbol,session in data['sessions'].items():
            data['sessions'][symbol]=replace(session,minutes={at:(Decimal('100'),)*4 for at in session.minutes})
    else: data=fixture(4)
    expected=features.prepare_features(**data)
    cache=cache_for(data)
    assert cached(data,cache)==expected
    assert canonical(cached(data,cache))==canonical(expected)


def test_owned_snapshots_are_deeply_immutable_and_ignore_later_original_mutation():
    data=fixture()
    lose(data,SYMBOLS[1],10,'conflict')
    expected=features.prepare_features(**data)
    cache=cache_for(data)
    at=data['target'].opening+50*cc.MINUTE
    data['sessions'][SYMBOLS[0]].minutes[at]=(Decimal('999'),)*4
    data['sessions'][SYMBOLS[1]].gaps[0]['state']='invalid_price'
    data['sources'][SYMBOLS[0]]['raw_chunk_metadata']['retrieved_at']='changed'
    data['sources'][SYMBOLS[1]]['data_vintage']='1'*64
    assert cached(data,cache)==expected
    with pytest.raises(TypeError): cache.sessions[SYMBOLS[0]].minutes[at]=None
    with pytest.raises(TypeError): cache.sessions[SYMBOLS[1]].gaps[0]['state']='changed'
    with pytest.raises(TypeError): cache.sources[SYMBOLS[0]]['raw_chunk_metadata']['source']='changed'
    with pytest.raises(FrozenInstanceError): cache.opening+=cc.MINUTE
    with pytest.raises(FrozenInstanceError): cache.sessions[SYMBOLS[0]].prefix[0].close=Decimal('999')


def test_validated_sources_sessions_and_exact_windows_are_reused_without_changing_values(monkeypatch):
    data=fixture()
    expected=features.prepare_features(**data)
    calls={'source':0,'session':0,'window':0}
    for name in calls:
        original=getattr(features,'_'+name)
        def counted(*args,__name=name,__original=original,**kwargs):
            calls[__name]+=1
            return __original(*args,**kwargs)
        monkeypatch.setattr(features,'_'+name,counted)
    cache=cache_for(data)
    assert calls=={'source':8,'session':8,'window':0}
    first=cached(data,cache)
    initial=dict(calls)
    second=cached({**data,'setup_id':'EA-B','target':replace(data['target'],policy='D+2')},cache)
    assert calls==initial and initial['window']==25
    assert first==expected
    assert second.features['setup_is_A'].value==0
    assert first.windows[0] is second.windows[0]
    assert first.source_evidence is second.source_evidence


@pytest.mark.parametrize('field',['sessions','sources'])
def test_equal_but_foreign_input_views_are_refused(field):
    data=fixture()
    cache=cache_for(data)
    arguments={**data,'sessions':cache.sessions,'sources':cache.sources}
    arguments[field]=dict(arguments[field])
    with pytest.raises(features.FeaturesRefusal,match='cache_owned_input_identity_required'):
        features.prepare_features(**arguments,day_cache=cache)


def test_foreign_date_or_target_vintage_is_not_admitted_by_cache():
    data=fixture()
    cache=cache_for(data)
    target=replace(data['target'],session_date=data['target'].session_date+timedelta(days=1),
        opening=data['target'].opening+timedelta(days=1),closing=data['target'].closing+timedelta(days=1))
    with pytest.raises(features.FeaturesRefusal,match='cache_day_or_price_basis_changed'):
        cached({**data,'target':target,'observed_end':data['observed_end']+timedelta(days=1),
            'decision_at':data['decision_at']+timedelta(days=1)},cache)
    changed=replace(data['target'],price_source=replace(data['target'].price_source,data_vintage='1'*64))
    with pytest.raises(features.FeaturesRefusal,match='target_source_binding_changed'):
        cached({**data,'target':changed},cache)


@pytest.mark.parametrize('change,reason',[
    (lambda d:d['sources'][SYMBOLS[1]].update(session='2025-09-05'),'source_identity_or_schema_changed'),
    (lambda d:d['sources'][SYMBOLS[1]].update(data_vintage='bad'),'invalid_source_digest'),
    (lambda d:d['sessions'].update({SYMBOLS[1]:replace(d['sessions'][SYMBOLS[1]],opening=d['target'].opening+cc.MINUTE)}),'session_opening_changed'),
])
def test_constructor_initially_validates_every_bound_source_and_session(change,reason):
    data=fixture()
    change(data)
    with pytest.raises(features.FeaturesRefusal,match=reason): cache_for(data)


def test_warmed_later_window_cannot_cross_an_earlier_decision_clock():
    data=fixture()
    cache=cache_for(data)
    opening=data['target'].opening
    later={**data,'observed_end':opening+80*cc.MINUTE,'decision_at':opening+82*cc.MINUTE}
    assert cached(later,cache)==features.prepare_features(**later)
    earlier={**data,'observed_end':opening+75*cc.MINUTE,'decision_at':opening+77*cc.MINUTE}
    result=cached(earlier,cache)
    assert result==features.prepare_features(**earlier)
    assert max(window.end_at for window in result.windows)==earlier['observed_end']
    with pytest.raises(features.FeaturesRefusal,match='future_or_late_available_window'):
        cache._observation(SYMBOLS[1],'open_to_signal',opening,opening+80*cc.MINUTE,
            data['target'],opening+77*cc.MINUTE)
    with pytest.raises(features.FeaturesRefusal,match='future_or_late_available_window'):
        cache._observation(SYMBOLS[1],'first30',opening,opening+30*cc.MINUTE,
            data['target'],opening+27*cc.MINUTE)
    with pytest.raises(features.FeaturesRefusal,match='future_first30_window'):
        cached({**data,'observed_end':opening+25*cc.MINUTE,'decision_at':opening+27*cc.MINUTE},cache)


def test_current_target_membership_stop_and_clock_guards_still_run_on_every_hit():
    data=fixture()
    cache=cache_for(data)
    cached(data,cache)
    for variant in ({**data,'structural_stop':Fraction(0)},
                    {**data,'decision_at':data['decision_at']+cc.MINUTE},
                    {**data,'target':replace(data['target'],policy='D+3')},
                    {**data,'universe':replace(data['universe'],fixed_at=data['target'].opening+cc.MINUTE)}):
        with pytest.raises(features.FeaturesRefusal): cached(variant,cache)


def test_future_malformed_price_list_is_frozen_without_becoming_accepted_ohlc():
    data=fixture()
    at=data['target'].opening+85*cc.MINUTE
    values=[Decimal('100')]*4
    data['sessions'][SYMBOLS[0]].minutes[at]=values
    cache=cache_for(data)
    expected=features.prepare_features(**data)
    values[0]=Decimal('999')
    assert cached(data,cache)==expected
    assert type(cache.sessions[SYMBOLS[0]].minutes[at]) is not tuple
    with pytest.raises(features.FeaturesRefusal,match='exact_decimal_ohlc_required'):
        cache._observation(SYMBOLS[0],'future_invalid',at,at+cc.MINUTE,data['target'],at+3*cc.MINUTE)
