"""Invented evidence for independent EH action payoffs and their clocks."""

from datetime import UTC, datetime
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_scenario_management_payoff_reference import PayoffPath, score_held_payoffs
from research.intraday_scenario_policy import Values


OPEN = datetime(2031, 2, 3, tzinfo=UTC)


def at(minutes):
    return OPEN + minutes * MINUTE


def path(opens, closes, *, weight=Fraction(1)):
    open_prices = {at(offset): Fraction(price) for offset, price in opens.items()}
    close_prices = {at(offset): Fraction(price) for offset, price in closes.items()}
    return PayoffPath(weight, lambda minute, _: close_prices.get(minute), open_prices.get)


def score(paths, *, cap=15, entered=1, quantity=11, reduced=False, stop=565, mark=590, penalty=Fraction()):
    return score_held_payoffs(paths, at=at(5), cap=at(cap), entered_at=at(entered), quantity=quantity,
                             half_reduced=reduced, stop=Fraction(stop), visible_mark=Fraction(mark),
                             downside_penalty=penalty)


def test_reduction_uses_the_next_open_and_catches_a_stop_due_during_its_fill():
    close_reads = []
    open_reads = []

    def close_at(minute, cutoff):
        close_reads.append((minute, cutoff))
        return Fraction(565) if minute == at(3) else Fraction(591)

    def open_at(execute_at):
        open_reads.append(execute_at)
        return {at(6): Fraction(590), at(7): Fraction(580), at(8): Fraction(570)}[execute_at]

    branch = PayoffPath(Fraction(1), close_at, open_at)
    assert score((branch,)) == Values(at(5), hold=Fraction(-110), reduce_half=Fraction(-120), exit=Fraction())
    assert close_reads == [(at(3), at(6)), (at(3), at(7))]
    assert open_reads == [at(7), at(6), at(8)]


def test_safe_first_check_reuses_liquidation_scan_and_immediate_sale_open():
    close_reads = []
    open_reads = []

    def close_at(minute, cutoff):
        close_reads.append((minute, cutoff))
        return Fraction(590)

    def open_at(execute_at):
        open_reads.append(execute_at)
        return {at(6): Fraction(600), at(12): Fraction(610)}[execute_at]

    branch = PayoffPath(Fraction(1), close_at, open_at)
    assert score((branch,), cap=12) == Values(at(5), hold=Fraction(220), reduce_half=Fraction(170), exit=Fraction(110))
    assert close_reads == [(at(minute), at(minute + 3)) for minute in range(3, 9)]
    assert open_reads == [at(12), at(6)]


def test_applies_downside_penalty_to_each_path_before_weighting_and_omits_second_reduction():
    first = path({6: 120, 8: 90}, {3: 100, 4: 100}, weight=Fraction(1, 4))
    second = path({6: 80, 8: 110}, {3: 100, 4: 100}, weight=Fraction(3, 4))
    assert score((first, second), cap=8, quantity=2, reduced=True, stop=95, mark=100,
                 penalty=Fraction(1, 2)) == Values(at(5), hold=Fraction(15, 2), exit=Fraction(-35))


def test_timed_exit_can_proceed_with_unknown_close_but_late_reduction_consumes_last_decision():
    branch = path({6: 590, 7: 570}, {})
    assert score((branch,), cap=7, reduced=True) == Values(at(5), hold=Fraction(-220), exit=Fraction())
    assert score((branch,), cap=7) is None


def test_earlier_gap_cannot_be_filled_by_a_later_publication_or_dropped_from_path_weight():
    cutoffs = []

    def late_close(minute, cutoff):
        assert minute == at(3)
        cutoffs.append(cutoff)
        return Fraction(565) if cutoff >= at(7) else None

    unsupported = PayoffPath(Fraction(1, 2), late_close, lambda _: pytest.fail("read after unresolved close"))
    complete = path({6: 590, 7: 580}, {3: 565}, weight=Fraction(1, 2))
    assert score((complete, unsupported)) is None
    assert cutoffs == [at(6)]


def test_forced_exit_does_not_read_closes_or_substitute_an_open():
    opens = []

    def next_open(execute_at):
        opens.append(execute_at)
        return Fraction(562)

    closed_only = PayoffPath(Fraction(1), lambda *_: pytest.fail("read beyond forced exit"), next_open)
    assert score((closed_only,), cap=6, mark=600) == Values(at(5), exit=Fraction(-418))
    assert score((closed_only,), mark=565) == Values(at(5), exit=Fraction(-33))
    assert opens == [at(6), at(6)]

    unknown = path({7: 562}, {})
    assert score((unknown,), mark=565) is None


def test_mark_from_before_fill_cannot_force_a_structural_exit():
    branch = path({6: 560, 8: 575}, {4: 565})
    assert score((branch,), entered=4, cap=9, mark=560) == Values(at(5), hold=Fraction(165))


def test_rejects_inexact_quote_and_missing_path_mass():
    branch = PayoffPath(Fraction(1), lambda *_: None, lambda _: 562)
    with pytest.raises(InputRefusal, match="invalid_quote"):
        score((branch,), mark=565)
    with pytest.raises(InputRefusal, match="invalid_weights"):
        score((path({}, {}, weight=Fraction(1, 2)),))
