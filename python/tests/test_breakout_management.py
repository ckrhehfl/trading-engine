"""The six policies must do what the registration says, and only that.

`.planning/tm-d-breakout-management-preregistration.md` is a contract
committed before any data was loaded. These tests check the
implementation against the document clause by clause, on synthetic bars
where the intended behaviour is unambiguous — because on real data every
policy produces a number and none of them tells you whether the rule was
implemented correctly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtest.engine import run_backtest
from backtest.kline import Kline
from research.strategies.breakout_management import (
    ATR_PERIOD,
    K,
    MAX_LAYERS,
    BreakoutManagementStrategy,
    Policy,
)
from research.strategies.fill_contract import verify_fill_contract
from schemas.order_intent import Side

FEE = Decimal("5")
SLIP = Decimal("1")
EQUITY = Decimal("10000")
START = datetime(2020, 1, 1, tzinfo=timezone.utc)


def bar(minute: int, o, h, low, c) -> Kline:
    return Kline(
        open_time=START + timedelta(minutes=minute),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=Decimal("1"),
    )


def flat_day(day: int, price: float, span: float = 10.0, minutes: int = 1440):
    """A full UTC day of quiet bars, to establish a previous range."""
    base = day * 1440
    out = []
    for m in range(minutes):
        out.append(bar(base + m, price, price + span, price - span, price))
    return out


def run(policy: Policy, klines: list[Kline]):
    strat = BreakoutManagementStrategy(policy, SLIP, EQUITY)
    result = run_backtest(klines, strat, FEE, SLIP, starting_equity=EQUITY)
    return strat, result


class TestEntry:
    def test_the_trigger_is_open_plus_half_the_previous_range(self):
        """`k = 0.5`, literal in the reference implementation."""
        klines = flat_day(0, 100.0, span=10.0)          # prev range = 20
        # day 1 opens at 100; long trigger = 100 + 0.5*20 = 110
        klines += [bar(1440, 100, 105, 99, 104)]        # below trigger
        klines += [bar(1441, 104, 111, 103, 110)]       # touches 110
        klines += [bar(1442, 110, 112, 108, 111)]
        strat, result = run(Policy.BASELINE, klines)
        assert len(result.fills) >= 1
        assert result.filled_intents[0].side is Side.LONG

    def test_no_entry_while_price_stays_inside_the_range(self):
        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440 + m, 100, 105, 96, 100) for m in range(5)]
        _, result = run(Policy.BASELINE, klines)
        assert result.fills == []

    def test_a_short_triggers_below_the_open(self):
        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440, 100, 101, 95, 96)]
        klines += [bar(1441, 96, 97, 88, 89)]           # touches 90
        klines += [bar(1442, 89, 90, 88, 89)]
        _, result = run(Policy.BASELINE, klines)
        assert result.filled_intents[0].side is Side.SHORT

    def test_a_bar_that_triggers_both_directions_skips_the_day(self):
        """The intrabar path is unobservable, so the registration's
        conservative reading applies — and the skip is counted."""
        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440, 100, 115, 85, 100)]        # spans both
        klines += [bar(1441, 100, 101, 99, 100)]
        strat, result = run(Policy.BASELINE, klines)
        assert result.fills == []
        assert strat.ambiguous_days == 1

    def test_only_one_entry_per_day(self):
        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440 + m, 100, 115, 99, 114) for m in range(10)]
        _, result = run(Policy.BASELINE, klines)
        entries = [i for i in result.filled_intents if i.side is Side.LONG]
        assert len(entries) == 1


class TestFillContract:
    def test_every_fill_matches_next_bar_open_times_slippage(self):
        """The registration's void condition, over every event type."""
        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440, 100, 111, 99, 110)]
        klines += [bar(1441 + m, 110, 112, 108, 111) for m in range(200)]
        klines += flat_day(2, 111.0, span=5.0)
        for policy in Policy:
            strat, result = run(policy, klines)
            breaches = verify_fill_contract(
                klines, result.fills, result.filled_intents, SLIP
            )
            assert breaches == [], f"{policy}: {[str(b) for b in breaches]}"

    def test_the_verifier_catches_a_tampered_fill(self):
        """A guard nobody has watched fail is not a guard."""
        from dataclasses import replace

        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440, 100, 111, 99, 110)]
        klines += [bar(1441 + m, 110, 112, 108, 111) for m in range(5)]
        _, result = run(Policy.BASELINE, klines)
        assert result.fills, "need at least one fill to tamper with"

        tampered = list(result.fills)
        tampered[0] = replace(tampered[0], fill_price=tampered[0].fill_price + 1)
        breaches = verify_fill_contract(klines, tampered, result.filled_intents, SLIP)
        assert len(breaches) == 1
        assert "not next-bar open" in breaches[0].reason


