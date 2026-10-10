"""Immutable lot accounting for synthetic activity-runner preparation.

Only inspected compulsory stock entitlements and verified final net cash
payments are supported. Evidence references do not certify their contents.
This module has no price loader, trade fills, event loop or performance runner.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
import json

from research.activity_accounting import (
    FinalCashDistribution, Lot, PriceBasis, settle_final_cash,
    spin_off_entitlements, successor_entitlement, sum_decimal_values,
)


def _identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a nonblank, unpadded string")


def _date(value: date, name: str) -> None:
    if type(value) is not date:
        raise ValueError(f"{name} must be a date")


def _snapshot(value: str) -> None:
    if (not isinstance(value, str) or len(value) != 64 or
            any(character not in "0123456789abcdef" for character in value)):
        raise ValueError("dataset SHA-256 must be a lowercase hexadecimal string")


def _basis(value: PriceBasis) -> None:
    if not isinstance(value, PriceBasis):
        raise ValueError("an explicit PriceBasis is required")
    _identifier(value.code, "basis code")
    _date(value.session, "basis session")
    _snapshot(value.dataset_sha256)


@dataclass(frozen=True)
class CompulsoryStockExchange:
    """One inspected mandatory cross-code event, applied to all old-code lots.

    The caller must establish compulsory eligibility and the public-share ratio;
    voluntary tender participation is outside this event's contract.
    """

    event_id: str
    ratio: Decimal
    old_basis: PriceBasis
    new_basis: PriceBasis
    effective_on: date
    available_on: date
    source: str

    def __post_init__(self) -> None:
        _identifier(self.event_id, "event id")
        if not isinstance(self.ratio, Decimal) or not self.ratio.is_finite() or self.ratio <= 0:
            raise ValueError("raw share exchange ratio must be a finite positive Decimal")
        _basis(self.old_basis)
        _basis(self.new_basis)
        _date(self.effective_on, "effective date")
        _date(self.available_on, "availability date")
        if self.old_basis.code == self.new_basis.code:
            raise ValueError("a compulsory exchange must be cross-code")
        if self.old_basis.dataset_sha256 != self.new_basis.dataset_sha256:
            raise ValueError("basis observations must use the same price snapshot")
        if not self.old_basis.session < self.effective_on <= self.available_on:
            raise ValueError("invalid pre-event price or entitlement dates")
        if self.new_basis.session != self.available_on:
            raise ValueError("successor basis must be anchored to availability")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("an inspected compulsory exchange source is required")


@dataclass(frozen=True)
class FinalCashPayment:
    """A stable event id for an already verified compulsory final distribution."""

    event_id: str
    distribution: FinalCashDistribution

    def __post_init__(self) -> None:
        _identifier(self.event_id, "event id")
        if not isinstance(self.distribution, FinalCashDistribution):
            raise ValueError("a verified FinalCashDistribution is required")
        _identifier(self.distribution.code, "distribution code")
        _basis(self.distribution.basis)
        _date(self.distribution.last_trading_on, "last trading date")
        _date(self.distribution.record_on, "record date")
        _date(self.distribution.paid_on, "payment date")


@dataclass(frozen=True)
class CompulsorySpinOff:
    """Inspected two-component compulsory allotment with an explicit carry rule.

    Final eligibility/ratios/delivery and the share-unit bridge remain evidence
    obligations. carry_source identifies a registered valuation convention or
    source-supported allocation; it does not certify market-value weights.
    last_eligible_entry_on is a source-attested purchase cutoff, distinct from
    the later record date; record-day entry alone does not establish rights.
    """

    event_id: str
    retained_ratio: Decimal
    new_ratio: Decimal
    old_basis: PriceBasis
    retained_basis: PriceBasis
    new_basis: PriceBasis
    last_eligible_entry_on: date
    record_on: date
    effective_on: date
    retained_available_on: date
    new_available_on: date
    retained_carry_weight: Decimal
    carry_source: str
    carry_evidence_level: str
    source: str

    def __post_init__(self) -> None:
        _identifier(self.event_id, "event id")
        for name in ("retained_ratio", "new_ratio", "retained_carry_weight"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be a finite positive Decimal")
        if self.retained_carry_weight >= 1:
            raise ValueError("retained carry weight must be strictly below one")
        for basis in (self.old_basis, self.retained_basis, self.new_basis):
            _basis(basis)
        for name in ("last_eligible_entry_on", "record_on", "effective_on", "retained_available_on", "new_available_on"):
            _date(getattr(self, name), name)
        if self.old_basis.code != self.retained_basis.code or self.new_basis.code == self.old_basis.code:
            raise ValueError("spin-off needs retained old code and a distinct new code")
        if len({basis.dataset_sha256 for basis in (self.old_basis, self.retained_basis, self.new_basis)}) != 1:
            raise ValueError("spin-off bases must use the same price snapshot")
        if not (self.old_basis.session < self.effective_on
                and self.last_eligible_entry_on <= self.record_on <= self.effective_on
                and self.effective_on <= min(self.retained_available_on, self.new_available_on)):
            raise ValueError("invalid spin-off record/effective/availability dates")
        if (self.retained_basis.session != self.retained_available_on
                or self.new_basis.session != self.new_available_on):
            raise ValueError("spin-off component bases must be anchored to their availability")
        _identifier(self.carry_source, "carry source")
        _identifier(self.source, "spin-off source")
        if (not isinstance(self.carry_evidence_level, str)
                or self.carry_evidence_level not in {"confirmed", "inferred", "assumed"}):
            raise ValueError("carry evidence level must be explicit")


@dataclass(frozen=True)
class ActivityBook:
    """Cash and distinct acquisition lots at a dated accounting cutoff.

    Components of one original investment share a slot, including unavailable
    entitlements. Legacy lots each occupy a slot. IDs are consumed only from
    effectiveness/payment onward, even when no matching lot is held. Reuse of a
    consumed ID is an error. Each event method returns a complete new book only
    after every matching lot passes the existing accounting primitive.
    """

    cash: Decimal
    lots: tuple[Lot, ...]
    as_of: date
    applied_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.cash, Decimal) or not self.cash.is_finite() or self.cash < 0:
            raise ValueError("cash must be a finite nonnegative Decimal")
        _date(self.as_of, "book date")
        if type(self.lots) is not tuple or type(self.applied_event_ids) is not tuple:
            raise ValueError("lots and applied event ids must be immutable tuples")
        investment_terms: dict[tuple[str, str], tuple[date, date, str]] = {}
        for lot in self.lots:
            if not isinstance(lot, Lot):
                raise ValueError("inventory must contain Lot objects")
            _identifier(lot.lot_id, "lot id")
            _identifier(lot.code, "lot code")
            if lot.investment_id is not None:
                _identifier(lot.investment_id, "investment id")
            _snapshot(lot.dataset_sha256)
            if lot.carried_value is not None and (
                not isinstance(lot.carried_value, Decimal) or not lot.carried_value.is_finite()
                or lot.carried_value <= 0
            ):
                raise ValueError("carried value must be a finite positive Decimal")
            for name in ("mark_date", "entered_on", "due_on", "available_on"):
                _date(getattr(lot, name), f"lot {name}")
            if lot.entered_on > self.as_of or lot.mark_date > self.as_of:
                raise ValueError("lot acquisition and mark cannot be after the book date")
            terms = (lot.entered_on, lot.due_on, lot.dataset_sha256)
            if investment_terms.setdefault(lot.investment_key, terms) != terms:
                raise ValueError("investment components must share acquisition, due date and price snapshot")
        if len({lot.lot_id for lot in self.lots}) != len(self.lots):
            raise ValueError("lot ids must be unique")
        for event_id in self.applied_event_ids:
            _identifier(event_id, "applied event id")
        if len(set(self.applied_event_ids)) != len(self.applied_event_ids):
            raise ValueError("applied event ids must be unique")

    @property
    def slot_count(self) -> int:
        return len({lot.investment_key for lot in self.lots})

    @property
    def nav(self) -> Decimal:
        """Marked inventory plus cash; a carried mark is not an executable quote."""
        return sum_decimal_values((self.cash,) + tuple(lot.marked_value for lot in self.lots))

    def _check_session(self, event_id: str, session: date) -> None:
        _date(session, "processing session")
        if session < self.as_of:
            raise ValueError("processing session cannot precede the book date")
        if event_id in self.applied_event_ids:
            raise ValueError("event id has already been applied")

    def apply_stock_exchange(self, event: CompulsoryStockExchange, *, session: date) -> ActivityBook:
        """Convert at effectiveness using share units from one fixed snapshot.

        The availability-date price pair supplies the adjusted/raw unit factor;
        its future price level is not booked. The carried mark preserves each
        lot's old marked value and observation date while delivery is pending.
        Observable successor marks remain blocked until availability, which
        itself does not establish an executable fill.
        """
        if not isinstance(event, CompulsoryStockExchange):
            raise ValueError("an inspected CompulsoryStockExchange is required")
        self._check_session(event.event_id, session)
        if session < event.effective_on:
            return replace(self, as_of=session)
        lots = tuple(
            successor_entitlement(
                lot, ratio=event.ratio, old_basis=event.old_basis,
                new_basis=event.new_basis, effective_on=event.effective_on,
                available_on=event.available_on, session=session, source=event.source,
            ) if lot.code == event.old_basis.code else lot
            for lot in self.lots
        )
        return replace(self, lots=lots, as_of=session,
                       applied_event_ids=self.applied_event_ids + (event.event_id,))

    def apply_final_cash(self, event: FinalCashPayment, *, session: date) -> ActivityBook:
        if not isinstance(event, FinalCashPayment):
            raise ValueError("a verified FinalCashPayment is required")
        self._check_session(event.event_id, session)
        remaining: list[Lot] = []
        cash = self.cash
        for lot in self.lots:
            if lot.code != event.distribution.code:
                remaining.append(lot)
                continue
            held, increment = settle_final_cash(lot, event.distribution, session=session)
            if held is not None:
                remaining.append(held)
            cash += increment
        consumed = session >= event.distribution.paid_on
        return replace(self, cash=cash, lots=tuple(remaining), as_of=session,
                       applied_event_ids=self.applied_event_ids + (event.event_id,)
                       if consumed else self.applied_event_ids)

    def apply_spin_off(self, event: CompulsorySpinOff, *, session: date) -> ActivityBook:
        """Keep both components in the original slot, atomically and once.

        Legacy acquisitions receive separate deterministic investment IDs.
        Existing explicit groups remain stable; generated component/group IDs
        are refused if they collide with any current inventory identity.
        """
        if not isinstance(event, CompulsorySpinOff):
            raise ValueError("an inspected CompulsorySpinOff is required")
        self._check_session(event.event_id, session)
        if session < event.effective_on:
            return replace(self, as_of=session)
        lot_ids = {lot.lot_id for lot in self.lots}
        investment_keys = {lot.investment_key for lot in self.lots}
        remaining = []
        for lot in self.lots:
            if lot.code != event.old_basis.code:
                remaining.append(lot)
                continue
            identity = json.dumps((event.event_id, lot.lot_id), ensure_ascii=True, separators=(",", ":"))
            new_lot_id = "spin-off-component:" + identity
            if new_lot_id in lot_ids:
                raise ValueError("spin-off component id collision")
            lot_ids.add(new_lot_id)
            investment_id = lot.investment_id
            if investment_id is None:
                investment_id = "spin-off-investment:" + identity
                key = ("investment", investment_id)
                if key in investment_keys:
                    raise ValueError("spin-off investment id collision")
                investment_keys.add(key)
            remaining.extend(spin_off_entitlements(
                lot, retained_ratio=event.retained_ratio, new_ratio=event.new_ratio,
                old_basis=event.old_basis, retained_basis=event.retained_basis, new_basis=event.new_basis,
                last_eligible_entry_on=event.last_eligible_entry_on,
                record_on=event.record_on, effective_on=event.effective_on,
                retained_available_on=event.retained_available_on, new_available_on=event.new_available_on,
                retained_carry_weight=event.retained_carry_weight, carry_source=event.carry_source,
                carry_evidence_level=event.carry_evidence_level, session=session, source=event.source,
                investment_id=investment_id, new_lot_id=new_lot_id,
            ))
        return replace(self, lots=tuple(remaining), as_of=session,
                       applied_event_ids=self.applied_event_ids + (event.event_id,))
