from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_candidate_exit as producer
from research import intraday_candidate_exit_reference as reference
from research.intraday_context_cost_census_inputs import invented_rows
from research.intraday_ed_inputs import Minute, SessionInputs


MINUTE = timedelta(minutes=1)


def _fixture(*, duration=10, quantity=1, exit_price=10100):
    row, roster = deepcopy(next((row, roster) for row, roster in invented_rows()
                               if row["questions"] and row["questions"][0]["baseline"]["status"] == "closed"))
    q = row["questions"][0]
    entry = datetime.fromisoformat(q["decision_at"]) + MINUTE
    exit_at = entry + duration * MINUTE
    arm = q["baseline"]
    arm.update(gross=str((exit_price - 10000) * quantity), cost=str(33 * quantity),
               net=str((exit_price - 10033) * quantity), entry_notional=str(10000 * quantity))
    arm["fill"].update(quantity=quantity, cost=str(33 * quantity))
    arm["holding"]["events"] = [
        {"at": (exit_at - MINUTE).isoformat(), "action": "exit", "execute_at": exit_at.isoformat()},
        {"at": exit_at.isoformat(), "action": "exit", "quantity": quantity,
         "fill_status": "modeled", "open_price": str(exit_price)},
    ]
    arm["holding"]["realized_exits"] = [
        {"entry_basis": str(10000 * quantity), "gross_proceeds": str(exit_price * quantity),
         "allocated_entry_cost": str(33 * quantity), "quantity": quantity, "symbol": row["symbol"]},
    ]
    row["modeled_opens"] = [[entry.isoformat(), "10000", None], [exit_at.isoformat(), str(exit_price), None]]
    minutes = {}
    for offset in range(duration + 1):
        at = entry + offset * MINUTE
        price = Decimal(exit_price if offset == duration else 10000)
        minutes[at] = Minute(row["symbol"], at, at + 3 * MINUTE, (price,) * 4, None, volume_state="missing")
    session = SessionInputs(row["symbol"], entry.replace(hour=9, minute=0),
                            entry.replace(hour=15, minute=30), minutes)
    return row, roster, session, entry


def _prices(session, at, *, op=None, close=None, high=None):
    old = session.minutes[at]
    op = old.ohlc[0] if op is None else Decimal(str(op))
    close = old.ohlc[3] if close is None else Decimal(str(close))
    high = max(op, close) if high is None else Decimal(str(high))
    session.minutes[at] = replace(old, ohlc=(op, high, min(op, close), close))


def _evaluate(row, roster, session):
    output = producer.evaluate_coordinate(row, roster, lambda day, symbol: session)
    reference.verify_coordinate(row, roster, session, output)
    return output


def _arm(output):
    return output["questions"][0]["candidate"]


def test_threshold_equality_uses_next_open_exact_integer_cash_and_keeps_original():
    row, roster, session, entry = _fixture(quantity=3)
    original = deepcopy(row)
    _prices(session, entry, close=10066)
    _prices(session, entry + 4 * MINUTE, op=10075)
    output = _evaluate(row, roster, session)
    arm = _arm(output)
    assert row == original and output["questions"][0]["baseline"] == original["questions"][0]["baseline"]
    assert (arm["selected_trigger"], arm["trigger_close"], arm["quantity"]) == ("profit66", "10066", 3)
    assert arm["request_at"] == (entry + 3 * MINUTE).isoformat()
    assert arm["exit_at"] == (entry + 4 * MINUTE).isoformat()
    assert (arm["gross"], arm["cost"], arm["net"], arm["entry_notional"]) == ("225", "99", "126", "30000")


def test_first_crossing_can_be_late_and_high_is_not_an_execution_or_signal():
    row, roster, session, entry = _fixture(duration=14)
    _prices(session, entry, close=10065, high=20000)
    _prices(session, entry + 5 * MINUTE, close=10066)
    _prices(session, entry + 9 * MINUTE, op=10070)
    arm = _arm(_evaluate(row, roster, session))
    assert arm["trigger_start_at"] == (entry + 5 * MINUTE).isoformat()
    assert arm["exit_price"] == "10070" and arm["net"] == "37"


