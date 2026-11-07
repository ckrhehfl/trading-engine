"""Invented audited days for EH management and prefix/provider boundaries."""

from collections.abc import Mapping
from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_scenario_management_day as management
from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_ed_inputs import InputRefusal, MINUTE
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_analogue_roster import ChartAnalogueRoster, HeldAnalogueRoster
from research.intraday_scenario_snapshot import project_opening_day
from test_intraday_scenario_management_compare import SYMBOL, at, row
from test_intraday_scenario_management_entry import breakout_frame


DAY = at(0).date().isoformat()
PRIOR = "2031-02-02"
FOLD = ChronologicalFold(1, (0, 1), (1, 2), (PRIOR,), (DAY,), at(0))


def _session(symbol, *, overrides=(), omit=()):
    _, _, source, _ = breakout_frame()
    prices = dict(source.minutes)
    prices.update((minute.start_at, minute) for minute in overrides)
    return replace(source, symbol=symbol, minutes={clock: replace(minute, symbol=symbol)
                                                   for clock, minute in prices.items() if clock not in omit})


def _day(sessions, *, scenario="S1", size=20):
    _, _, _, event = breakout_frame()
    symbols = (*sessions, *(f"invented-unavailable-{index}" for index in range(size - len(sessions))))
    targets = tuple(TargetCoordinate(scenario, symbol, DAY, index, None, "no_stored_rows_unknown_reason",
                                     b"invented-management-roster") for index, symbol in enumerate(symbols, 1))
    evidence = dict.fromkeys(sessions, event.level.current_evidence)
    return project_opening_day(DAY, targets, sessions, evidence, members_known_at=at(0)), symbols


def _run(day, provider, *, scenario="S1", cost=33, checkpoint=None, held_roster=None):
    roster = ChartAnalogueRoster((), fold=FOLD)
    return management.run_management_day(day, scenario=scenario, roster=roster, read_session=provider,
        risk_budget=Fraction(880), cost_bps=cost, downside_penalty=Fraction(1), fresh_reentry=False,
        checkpoint=checkpoint, held_roster=held_roster)


def _baseline(day, provider, *, risk=880, checkpoint=None):
    return management.baseline_holding_inputs(day, scenario="S1", read_session=provider,
        risk_budget=Fraction(risk), cost_bps=33, checkpoint=checkpoint)