class TestPolicies:
    def _breakout_then_run_up(self, up_to: float = 200.0, minutes: int = 400):
        """A clean breakout followed by a sustained advance, so the
        policies visibly differ rather than all exiting the same way."""
        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440, 100, 111, 99, 110)]
        price = 110.0
        step = (up_to - 110.0) / minutes
        for m in range(minutes):
            nxt = price + step
            klines.append(bar(1441 + m, price, nxt + 0.5, price - 0.5, nxt))
            price = nxt
        klines += flat_day(2, up_to, span=1.0)
        return klines

    def test_p0_has_no_stop_and_exits_at_the_day_close(self):
        klines = self._breakout_then_run_up()
        strat, result = run(Policy.BASELINE, klines)
        assert len(strat.episodes) == 1
        assert strat.episodes[0].stop is None
        assert strat.episodes[0].stopped is False
        # `result` was unused, which is how "exits at the day close"
        # went unverified. This fixture has no 23:59 bar (its day 1 is
        # truncated), so the exit is the safety net's, on the first bar
        # of the next day.
        assert result.fills[-1].fill_time.hour == 0

    def test_p1_places_a_stop_below_entry_for_a_long(self):
        klines = self._breakout_then_run_up()
        strat, _ = run(Policy.STOP, klines)
        ep = strat.episodes[0]
        assert ep.stop is not None
        assert ep.stop < ep.trigger, "a long's stop must sit below entry"

    def test_a_shorts_stop_sits_ABOVE_entry(self):
        """The sign error the registration retracted: `1R` is a positive
        distance, so a bare subtraction gives a short no protection."""
        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440, 100, 101, 89, 90)]
        klines += [bar(1441 + m, 90, 91, 89, 90) for m in range(50)]
        strat, _ = run(Policy.STOP, klines)
        ep = strat.episodes[0] if strat.episodes else strat._episode
        assert ep is not None and ep.side is Side.SHORT
        assert ep.stop > ep.trigger, "a short's stop must sit above entry"

    def test_p4_adds_at_most_three_layers(self):
        klines = self._breakout_then_run_up(up_to=400.0)
        strat, _ = run(Policy.PYRAMID, klines)
        ep = strat.episodes[0] if strat.episodes else strat._episode
        assert ep is not None
        assert len(ep.legs) <= MAX_LAYERS
        assert ep.levels_taken <= MAX_LAYERS

    def test_p4_each_layer_is_half_the_previous(self):
        klines = self._breakout_then_run_up(up_to=400.0)
        strat, _ = run(Policy.PYRAMID, klines)
        ep = strat.episodes[0] if strat.episodes else strat._episode
        assert ep is not None and len(ep.legs) >= 2
        for a, b in zip(ep.legs, ep.legs[1:]):
            assert b.qty == a.qty / 2

    def test_p4_adds_at_most_one_layer_per_bar(self):
        """A single bar spanning +1R and +2R must add one layer, because
        1m OHLC cannot say which level was reached first."""
        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440, 100, 111, 99, 110)]
        klines += [bar(1441, 110, 111, 109, 110)]
        klines += [bar(1442, 110, 111, 109, 110)]
        klines += [bar(1443, 110, 400, 109, 399)]        # spans many levels
        klines += [bar(1444 + m, 399, 400, 398, 399) for m in range(5)]
        strat, _ = run(Policy.PYRAMID, klines)
        ep = strat.episodes[0] if strat.episodes else strat._episode
        assert ep is not None
        # Index 1443 is the bar that spans several levels. An earlier
        # version looked at 1444, where no add can occur, so the
        # assertion held vacuously and a two-adds-per-bar regression
        # would have stayed green.
        adds_on_that_bar = [leg for leg in ep.legs if leg.opened_at == 1443]
        assert len(adds_on_that_bar) == 1, (
            f"expected exactly one layer added on the multi-level bar, got "
            f"{len(adds_on_that_bar)}"
        )

    def test_p3_closes_half_at_one_r(self):
        klines = self._breakout_then_run_up()
        strat, result = run(Policy.SCALE_OUT, klines)
        ep = strat.episodes[0] if strat.episodes else strat._episode
        assert ep is not None and ep.scaled_out

        # The flag alone would stay green if the close were the wrong
        # size, or the core leg were not reduced.
        entry_qty = result.filled_intents[0].quantity
        closes = [i for i in result.filled_intents if i.side is Side.SHORT]
        assert closes, "the partial close must reach the engine as an intent"
        assert closes[0].quantity == entry_qty / 2, (
            f"partial close was {closes[0].quantity}, expected half of "
            f"{entry_qty}"
        )
        assert ep.legs[0].qty == entry_qty / 2, "the core leg must be halved"

    def test_p5_opens_an_opposing_leg_instead_of_closing(self):
        klines = self._breakout_then_run_up()
        strat, _ = run(Policy.HEDGE, klines)
        ep = strat.episodes[0] if strat.episodes else strat._episode
        assert ep is not None and ep.hedge is not None
        assert ep.hedge.side is Side.SHORT
        assert ep.open_qty == ep.legs[0].qty, "the core leg must not shrink"

    def test_p3_and_p5_scale_at_the_same_moment(self):
        """P5 is a control: it must differ from P3 only in *how* the
        reduction is taken, never in when."""
        klines = self._breakout_then_run_up()
        s3, r3 = run(Policy.SCALE_OUT, klines)
        s5, r5 = run(Policy.HEDGE, klines)
        assert [f.fill_time for f in r3.fills] == [f.fill_time for f in r5.fills]


