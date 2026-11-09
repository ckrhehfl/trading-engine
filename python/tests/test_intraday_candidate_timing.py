from copy import deepcopy
from dataclasses import replace

import pytest

from research import intraday_candidate_exit as exit_rule
from research import intraday_candidate_exit_reference as reference
from research import intraday_candidate_timing as timing
from research.intraday_ed_inputs import MINUTE
from test_intraday_candidate_adverse import _fixture, _prices


def _evaluate(row, session):
    produced = timing.evaluate_coordinate(row, session)
    timing.verify_coordinate(row, deepcopy(session), produced)
    return produced


def test_timer_uses_only_next_open_with_exact_quantity_cost_and_negative_payoff():
    row, session, entry = _fixture(quantity=3, profit_offset=7, duration=14)
    saved = deepcopy(row)
    source = saved["questions"][0]
    assert source["candidate"]["selected_trigger"] == "profit66"
    assert timing.needs_session(row)
    assert timing.needs_reference_session(row)
    _prices(session, entry + 9 * MINUTE, op=10200)
    _prices(session, entry + 10 * MINUTE, op="9989.5", close=10100)
    produced = _evaluate(row, session)
    question = produced["questions"][0]
    assert row == saved
    assert question["baseline"] == {**source["candidate"], "fill": source["baseline"]["fill"]}
    assert question["activity_selection"] == source["activity_selection"]
    arm = question["candidate"]
    assert (arm["selected_trigger"], arm["request_at"], arm["exit_at"]) == (
        "timer10", (entry + 9 * MINUTE).isoformat(), (entry + 10 * MINUTE).isoformat())
    assert arm["trigger_start_at"] is arm["trigger_close"] is None
    assert (arm["entry_at"], arm["entry_price"], arm["quantity"], arm["entry_notional"]) == (
        entry.isoformat(), "10000", 3, "30000")
    assert (arm["exit_price"], arm["gross"], arm["cost"], arm["net"]) == ("19979/2", "-63/2", "99", "-261/2")
    assert question["exit_capacity"] == {
        "earlier_opens": 7, "missing_opens": 0, "best_open": "10200", "best_net_bps": "167",
    }

    summary = exit_rule.summarize([produced])
    reference.verify_summary([produced], summary)
    assert summary["groups"]["all"]["totals"]["selected_triggers"] == {"timer10": 1}


@pytest.mark.parametrize("duration, profit_offset", [(10, None), (13, 6), (9, None), (13, 5), (5, None)])
def test_prior_request_wins_tie_or_earlier_even_with_missing_diagnostic_quotes(duration, profit_offset):
    row, _, _ = _fixture(duration=duration, profit_offset=profit_offset)
    source = row["questions"][0]["candidate"]
    assert timing.needs_session(row)
    assert timing.needs_reference_session(row)
    output = _evaluate(row, None)["questions"][0]
    assert output["candidate"] == source
    earlier = (duration if profit_offset is None else profit_offset + 4) - 4
    assert output["exit_capacity"] == {
        "earlier_opens": earlier, "missing_opens": earlier, "best_open": None, "best_net_bps": None,
    }


@pytest.mark.parametrize("duration", [3, 4])
def test_empty_earlier_open_interval_uses_saved_control_without_session(duration):
    row, _, _ = _fixture(duration=duration)
    assert not timing.needs_session(row)
    assert not timing.needs_reference_session(row)
    assert _evaluate(row, None)["questions"][0]["exit_capacity"] == {
        "earlier_opens": 0, "missing_opens": 0, "best_open": "10100", "best_net_bps": "67",
    }


