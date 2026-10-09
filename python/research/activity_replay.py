"""Pure synthetic full-session accounting preparation; not a real-data runner.

The caller supplies toy Series, the original v1 parameter mapping (no numeric
defaults here), one explicit SyntheticSelection per v1 formation, a fixed
synthetic snapshot id, and inspected-event-shaped synthetic inputs. Selection
rows must cover ALL present, non-frozen formation bars, including held codes
and excluded controls. They are separate synthetic dispositions, NOT output
adapters for AQ/AR/AU audits or certification of historical sources. In
particular, fixed-calendar absolute liquidity and operating-period normal
activity history remain distinct. Their calculations and evidence are outside
this component. Unknown/conflicting classification stops the entire selection
before held-code exclusion and hashing; unresolved screens for operating common
shares also stop it. Known excluded controls need no operating-company screens.
Classification evidence must be dated no later than formation, retaining AG's
cutoff. The market decision remains the next session under a date-granularity
toy convention; its date does NOT establish publication before 08:30. Actual
publication timestamps, sources and historical timing remain uncertified (R3).

Every calendar session from lookback+1 through end is processed in this order:
(1) due compulsory exchanges/final net payments, chronologically, with supplied
order breaking same-day ties; (2) opening due sales; (3) formation+1 entries;
(4) closing observable marks. Non-session event dates apply on the first later
session. Pending deliveries, absent/frozen bars retain inventory, marked value
and occupied lots. Daily-open fills, including limit-locked bars, retain v1's
unverified queue proxy. Held CURRENT codes are excluded, selected failed fills
get no substitute, allocation is current opening equity/slots, and fractional
shares include entry costs in the cash budget. Sales use the existing KOSPI
upper-bound tax convention and T+2 calendar schedule. Exchange units/carried
marks retain AG's fixed-snapshot coordinates and never enter eligibility.
Supplied verified event bounds must agree with quotes: an old issue cannot
have non-frozen opens/closes on/after a compulsory exchange's effectiveness,
or after a final distribution's last trading date. Conflicting input raises;
it never silently filters a candidate or substitutes another entry. An absent
or frozen next-session old-issue bar remains a v1 unfilled entry.
Before those event bounds ordinary selection/fills remain permitted. No
cessation is inferred for an issue without an explicit supported event.

Only immutable books, chosen codes and accounting diagnostics are returned;
there is no IO, CLI, loader, certification, trial logging, sizing or comparison.
Synthetic dispositions do not close R2-R5/R7-R8 or R6's actual-data integration.
The frozen activity_portfolio v1 and accounting primitives remain unchanged.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
import hashlib

from research.activity_accounting import Lot, observable_mark
from research.activity_book import ActivityBook, CompulsoryStockExchange, FinalCashPayment
from research.activity_portfolio import Series
from research.krx_tax_schedule import KOSPI, total_bp


@dataclass(frozen=True)
class SyntheticCandidate:
    """Explicit toy selector result, with no implied source certification.

    Classification is operating_common/excluded/unknown/conflicting. Each
    history disposition is pass/fail/insufficient/unknown/conflicting; an
    insufficient history is verified short in the fixture, never a missing
    observation silently treated as a failed screen. activity_pass is separate
    from the operating-baseline disposition and must be explicitly known.
    classification_known_on must be no later than the formation date.
    """

    code: str
    classification: str
    classification_known_on: date | None
    size_pass: bool | None
    absolute_liquidity: str
    operating_baseline: str
    activity_pass: bool | None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code or self.code != self.code.strip():
            raise ValueError("synthetic candidate needs an unpadded code")
        if self.classification not in {"operating_common", "excluded", "unknown", "conflicting"}:
            raise ValueError("invalid synthetic classification")
        if self.classification_known_on is not None and type(self.classification_known_on) is not date:
            raise ValueError("synthetic classification availability must be a date or unknown")
        for value in (self.absolute_liquidity, self.operating_baseline):
            if value not in {"pass", "fail", "insufficient", "unknown", "conflicting"}:
                raise ValueError("invalid synthetic history disposition")
        if any(value is not None and type(value) is not bool for value in (self.size_pass, self.activity_pass)):
            raise ValueError("synthetic size/activity decisions must be booleans or unknown")


@dataclass(frozen=True)
class SyntheticSelection:
    """A complete toy formation pool, usable only on its next market session."""

    formation_on: date
    decision_on: date
    candidates: tuple[SyntheticCandidate, ...]

    def __post_init__(self) -> None:
        if type(self.formation_on) is not date or type(self.decision_on) is not date:
            raise ValueError("synthetic selection dates must be dates")
        if self.decision_on <= self.formation_on or type(self.candidates) is not tuple:
            raise ValueError("synthetic selection needs a later decision and immutable candidates")
        if any(not isinstance(row, SyntheticCandidate) for row in self.candidates):
            raise ValueError("synthetic selection requires SyntheticCandidate rows")
        if len({row.code for row in self.candidates}) != len(self.candidates):
            raise ValueError("duplicate synthetic candidate")


@dataclass(frozen=True)
class SyntheticReplay:
    """Session books and accounting observations, with no readiness verdict."""

    books: tuple[ActivityBook, ...]
    selections: tuple[tuple[date, tuple[str, ...]], ...]
    diagnostics: tuple[tuple[str, int], ...]
    closed_trades: int

    @property
    def navs(self) -> tuple[Decimal, ...]:
        return tuple(book.nav for book in self.books)


def _number(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{name} must be finite and nonnegative")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return result


def _quote(series: Series, index: int) -> tuple[Decimal, Decimal, bool, bool]:
    if series.frozen[index] not in (0, 1) or series.locked[index] not in (0, 1):
        raise ValueError("synthetic bar states must be boolean")
    return (_number(series.opens[index], "open"), _number(series.closes[index], "close"),
            bool(series.frozen[index]), bool(series.locked[index]))


def _selected(
    selection: SyntheticSelection, *, formation_on: date, decision_on: date,
    present_codes: set[str], held_codes: set[str], seed: int, free_slots: int,
) -> tuple[str, ...]:
    if selection.formation_on != formation_on or selection.decision_on != decision_on:
        raise ValueError("synthetic selection must use the formation's next market session")
    if {row.code for row in selection.candidates} != present_codes:
        raise ValueError("synthetic selection must cover every non-frozen formation bar")
    # Check EVERY classification before any screen, held-code exclusion or hash.
    for row in selection.candidates:
        if (row.classification in {"unknown", "conflicting"}
                or row.classification_known_on is None
                or row.classification_known_on > formation_on):
            raise ValueError(f"unknown or conflicting point-in-time synthetic classification: {row.code}")
    pool = []
    for row in selection.candidates:
        if row.classification == "excluded":
            continue
        if (row.size_pass is None or row.activity_pass is None
                or row.absolute_liquidity in {"unknown", "conflicting"}
                or row.operating_baseline in {"unknown", "conflicting"}):
            raise ValueError(f"unresolved synthetic selector input: {row.code}")
        if (row.classification == "operating_common" and row.size_pass
                and row.absolute_liquidity == row.operating_baseline == "pass"
                and row.activity_pass and row.code not in held_codes):
            pool.append(row.code)
    pool.sort(key=lambda code: hashlib.sha256(f"{seed}:{formation_on}:{code}".encode()).digest())
    return tuple(pool[:free_slots])


def _event_date(event: CompulsoryStockExchange | FinalCashPayment) -> date:
    if isinstance(event, CompulsoryStockExchange):
        return event.effective_on
    if isinstance(event, FinalCashPayment):
        return event.distribution.paid_on
    raise ValueError("only synthetic compulsory exchanges/final net payments are supported")


def _check_event_quotes(
    events: Sequence[CompulsoryStockExchange | FinalCashPayment],
    panel: Mapping[str, Series], *, session: date, index: int,
) -> None:
    """Refuse bars contradicting explicit event bounds, even before payment."""
    for event in events:
        if isinstance(event, CompulsoryStockExchange):
            code, ceased = event.old_basis.code, session >= event.effective_on
        else:
            code = event.distribution.code
            ceased = session > event.distribution.last_trading_on
        if ceased and code in panel:
            opening, close, frozen, _ = _quote(panel[code], index)
            if not frozen and (opening or close):
                raise ValueError(f"synthetic event/quote conflict for ceased issue: {code} {session}")


def replay_synthetic(
    dates: Sequence[date], panel: Mapping[str, Series], params: Mapping,
    selections: Sequence[SyntheticSelection], *, dataset_sha256: str,
    events: tuple[CompulsoryStockExchange | FinalCashPayment, ...] = (),
    initial_book: ActivityBook | None = None,
) -> SyntheticReplay:
    """Replay caller-supplied toy inputs without reading or writing anything.

    Optional initial inventory is only a synthetic test seam: its cutoff must
    equal the first formation date, and pending future events cannot already be
    due at that cutoff. Events may not reuse initial consumed ids. All supplied
    observations and events must share the explicitly identified snapshot.
    """
    calendar = list(dates)
    if (not calendar or any(type(day) is not date for day in calendar)
            or any(left >= right for left, right in zip(calendar, calendar[1:]))):
        raise ValueError("synthetic calendar must contain strictly increasing dates")
    lookback, holding, slots = (params[key] for key in ("lookback", "holding_sessions", "slots"))
    if any(type(value) is not int or value < 1 for value in (lookback, holding, slots)):
        raise ValueError("lookback, holding and slots must be positive integers")
    if type(params["seed"]) is not int:
        raise ValueError("synthetic selection seed must be an integer")
    _number(params["threshold"], "threshold")
    cost = sum((_number(params[key], key) for key in
                ("commission_bps_per_side", "slippage_bps_per_side")), Decimal(0))
    end = calendar.index(date.fromisoformat(params["end"]))
    if not lookback < end or end + 2 >= len(calendar):
        raise ValueError("calendar needs replay sessions and two settlement sessions after end")
    if (not isinstance(dataset_sha256, str) or len(dataset_sha256) != 64
            or any(char not in "0123456789abcdef" for char in dataset_sha256)):
        raise ValueError("synthetic snapshot must be a lowercase SHA-256 string")
    for code, series in panel.items():
        if not isinstance(code, str) or not code or code != code.strip() or not isinstance(series, Series):
            raise ValueError("synthetic panel requires codes and Series")
        if any(len(values) != len(calendar) for values in
               (series.opens, series.closes, series.turnover, series.frozen, series.locked)):
            raise ValueError("synthetic series must align to the complete calendar")
    formation_indices = tuple(range(lookback, end - holding, holding))
    if any(not isinstance(selection, SyntheticSelection) for selection in selections):
        raise ValueError("explicit SyntheticSelection inputs are required")
    formation_map = {selection.formation_on: selection for selection in selections}
    if (len(formation_map) != len(selections)
            or set(formation_map) != {calendar[index] for index in formation_indices}):
        raise ValueError("synthetic selections must match every v1 formation exactly")
    book = initial_book if initial_book is not None else ActivityBook(Decimal(1), (), calendar[lookback])
    if not isinstance(book, ActivityBook) or book.as_of != calendar[lookback] or book.slot_count > slots:
        raise ValueError("initial synthetic book must match the first formation cutoff and slots")
    if any(lot.code not in panel or lot.dataset_sha256 != dataset_sha256 for lot in book.lots):
        raise ValueError("initial synthetic lots must share the panel snapshot and known codes")
    if type(events) is not tuple:
        raise ValueError("synthetic events must be an immutable tuple")
    ordered_events = sorted(events, key=_event_date)  # stable: explicit same-day order
    if len({event.event_id for event in ordered_events}) != len(ordered_events):
        raise ValueError("synthetic event ids must be unique")
    for event in ordered_events:
        bases = ((event.old_basis, event.new_basis) if isinstance(event, CompulsoryStockExchange)
                 else (event.distribution.basis,))
        if (any(basis.dataset_sha256 != dataset_sha256 for basis in bases)
                or _event_date(event) <= book.as_of or event.event_id in book.applied_event_ids):
            raise ValueError("synthetic events need the same snapshot, future due dates and unused ids")
        if isinstance(event, CompulsoryStockExchange) and event.new_basis.code not in panel:
            raise ValueError("synthetic successor must have an explicit panel series, even if absent")
    _check_event_quotes(ordered_events, panel, session=calendar[lookback], index=lookback)
    books, choices = [], []
    diagnostics: Counter[str] = Counter()
    closed, cursor = 0, 0
    for index in range(lookback + 1, end + 1):
        session = calendar[index]
        _check_event_quotes(ordered_events, panel, session=session, index=index)
        book = replace(book, as_of=session)
        while cursor < len(ordered_events) and _event_date(ordered_events[cursor]) <= session:
            event = ordered_events[cursor]
            book = (book.apply_stock_exchange(event, session=session)
                    if isinstance(event, CompulsoryStockExchange)
                    else book.apply_final_cash(event, session=session))
            cursor += 1
        remaining, cash = [], book.cash
        for lot in book.lots:
            opening, close, frozen, locked = _quote(panel[lot.code], index)
            available = session >= lot.available_on
            if not close or frozen:
                diagnostics["unpriced_or_frozen_position_sessions"] += 1
            if not available:
                diagnostics["unavailable_position_sessions"] += 1
            if session >= lot.due_on:
                if available and opening and not frozen:
                    tax = Decimal(str(total_bp(KOSPI, session, calendar)))
                    if cost + tax > 10000:
                        raise ValueError("sale costs cannot exceed the sale notional")
                    cash += lot.shares * opening * (1 - (cost + tax) / 10000)
                    diagnostics["delayed_exits"] += session > lot.due_on
                    diagnostics["limit_locked_fills"] += locked
                    closed += 1
                    continue
                diagnostics["pending_exit_sessions"] += 1
            remaining.append(lot)
        book = replace(book, cash=cash, lots=tuple(remaining))
        if index - 1 in formation_indices:
            formation_on = calendar[index - 1]
            present_codes = {code for code, series in panel.items()
                             if _quote(series, index - 1)[1] and not series.frozen[index - 1]}
            selected = _selected(
                formation_map[formation_on], formation_on=formation_on, decision_on=session,
                present_codes=present_codes, held_codes={lot.code for lot in book.lots},
                seed=params["seed"], free_slots=max(0, slots - book.slot_count),
            )
            choices.append((formation_on, selected))
            equity = book.cash
            for lot in book.lots:
                opening, _, frozen, _ = _quote(panel[lot.code], index)
                equity += lot.shares * (opening if opening and not frozen
                                        and session >= lot.available_on else lot.mark)
            allocation = equity / slots
            lots, cash = list(book.lots), book.cash
            for code in selected:
                opening, _, frozen, locked = _quote(panel[code], index)
                if not opening or frozen:
                    diagnostics["unfilled_entries"] += 1
                    continue
                budget = min(cash, allocation)
                if budget <= 0:
                    break
                lots.append(Lot(f"{code}:{session}", code, budget / (opening * (1 + cost / 10000)),
                                opening, session, session, calendar[index + holding], session,
                                dataset_sha256))
                cash -= budget
                diagnostics["entries"] += 1
                diagnostics["limit_locked_fills"] += locked
            book = replace(book, cash=cash, lots=tuple(lots))
        marked = []
        for lot in book.lots:
            _, close, frozen, _ = _quote(panel[lot.code], index)
            marked.append(observable_mark(lot, session=session, close=close, frozen=frozen)
                          if close else lot)
        book = replace(book, lots=tuple(marked))
        diagnostics["invested_sessions"] += bool(book.lots)
        books.append(book)
    return SyntheticReplay(tuple(books), tuple(choices), tuple(sorted(diagnostics.items())), closed)