class TestNoManagementOnTheFillBar:
    def test_a_position_is_not_stopped_on_the_bar_it_filled_on(self):
        """A single volatile bar read as both a fill and a stop-out is a
        fill-model artefact, not a market event."""
        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440, 100, 111, 99, 110)]        # signal
        klines += [bar(1441, 110, 112, 80, 85)]         # fill bar, dives
        klines += [bar(1442, 85, 86, 84, 85)]
        klines += [bar(1443 + m, 85, 86, 84, 85) for m in range(5)]
        strat, result = run(Policy.STOP, klines)
        exits = [f for f in result.fills if f.fill_time > result.fills[0].fill_time]
        assert exits, "it should still stop out, just not on the fill bar"

        # The exact bar matters, and a `>` comparison does not pin it.
        # An earlier version asserted only that the exit came after the
        # fill bar, which is true whether management starts at t+1 or
        # t+2 -- so allowing management on the fill bar passed it. That
        # was an inert guard, found by making the change and watching
        # every test stay green.
        #
        #   entry signals 1440, fills 1441 (t+1)
        #   management starts 1442 (t+2), stop touched there
        #   so the exit fills at 1443
        #
        # If management began at t+1 the stop would trigger on 1441 and
        # the exit would fill at 1442.
        assert exits[0].fill_time == klines[1443].open_time, (
            f"exit filled at {exits[0].fill_time}, expected "
            f"{klines[1443].open_time}: a fill at {klines[1442].open_time} "
            f"means the position was managed on its own fill bar"
        )


class TestAtr:
    def test_the_trail_is_unavailable_until_enough_completed_days(self):
        """No trail is better than one seeded from a partial history."""
        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440, 100, 111, 99, 110)]
        klines += [bar(1441 + m, 110, 111, 109, 110) for m in range(10)]
        strat, _ = run(Policy.TRAIL, klines)
        assert strat._atr is None

    def test_atr_appears_once_enough_days_are_complete(self):
        klines: list[Kline] = []
        for d in range(ATR_PERIOD + 3):
            klines += flat_day(d, 100.0 + d, span=5.0, minutes=60)
        strat = BreakoutManagementStrategy(Policy.TRAIL, SLIP, EQUITY)
        run_backtest(klines, strat, FEE, SLIP, starting_equity=EQUITY)
        assert strat._atr is not None and strat._atr > 0


def test_the_constants_are_the_registered_ones():
    """If any of these drifts, the run is no longer the registered
    experiment — which is the whole point of pinning them."""
    assert K == Decimal("0.5")
    assert ATR_PERIOD == 14
    assert MAX_LAYERS == 3


