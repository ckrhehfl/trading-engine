"""Invented chronological and price-coordinate checks for EH replay paths."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_ed_inputs import InputRefusal, MINUTE, Minute, SessionInputs
from research.intraday_scenario_analogue_path import rebase_analogue_path
from research.intraday_scenario_rollout import _visible


PRIOR = datetime(2031, 2, 3, 0, tzinfo=UTC)
QUERY = datetime(2031, 2, 4, 0, tzinfo=UTC)
SOURCE_AT, DECISION_AT = (opening + 22 * MINUTE for opening in (PRIOR, QUERY))
FOLD = ChronologicalFold(1, (0, 1), (1, 2), ("2031-02-03",), ("2031-02-04",), QUERY)


def minute(at, symbol, price, *, delay=3, state="observed"):
    ohlc = None if state != "observed" else (Decimal(price),) * 4
    return Minute(symbol, at, at + delay * MINUTE, ohlc, None, price_state=state, volume_state="missing")


def source_session(*rows):
    anchor = minute(SOURCE_AT - 3 * MINUTE, "prior", 3)
    return SessionInputs("prior", PRIOR, PRIOR + 390 * MINUTE, {row.start_at: row for row in (anchor, *rows)})


def replay(source, **overrides):
    arguments = {"key": "invented-prior", "weight": Fraction(2, 3), "decision_at": DECISION_AT,
                 "visible_minute": minute(DECISION_AT - 3 * MINUTE, "query", 1), "query_symbol": "query",
                 "query_opening": QUERY, "query_closing": QUERY + 390 * MINUTE, "cap": DECISION_AT + 5 * MINUTE}
    return rebase_analogue_path(source, overrides.pop("source_decision_at", SOURCE_AT),
                               overrides.pop("fold", FOLD), **(arguments | overrides))


def test_rebases_late_bar_with_fixed_context_but_leaves_its_open_unknown():
    old = minute(SOURCE_AT + MINUTE, "prior", 2, delay=5)
    source = source_session(old)
    with localcontext() as context:
        context.prec = 5
        result = replay(source)
    expected = Decimal("0.6666666666666666666666666667")
    at = DECISION_AT + MINUTE
    row = result.session.minutes[at]
    assert result.key == "invented-prior" and result.weight == Fraction(2, 3)
    assert result.session.minutes[DECISION_AT - 3 * MINUTE].ohlc == (Decimal(1),) * 4
    assert (row.symbol, row.start_at, row.available_at, row.ohlc) == ("query", at, at + 5 * MINUTE, (expected,) * 4)
    assert result.modeled_opens[at] is None
    assert _visible(result.session, at, at + 4 * MINUTE) == (None, "minute_available_after_cutoff")
    assert _visible(result.session, at, at + 5 * MINUTE) == (row, None)


def test_missing_unknown_and_out_of_session_prices_keep_branch_weight():
    halted = minute(SOURCE_AT + MINUTE, "prior", None, state="halt")
    after_close = minute(SOURCE_AT + 3 * MINUTE, "prior", 6)
    source = replace(source_session(halted, after_close), closing=SOURCE_AT + 3 * MINUTE)
    result = replay(source)
    assert result.weight == Fraction(2, 3)
    assert result.session.minutes[DECISION_AT + MINUTE].price_state == "halt"
    assert result.modeled_opens[DECISION_AT + MINUTE] is None
    assert DECISION_AT + 2 * MINUTE not in result.session.minutes
    assert DECISION_AT + 3 * MINUTE not in result.modeled_opens


@pytest.mark.parametrize("delay,reason", [(2, "early_source_publication"), (24 * 60, "suffix_not_mature")])
def test_source_suffix_cannot_publish_before_scheduled_lag_or_after_fold_start(delay, reason):
    source = source_session(minute(SOURCE_AT + MINUTE, "prior", 3, delay=delay))
    with pytest.raises(InputRefusal, match=reason):
        replay(source)


def test_query_mark_cannot_come_from_current_or_unpublished_minute():
    for mark in (minute(DECISION_AT, "query", 1), minute(DECISION_AT - 3 * MINUTE, "query", 1, delay=4),
                 minute(DECISION_AT - 3 * MINUTE, "query", 1, delay=2)):
        with pytest.raises(InputRefusal, match="same_clock_query_mark"):
            replay(source_session(), visible_minute=mark)


def test_fold_rejects_same_day_source_and_query_outside_evaluation_dates():
    source = SessionInputs("prior", QUERY, QUERY + 390 * MINUTE, {})
    with pytest.raises(InputRefusal, match="distinct_prior_and_test_sessions"):
        replay(source, source_decision_at=DECISION_AT, fold=replace(FOLD, training_sessions=FOLD.test_sessions))
    with pytest.raises(InputRefusal, match="distinct_prior_and_test_sessions"):
        replay(source_session(), query_opening=QUERY + 1440 * MINUTE, query_closing=QUERY + 1830 * MINUTE)


def test_entire_nominal_suffix_must_mature_even_if_all_future_rows_are_missing():
    prior = datetime(2031, 2, 3, 14, 30, tzinfo=UTC)
    first = datetime(2031, 2, 3, 15, 1, tzinfo=UTC)
    prior_at, at = prior + 27 * MINUTE, first + 22 * MINUTE
    fold = replace(FOLD, first_test_opening=first)
    source = SessionInputs("prior", prior, prior + 29 * MINUTE, {})
    with pytest.raises(InputRefusal, match="suffix_not_mature"):
        replay(source, source_decision_at=prior_at, fold=fold, query_opening=first,
               query_closing=first + 390 * MINUTE, decision_at=at, cap=at + MINUTE)


def test_selected_suffix_reads_stop_at_registered_cap():
    class ReadFence(dict):
        def get(self, at, default=None):
            assert SOURCE_AT - 3 * MINUTE <= at <= SOURCE_AT + 5 * MINUTE
            return super().get(at, default)

    source = source_session(minute(SOURCE_AT + 5 * MINUTE, "prior", 6))
    result = replay(replace(source, minutes=ReadFence(source.minutes)))
    assert result.modeled_opens[DECISION_AT + 5 * MINUTE] == 2
