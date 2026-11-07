"""Independently reconcile EH management economics from verified saved fills."""

from datetime import datetime, timedelta
from fractions import Fraction

from research.intraday_campaign_cash_book import INITIAL_CAPITAL
from research.intraday_ed_inputs import MINUTE, _clock, _require
from research.intraday_scenario_management_metrics import ArmMetrics
from research.intraday_scenario_snapshot import _read_time


def _arm_metrics(name: str, status: str, trades: list, entered_at: datetime, initial_price: Fraction,
                 initial_units: int, duration: int, rate: Fraction) -> ArmMetrics:
    at, price, units = entered_at, initial_price, initial_units
    bought = price * units
    sold = exposure = Fraction()
    entries = 1
    for action, _, executed_at, _, fill_status, filled, raw_price, _ in trades:
        if fill_status != "modeled":
            continue
        executed = _read_time(executed_at)
        elapsed = executed - at
        _require(elapsed > timedelta(0) and elapsed % MINUTE == timedelta(0)
                 and type(filled) is int and filled > 0, "eh_saved_cash_fill_differs")
        exposure += price * units * (elapsed // MINUTE)
        at = executed
        fill_price = Fraction(raw_price)
        _require(fill_price > 0, "eh_saved_cash_price_differs")
        if action == "reenter":
            _require(name == "reentry" and units == 0 and entries == 1, "eh_saved_cash_reentry_differs")
            price, units = fill_price, filled
            bought += fill_price * filled
            entries += 1
        else:
            _require(action in ("reduce_half", "exit") and filled <= units, "eh_saved_cash_inventory_differs")
            sold += fill_price * filled
            units -= filled

    if status == "unresolved":
        return ArmMetrics(name, status, entries, None, None, None, None, None)
    _require(units == 0, "eh_saved_cash_known_arm_requires_flat_inventory")
    gross = sold - bought
    cost = bought * rate
    return ArmMetrics(name, status, entries, gross, gross - cost, cost,
                      exposure / (INITIAL_CAPITAL * duration), bought + sold)


def saved_management_metrics(
    row: dict, *, event_start: datetime | None, cost_bps: int,
) -> tuple[Fraction | None, tuple[ArmMetrics, ...]]:
    """Read a structurally verified trace row using its authenticated event start."""
    _require(type(cost_bps) is int and cost_bps in (33, 43), "eh_saved_cash_cost_profile_differs")
    if row["status"] != "confirmed":
        return None, ()
    _clock(event_start)
    initial = row["initial"]["trade"]
    entered_at = _read_time(initial[2])
    expiry = _read_time(row["event_expiry"])
    window = expiry - event_start
    _require(event_start < entered_at < expiry and window % MINUTE == timedelta(0),
             "eh_saved_cash_event_window_differs")
    duration = window // MINUTE
    initial_price, initial_units = Fraction(initial[6]), initial[5]
    _require(initial_price > 0 and type(initial_units) is int and initial_units > 0,
             "eh_saved_cash_initial_fill_differs")

    fixed, dynamic = row["arms"]["fixed"], row["arms"]["dynamic"]
    arms = [("fixed", *fixed), ("dynamic", *dynamic)]
    if "fresh_increment" in row["arms"]:
        status, increment = row["arms"]["fresh_increment"]
        arms.append(("reentry", status, dynamic[1] + increment))
    rate = Fraction(cost_bps, 10_000)
    metrics = tuple(_arm_metrics(name, status, trades, entered_at, initial_price, initial_units, duration, rate)
                    for name, status, trades in arms)
    return initial_price * initial_units, metrics
