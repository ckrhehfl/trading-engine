"""Independent day-cache parity over invented parser-shaped minute inputs."""
from dataclasses import replace
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_campaign_feature_verifier as verifier
from research import intraday_campaign_features as producer
from test_intraday_campaign_feature_verifier import feature_inputs, lose, SYMBOLS


def cached_arguments(data):
    cache = verifier.FeatureVerifierDayCache(opening=data["target"].opening, closing=data["target"].closing,
        sessions=data["sessions"], sources=data["sources"])
    return cache, {**data, "sessions": cache.sessions, "sources": cache.sources, "day_cache": cache}


@pytest.mark.parametrize("case", ["complete", "missing_conflict_invalid", "nan", "zero_range", "absent_target", "absent_peer", "quorum4", "quorum5"])
def test_cached_full_prepared_features_equal_uncached_independent_reconstruction(case):
    data = feature_inputs(4 if case == "quorum4" else 5 if case == "quorum5" else 7)
    if case == "missing_conflict_invalid":
        lose(data, SYMBOLS[1], 10, "conflict")
        lose(data, SYMBOLS[2], 50, "invalid_price")
        lose(data, SYMBOLS[0], 10, "missing")
    elif case == "nan":
        data["sessions"][SYMBOLS[0]].minutes[data["target"].opening + 50 * verifier.MINUTE] = (Decimal("NaN"),) * 4
    elif case == "zero_range":
        for symbol, session in data["sessions"].items():
            data["sessions"][symbol] = replace(session, minutes={at: (Decimal(100),) * 4 for at in session.minutes})
    elif case == "absent_target":
        data["sessions"][SYMBOLS[0]] = None
    elif case == "absent_peer":
        data["sessions"][SYMBOLS[1]] = None
    expected = deepcopy(verifier._expected_features(**data))
    prepared = producer.prepare_features(**data)
    _, cached = cached_arguments(data)
    actual = verifier._expected_features(**cached)
    verifier._same(actual, expected, "cached_full_record")
    assert verifier.verify_features(prepared, **cached)["verified"] is True


def test_cache_reuses_only_identical_raw_windows_not_target_or_membership_results(monkeypatch):
    data = feature_inputs()
    cache, cached = cached_arguments(data)
    first = verifier._expected_features(**cached)
    count = len(cache._observations)
    original_observe = verifier._observe
    calls = []

    def counted(*args):
        calls.append(args[:4])
        return original_observe(*args)

    monkeypatch.setattr(verifier, "_observe", counted)
    verifier._same(verifier._expected_features(**cached), first, "identical_cached_record")
    assert calls == [] and len(cache._observations) == count
    for minute in (65, 75):
        changed = {**data, "observed_end": data["target"].opening + minute * verifier.MINUTE,
                   "decision_at": data["target"].opening + (minute+2) * verifier.MINUTE}
        verifier._same(verifier._expected_features(**{**cached, **{key: changed[key] for key in ("observed_end", "decision_at")}}),
                       verifier._expected_features(**changed), "different_decision")
    universe = replace(data["universe"], members=data["universe"].members[:5])
    expected = verifier._expected_features(**{**data, "universe": universe, "setup_id": "EA-B"})
    verifier._same(verifier._expected_features(**{**cached, "universe": universe, "setup_id": "EA-B"}), expected,
                   "changed_membership_and_quorum")
    assert expected.features["peer_last15_return"].value is None
    target = replace(data["target"], symbol=SYMBOLS[1],
        price_source=replace(data["target"].price_source, data_vintage=data["sources"][SYMBOLS[1]]["data_vintage"]))
    changes = {"target": target, "structural_stop": Fraction(96), "setup_id": "EA-B"}
    expected = verifier._expected_features(**{**data, **changes})
    verifier._same(verifier._expected_features(**{**cached, **changes}), expected, "changed_target_and_stop")
    assert SYMBOLS[0] in expected.common_peers and SYMBOLS[1] not in expected.common_peers


def test_cache_owned_snapshot_ignores_later_original_mutation_and_rejects_foreign_views():
    data = feature_inputs()
    expected = deepcopy(verifier._expected_features(**data))
    cache, cached = cached_arguments(data)
    data["sessions"][SYMBOLS[1]].minutes.clear()
    data["sources"][SYMBOLS[1]]["data_vintage"] = "f" * 64
    verifier._same(verifier._expected_features(**cached), expected, "owned_snapshot")
    with pytest.raises(verifier.InputVerificationRefusal, match="foreign_day_views"):
        verifier._expected_features(**{**cached, "sources": data["sources"]})
    with pytest.raises(TypeError):
        cache.sources[SYMBOLS[1]]["data_vintage"] = "f" * 64
    with pytest.raises(TypeError):
        cache.sessions[SYMBOLS[1]].minutes[data["target"].opening] = None


def test_cached_window_rechecks_availability_and_rejects_cross_day_or_vintage():
    data = feature_inputs()
    cache, cached = cached_arguments(data)
    verifier._expected_features(**cached)
    symbol = SYMBOLS[1]
    opening = data["target"].opening
    identity = cache._receipts[symbol][1]
    with pytest.raises(verifier.InputVerificationRefusal, match="availability"):
        cache.observe(symbol, "first30", opening, opening + 30 * verifier.MINUTE, cache.sessions[symbol], identity,
                      opening + 31 * verifier.MINUTE)
    target = replace(data["target"], session_date=data["target"].session_date + timedelta(days=1),
        opening=opening+timedelta(days=1), closing=data["target"].closing+timedelta(days=1))
    with pytest.raises(verifier.InputVerificationRefusal, match="cross_day"):
        verifier._expected_features(**{**cached, "target": target})
    target = replace(data["target"], price_source=replace(data["target"].price_source, data_vintage="f" * 64))
    with pytest.raises(verifier.InputVerificationRefusal, match="price_source"):
        verifier._expected_features(**{**cached, "target": target})


def test_cache_is_independent_of_producer_window_and_cache_computation(monkeypatch):
    data = feature_inputs()
    prepared = producer.prepare_features(**data)
    _, cached = cached_arguments(data)

    def forbidden(*args, **kwargs):
        pytest.fail("independent cache delegated producer computation")

    monkeypatch.setattr(producer, "prepare_features", forbidden)
    monkeypatch.setattr(producer, "_window", forbidden)
    assert verifier.verify_features(prepared, **cached)["verified"] is True
    with pytest.raises(verifier.InputVerificationRefusal, match="independent_day_cache"):
        verifier._expected_features(**{**cached, "day_cache": object()})


@pytest.mark.parametrize("bad", [object(), [Decimal(100)] * 4, (100, 100, 100, 100)])
def test_malformed_future_price_is_not_a_cache_admission_filter(bad):
    data = feature_inputs()
    at = data["target"].opening + 85 * verifier.MINUTE
    data["sessions"][SYMBOLS[1]].minutes[at] = bad
    data["sessions"][SYMBOLS[1]] = replace(data["sessions"][SYMBOLS[1]], prefix=(object(),))
    _, cached = cached_arguments(data)
    verifier._same(verifier._expected_features(**cached), verifier._expected_features(**data), "unused_future")
    later = {"observed_end": data["target"].opening + 90 * verifier.MINUTE,
             "decision_at": data["target"].opening + 92 * verifier.MINUTE}
    for original in (data, cached):
        with pytest.raises(verifier.InputVerificationRefusal, match="exact_decimal_ohlc"):
            verifier._expected_features(**{**original, **later})
