"""Tests for `metrics.book` -- Trade Management Task A, Stage 1.

The test that matters most is `TestTheOperatorsScenario`: it writes out,
step by step, the exact sequence this module was built to make
expressible. Everything else supports it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from metrics.book import (
    Book,
    BookReconciliationError,
    Leg,
    LegPurpose,
)
from schemas.order_intent import Side

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYM = "BTC-USDT"


def _leg(side=Side.LONG, qty="1", price="100", purpose=LegPurpose.CORE, **kw) -> Leg:
    return Leg(
        symbol=kw.pop("symbol", SYM),
        side=side,
        quantity=Decimal(qty),
        entry_price=Decimal(price),
        entry_time=kw.pop("entry_time", T0),
        purpose=purpose,
        **kw,
    )


class TestTheOperatorsScenario:
    """The sequence this module exists for, in the operator's own terms:

    enter long, it rises, weakness appears but a bounce looks possible,
    so add a short hedge there; when it drops, close ONLY the short for
    profit and keep the core long.

    Under the previous single-signed-quantity model this could not be
    written down at all -- the short would simply have reduced the long.
    """

    def test_the_whole_sequence(self):
        book = Book()

        # 1. Enter long at 100.
        core = book.open(_leg(Side.LONG, "2", "100", LegPurpose.CORE))
        assert book.net(SYM) == Decimal("2")

        # 2. It rises to 120. The core is up, still open.
        assert book.unrealized_pnl(SYM, Decimal("120")) == Decimal("40")

        # 3. Weakness appears at 120. Add a tactical SHORT -- the core is
        #    untouched, which is the part the old model could not do.
        hedge = book.open(_leg(Side.SHORT, "1", "120", LegPurpose.TACTICAL))
        assert book.leg(core.leg_id) is not None, "the core must survive the hedge"
        assert book.net(SYM) == Decimal("1")      # net exposure halves
        assert book.gross(SYM) == Decimal("3")    # but gross exposure GREW

        # 4. It drops to 110. Close ONLY the short.
        result = book.close(hedge.leg_id, exit_price=Decimal("110"), exit_time=T0 + timedelta(days=1))
        assert result.realized_pnl == Decimal("10")     # short 1 from 120 -> 110
        assert result.purpose is LegPurpose.TACTICAL

        # 5. The core is still open, at its ORIGINAL entry.
        remaining = book.leg(core.leg_id)
        assert remaining is not None
        assert remaining.quantity == Decimal("2")
        assert remaining.entry_price == Decimal("100"), (
            "the core's entry must not be re-averaged by the hedge"
        )
        assert book.net(SYM) == Decimal("2")

        # 6. And the two contributions are separable.
        by_purpose = book.realized_pnl_by_purpose(SYM)
        assert by_purpose[LegPurpose.TACTICAL] == Decimal("10")
        assert by_purpose[LegPurpose.CORE] == Decimal("0")

    def test_the_old_model_would_have_shown_only_the_net(self):
        """Contrast, asserted so the difference is not just narrative: a
        single signed quantity collapses the two legs to one number and
        loses both entry prices."""
        book = Book()
        book.open(_leg(Side.LONG, "2", "100", LegPurpose.CORE))
        book.open(_leg(Side.SHORT, "1", "120", LegPurpose.TACTICAL))
        assert book.net(SYM) == Decimal("1")          # all the old model had
        assert len(book.for_symbol(SYM)) == 2          # what this model keeps


class TestLeg:
    def test_signed_quantity_follows_side(self):
        assert _leg(Side.LONG, "3").signed_quantity == Decimal("3")
        assert _leg(Side.SHORT, "3").signed_quantity == Decimal("-3")

    @pytest.mark.parametrize(
        "side,mark,expected",
        [
            (Side.LONG, "110", "10"),
            (Side.LONG, "90", "-10"),
            (Side.SHORT, "110", "-10"),
            (Side.SHORT, "90", "10"),
        ],
    )
    def test_unrealized_pnl(self, side, mark, expected):
        assert _leg(side, "1", "100").unrealized_pnl(Decimal(mark)) == Decimal(expected)

    def test_rejects_a_non_positive_quantity(self):
        with pytest.raises(ValueError, match="quantity must be positive"):
            _leg(qty="0")

    def test_rejects_a_non_positive_entry_price(self):
        with pytest.raises(ValueError, match="entry_price must be positive"):
            _leg(price="0")

    def test_each_leg_gets_its_own_id(self):
        assert _leg().leg_id != _leg().leg_id

    def test_per_leg_invalidation_is_independent(self):
        """A tactical leg can be wrong while the core is right -- the
        thing one shared stop could not express."""
        core = _leg(Side.LONG, purpose=LegPurpose.CORE, invalidation=Decimal("80"))
        tac = _leg(Side.SHORT, purpose=LegPurpose.TACTICAL, invalidation=Decimal("130"))
        assert core.invalidation != tac.invalidation


class TestExposure:
    def test_net_and_gross_diverge_when_hedged(self):
        book = Book()
        book.open(_leg(Side.LONG, "2"))
        book.open(_leg(Side.SHORT, "2"))
        assert book.net(SYM) == Decimal("0")
        assert book.gross(SYM) == Decimal("4"), (
            "a delta-flat book still consumes margin on both sides -- the venue "
            "does not net it, so a risk check reading only `net` would treat "
            "this as free"
        )

    def test_net_by_side_matches_the_venues_hedge_mode_shape(self):
        book = Book()
        book.open(_leg(Side.LONG, "2"))
        book.open(_leg(Side.LONG, "1"))
        book.open(_leg(Side.SHORT, "3"))
        assert book.net_by_side(SYM) == {Side.LONG: Decimal("3"), Side.SHORT: Decimal("3")}

    def test_symbols_are_isolated(self):
        book = Book()
        book.open(_leg(Side.LONG, "2"))
        book.open(_leg(Side.SHORT, "5", symbol="ETH-USDT"))
        assert book.net(SYM) == Decimal("2")
        assert book.net("ETH-USDT") == Decimal("-5")

    def test_empty_book_is_flat_not_an_error(self):
        book = Book()
        assert book.net(SYM) == Decimal("0")
        assert book.gross(SYM) == Decimal("0")
        assert book.unrealized_pnl(SYM, Decimal("100")) == Decimal("0")


class TestClosing:
    def test_closes_the_named_leg_and_leaves_the_others(self):
        book = Book()
        a = book.open(_leg(Side.LONG, "1", "100"))
        b = book.open(_leg(Side.LONG, "1", "110"))
        book.close(a.leg_id, exit_price=Decimal("120"), exit_time=T0)
        assert book.leg(a.leg_id) is None
        assert book.leg(b.leg_id) is not None

    def test_closing_is_by_id_not_by_queue_order(self):
        """FIFO/LIFO would close whichever leg is oldest and defeat the
        entire purpose of naming a leg."""
        book = Book()
        first = book.open(_leg(Side.LONG, "1", "100"))
        second = book.open(_leg(Side.SHORT, "1", "120", purpose=LegPurpose.HEDGE))
        book.close(second.leg_id, exit_price=Decimal("110"), exit_time=T0)
        assert book.leg(first.leg_id) is not None, "the older leg must be untouched"

    def test_partial_close_keeps_the_identity_and_entry(self):
        book = Book()
        leg = book.open(_leg(Side.LONG, "3", "100", LegPurpose.RUNNER))
        book.close(leg.leg_id, exit_price=Decimal("120"), exit_time=T0, quantity=Decimal("1"))
        rest = book.leg(leg.leg_id)
        assert rest is not None
        assert rest.quantity == Decimal("2")
        assert rest.entry_price == Decimal("100"), "a runner keeps its original entry"

    def test_partial_close_realizes_only_the_closed_part(self):
        book = Book()
        leg = book.open(_leg(Side.LONG, "3", "100"))
        r = book.close(leg.leg_id, exit_price=Decimal("120"), exit_time=T0, quantity=Decimal("1"))
        assert r.realized_pnl == Decimal("20")

    @pytest.mark.parametrize("side,exit_price,expected", [
        (Side.LONG, "120", "40"), (Side.LONG, "80", "-40"),
        (Side.SHORT, "80", "40"), (Side.SHORT, "120", "-40"),
    ])
    def test_realized_pnl_sign(self, side, exit_price, expected):
        book = Book()
        leg = book.open(_leg(side, "2", "100"))
        r = book.close(leg.leg_id, exit_price=Decimal(exit_price), exit_time=T0)
        assert r.realized_pnl == Decimal(expected)

    def test_an_unknown_leg_raises(self):
        with pytest.raises(KeyError, match="no open leg"):
            Book().close(uuid4(), exit_price=Decimal("100"), exit_time=T0)

    def test_closing_more_than_the_leg_is_refused_as_a_flip(self):
        book = Book()
        leg = book.open(_leg(Side.LONG, "1"))
        with pytest.raises(ValueError, match="a flip"):
            book.close(leg.leg_id, exit_price=Decimal("100"), exit_time=T0, quantity=Decimal("2"))

    def test_non_positive_close_quantity_is_refused(self):
        book = Book()
        leg = book.open(_leg(Side.LONG, "1"))
        with pytest.raises(ValueError, match="closing quantity must be positive"):
            book.close(leg.leg_id, exit_price=Decimal("100"), exit_time=T0, quantity=Decimal("0"))

    def test_reopening_the_same_id_is_refused(self):
        book = Book()
        leg = _leg()
        book.open(leg)
        with pytest.raises(ValueError, match="already open"):
            book.open(leg)


class TestPurposeReporting:
    def test_splits_realized_pnl_by_what_each_leg_was_for(self):
        """Did the tactical overlay pay for itself, or was the core
        carrying it? The old model could not answer this."""
        book = Book()
        core = book.open(_leg(Side.LONG, "1", "100", LegPurpose.CORE))
        tac = book.open(_leg(Side.SHORT, "1", "120", LegPurpose.TACTICAL))
        book.close(tac.leg_id, exit_price=Decimal("110"), exit_time=T0)   # +10
        book.close(core.leg_id, exit_price=Decimal("90"), exit_time=T0)   # -10
        by = book.realized_pnl_by_purpose(SYM)
        assert by[LegPurpose.TACTICAL] == Decimal("10")
        assert by[LegPurpose.CORE] == Decimal("-10")
        assert book.realized_pnl(SYM) == Decimal("0")

    def test_every_purpose_is_present_even_when_unused(self):
        assert set(Book().realized_pnl_by_purpose()) == set(LegPurpose)

    def test_by_purpose_filters_open_legs(self):
        book = Book()
        book.open(_leg(Side.LONG, purpose=LegPurpose.CORE))
        book.open(_leg(Side.SHORT, purpose=LegPurpose.HEDGE))
        assert len(book.by_purpose(SYM, LegPurpose.CORE)) == 1
        assert len(book.by_purpose(SYM, LegPurpose.HEDGE)) == 1


class TestReconciliation:
    def test_agreement_passes_silently(self):
        book = Book()
        book.open(_leg(Side.LONG, "2"))
        book.open(_leg(Side.SHORT, "1"))
        book.reconcile(SYM, venue_long=Decimal("2"), venue_short=Decimal("1"))

    def test_drift_raises_rather_than_reporting(self):
        """A book that has drifted is already producing wrong sizes.
        Continuing is how duplicate orders and position mismatches
        happen, and both must be zero to pass paper trading."""
        book = Book()
        book.open(_leg(Side.LONG, "2"))
        with pytest.raises(BookReconciliationError, match="LONG: book 2 vs venue 1"):
            book.reconcile(SYM, venue_long=Decimal("1"), venue_short=Decimal("0"))

    def test_reports_every_mismatched_side_not_just_the_first(self):
        book = Book()
        book.open(_leg(Side.LONG, "2"))
        book.open(_leg(Side.SHORT, "2"))
        with pytest.raises(BookReconciliationError) as exc:
            book.reconcile(SYM, venue_long=Decimal("1"), venue_short=Decimal("3"))
        assert "LONG" in str(exc.value) and "SHORT" in str(exc.value)

    def test_tolerance_is_exact_by_default(self):
        book = Book()
        book.open(_leg(Side.LONG, "1"))
        with pytest.raises(BookReconciliationError):
            book.reconcile(SYM, venue_long=Decimal("0.9999"), venue_short=Decimal("0"))

    def test_tolerance_must_be_opted_into(self):
        book = Book()
        book.open(_leg(Side.LONG, "1"))
        book.reconcile(
            SYM, venue_long=Decimal("0.9999"), venue_short=Decimal("0"),
            tolerance=Decimal("0.001"),
        )

    def test_a_hedged_book_reconciles_per_side_not_on_the_net(self):
        """The venue reports two positions in hedge mode. A book that
        only matched the net could hold long 5 / short 4 against a venue
        holding long 1 / short 0 and call it agreement."""
        book = Book()
        book.open(_leg(Side.LONG, "5"))
        book.open(_leg(Side.SHORT, "4"))
        assert book.net(SYM) == Decimal("1")
        with pytest.raises(BookReconciliationError):
            book.reconcile(SYM, venue_long=Decimal("1"), venue_short=Decimal("0"))


class TestImmutability:
    def test_legs_returns_a_tuple_a_caller_cannot_mutate(self):
        book = Book()
        book.open(_leg())
        assert isinstance(book.legs, tuple)

    def test_a_held_reference_is_not_changed_by_a_partial_close(self):
        book = Book()
        leg = book.open(_leg(Side.LONG, "3"))
        held = book.leg(leg.leg_id)
        book.close(leg.leg_id, exit_price=Decimal("110"), exit_time=T0, quantity=Decimal("1"))
        assert held is not None and held.quantity == Decimal("3")
        assert book.leg(leg.leg_id).quantity == Decimal("2")