def test_capacity_excludes_opens_before_start_and_after_control_and_uses_saved_control_price():
    row, session, entry = _fixture(duration=11)
    _prices(session, entry + 3 * MINUTE, op=11000)
    _prices(session, entry + 4 * MINUTE, op=10090)
    _prices(session, entry + 11 * MINUTE, op=11000)
    _prices(session, entry + 12 * MINUTE, op=12000)
    output = _evaluate(row, session)["questions"][0]
    assert output["exit_capacity"] == {
        "earlier_opens": 7, "missing_opens": 0, "best_open": "10100", "best_net_bps": "67",
    }

    _prices(session, entry + 4 * MINUTE, op=10110)
    assert _evaluate(row, session)["questions"][0]["exit_capacity"]["best_open"] == "10110"


def test_earlier_gaps_and_unavailable_entry_quote_do_not_change_timer_outcome():
    row, session, entry = _fixture(duration=11)
    for offset in (0, 4, 9):
        del session.minutes[entry + offset * MINUTE]
    _prices(session, entry + 10 * MINUTE, op=10040)
    output = _evaluate(row, session)["questions"][0]
    assert (output["candidate"]["status"], output["candidate"]["net"]) == ("closed", "7")
    assert output["exit_capacity"] == {
        "earlier_opens": 7, "missing_opens": 2, "best_open": None, "best_net_bps": None,
    }


@pytest.mark.parametrize("fault", ["missing", "late", "conflict", "session"])
def test_unknown_timer_open_retains_request_and_never_uses_later_open(fault):
    row, session, entry = _fixture(duration=11)
    execution = entry + 10 * MINUTE
    if fault == "missing":
        del session.minutes[execution]
    elif fault == "late":
        session.minutes[execution] = replace(session.minutes[execution], available_at=execution + 4 * MINUTE)
    elif fault == "conflict":
        session.minutes[execution] = replace(session.minutes[execution], price_state="conflict", ohlc=None)
    else:
        session = None
    output = _evaluate(row, session)["questions"][0]
    arm = output["candidate"]
    assert (arm["status"], arm["issue"], arm["selected_trigger"]) == ("unresolved", "exit_price_unknown", "timer10")
    assert (arm["request_at"], arm["exit_at"], arm["quantity"]) == (
        (execution - MINUTE).isoformat(), execution.isoformat(), 1)
    assert all(arm[key] is None for key in ("exit_price", "gross", "cost", "net", "entry_notional"))
    assert output["exit_capacity"]["missing_opens"] == (7 if session is None else 1)
    assert output["baseline"]["net"] == "67"


@pytest.mark.parametrize("question_status, status, choice", [
    ("opportunity", "no_entry", "decline"), ("opportunity", "unresolved", "select"),
    ("opportunity", "no_fill", "select"), ("unsupported", "closed", "select"),
])
def test_noneligible_questions_keep_previous_outcome_without_capacity(question_status, status, choice):
    row, _, _ = _fixture()
    source = row["questions"][0]
    source["status"] = question_status
    source["activity_selection"]["state"] = choice
    source["candidate"]["status"] = status
    assert not timing.needs_session(row)
    assert not timing.needs_reference_session(row)
    output = _evaluate(row, None)["questions"][0]
    assert output["candidate"] == source["candidate"]
    assert output["exit_capacity"] is None


def test_reference_session_admission_stays_independent_of_producer_support_and_cutoff(monkeypatch):
    row, _, _ = _fixture(duration=5)
    monkeypatch.setattr(timing, "FIRST_EARLIER_OPEN", 6 * MINUTE)
    assert not timing.needs_session(row)
    assert timing.needs_reference_session(row)

    monkeypatch.setattr(timing.adverse, "_eligible", lambda question: False)
    assert timing.needs_reference_session(row)


@pytest.mark.parametrize("section, field, value", [("candidate", "net", "1"), ("exit_capacity", "best_net_bps", "1")])
def test_independent_reconstruction_rejects_changed_timer_cash_or_capacity(section, field, value):
    row, session, _ = _fixture()
    produced = _evaluate(row, session)
    produced["questions"][0][section][field] = value
    with pytest.raises(ValueError, match="timing coordinate, capacity or payoff differs"):
        timing.verify_coordinate(row, session, produced)
