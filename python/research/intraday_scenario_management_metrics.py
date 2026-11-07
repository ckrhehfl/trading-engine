"""Exact EH event accounting for paired management outcomes."""

from dataclasses import dataclass
from fractions import Fraction

from research.intraday_ed_inputs import MINUTE, _require
from research.intraday_scenario_management_compare import ManagementComparison


@dataclass(frozen=True)
class ArmMetrics:
    name: str
    status: str
    confirmed_entries: int
    gross_pnl: Fraction | None
    net_pnl: Fraction | None
    paid_cost: Fraction | None
    entry_basis_exposure: Fraction | None
    gross_turnover: Fraction | None


def management_metrics(result: ManagementComparison) -> tuple[ArmMetrics, ...]:
    """Keep event exposure and costs separate from unobserved terminal NAV.

    Exposure is entry-price notional integrated over minutes as a fraction of
    the initial nominal capital over the common original event window.
    """
    initial = result.initial
    arms = [("fixed", result.fixed.status, result.fixed.episode, result.fixed.events),
            ("dynamic", result.dynamic.status, result.dynamic.episode, result.dynamic.events)]
    if result.reentry is not None:
        second = result.reentry
        later = second.holding.events if second.holding is not None else ()
        arms.append(("reentry", second.status, second.episode, (*result.dynamic.events, *second.events, *later)))

    lot, = initial.book.lots
    original_basis = lot.entry_price * lot.quantity
    duration = (initial.end_at - initial.start_at) // MINUTE
    denominator = initial.book.initial_capital * duration
    _require(denominator > 0, "invalid_EH_exposure_denominator")
    metrics = []
    for name, status, episode, events in arms:
        fills = tuple(row for row in events if row.fill_status == "modeled")
        entries = 1 + sum(row.action == "reenter" for row in fills)
        if status == "unresolved":
            metrics.append(ArmMetrics(name, status, entries, None, None, None, None, None))
            continue

        _require(not episode.book.lots and not episode.book.indeterminate and episode.end_at == initial.end_at,
                 "EH_terminal_management_ledger_required")
        at, price, quantity = initial.entered_at, lot.entry_price, lot.quantity
        area, turnover = Fraction(), original_basis
        for fill in fills:
            _require(at < fill.at <= initial.end_at and fill.open_price is not None and fill.quantity is not None
                     and fill.quantity > 0, "invalid_EH_management_fill")
            area += price * quantity * ((fill.at - at) // MINUTE)
            turnover += fill.open_price * fill.quantity
            at = fill.at
            if fill.action == "reenter":
                _require(quantity == 0, "EH_reentry_requires_flat_exposure")
                price, quantity = fill.open_price, fill.quantity
            else:
                _require(fill.action in ("exit", "reduce_half") and fill.quantity <= quantity,
                         "EH_management_sale_exceeds_exposure")
                quantity -= fill.quantity
        _require(quantity == 0, "EH_management_fill_trace_not_flat")
        legs = tuple(row for row in episode.book.realized_exits if row.lot_id >= initial.first_lot_id)
        gross = sum((row.gross_pnl for row in legs), Fraction())
        cost = sum((row.allocated_entry_cost for row in legs), Fraction())
        net = gross - cost
        _require(initial.book.cash + original_basis + lot.original_entry_cost + net == episode.book.cash,
                 "EH_management_pnl_cash_differs")
        metrics.append(ArmMetrics(name, status, entries, gross, net, cost, area / denominator, turnover))
    return tuple(metrics)
