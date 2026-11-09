from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from research import intraday_candidate_activity as activity
from research import intraday_candidate_adverse as adverse
from research import intraday_candidate_exit as cash_summary
from research import intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_room as room
from research.intraday_context_cost_census_inputs import invented_rows
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


def _prices(session, at, *, op=10000, close=10000, low=None):
    op, close = Decimal(op), Decimal(close)
    low = min(op, close) if low is None else Decimal(low)
    session.minutes[at] = Minute(session.symbol, at, at + 3 * MINUTE, (op, max(op, close), low, close),
                                 None, volume_state="missing")


def _fixture(*, quantity=1, profit_offset=None, loss_offset=None, duration=11):
    row, roster = next((row, roster) for row, roster in invented_rows()
                       if row["symbol"] == "100000" and row["questions"])
    original = row["questions"][0]
    entry = datetime.fromisoformat(original["decision_at"]) + MINUTE
    exit_at = entry + duration * MINUTE
    arm = original["baseline"]
    arm.update(gross=str(100 * quantity), cost=str(33 * quantity), net=str(67 * quantity),
               entry_notional=str(10000 * quantity))
    arm["fill"].update(quantity=quantity, cost=str(33 * quantity))
    arm["holding"]["events"][0].update(at=(exit_at - MINUTE).isoformat(), execute_at=exit_at.isoformat())
    arm["holding"]["events"][1].update(at=exit_at.isoformat(), quantity=quantity)
    arm["holding"]["realized_exits"][0].update(quantity=quantity, entry_basis=str(10000 * quantity),
                                               gross_proceeds=str(10100 * quantity),
                                               allocated_entry_cost=str(33 * quantity))
    row["modeled_opens"] = [[entry.isoformat(), "10000", None], [exit_at.isoformat(), "10100", None]]
    session = SessionInputs(row["symbol"], entry.replace(hour=9, minute=0),
                            entry.replace(hour=15, minute=30), {})
    for offset in range(duration):
        _prices(session, entry + offset * MINUTE)
    _prices(session, exit_at, op=10100, close=10100)
    if loss_offset is not None:
        _prices(session, entry + loss_offset * MINUTE, close=9967)
    if profit_offset is not None:
        _prices(session, entry + profit_offset * MINUTE, close=10066)
    prior = cash_summary.evaluate_coordinate(row, roster, lambda *_: session)
    cash_reference.verify_coordinate(row, roster, session, prior)
    prepared, prior = activity.invented_decision(prior, roster)
    decisions = activity.selections(prepared)
    activity.verify_selections(prepared, decisions)
    gated = room.project(prior, decisions, choice_field="activity_selection")
    room.verify_projection(prior, decisions, gated, choice_field="activity_selection")
    return gated, session, entry


def _evaluate(row, session):
    produced = adverse.evaluate_coordinate(row, session)
    adverse.verify_coordinate(row, deepcopy(session), produced)
    return produced


def _candidate(row):
    return row["questions"][0]["candidate"]


def test_exact_threshold_from_entry_minute_next_open_and_original_integer_quantity():
    row, session, entry = _fixture(quantity=3, loss_offset=0, profit_offset=3)
    frozen = deepcopy(row)
    _prices(session, entry + 4 * MINUTE, op=9910)
    _prices(session, entry + MINUTE, close=9800)
    produced = _evaluate(row, session)
    original, output = frozen["questions"][0], produced["questions"][0]
    assert adverse.needs_session(row)
    assert row == frozen
    assert output["baseline"] == {**original["candidate"], "fill": original["baseline"]["fill"]}
    assert output["activity_selection"] == original["activity_selection"]
    arm = output["candidate"]
    assert (arm["selected_trigger"], arm["trigger_start_at"], arm["trigger_close"]) == (
        "loss33", entry.isoformat(), "9967")
    assert (arm["request_at"], arm["exit_at"]) == ((entry + 3 * MINUTE).isoformat(),
                                                  (entry + 4 * MINUTE).isoformat())
    assert (arm["entry_price"], arm["exit_price"], arm["quantity"], arm["entry_notional"]) == (
        "10000", "9910", 3, "30000")
    assert (arm["gross"], arm["cost"], arm["net"]) == ("-270", "99", "-369")

    summary = cash_summary.summarize([produced])
    cash_reference.verify_summary([produced], summary)
    totals = summary["groups"]["all"]["totals"]
    assert totals["original_modeled_entries"] == 1
    assert totals["paired"]["closed_trades"] == 1
    assert totals["selected_triggers"] == {"loss33": 1}


@pytest.mark.parametrize("exit_type, duration, profit_offset, loss_offset", [
    ("original", 5, None, 1),  # request at entry + 4 ties the original
    ("original", 4, None, 0),  # entry minute ties the original
    ("profit66", 11, 1, 2),   # adverse observation is later than the prior request
])
def test_prior_request_has_priority_and_never_uses_future_close(exit_type, duration, profit_offset, loss_offset):
    row, session, entry = _fixture(duration=duration, profit_offset=profit_offset, loss_offset=loss_offset)
    expected = _candidate(row)
    assert expected["selected_trigger"] == exit_type
    assert _candidate(_evaluate(row, session)) == expected
    if loss_offset:
        del session.minutes[entry + loss_offset * MINUTE]
        assert _candidate(_evaluate(row, session)) == expected