def test_original_exit_wins_same_clock_without_reading_missing_tie_bar():
    row, roster, session, entry = _fixture(duration=5)
    _prices(session, entry + MINUTE, close=10066)
    assert _arm(_evaluate(row, roster, session))["selected_trigger"] == "original"
    del session.minutes[entry + MINUTE]
    assert _arm(_evaluate(row, roster, session))["net"] == "67"


def test_gap_after_profitable_close_can_realize_a_net_loss():
    row, roster, session, entry = _fixture()
    _prices(session, entry, close=10066)
    _prices(session, entry + 4 * MINUTE, op=9990)
    arm = _arm(_evaluate(row, roster, session))
    assert (arm["gross"], arm["cost"], arm["net"]) == ("-10", "33", "-43")


@pytest.mark.parametrize("missing_kind", ["absent", "conflict", "invalid", "late"])
def test_unknown_earlier_bar_prevents_skipping_ahead_to_profitable_trigger(missing_kind):
    row, roster, session, entry = _fixture()
    at = entry + MINUTE
    if missing_kind == "absent":
        del session.minutes[at]
    elif missing_kind == "conflict":
        session.minutes[at] = replace(session.minutes[at], price_state="conflict", ohlc=None)
    elif missing_kind == "invalid":
        session.minutes[at] = replace(session.minutes[at], ohlc=(Decimal(10000), Decimal(9999), Decimal(9990), Decimal(10000)))
    else:
        session.minutes[at] = replace(session.minutes[at], available_at=at + 4 * MINUTE)
    _prices(session, entry + 2 * MINUTE, close=10100)
    arm = _arm(_evaluate(row, roster, session))
    assert arm["status"] == "unresolved" and arm["issue"] == "first_trigger_unknown"
    assert arm["selected_trigger"] == "unknown" and arm["exit_at"] is None
    assert all(arm[key] is None for key in ("gross", "cost", "net", "entry_notional"))


def test_missing_requested_open_is_unresolved_and_does_not_use_old_profitable_exit():
    row, roster, session, entry = _fixture()
    _prices(session, entry, close=10066)
    del session.minutes[entry + 4 * MINUTE]
    arm = _arm(_evaluate(row, roster, session))
    assert (arm["status"], arm["selected_trigger"], arm["issue"]) == ("unresolved", "profit66", "exit_price_unknown")
    assert arm["net"] is None


@pytest.mark.parametrize("status", ["unresolved", "unsupported", "no_fill", "no_entry"])
def test_original_unknown_and_nonentry_states_are_retained_without_market_reads(status):
    row, roster = next((row, roster) for row, roster in invented_rows()
                       if row["questions"] and row["questions"][0]["status"] == "opportunity"
                       and row["questions"][0]["baseline"]["status"] == status)

    def forbidden(*args):
        pytest.fail("Retained old outcome must not invoke a session reader")

    output = producer.evaluate_coordinate(row, roster, forbidden)
    reference.verify_coordinate(row, roster, None, output)
    assert _arm(output)["status"] == status


def test_no_trigger_keeps_original_exit_and_read_callback_is_coordinate_scoped():
    row, roster, session, _ = _fixture()
    calls = []
    row["questions"].append({**deepcopy(row["questions"][0]), "question_sha256": "another-invented-question"})

    def read(day, symbol):
        calls.append((day, symbol))
        return session

    output = producer.evaluate_coordinate(row, roster, read)
    reference.verify_coordinate(row, roster, session, output)
    assert calls == [(row["session"], row["symbol"])]
    assert all(q["candidate"]["selected_trigger"] == "original" for q in output["questions"])
    assert _arm(output)["net"] == "67"