@pytest.mark.parametrize("cost", [33, 43])
def test_all_original_coordinates_keep_status_and_share_confirmed_entry(cost):
    below = tuple(row(index, 560) for index in range(20, 65))
    sessions = {
        SYMBOL: _session(SYMBOL, overrides=(row(28, opened=590),)),
        "invented-declined": _session("invented-declined", overrides=(row(28, opened=606),)),
        "invented-unresolved": _session("invented-unresolved", omit=(at(28),)),
        "invented-unresolved-exit": _session("invented-unresolved-exit", omit=(at(58),)),
        "invented-later-gap": _session("invented-later-gap", overrides=below, omit=(at(33),)),
        "invented-approach": _session("invented-approach", overrides=below),
        "invented-quiet": _session("invented-quiet", overrides=tuple(
            row(index, 560, high=561, low=559) for index in range(15, 65))),
        "invented-late-breakout": _session("invented-late-breakout", overrides=below[:20]),
    }
    day, symbols = _day(sessions)
    requests, checkpoints = [], []

    def provider(day_key, symbol):
        requests.append((day_key, symbol))
        return sessions[symbol]

    result = _run(day, provider, cost=cost, checkpoint=checkpoints.append)
    assert (result.day, result.scenario, result.symbols) == (DAY, "S1", symbols)
    assert tuple(result.observations) == tuple((DAY, symbol) for symbol in symbols)
    assert [case.status for case in result.cases[:8]] == [
        "confirmed", "declined", "unresolved", "confirmed", "unknown", "no_entry", "no_event", "unsupported",
    ]
    assert all(case.status == "unknown" for case in result.cases[8:])
    assert requests == [(DAY, symbol) for symbol in symbols[:4]]
    assert len(checkpoints) == 20
    assert result.observations[(DAY, "invented-unresolved")].entry_status == "unconfirmed"
    assert result.observations[(DAY, "invented-later-gap")].entry_status == "unconfirmed"

    modeled, declined, unknown_open, unknown_exit = (case.initial_entry for case in result.cases[:4])
    assert (modeled.open_price, modeled.fill_status, modeled.fill_quantity) == (590, "modeled", 10)
    assert (declined.open_price, declined.fill_status, declined.fill_quantity) == (606, "declined", 0)
    assert (unknown_open.open_price, unknown_open.fill_status, unknown_open.fill_quantity) == (None, "indeterminate", 0)
    assert (unknown_exit.open_price, unknown_exit.fill_status, unknown_exit.fill_quantity) == (600, "modeled", 10)
    assert all(trace.decision_at == at(27) and trace.request == modeled.request
               for trace in (declined, unknown_open, unknown_exit))
    assert (modeled.decision_at, modeled.request.action, modeled.request.execute_at, modeled.request.quantity) == (
        at(27), "enter", at(28), 10,
    )
    assert (modeled.request.bounds.stop, modeled.request.bounds.max_price) == (565, 605)
    assert all(case.initial_entry is None for case in result.cases[4:])

    comparison = result.cases[0].managed.comparison
    lot, = comparison.initial.book.lots
    assert (comparison.initial.entered_at, lot.entry_price, lot.quantity) == (at(28), 590, 10)
    assert comparison.initial.cost_bps == cost
    assert comparison.fixed.episode.book == comparison.dynamic.episode.book
    assert comparison.fixed.episode.book.realized_gross == 100
    assert comparison.fixed.episode.book.realized_net == 100 - Fraction(5900 * cost, 10_000)
    assert result.cases[3].managed.comparison.fixed.status == "unresolved"
    assert result.cases[3].managed.comparison.dynamic.status == "unresolved"


def test_open_is_not_read_before_preparation_and_inconsistent_prefix_is_refused(monkeypatch):
    source = _session(SYMBOL)
    day, _ = _day({SYMBOL: source}, scenario="S2")
    original = management.prepare_matched_entry
    prepared = False

    def prepare(*args, **kwargs):
        nonlocal prepared
        result = original(*args, **kwargs)
        assert result.episode.pending.execute_at == at(28)
        prepared = True
        return result

    class PrefixProbe(Mapping):
        def __getitem__(self, clock):
            assert prepared or clock <= at(24)
            return source.minutes[clock]

        def __iter__(self):
            raise AssertionError("must not scan stored minute keys")

        def __len__(self):
            return len(source.minutes)

    monkeypatch.setattr(management, "prepare_matched_entry", prepare)
    result = _run(day, lambda *_: replace(source, minutes=PrefixProbe()), scenario="S2")
    assert result.cases[0].status == "confirmed"

    prepared = False
    source = _session(SYMBOL, overrides=(row(24, 601),))
    with pytest.raises(InputRefusal, match="breakout_prefix_differs"):
        _run(day, lambda *_: replace(source, minutes=PrefixProbe()), scenario="S2")


def test_prior_provider_is_limited_to_earlier_fold_sessions(monkeypatch):
    source = _session(SYMBOL)
    day, _ = _day({SYMBOL: source})
    requests = []
    shifted = replace(source, opening=source.opening - 1440 * MINUTE, closing=source.closing - 1440 * MINUTE,
                      minutes={})
    original = management.replay_matched_breakout

    def provider(date, symbol):
        requests.append((date, symbol))
        return source if date == DAY else shifted

    def replay(*args, read_prior_session, **kwargs):
        assert read_prior_session(PRIOR, SYMBOL) is shifted
        for forbidden in (DAY, "2031-02-04", "2031-02-01"):
            with pytest.raises(InputRefusal, match="outside_training_fold"):
                read_prior_session(forbidden, SYMBOL)
        return original(*args, read_prior_session=read_prior_session, **kwargs)

    monkeypatch.setattr(management, "replay_matched_breakout", replay)
    assert _run(day, provider).cases[0].status == "confirmed"
    assert requests == [(DAY, SYMBOL), (PRIOR, SYMBOL)]


