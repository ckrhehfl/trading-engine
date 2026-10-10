"""Pure descriptive paired timing reports over existing synthetic results."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import math
from statistics import stdev

from research.activity_book import ActivityBook
from research.activity_partition_selection import PartitionSelection
from research.activity_replay import SyntheticReplay


def _require(condition: bool, message: str) -> None:
    """Reject contradictory results without repairing or rerunning them."""
    if not condition:
        raise ValueError(message)


def _arm(result: SyntheticReplay, selections: tuple[PartitionSelection, ...], initial: Decimal) -> dict:
    """Validate complete targets and derive Decimal paths before float dispersion."""
    _require(isinstance(result, SyntheticReplay) and type(result.books) is tuple and bool(result.books)
             and all(isinstance(book, ActivityBook) for book in result.books), "nonempty immutable replay books required")
    days = tuple(book.as_of for book in result.books)
    _require(all(type(day) is date for day in days) and days == tuple(sorted(set(days))),
             "session books must have unique increasing dates")
    _require(type(selections) is tuple and all(isinstance(row, PartitionSelection) for row in selections),
             "explicit immutable partition selections required")
    formations = tuple(row.formation_on for row in selections)
    _require(formations == tuple(sorted(set(formations)))
             and all(row.decision_on in days for row in selections), "ordered formation/decision coordinates required")
    _require(type(result.selections) is tuple and len(result.selections) == len(selections),
             "complete actual target declarations required")
    for target, selection in zip(result.selections, selections):
        _require(type(target) is tuple and len(target) == 2 and target[0] == selection.formation_on
                 and type(target[1]) is tuple and all(type(code) is str and bool(code) and code == code.strip() for code in target[1])
                 and len(target[1]) == len(set(target[1])) and set(target[1]) <= set(selection.eligible_codes),
                 "actual targets must match each declared formation and eligible pool")
    _require(type(result.diagnostics) is tuple, "immutable replay diagnostics required")
    diagnostics = {}
    for row in result.diagnostics:
        _require(type(row) is tuple and len(row) == 2 and type(row[0]) is str and bool(row[0])
                 and row[0] not in diagnostics and type(row[1]) is int and row[1] >= 0,
                 "unique nonnegative diagnostic counts required")
        diagnostics[row[0]] = row[1]
    # PartitionSelection requires explicit timing: every target is filled or
    # increments unfilled_entries, including held/capacity/quote/cash failures.
    _require(diagnostics.get("entries", 0) + diagnostics.get("unfilled_entries", 0)
             == sum(len(codes) for _, codes in result.selections),
             "filled/unfilled entry counts must account for every actual target")
    _require(type(result.closed_trades) is int and result.closed_trades >= 0, "ordinary closed-trade count required")
    previous = peak = initial
    maximum_drawdown = Decimal(0)
    returns, path = [], []
    for book in result.books:
        nav = book.nav
        _require(isinstance(nav, Decimal) and nav.is_finite() and nav >= 0, "finite nonnegative Decimal NAV required")
        _require(previous != 0 or nav == 0, "NAV cannot recover from zero without modeled external cash flow")
        session_return = Decimal(0) if previous == 0 else nav / previous - 1
        returns.append(session_return)
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1 - nav / peak)
        path.append({"date": book.as_of.isoformat(), "nav": str(nav),
                     "nav_ratio": str(nav / initial), "session_return": str(session_return)})
        previous = nav
    dispersion = None
    if len(returns) >= 2:
        statistical_returns = [float(value) for value in returns]
        _require(all(math.isfinite(value) for value in statistical_returns), "session returns exceed finite statistical range")
        dispersion = stdev(statistical_returns)
        _require(math.isfinite(dispersion), "session dispersion exceeds finite statistical range")
    return {"path": path, "total_return": str(previous / initial - 1),
            "maximum_drawdown": str(maximum_drawdown), "session_return_sd": dispersion,
            "session_return_count": len(returns), "filled_entries": diagnostics.get("entries", 0),
            "ordinary_closed_trades": result.closed_trades, "terminal_open_lots": len(result.books[-1].lots)}


def _overlap(left: tuple, right: tuple) -> dict:
    """Aggregate per-formation symmetric differences over their union counts."""
    rows, numerator, denominator = [], 0, 0
    for (formation, first), (_, second) in zip(left, right):
        difference, union = len(set(first) ^ set(second)), len(set(first) | set(second))
        numerator += difference
        denominator += union
        rows.append({"formation": formation.isoformat(), "D1_codes": list(first), "D2_codes": list(second),
                     "symmetric_difference_count": difference, "union_count": union})
    return {"formations": rows, "symmetric_difference_count": numerator, "union_count": denominator,
            "rate": None if denominator == 0 else str(Decimal(numerator) / Decimal(denominator))}


def paired_timing_report(
    d1: SyntheticReplay,
    d2: SyntheticReplay,
    selections_d1: tuple[PartitionSelection, ...],
    selections_d2: tuple[PartitionSelection, ...],
    *,
    initial_nav: Decimal,
) -> dict:
    """Describe paired immutable replay outcomes without IO or choosing a lag.

    Require common ordered session books, formation coordinates, signal/source
    partitions and complete actual target declarations. Decimal NAV/return and
    drawdown results are serialized as decimal text; only unannualized sample
    session-return dispersion crosses to float. Initial NAV supplies the first
    return denominator and drawdown peak. Zero NAV is absorbing; fewer than two
    session returns has unknown dispersion. Overlap rates sum per-formation
    counts and are unknown for an empty union. Entries, ordinary sales and open
    terminal lots remain separate counts. This descriptive helper supplies no
    event certificate, replay execution, trial logging, family sizing or winner.
    Common coordinates do not prove equal costs, initial books or data/event truth.
    """
    _require(isinstance(initial_nav, Decimal) and initial_nav.is_finite() and initial_nav > 0,
             "explicit finite positive Decimal initial NAV required")
    first = _arm(d1, selections_d1, initial_nav)
    second = _arm(d2, selections_d2, initial_nav)
    _require(tuple(book.as_of for book in d1.books) == tuple(book.as_of for book in d2.books),
             "paired replay session dates must agree")
    _require(tuple(row.formation_on for row in selections_d1) == tuple(row.formation_on for row in selections_d2),
             "paired formation coordinates must agree")
    for left, right in zip(selections_d1, selections_d2):
        _require((left.eligible_codes, left.source_population_count, left.source_partition_sha256)
                 == (right.eligible_codes, right.source_population_count, right.source_partition_sha256),
                 "paired signal pools/source partitions must agree")
    return {"schema": "activity-paired-timing-report-v1", "initial_nav": str(initial_nav),
            "units": {"nav": "caller accounting units; exact Decimal text", "nav_ratio": "multiple of initial NAV",
                      "returns": "fraction; exact Decimal text", "maximum_drawdown": "nonnegative fraction; exact Decimal text",
                      "session_return_sd": "unannualized sample session-return fraction; float or null",
                      "overlap_rate": "summed symmetric-difference count / summed union count; Decimal text or null"},
            "arms": {"D1": first, "D2": second},
            "signal_overlap": _overlap(tuple((row.formation_on, row.eligible_codes) for row in selections_d1),
                                       tuple((row.formation_on, row.eligible_codes) for row in selections_d2)),
            "target_overlap": _overlap(d1.selections, d2.selections)}