@pytest.mark.parametrize("duration, profit_offset", [(3, None), (4, None), (11, 0)])
def test_empty_trigger_window_keeps_prior_exit_without_session_or_entry_quote(duration, profit_offset):
    row, session, entry = _fixture(duration=duration, profit_offset=profit_offset, loss_offset=0)
    expected = _candidate(row)
    assert not adverse.needs_session(row)
    assert _candidate(_evaluate(row, None)) == expected
    del session.minutes[entry]
    assert _candidate(_evaluate(row, session)) == expected

    _candidate(row)["cost"] = "0"
    with pytest.raises(ValueError, match="cost"):
        adverse.evaluate_coordinate(row, None)
    with pytest.raises(ValueError, match="cost"):
        adverse.verify_coordinate(row, None, row)


def test_below_threshold_intraminute_low_does_not_replace_prior_exit():
    row, session, entry = _fixture()
    _prices(session, entry, close="9967.0001", low=9500)
    assert _candidate(_evaluate(row, session)) == _candidate(row)


def test_first_known_later_crossing_and_improved_next_open():
    row, session, entry = _fixture(loss_offset=2)
    _prices(session, entry + 6 * MINUTE, op=10040)
    arm = _candidate(_evaluate(row, session))
    assert arm["trigger_start_at"] == (entry + 2 * MINUTE).isoformat()
    assert (arm["exit_price"], arm["gross"], arm["net"]) == ("10040", "40", "7")


@pytest.mark.parametrize("fault", ["absent", "conflict", "late"])
def test_missing_earlier_close_does_not_skip_ahead_and_clears_prior_trigger(fault):
    row, session, entry = _fixture(loss_offset=2, profit_offset=3)
    at = entry + MINUTE
    if fault == "absent":
        del session.minutes[at]
    elif fault == "conflict":
        session.minutes[at] = replace(session.minutes[at], price_state="conflict", ohlc=None)
    else:
        session.minutes[at] = replace(session.minutes[at], available_at=at + 4 * MINUTE)
    arm = _candidate(_evaluate(row, session))
    assert (arm["status"], arm["issue"], arm["selected_trigger"]) == ("unresolved", "first_trigger_unknown", "unknown")
    assert all(arm[k] is None for k in ("request_at", "exit_at", "exit_price", "trigger_start_at", "trigger_close",
                                       "gross", "cost", "net", "entry_notional"))
    assert arm["quantity"] == 1


def test_missing_loss_exit_open_retains_trigger_and_clears_payoff():
    row, session, entry = _fixture(loss_offset=0)
    del session.minutes[entry + 4 * MINUTE]
    produced = _evaluate(row, session)
    arm = _candidate(produced)
    assert (arm["status"], arm["issue"], arm["selected_trigger"]) == ("unresolved", "exit_price_unknown", "loss33")
    assert (arm["trigger_close"], arm["exit_at"]) == ("9967", (entry + 4 * MINUTE).isoformat())
    assert all(arm[k] is None for k in ("exit_price", "gross", "cost", "net", "entry_notional"))
    assert produced["questions"][0]["baseline"]["net"] == "67"


def test_missing_session_and_missing_entry_are_explicitly_unresolved():
    row, session, entry = _fixture(loss_offset=0)
    assert _candidate(_evaluate(row, None))["issue"] == "missing_session"
    del session.minutes[entry]
    assert _candidate(_evaluate(row, session))["issue"] == "entry_price_unknown"


@pytest.mark.parametrize("status, choice", [("no_entry", "decline"), ("unresolved", "unresolved"),
                                              ("unresolved", "select"), ("no_fill", "select")])
def test_out_of_scope_outcomes_preserve_prior_support_and_do_not_require_quotes(status, choice):
    row, _, _ = _fixture()
    question = row["questions"][0]
    question["activity_selection"]["state"] = choice
    question["candidate"]["status"] = status
    assert not adverse.needs_session(row)
    assert _candidate(_evaluate(row, None)) == question["candidate"]


@pytest.mark.parametrize("key, value, error", [
    ("cost", "0", "cost"), ("net", "0", "payoff"), ("entry_notional", "1", "basis"),
    ("quantity", True, "quantity"), ("entry_at", "2026-01-27T09:33:30+09:00", "aware minute"),
    ("request_at", "2026-01-27T09:32:00+09:00", "geometry"),
])
def test_corrupt_saved_cost_basis_size_or_clock_is_rejected(key, value, error):
    row, session, _ = _fixture()
    produced = _evaluate(row, session)
    _candidate(row)[key] = value
    with pytest.raises(ValueError, match=error):
        adverse.evaluate_coordinate(row, session)
    with pytest.raises(ValueError, match=error):
        adverse.verify_coordinate(row, session, produced)


def test_mismatched_archive_entry_and_tampered_reconstruction_are_rejected():
    row, session, entry = _fixture(loss_offset=0)
    produced = _evaluate(row, session)
    altered = deepcopy(produced)
    _candidate(altered)["trigger_close"] = "9968"
    with pytest.raises(ValueError, match="coordinate or payoff"):
        adverse.verify_coordinate(row, session, altered)
    _prices(session, entry, op=9999)
    with pytest.raises(ValueError, match="archived adverse entry"):
        adverse.evaluate_coordinate(row, session)
    with pytest.raises(ValueError, match="archived adverse entry"):
        adverse.verify_coordinate(row, session, produced)
