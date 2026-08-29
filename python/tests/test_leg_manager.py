"""Tests for `research.strategies.leg_manager` -- Task A, Stage 2.

`TestTheOperatorsScenario` drives the full sequence through the manager
and checks BOTH halves: that the book records it as core-plus-tactical,
and that the emitted orders are ones a venue would actually accept.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from metrics.book import LegPurpose
from research.strategies.leg_manager import LegManager
from schemas.order_intent import OrderType, Side

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYM = "BTC-USDT"


def _mgr() -> LegManager:
    return LegManager(symbol=SYM, signal_timeframe="1d")


class TestTheOperatorsScenario:
    def test_core_plus_tactical_hedge_then_close_only_the_hedge(self):
        m = _mgr()

        core = m.open_leg(side=Side.LONG, quantity=Decimal("2"), price=Decimal("100"),
                          time=T0, purpose=LegPurpose.CORE, invalidation=Decimal("85"))
        opened = m.drain()
        assert opened is not None and len(opened) == 1
        assert opened[0].side is Side.LONG and opened[0].quantity == Decimal("2")

        hedge = m.open_leg(side=Side.SHORT, quantity=Decimal("1"), price=Decimal("120"),
                           time=T0 + timedelta(hours=6), purpose=LegPurpose.TACTICAL,
                           invalidation=Decimal("128"))
        assert m.net_exposure() == Decimal("1")
        assert m.gross_exposure() == Decimal("3"), "hedging raises margin use, not lowers it"
        m.drain()

        record = m.close_leg(hedge.leg_id, price=Decimal("110"), time=T0 + timedelta(days=1))
        assert record.realized_pnl == Decimal("10")

        closing = m.drain()
        assert closing is not None and len(closing) == 1
        assert closing[0].side is Side.LONG, (
            "closing a short emits a BUY -- on the wire it looks like opening a "
            "long, and the book is what preserves the distinction"
        )

        # The core survives, at its original entry.
        assert m.book.leg(core.leg_id).entry_price == Decimal("100")
        assert m.net_exposure() == Decimal("2")
        assert m.book.realized_pnl_by_purpose(SYM)[LegPurpose.TACTICAL] == Decimal("10")

    def test_every_emitted_intent_is_venue_shaped(self):
        m = _mgr()
        leg = m.open_leg(side=Side.LONG, quantity=Decimal("1"), price=Decimal("100"),
                         time=T0, purpose=LegPurpose.CORE)
        m.close_leg(leg.leg_id, price=Decimal("110"), time=T0)
        for intent in m.drain():
            assert intent.symbol == SYM
            assert intent.order_type is OrderType.GUARDED_MARKET
            assert intent.quantity > 0
            assert intent.signal_timeframe == "1d"


class TestDrain:
    def test_returns_none_when_nothing_happened(self):
        assert _mgr().drain() is None

    def test_batches_several_actions_from_one_bar(self):
        """Splitting these across bars would change the prices they act
        at, which is why the engine accepts a sequence."""
        m = _mgr()
        a = m.open_leg(side=Side.LONG, quantity=Decimal("1"), price=Decimal("100"),
                       time=T0, purpose=LegPurpose.CORE)
        m.drain()
        m.open_leg(side=Side.SHORT, quantity=Decimal("1"), price=Decimal("120"),
                   time=T0, purpose=LegPurpose.TACTICAL)
        m.close_leg(a.leg_id, price=Decimal("120"), time=T0)
        batch = m.drain()
        assert batch is not None and len(batch) == 2

    def test_draining_twice_does_not_repeat_orders(self):
        m = _mgr()
        m.open_leg(side=Side.LONG, quantity=Decimal("1"), price=Decimal("100"),
                   time=T0, purpose=LegPurpose.CORE)
        assert m.drain() is not None
        assert m.drain() is None

    def test_order_within_a_batch_is_preserved(self):
        m = _mgr()
        first = m.open_leg(side=Side.LONG, quantity=Decimal("1"), price=Decimal("100"),
                           time=T0, purpose=LegPurpose.CORE)
        m.drain()
        m.close_leg(first.leg_id, price=Decimal("110"), time=T0)
        m.open_leg(side=Side.SHORT, quantity=Decimal("3"), price=Decimal("110"),
                   time=T0, purpose=LegPurpose.CORE)
        batch = m.drain()
        assert [i.quantity for i in batch] == [Decimal("1"), Decimal("3")]


class TestScalingIn:
    def test_add_creates_a_separate_tranche_not_a_re_average(self):
        """Averaging would rebuild the single blended position this whole
        module exists to get away from."""
        m = _mgr()
        leg = m.open_leg(side=Side.LONG, quantity=Decimal("1"), price=Decimal("100"),
                         time=T0, purpose=LegPurpose.CORE, invalidation=Decimal("90"))
        added = m.add_to_leg(leg.leg_id, quantity=Decimal("1"), price=Decimal("110"), time=T0)
        assert added.leg_id != leg.leg_id
        assert m.book.leg(leg.leg_id).entry_price == Decimal("100")
        assert added.entry_price == Decimal("110")
        assert added.purpose is LegPurpose.CORE
        assert added.invalidation == Decimal("90"), "the tranche inherits the thesis level"

    def test_adding_increases_gross_exposure(self):
        m = _mgr()
        leg = m.open_leg(side=Side.LONG, quantity=Decimal("1"), price=Decimal("100"),
                         time=T0, purpose=LegPurpose.CORE)
        m.add_to_leg(leg.leg_id, quantity=Decimal("2"), price=Decimal("110"), time=T0)
        assert m.gross_exposure() == Decimal("3")

    def test_adding_to_an_unknown_leg_raises(self):
        with pytest.raises(KeyError, match="no open leg"):
            _mgr().add_to_leg(uuid4(), quantity=Decimal("1"), price=Decimal("1"), time=T0)


class TestScalingOut:
    def test_partial_close_leaves_a_runner_with_its_original_entry(self):
        m = _mgr()
        leg = m.open_leg(side=Side.LONG, quantity=Decimal("3"), price=Decimal("100"),
                         time=T0, purpose=LegPurpose.RUNNER)
        m.drain()
        m.close_leg(leg.leg_id, price=Decimal("130"), time=T0, quantity=Decimal("2"))
        rest = m.book.leg(leg.leg_id)
        assert rest.quantity == Decimal("1")
        assert rest.entry_price == Decimal("100")
        assert m.drain()[0].quantity == Decimal("2")


class TestInvalidation:
    def test_reports_only_the_leg_whose_own_level_was_touched(self):
        """The thing one shared stop could not do."""
        m = _mgr()
        core = m.open_leg(side=Side.LONG, quantity=Decimal("1"), price=Decimal("100"),
                          time=T0, purpose=LegPurpose.CORE, invalidation=Decimal("85"))
        hedge = m.open_leg(side=Side.SHORT, quantity=Decimal("1"), price=Decimal("120"),
                           time=T0, purpose=LegPurpose.TACTICAL, invalidation=Decimal("128"))
        hit = m.triggered(bar_high=Decimal("130"), bar_low=Decimal("119"))
        assert [x.leg_id for x in hit] == [hedge.leg_id]
        assert core.leg_id not in [x.leg_id for x in hit]

    def test_a_leg_without_an_invalidation_never_triggers(self):
        m = _mgr()
        m.open_leg(side=Side.LONG, quantity=Decimal("1"), price=Decimal("100"),
                   time=T0, purpose=LegPurpose.CORE)
        assert m.triggered(bar_high=Decimal("999"), bar_low=Decimal("1")) == ()

    def test_both_sides_can_trigger_together(self):
        m = _mgr()
        m.open_leg(side=Side.LONG, quantity=Decimal("1"), price=Decimal("100"),
                   time=T0, purpose=LegPurpose.CORE, invalidation=Decimal("95"))
        m.open_leg(side=Side.SHORT, quantity=Decimal("1"), price=Decimal("100"),
                   time=T0, purpose=LegPurpose.TACTICAL, invalidation=Decimal("105"))
        assert len(m.triggered(bar_high=Decimal("106"), bar_low=Decimal("94"))) == 2


class TestCloseAll:
    def test_closes_only_the_named_purpose(self):
        m = _mgr()
        m.open_leg(side=Side.LONG, quantity=Decimal("1"), price=Decimal("100"),
                   time=T0, purpose=LegPurpose.CORE)
        m.open_leg(side=Side.SHORT, quantity=Decimal("1"), price=Decimal("120"),
                   time=T0, purpose=LegPurpose.TACTICAL)
        m.close_all(price=Decimal("110"), time=T0, purpose=LegPurpose.TACTICAL)
        assert len(m.legs()) == 1
        assert m.legs()[0].purpose is LegPurpose.CORE

    def test_closes_everything_when_no_purpose_given(self):
        m = _mgr()
        m.open_leg(side=Side.LONG, quantity=Decimal("1"), price=Decimal("100"),
                   time=T0, purpose=LegPurpose.CORE)
        m.open_leg(side=Side.SHORT, quantity=Decimal("1"), price=Decimal("120"),
                   time=T0, purpose=LegPurpose.TACTICAL)
        m.close_all(price=Decimal("110"), time=T0)
        assert m.legs() == ()
        assert m.gross_exposure() == Decimal("0")

    def test_is_a_no_op_on_an_empty_book(self):
        assert _mgr().close_all(price=Decimal("100"), time=T0) == ()


class TestQueries:
    def test_has_reports_purpose_presence(self):
        m = _mgr()
        assert not m.has(LegPurpose.HEDGE)
        m.open_leg(side=Side.SHORT, quantity=Decimal("1"), price=Decimal("100"),
                   time=T0, purpose=LegPurpose.HEDGE)
        assert m.has(LegPurpose.HEDGE)

    def test_closing_an_unknown_leg_raises(self):
        with pytest.raises(KeyError, match="no open leg"):
            _mgr().close_leg(uuid4(), price=Decimal("100"), time=T0)
