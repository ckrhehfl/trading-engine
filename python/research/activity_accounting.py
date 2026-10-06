"""Evidence-gated arithmetic for the next activity study, not a live broker.

The v1 pilot is unchanged. These primitives deliberately have no price loader
or performance runner: a complete dated universe and corporate-action ledger
are prerequisites for wiring a new, separately registered portfolio replay.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal


def _positive(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a finite positive Decimal")


def _source(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("an inspected evidence reference is required")


@dataclass(frozen=True)
class PriceBasis:
    """Same-session raw/adjusted close pair in one identified price snapshot.

    A reference is provenance, not machine verification of the document. The
    caller must establish that both observations refer to the same share class
    and session and that the adjustment convention permits this conversion.
    """

    code: str
    session: date
    raw_close: Decimal
    adjusted_close: Decimal
    dataset_sha256: str
    source: str

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("price basis needs a code")
        _positive(self.raw_close, "raw close")
        _positive(self.adjusted_close, "adjusted close")
        if (len(self.dataset_sha256) != 64 or
                any(c not in "0123456789abcdef" for c in self.dataset_sha256)):
            raise ValueError("price basis needs a dataset SHA-256")
        _source(self.source)

    @property
    def raw_shares_per_adjusted_share(self) -> Decimal:
        """q_adjusted * P_adjusted == q_raw * P_raw."""
        return self.adjusted_close / self.raw_close


@dataclass(frozen=True)
class Lot:
    """Keep acquisition lots separate even when they acquire the same code."""

    lot_id: str
    code: str
    shares: Decimal
    mark: Decimal
    mark_date: date
    entered_on: date
    due_on: date
    available_on: date
    dataset_sha256: str

    def __post_init__(self) -> None:
        if not self.lot_id or not self.code:
            raise ValueError("lot id and code are required")
        _positive(self.shares, "shares")
        _positive(self.mark, "mark")
        if self.due_on < self.entered_on or self.mark_date < self.entered_on:
            raise ValueError("lot dates precede acquisition")
        if self.available_on < self.entered_on:
            raise ValueError("availability precedes acquisition")
        if (len(self.dataset_sha256) != 64 or
                any(c not in "0123456789abcdef" for c in self.dataset_sha256)):
            raise ValueError("lot needs a dataset SHA-256")


def successor_entitlement(
    lot: Lot, *, ratio: Decimal, old_basis: PriceBasis, new_basis: PriceBasis,
    effective_on: date, available_on: date, session: date, source: str,
) -> Lot:
    """Convert a verified mandatory stock entitlement without realizing cash.

    This is a transition to apply once at the effective date to a qualifying
    pre-event holding, not a selector or a claim that an offer was exercised.
    Fractional entitlements retain v1's analytical fractional-share convention;
    actual whole-share rounding/fractional cash is not represented.
    """
    _positive(ratio, "raw share exchange ratio")
    _source(source)
    if session < effective_on:
        raise ValueError("cannot apply an exchange before effectiveness")
    if lot.code != old_basis.code or old_basis.code == new_basis.code:
        raise ValueError("a cross-code exchange must match the old holding")
    if not lot.dataset_sha256 == old_basis.dataset_sha256 == new_basis.dataset_sha256:
        raise ValueError("basis observations must use the same price snapshot")
    if not (lot.entered_on <= old_basis.session < effective_on <= available_on):
        raise ValueError("invalid pre-event price or entitlement dates")
    if lot.available_on >= effective_on or lot.mark_date >= effective_on:
        raise ValueError("holding must be available and marked before the event")
    if new_basis.session != available_on:
        raise ValueError("successor basis must be anchored to availability")
    if old_basis.session != lot.mark_date or old_basis.adjusted_close != lot.mark:
        raise ValueError("old basis must reconcile the last observable mark")
    multiplier = (old_basis.raw_shares_per_adjusted_share * ratio /
                  new_basis.raw_shares_per_adjusted_share)
    # Preserve economic value during the unavailable interval. Applying the
    # raw ratio directly to adjusted units silently creates/destroys value.
    return replace(lot, code=new_basis.code, shares=lot.shares * multiplier,
                   mark=lot.mark / multiplier, available_on=available_on)


def observable_mark(lot: Lot, *, session: date, close: Decimal, frozen: bool) -> Lot:
    """No mark update before delivery or from frozen bars; no backward time."""
    if session < lot.mark_date:
        raise ValueError("mark date cannot move backwards")
    if session < lot.available_on or frozen:
        return lot
    _positive(close, "observable close")
    return replace(lot, mark=close, mark_date=session)


@dataclass(frozen=True)
class FinalCashDistribution:
    """Verified compulsory final payout; not a proposal or appraisal option.

    Only a fully redeemed, non-trading issue is supported. Ordinary dividends,
    partial distributions and ex-distribution trades require a different model.
    The source must establish actual payment and the net public-share amount.
    """

    code: str
    last_trading_on: date
    record_on: date
    paid_on: date
    net_cash_per_raw_share: Decimal
    basis: PriceBasis
    payment_source: str

    def __post_init__(self) -> None:
        if self.code != self.basis.code or self.basis.session != self.last_trading_on:
            raise ValueError("cash basis must match the last trading session")
        if not self.last_trading_on < self.record_on <= self.paid_on:
            raise ValueError("unsupported distribution dates")
        # A verified zero recovery is valid; an unknown recovery is not zero.
        amount = self.net_cash_per_raw_share
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
            raise ValueError("verified net cash must be a finite nonnegative Decimal")
        _source(self.payment_source)


def settle_final_cash(
    lot: Lot | None, payout: FinalCashDistribution, *, session: date,
) -> tuple[Lot | None, Decimal]:
    """Return remaining inventory and cash increment; feed both into the book.

    A paid lot is removed. Reusing the returned inventory on a later date cannot
    credit it twice. An announced date/amount must never construct a payout.
    """
    if lot is None:
        return None, Decimal(0)
    if lot.code != payout.code:
        raise ValueError("payout does not match the holding")
    if lot.dataset_sha256 != payout.basis.dataset_sha256:
        raise ValueError("cash basis must use the holding's price snapshot")
    if (lot.entered_on > payout.last_trading_on or
            lot.available_on > payout.record_on or lot.mark_date > session or
            lot.mark_date > payout.last_trading_on):
        raise ValueError("holding does not establish payout eligibility")
    if session < payout.paid_on:
        return lot, Decimal(0)
    cash = (lot.shares * payout.basis.raw_shares_per_adjusted_share *
            payout.net_cash_per_raw_share)
    return None, cash


@dataclass(frozen=True)
class IdentityPeriod:
    """Instrument classification, effective [start, end), with public evidence.

    known_on is when the classification was public, not when we downloaded it.
    Future delisting/merger outcomes are never an eligibility input.
    """

    code: str
    start: date
    end: date
    known_on: date
    common_stock: bool
    source: str

    def __post_init__(self) -> None:
        if not self.code or self.start >= self.end or type(self.common_stock) is not bool:
            raise ValueError("invalid instrument classification interval")
        _source(self.source)


def common_stock_at(periods: tuple[IdentityPeriod, ...], *, code: str, session: date) -> bool:
    """Unknown/conflicting classification stops a run; it cannot drop a name.

    Invoke for every bar-eligible name before hash selection, never only for
    names that survived or became terminal holdings. A recent master snapshot
    cannot stand in for a historical interval's contemporaneous evidence.
    """
    matches = [p for p in periods if p.code == code and p.start <= session < p.end]
    if len(matches) != 1 or matches[0].known_on > session:
        raise ValueError(f"unknown or conflicting historical instrument identity: {code} {session}")
    return matches[0].common_stock