class TestTimeExitFiresOnTheClockNotTheSafetyNet:
    """The clock rule and the missing-bar safety net must be
    distinguishable, or one of them is untested.

    Breaking `_is_last_bar_of_day` and re-running left every other test
    in this file green, because the safety net closes the position on
    the first bar of the next day and no assertion looked at *which*
    bar. That is an inert guard: the clock rule could be deleted and
    nothing would say so.
    """

    def _two_full_days(self):
        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440, 100, 111, 99, 110)]
        klines += [bar(1441 + m, 110, 111, 109, 110) for m in range(1439)]
        klines += flat_day(2, 110.0, span=2.0, minutes=10)
        return klines

    def test_the_exit_signals_on_the_2359_bar(self):
        klines = self._two_full_days()
        strat, result = run(Policy.BASELINE, klines)
        assert len(strat.episodes) == 1
        exit_fill = result.fills[-1]
        # Signalled on the 23:59 bar => filled at the next bar's open,
        # 00:00. A 00:01 fill means the safety net fired instead.
        assert (exit_fill.fill_time.hour, exit_fill.fill_time.minute) == (0, 0), (
            f"exit filled at {exit_fill.fill_time}; the clock rule signals on "
            f"the 23:59 bar, so this means the safety net fired instead"
        )

    def test_the_safety_net_still_covers_a_missing_2359_bar(self):
        """The other half: with 23:59 absent the position must still
        close rather than silently carry into the next day."""
        klines = self._two_full_days()
        klines = [
            k for k in klines
            if not (k.open_time.hour == 23 and k.open_time.minute == 59)
        ]
        strat, _ = run(Policy.BASELINE, klines)
        assert len(strat.episodes) == 1, "a missing 23:59 must not strand the position"


class TestTheFillGuardChecksTiming:
    """The guard must reject same-candle execution, not just a wrong price.

    A first version derived the fill bar from `fill_time` and compared
    the price against *that same bar's* open — so a fill on the signal
    bar would have produced a matching expected price and passed. The
    guard existed to prevent exactly that.
    """

    def test_a_same_candle_fill_is_rejected(self):
        from dataclasses import replace

        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440, 100, 111, 99, 110)]
        klines += [bar(1441 + m, 110, 112, 108, 111) for m in range(5)]
        _, result = run(Policy.BASELINE, klines)
        assert result.fills

        # Move the first fill back onto its own signal bar, and price it
        # from that bar's open so a price-only check would be satisfied.
        intent = result.filled_intents[0]
        signal_bar = next(k for k in klines if k.open_time == intent.created_at)
        same_candle = replace(
            result.fills[0],
            fill_time=signal_bar.open_time,
            fill_price=signal_bar.open * (Decimal(1) + SLIP / Decimal("10000")),
        )
        breaches = verify_fill_contract(
            klines, [same_candle] + list(result.fills[1:]), result.filled_intents, SLIP
        )
        assert breaches, "same-candle execution must be a breach"
        assert "the bar after the signal" in breaches[0].reason


class TestTheFillGuardJoinsById:
    """Position-based pairing can validate a fill against the wrong
    intent, which both voids valid runs and passes mismatched ones.

    A first version used `zip(..., strict=True)`, which checks only that
    the two lists are the same length.
    """

    def _one_run(self):
        # A full day 1 so the 23:59 time exit fires and there is a
        # closing fill to pair as well as an opening one.
        klines = flat_day(0, 100.0, span=10.0)
        klines += [bar(1440, 100, 111, 99, 110)]
        klines += [bar(1441 + m, 110, 112, 108, 111) for m in range(1439)]
        klines += flat_day(2, 111.0, span=2.0, minutes=10)
        _, result = run(Policy.BASELINE, klines)
        assert len(result.fills) >= 2, "need two fills to reorder"
        return klines, result

    def test_reordering_the_intents_does_not_change_the_verdict(self):
        klines, result = self._one_run()
        clean = verify_fill_contract(klines, result.fills, result.filled_intents, SLIP)
        assert clean == []

        shuffled = list(reversed(result.filled_intents))
        still_clean = verify_fill_contract(klines, result.fills, shuffled, SLIP)
        assert still_clean == [], (
            "reordering the intent list must not matter: the join is by "
            f"intent_id, not position — got {[str(b) for b in still_clean]}"
        )

    def test_a_fill_with_no_matching_intent_is_a_breach(self):
        klines, result = self._one_run()
        breaches = verify_fill_contract(
            klines, result.fills, result.filled_intents[1:], SLIP
        )
        assert any("no matching intent" in b.reason for b in breaches)

    def test_an_intent_with_no_fill_is_a_breach(self):
        klines, result = self._one_run()
        breaches = verify_fill_contract(
            klines, result.fills[1:], result.filled_intents, SLIP
        )
        assert any("no matching fill" in b.reason for b in breaches)

    def test_two_fills_sharing_an_intent_id_is_a_breach(self):
        klines, result = self._one_run()
        doubled = list(result.fills) + [result.fills[0]]
        breaches = verify_fill_contract(klines, doubled, result.filled_intents, SLIP)
        assert any("two fills share one intent_id" in b.reason for b in breaches)
