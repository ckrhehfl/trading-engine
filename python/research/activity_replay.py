"""Pure full-session accounting with separate toy and evidence selection seams.

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
cutoff. The market decision uses an explicit D+1/D+2 session policy (D+1 by
default), with 08:30 KST selection before daily-open-price proxy execution.
Its modeled cutoff does NOT certify actual historical publication (R3).

PartitionSelection instead declares eligible codes from a complete dated source
proof partition. It requires an explicit timing policy and never rebuilds that
source population from the holding-price panel. Its evidence and fingerprint
remain caller declarations: this pure seam checks internal consistency and
accounting, not actual source, classification, price or event correctness.
An empty eligible partition with no panel, inventory or events may carry no
quote snapshot (None); cash-only books need no invented price identity.

With an explicit timing policy, the final 08:30 target list is fixed from the
prior book and scheduled due exits before any current-session quote is read.
Due, available lots reserve prospective exit slots without assuming a sale or
cash receipt. Only prior-book due/availability dates inform that plan; actual
future event/payment dates do not. Pending claims retain actual inventory and
slots until settled, so event/sale failure may invalidate a target with no
replacement selected.
The legacy timing_policy=None path retains v1's post-sale selection convention.

Every calendar session from lookback+1 through end then processes:
(1) due compulsory exchanges/final net payments, chronologically, with supplied
order breaking same-day ties; (2) opening due sales; (3) policy-lagged entries;
(4) closing observable marks. Non-session event dates apply on the first later
session. Pending deliveries, absent/frozen bars retain inventory, marked value
and occupied lots. Daily-open fills, including limit-locked bars, retain v1's
unverified queue proxy. Retained CURRENT codes are excluded, selected failed fills
get no substitute, allocation is current opening equity/slots for execution
sizing only, and fractional
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
from datetime import date, datetime
from decimal import Decimal
import hashlib

from research.activity_accounting import Lot, observable_mark
from research.activity_book import ActivityBook, CompulsoryStockExchange, FinalCashPayment
from research.activity_portfolio import Series
from research.activity_partition_selection import PartitionSelection, select_partition
from research.krx_tax_schedule import KOSPI, total_bp
from research.activity_timing import SessionLagPolicy


@dataclass(frozen=True)
class DecimalSeries:
    """Lossless scan observations, distinct from absent calendar sessions.

    An observed NULL is unresolved, not an absent bar or zero. This boundary
    establishes no source completeness, identity or publication-time proof.
    """

    opens: tuple[Decimal | None, ...]
    closes: tuple[Decimal | None, ...]
    turnover: tuple[Decimal | None, ...]
    frozen: tuple[bool | None, ...]
    locked: tuple[bool | None, ...]
    observed: tuple[bool, ...]

    def __post_init__(self) -> None:
        fields = (self.opens, self.closes, self.turnover, self.frozen,
                  self.locked, self.observed)
        if any(type(values) is not tuple for values in fields) or len({len(v) for v in fields}) != 1:
            raise ValueError("Decimal series requires aligned immutable tuples")
        for index, observed in enumerate(self.observed):
            if type(observed) is not bool:
                raise ValueError("Decimal observation presence must be explicit")
            for values, positive in ((self.opens, True), (self.closes, True), (self.turnover, False)):
                value = values[index]
                if value is not None and (not isinstance(value, Decimal)
                                          or not value.is_finite() or value < 0
                                          or (positive and value == 0)):
                    raise ValueError("Decimal series requires positive prices/nonnegative turnover Decimals or NULL")
            if any(values[index] is not None and type(values[index]) is not bool
                   for values in (self.frozen, self.locked)):
                raise ValueError("Decimal bar states must be explicit booleans or unknown")
            if not observed and any(values[index] is not None for values in fields[:-1]):
                raise ValueError("absent Decimal observation cannot carry values")


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
    """Complete formation pool for the explicitly modeled execution session."""

    formation_on: date
    decision_on: date
    candidates: tuple[SyntheticCandidate, ...]
    selection_at: datetime | None = None

    def __post_init__(self) -> None:
        if type(self.formation_on) is not date or type(self.decision_on) is not date:
            raise ValueError("synthetic selection dates must be dates")
        if self.decision_on <= self.formation_on or type(self.candidates) is not tuple:
            raise ValueError("synthetic selection needs a later decision and immutable candidates")
        if any(not isinstance(row, SyntheticCandidate) for row in self.candidates):
            raise ValueError("synthetic selection requires SyntheticCandidate rows")
        if len({row.code for row in self.candidates}) != len(self.candidates):
            raise ValueError("duplicate synthetic candidate")
        if self.selection_at is not None and (
                not isinstance(self.selection_at, datetime) or self.selection_at.utcoffset() is None):
            raise ValueError("selection_at must be an aware timestamp")


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


def _quote(series: Series | DecimalSeries, index: int) -> tuple[Decimal | None, Decimal | None, bool, bool]:
    if isinstance(series, DecimalSeries):
        if not series.observed[index]:
            return None, None, False, False
        if any(values[index] is None for values in
               (series.opens, series.closes, series.turnover, series.frozen, series.locked)):
            raise ValueError("unresolved observed Decimal quote")
    if series.frozen[index] not in (0, 1) or series.locked[index] not in (0, 1):
        raise ValueError("synthetic bar states must be boolean")
    return (_number(series.opens[index], "open"), _number(series.closes[index], "close"),
            bool(series.frozen[index]), bool(series.locked[index]))


def _selected(
    selection: SyntheticSelection, *, formation_on: date, decision_on: date,
    present_codes: set[str], held_codes: set[str], seed: int, free_slots: int,
    selection_at: datetime,
) -> tuple[str, ...]:
    if selection.formation_on != formation_on or selection.decision_on != decision_on:
        raise ValueError("synthetic selection must use the formation's next market session under its policy")
    if selection.selection_at is not None and selection.selection_at != selection_at:
        raise ValueError("synthetic selection must use the policy's 08:30 KST cutoff")
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
    panel: Mapping[str, Series | DecimalSeries], *, session: date, index: int,
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
    dates: Sequence[date], panel: Mapping[str, Series | DecimalSeries], params: Mapping,
    selections: Sequence[SyntheticSelection | PartitionSelection], *, dataset_sha256: str | None,
    events: tuple[CompulsoryStockExchange | FinalCashPayment, ...] = (),
    initial_book: ActivityBook | None = None,
    timing_policy: SessionLagPolicy | None = None,
) -> SyntheticReplay:
    """Replay pure inputs through a toy or explicit evidence-selection seam.

    SyntheticSelection keeps its complete toy formation-pool validation and
    legacy timing convention. PartitionSelection requires an explicit policy,
    one declaration per formation and panel coverage of every eligible code;
    the panel does not represent or reconstruct its complete source partition.
    Both paths check internal book consistency without IO or certification of
    actual source evidence, classification, prices, events or study completion.
    Only an explicit PartitionSelection cash-only path accepts dataset_sha256=None:
    the panel, every eligible tuple, initial inventory and events must all be
    empty. An optional initial cash-only book retains its existing cash amount.
    This absence of supplied events does not certify actual no-event coverage.

    Optional initial inventory is only a synthetic test seam: its cutoff must
    equal the first formation date, and pending future events cannot already be
    due at that cutoff. Events may not reuse initial consumed ids. All supplied
    observations and events must share the explicitly identified snapshot.
    Paired policies use the same lag-2 terminal formation horizon and evaluation
    calendar; each lot's holding period still begins at its actual proxy entry.
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
    for code, series in panel.items():
        if (not isinstance(code, str) or not code or code != code.strip()
                or not isinstance(series, (Series, DecimalSeries))):
            raise ValueError("synthetic panel requires codes and Series or DecimalSeries")
        if any(len(values) != len(calendar) for values in
               (series.opens, series.closes, series.turnover, series.frozen, series.locked)):
            raise ValueError("synthetic series must align to the complete calendar")
    policy = timing_policy if timing_policy is not None else SessionLagPolicy()
    if not isinstance(policy, SessionLagPolicy):
        raise ValueError("explicit SessionLagPolicy is required")
    formation_indices = policy.formation_indices(
        calendar, lookback=lookback, holding_sessions=holding, end_index=end)
    partition_path = any(isinstance(selection, PartitionSelection) for selection in selections)
    selection_type = PartitionSelection if partition_path else SyntheticSelection
    if any(not isinstance(selection, selection_type) for selection in selections):
        raise ValueError("explicit unmixed SyntheticSelection or PartitionSelection inputs are required")
    if partition_path:
        if timing_policy is None:
            raise ValueError("partition selections require an explicit timing policy")
        if any(code not in panel for selection in selections for code in selection.eligible_codes):
            raise ValueError("every partition-eligible code needs an explicit panel series")
    if timing_policy is not None and any(selection.selection_at is None for selection in selections):
        raise ValueError("explicit timing policy requires selection_at timestamps")
    formation_map = {selection.formation_on: selection for selection in selections}
    if (len(formation_map) != len(selections)
            or set(formation_map) != {calendar[index] for index in formation_indices}):
        raise ValueError("synthetic selections must match every v1 formation exactly")
    book = initial_book if initial_book is not None else ActivityBook(Decimal(1), (), calendar[lookback])
    if not isinstance(book, ActivityBook) or book.as_of != calendar[lookback] or book.slot_count > slots:
        raise ValueError("initial synthetic book must match the first formation cutoff and slots")
    cash_only_without_quotes = (dataset_sha256 is None and partition_path and not panel
                                and all(not selection.eligible_codes for selection in selections)
                                and not book.lots and type(events) is tuple and not events)
    if not cash_only_without_quotes and (
            not isinstance(dataset_sha256, str) or len(dataset_sha256) != 64
            or any(char not in "0123456789abcdef" for char in dataset_sha256)):
        raise ValueError("synthetic snapshot must be a lowercase SHA-256 string")
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
        formation_index = index - policy.session_lag
        selected = None
        if timing_policy is not None and formation_index in formation_indices:
            # Freeze portfolio choices at 08:30 without consulting today's
            # opening/frozen prices or assuming actual event/sale proceeds.
            retained = tuple(lot for lot in book.lots
                             if lot.due_on > session or lot.available_on > session)
            retained_slots = len({lot.investment_key for lot in retained})
            formation_on = calendar[formation_index]
            if partition_path:
                selected = select_partition(
                    formation_map[formation_on], formation_on=formation_on, decision_on=session,
                    held_codes={lot.code for lot in retained}, seed=params["seed"],
                    free_slots=max(0, slots - retained_slots),
                    selection_at=policy.selection_at(calendar, formation_index),
                )
            else:
                present_codes = {code for code, series in panel.items()
                                 if _quote(series, formation_index)[1] and not series.frozen[formation_index]}
                selected = _selected(
                    formation_map[formation_on], formation_on=formation_on, decision_on=session,
                    present_codes=present_codes, held_codes={lot.code for lot in retained},
                    seed=params["seed"], free_slots=max(0, slots - retained_slots),
                    selection_at=policy.selection_at(calendar, formation_index),
                )
            choices.append((formation_on, selected))
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
        if formation_index in formation_indices:
            if timing_policy is None:
                formation_on = calendar[formation_index]
                present_codes = {code for code, series in panel.items()
                                 if _quote(series, formation_index)[1] and not series.frozen[formation_index]}
                selected = _selected(
                    formation_map[formation_on], formation_on=formation_on, decision_on=session,
                    present_codes=present_codes, held_codes={lot.code for lot in book.lots},
                    seed=params["seed"], free_slots=max(0, slots - book.slot_count),
                    selection_at=policy.selection_at(calendar, formation_index),
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
                if timing_policy is not None and any(lot.code == code for lot in lots):
                    diagnostics["unfilled_entries"] += 1
                    diagnostics["unfilled_held_targets"] += 1
                    continue
                if timing_policy is not None and len({lot.investment_key for lot in lots}) >= slots:
                    diagnostics["unfilled_entries"] += 1
                    diagnostics["unfilled_capacity_targets"] += 1
                    continue
                opening, _, frozen, locked = _quote(panel[code], index)
                if not opening or frozen:
                    diagnostics["unfilled_entries"] += 1
                    continue
                budget = min(cash, allocation)
                if budget <= 0:
                    if timing_policy is not None:
                        diagnostics["unfilled_entries"] += 1
                        diagnostics["unfilled_cash_targets"] += 1
                        continue
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
