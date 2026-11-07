"""Invented EH flows with hand-written utilities and modeled open prices.

The scores below are test inputs. They are neither forecasts nor estimates
from the later path labels.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

from research import intraday_chart_cash as cash
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_scenario_labels import PathQuestion, label_path
from research.intraday_scenario_policy import (
    EntryBounds, Values, apply_modeled_open, decide, start_episode,
)
from research.intraday_scenario_state import (
    PriceEvidence, previous_session_high, reentry_evidence, scan_previous_high,
)


OPENING = datetime(2099, 2, 3, 0, tzinfo=UTC)
SYMBOL = "invented-EH-integration"


def at(offset):
    return OPENING + offset * MINUTE


def minute(offset, prices):
    return Minute(SYMBOL, at(offset), at(offset + 3), tuple(map(Decimal, prices)),
                  None, volume_state="missing")


def session(changes=None):
    prices = {
        offset: (94, 99, 89, 94) if offset < 15 else
        (94, 101, 93, 100) if offset < 20 else (100, 101, 99, 100)
        for offset in range(120)
    }
    prices.update(changes or {})
    return SessionInputs(SYMBOL, at(0), at(120),
                         {at(offset): minute(offset, value) for offset, value in prices.items()})


class VisiblePrefix(dict):
    def __init__(self, rows, cutoff):
        super().__init__(rows)
        self.cutoff = cutoff

    def get(self, key, default=None):
        assert key + 3 * MINUTE <= self.cutoff, "decision inspected an unpublished minute"
        return super().get(key, default)


def candidate(current):
    previous_opening = OPENING - timedelta(days=1)
    prior = SessionInputs(
        SYMBOL, previous_opening, previous_opening + 5 * MINUTE,
        {previous_opening + index * MINUTE: Minute(
            SYMBOL, previous_opening + index * MINUTE, previous_opening + (index + 3) * MINUTE,
            tuple(map(Decimal, (95, 100, 90, 95))), None, volume_state="missing",
        ) for index in range(5)},
    )
    level = previous_session_high(
        current, prior, expected_previous_opening=previous_opening,
        previous_evidence=PriceEvidence("invented-prior-receipt", "invented-unit", "invented-basis"),
        current_evidence=PriceEvidence("invented-current-receipt", "invented-unit", "invented-basis"),
        decision_at=at(0),
    )
    result = scan_previous_high(current, level, through=at(22))
    assert result.status == "event" and result.event.kind == "prepared"
    return result.event


def initial_request(current):
    prefix = replace(current, minutes=VisiblePrefix(current.minutes, at(22)))
    event = candidate(prefix)
    stop, target = event.prepared_bounds
    question = PathQuestion(at(22), at(52), event.level.price, event.band, target, stop)
    label = label_path(question, event.origin.close, prefix, cutoff_at=at(22))
    assert label.status == "pending" and label.path is None
    episode = start_episode(cash.new_book("invented-EH-integration"), SYMBOL, event.origin.available_at,
                            event.expires_at, risk_budget=Fraction(100), cost_bps=33)
    request = decide(
        episode, at(22), visible_minute=current.minutes[at(19)],
        bounds=EntryBounds(stop, Fraction(101)),
        values=Values(at(22), wait=Fraction(1), enter=Fraction(2)),
    )
    assert request.action == "enter"
    assert request.episode.pending.execute_at == at(23)
    assert request.episode.pending.quantity == 15
    return event, question, request


def test_identical_observations_choose_same_action_before_distinct_future_labels():
    up = session({24: (100, 103, 99, 102)})
    down = session({24: (100, 101, 97, 97)})
    up_event, up_question, up_request = initial_request(up)
    down_event, down_question, down_request = initial_request(down)
    assert up_event == down_event
    assert up_question.question_id == down_question.question_id
    assert up_request == down_request

    prefixes, actions = [], []
    for current, request in ((up, up_request), (down, down_request)):
        held, fill = apply_modeled_open(request.episode, at(23), Fraction(current.minutes[at(23)].ohlc[0]))
        assert fill.filled and fill.quantity == 15
        prefix = replace(current, minutes=VisiblePrefix(current.minutes, at(25)))
        prefixes.append(label_path(up_question, up_event.origin.close, prefix, cutoff_at=at(25)))
        actions.append(decide(held, at(25), visible_minute=current.minutes[at(22)],
                              values=Values(at(25), hold=Fraction(1), exit=Fraction())))
    assert prefixes[0] == prefixes[1]
    assert prefixes[0].status == "pending"
    assert actions[0] == actions[1] and actions[0].action == "hold"

    outcomes = [label_path(up_question, up_event.origin.close, current, cutoff_at=at(27))
                for current in (up, down)]
    assert [result.path for result in outcomes] == ["P1", "P3"]
    assert [result.mature_at for result in outcomes] == [at(27), at(27)]
    assert up_request.episode.pending == down_request.episode.pending


def test_prepared_entry_partial_exit_and_fresh_reentry_keep_one_original_deadline():
    changes = {29: (100, 101, 97, 97), 33: (96, 97, 95, 96)}
    changes.update({offset: (100, 106, 100, 104) for offset in range(35, 40)})
    changes.update({offset: (104, 105, 100, 103) for offset in range(40, 45)})
    changes.update({offset: (103, 107, 102, 106) for offset in range(45, 50)})
    changes.update({offset: (106, 108, 105, 107) for offset in range(50, 82)})
    changes[82] = (110, 111, 109, 110)
    current = session(changes)
    event, question, request = initial_request(current)
    assert event.expires_at == at(82) and question.end_at == at(52)

    held, purchase = apply_modeled_open(request.episode, at(23), Fraction(current.minutes[at(23)].ohlc[0]))
    assert purchase.filled and held.stop == question.stop == 98
    assert purchase.quantity == 15 and held.book.lots[0].quantity == 15
    assert label_path(question, event.origin.close, current, cutoff_at=at(27)).status == "pending"
    half = decide(held, at(27), visible_minute=current.minutes[at(24)],
                  values=Values(at(27), hold=Fraction(-1), reduce_half=Fraction(1), exit=Fraction()))
    assert half.action == "reduce_half" and half.episode.pending.quantity == 7
    assert half.episode.book == held.book
    held, partial_fill = apply_modeled_open(half.episode, at(28), Fraction(current.minutes[at(28)].ohlc[0]))
    assert partial_fill.filled and held.book.lots[0].quantity == 8
    assert held.stop == 98

    stopped_label = label_path(question, event.origin.close, current, cutoff_at=at(32))
    assert stopped_label.status == "resolved" and stopped_label.path == "P3"
    assert stopped_label.mature_at == at(32)
    forced = decide(held, at(32), visible_minute=current.minutes[at(29)],
                    values=Values(at(32), hold=Fraction(100), exit=Fraction()))
    assert forced.reason == "structural_close_failure" and forced.episode.pending.quantity == 8
    assert forced.episode.book.lots[0].quantity == 8
    flat, final_fill = apply_modeled_open(forced.episode, at(33), Fraction(current.minutes[at(33)].ohlc[0]))
    assert final_fill.filled and flat.closed_at == at(33) and not flat.book.lots
    assert flat.book.realized_exits[-1].gross_proceeds == 8 * 96
    assert flat.book.realized_gross == -32
    assert flat.book.realized_net == -32 - purchase.assumed_cost

    pending = reentry_evidence(current, event, exit_confirmed_at=flat.closed_at,
                               route="below_level", through=at(47))
    assert pending.status == "pending" and pending.confirmation is None
    waiting = decide(flat, at(47), visible_minute=current.minutes[at(44)],
                     bounds=EntryBounds(Fraction(99), Fraction(107)),
                     values=Values(at(47), wait=Fraction(), enter=Fraction(100)))
    assert waiting.reason == "fresh_reset_required"
    assert waiting.episode.book == flat.book
    ready = reentry_evidence(current, event, exit_confirmed_at=flat.closed_at,
                             route="below_level", through=at(52))
    assert ready.status == "ready" and ready.confirmation.available_at == at(52)
    assert [bar.start_at for bar in ready.context] == [at(35), at(40)]
    assert all(bar.start_at >= flat.closed_at for bar in (*ready.context, ready.confirmation))

    reset_stop = min(Fraction(ready.context[-1].low), event.level.price - event.band)
    # This second target is an invented test coordinate, not an observed obstacle.
    next_question = PathQuestion(at(52), event.expires_at, event.level.price, event.band, 112, reset_stop)
    assert next_question.question_id != question.question_id
    assert label_path(next_question, ready.confirmation.close, current, cutoff_at=at(52)).status == "pending"
    again = decide(waiting.episode, at(52), visible_minute=current.minutes[at(49)],
                   bounds=EntryBounds(reset_stop, Fraction(107)), reset_at=ready.confirmation.available_at,
                   values=Values(at(52), wait=Fraction(), enter=Fraction(2)))
    assert again.action == "reenter" and again.episode.pending.quantity == 7
    assert again.episode.end_at == request.episode.end_at == event.expires_at
    held, second_purchase = apply_modeled_open(again.episode, at(53), Fraction(current.minutes[at(53)].ohlc[0]))
    assert second_purchase.filled and held.attempts == 2
    assert held.book.lots[0].lot_id != request.episode.first_lot_id
    assert held.book.realized_exits == flat.book.realized_exits

    close = decide(held, at(81))
    assert close.reason == "fixed_episode_end" and close.episode.pending.execute_at == at(82)
    finished, _ = apply_modeled_open(close.episode, at(82), Fraction(current.minutes[at(82)].ohlc[0]))
    assert not finished.book.lots and finished.attempts == 2
    assert finished.book.cash == cash.INITIAL_CAPITAL + finished.book.realized_net
    assert decide(finished, at(83)).action == "end"