def test_truncated_roster_and_nonregistered_cost_are_refused():
    partial, _ = _day({SYMBOL: _session(SYMBOL)}, size=19)

    def unexpected(*_):
        pytest.fail("invalid input must not call the provider")

    with pytest.raises(InputRefusal, match="original_roster_differs"):
        _run(partial, unexpected)
    with pytest.raises(InputRefusal, match="invalid_day_inputs"):
        _run(partial, unexpected, cost=0)
    with pytest.raises(InputRefusal, match="original_roster_differs"):
        _baseline(partial, unexpected)


def test_baseline_keeps_holding_clocks_after_initial_path_resolves_and_freezes_prefix():
    source = _session(SYMBOL, overrides=(row(28, opened=590), row(32, 620, high=700)))
    day, _ = _day({SYMBOL: source})
    assert any(item.frame.initial.stage == "breakout" and item.frame.observed_at == at(37)
               and item.frame.chart.status == "resolved" for item in day.rows)
    requested, checkpoints = [], []

    def provider(date, symbol):
        requested.append((date, symbol))
        return source

    baseline = _baseline(day, provider, checkpoint=checkpoints.append)
    assert requested == [(DAY, SYMBOL)] and len(checkpoints) == 20
    assert tuple(item.decision_at for item in baseline) == tuple(at(clock) for clock in range(32, 57, 5))
    assert all(item.attempt == 1 and item.entered_at == at(28) and item.entry_in_bands == 4
               and item.planning_cap == at(58) and item.status == "ready" for item in baseline)
    assert _run(day, provider).cases[0].managed.comparison.initial.entered_at == baseline[0].entered_at

    # This newly observed structural failure belongs to a later clock. It
    # closes the baseline before the third observation and changes no prefix.
    source = replace(source, minutes=source.minutes | {at(35): row(35, 560)})
    assert _baseline(day, provider) == baseline[:2]


def test_baseline_retains_unknown_held_prefix_and_skips_unconfirmed_entries():
    sessions = {
        SYMBOL: _session(SYMBOL, omit=(at(25),)),
        "invented-open-missing": _session("invented-open-missing", omit=(at(28),)),
        "invented-approach-only": _session("invented-approach-only", overrides=tuple(
            row(index, 560) for index in range(20, 65))),
    }
    day, _ = _day(sessions)
    requested = []

    def provider(date, symbol):
        requested.append((date, symbol))
        return sessions[symbol]

    observations = _baseline(day, provider)
    assert requested == [(DAY, SYMBOL), (DAY, "invented-open-missing")]
    assert observations and all(item.symbol == SYMBOL for item in observations)
    assert observations[0].status == "unknown" and observations[0].issues[0].minute == at(25)
    assert observations[-1].status == "ready"
    assert _baseline(day, provider, risk=1) == ()


def test_management_held_roster_must_share_the_fold_and_receives_continued_clock():
    source = _session(SYMBOL, overrides=(row(32, 620, high=700),))
    day, _ = _day({SYMBOL: source})
    wrong_fold = HeldAnalogueRoster((), fold=replace(FOLD, index=2))

    def unexpected(*_):
        pytest.fail("fold identity must be checked before requesting a session")

    with pytest.raises(InputRefusal, match="held_roster_fold_differs"):
        _run(day, unexpected, held_roster=wrong_fold)

    correct = HeldAnalogueRoster((), fold=FOLD)
    result = _run(day, lambda *_: source, held_roster=correct)
    assessments = result.cases[0].managed.decisions
    assert any(item.at > at(37) and item.reason == "insufficient_prior_chart_paths" for item in assessments)
