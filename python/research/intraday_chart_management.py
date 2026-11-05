"""EF holding decisions from predictions available before the modeled OPEN."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Literal

from research.intraday_ed_inputs import MINUTE, InputRefusal


UPDATE_MINUTES = (5, 10, 15)
FIXED_EXIT_MINUTES = 30


@dataclass(frozen=True)
class HoldingDecision:
    decision_at: datetime
    gross15_pct: float | None
    action: Literal["hold", "reduce_half", "exit"]
    execute_at: datetime | None
    reason: str


def holding_decision(
    entry_at: datetime,
    history: tuple[HoldingDecision, ...],
    decision_at: datetime,
    gross15_pct: float | None,
) -> HoldingDecision:
    """Return the next signal only. The caller adjudicates any resulting fill.

    Unknown forecasts never authorize a sale. Once the update sequence ends,
    the remaining lot uses the same original fixed exit as the control.
    """
    if not isinstance(entry_at, datetime) or entry_at.utcoffset() is None:
        raise InputRefusal("aware_holding_entry_required")
    if len(history) >= len(UPDATE_MINUTES) or any(item.action == "exit" for item in history):
        raise InputRefusal("holding_updates_already_finished")
    offset = UPDATE_MINUTES[len(history)]
    execution = entry_at + offset * MINUTE
    if (not isinstance(decision_at, datetime) or decision_at.utcoffset() is None
            or decision_at != execution - MINUTE):
        raise InputRefusal("next_holding_decision_clock_required")
    if gross15_pct is None:
        return HoldingDecision(decision_at, None, "hold", None, "forecast_unavailable")
    if type(gross15_pct) is not float or not isfinite(gross15_pct):
        raise InputRefusal("finite_holding_forecast_required")
    if gross15_pct > 0:
        return HoldingDecision(decision_at, gross15_pct, "hold", None, "positive_forecast")
    previous = history[-1].gross15_pct if history else None
    if previous is not None and previous <= 0:
        return HoldingDecision(decision_at, gross15_pct, "exit", execution, "consecutive_nonpositive")
    if not any(item.action == "reduce_half" for item in history):
        return HoldingDecision(decision_at, gross15_pct, "reduce_half", execution, "first_nonpositive")
    return HoldingDecision(decision_at, gross15_pct, "hold", None, "reduction_already_requested")