def test_original_expiry_prevents_a_late_trigger_from_extending_holding():
    row, roster, session, entry = _fixture(duration=3)
    _prices(session, entry, close=10100)
    arm = _arm(_evaluate(row, roster, session))
    assert arm["selected_trigger"] == "original"
    assert arm["exit_at"] == (entry + 3 * MINUTE).isoformat()


@pytest.mark.parametrize("at_offset, message", [(0, "entry"), (10, "original exit")])
def test_observed_archived_prices_must_match_original_saved_prices(at_offset, message):
    row, roster, session, entry = _fixture()
    _prices(session, entry + at_offset * MINUTE, op=9999)
    with pytest.raises(ValueError, match=message):
        producer.evaluate_coordinate(row, roster, lambda *args: session)


def test_reference_detects_tampered_trigger_cash_and_original_row():
    row, roster, session, entry = _fixture()
    _prices(session, entry, close=10066)
    output = _evaluate(row, roster, session)
    for key, value in (("net", "999"), ("exit_at", (entry + 3 * MINUTE).isoformat()),
                       ("trigger_close", "10067")):
        altered = deepcopy(output)
        _arm(altered)[key] = value
        with pytest.raises(ValueError, match="coordinate or payoff"):
            reference.verify_coordinate(row, roster, session, altered)
    altered = deepcopy(output)
    altered["questions"][0]["baseline"]["net"] = "999"
    with pytest.raises(ValueError, match="coordinate or payoff"):
        reference.verify_coordinate(row, roster, session, altered)


def test_summary_retains_zero_trade_weeks_and_common_known_denominator():
    row, roster, session, entry = _fixture()
    _prices(session, entry, close=10066)
    _prices(session, entry + 4 * MINUTE, op=10070)
    first = _evaluate(row, roster, session)
    row2, roster2, session2, entry2 = _fixture(exit_price=11000)
    row2["symbol"] = roster2["symbol"] = "another-stock"
    row2["questions"][0]["episode_origin_context"] = "B"
    row2["questions"][0]["baseline"]["holding"]["realized_exits"][0]["symbol"] = "another-stock"
    session2 = replace(session2, symbol="another-stock", minutes={at: replace(m, symbol="another-stock")
                                                                  for at, m in session2.minutes.items()})
    del session2.minutes[entry2 + MINUTE]
    second = _evaluate(row2, roster2, session2)
    empty = deepcopy(first)
    empty["session"] = empty["roster"]["session"] = "2026-02-09"
    empty["questions"] = []
    empty["modeled_opens"] = []
    rows = [first, second, empty]
    report = producer.summarize(rows)
    reference.verify_summary(rows, report)
    total = report["groups"]["all"]["totals"]
    assert total["baseline"]["closed_trades"] == 2 and total["candidate"]["closed_trades"] == 1
    assert total["newly_unresolved"] == 1 and total["paired"]["closed_trades"] == 1
    assert total["paired"]["baseline"]["net_cash"] == "67"
    assert total["paired"]["delta_net_cash"] == "-30"
    assert Fraction(total["paired"]["mean_delta_net_return"]) == Fraction(-30, 10000)
    assert report["groups"]["all"]["weekly"]["2026-W07"]["opportunities"] == 0
    assert report["groups"]["other"]["weekly"]["2026-W07"]["candidate"]["net_cash"] == "0"
    assert set(report["folds"]) == {"1", "2", "3", "4", "5"}
    assert report["session_concentration"]["candidate"]["net_cash_without_largest_positive_session"] == "0"
    damaged = deepcopy(report)
    damaged["groups"]["all"]["totals"]["paired"]["closed_trades"] = 2
    with pytest.raises(ValueError, match="published summary"):
        reference.verify_summary(rows, damaged)


def test_empty_summary_has_no_invented_return_and_verifies():
    report = producer.summarize([])
    reference.verify_summary([], report)
    assert report["groups"]["all"]["totals"]["candidate"]["mean_net_return"] is None
    assert report["groups"]["all"]["totals"]["candidate"]["profit_factor"] is None
