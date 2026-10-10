"""Evidence-gated arithmetic for the next activity study, not a live broker.

The v1 pilot is unchanged. These primitives deliberately have no price loader
or performance runner: a complete dated universe and corporate-action ledger
are prerequisites for wiring a new, separately registered portfolio replay.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, localcontext


def sum_decimal_values(values: tuple[Decimal, ...]) -> Decimal:
    """Add bounded finite values exactly, without intermediate NAV rounding.

    Existing share/price products keep their Decimal convention. Only their
    aggregation uses extra local precision; no global context is changed and
    the exact result is retained rather than rounded at each component.
    """
    if type(values) is not tuple or len(values) > 100_000:
        raise ValueError("a bounded tuple of Decimal values is required")
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise ValueError("finite Decimal values are required")
    nonzero = tuple(value for value in values if value)
    if not nonzero:
        return Decimal(0)
    highest = max(value.adjusted() for value in nonzero)
    lowest = min(value.as_tuple().exponent for value in nonzero)
    precision = highest - lowest + len(str(len(nonzero))) + 2
    if precision > 4096 or abs(highest) > 4096 or abs(lowest) > 4096:
        raise ValueError("bounded exact Decimal precision is required")
    with localcontext() as context:
        context.prec = max(1, precision)
        return sum(values, Decimal(0))


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
    """Separate component lots while retaining their original investment.

    carried_value preserves an explicit pending allocation when shares*mark
    cannot represent it exactly. A valid observable mark clears this override;
    the carried per-share mark is never an observed PriceBasis.
    """

    lot_id: str
    code: str
    shares: Decimal
    mark: Decimal
    mark_date: date
    entered_on: date
    due_on: date
    available_on: date
    dataset_sha256: str
    investment_id: str | None = None
    carried_value: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.lot_id or not self.code:
            raise ValueError("lot id and code are required")
        if self.investment_id is not None and (
            not isinstance(self.investment_id, str) or not self.investment_id or
            self.investment_id != self.investment_id.strip()
        ):
            raise ValueError("investment id must be a nonblank, unpadded string")
        _positive(self.shares, "shares")
        _positive(self.mark, "mark")
        if self.carried_value is not None:
            _positive(self.carried_value, "carried value")
        if self.due_on < self.entered_on or self.mark_date < self.entered_on:
            raise ValueError("lot dates precede acquisition")
        if self.available_on < self.entered_on:
            raise ValueError("availability precedes acquisition")
        if (len(self.dataset_sha256) != 64 or
                any(c not in "0123456789abcdef" for c in self.dataset_sha256)):
            raise ValueError("lot needs a dataset SHA-256")

    @property
    def investment_key(self) -> tuple[str, str]:
        """Legacy lots occupy independent slots without explicit-ID collisions."""
        if self.investment_id is None:
            return ("lot", self.lot_id)
        return ("investment", self.investment_id)

    @property
    def marked_value(self) -> Decimal:
        """An exact pending allocation is not an observed per-share quote."""
        return self.carried_value if self.carried_value is not None else self.shares * self.mark


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
    if lot.carried_value is not None:
        raise ValueError("a carried allocation is not an observable exchange basis")
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


def spin_off_entitlements(
    lot: Lot, *, retained_ratio: Decimal, new_ratio: Decimal,
    old_basis: PriceBasis, retained_basis: PriceBasis, new_basis: PriceBasis,
    last_eligible_entry_on: date, record_on: date, effective_on: date, retained_available_on: date,
    new_available_on: date, retained_carry_weight: Decimal,
    carry_source: str, carry_evidence_level: str, session: date, source: str,
    investment_id: str, new_lot_id: str,
) -> tuple[Lot, Lot]:
    """Partition one original investment into two compulsory stock components.

    Raw ratios determine share quantities, never pending market-value weights.
    The explicit carry convention partitions the old marked value; the second
    component receives its exact residual. Availability-basis prices provide
    unit factors only, with no future price level booked. Analytical fractional
    shares are retained. IDs/collisions across inventory belong to ActivityBook.
    This primitive converts still-held eligible inventory; replay separately
    refuses unsupported sales that would detach rights before effectiveness.
    """
    _positive(retained_ratio, "retained raw share ratio")
    _positive(new_ratio, "new raw share ratio")
    _positive(retained_carry_weight, "retained carry weight")
    if retained_carry_weight >= 1:
        raise ValueError("retained carry weight must be strictly below one")
    _source(source)
    _source(carry_source)
    if not isinstance(carry_evidence_level, str) or carry_evidence_level not in {"confirmed", "inferred", "assumed"}:
        raise ValueError("carry evidence level must be explicit")
    if old_basis.code != lot.code or retained_basis.code != lot.code or new_basis.code == lot.code:
        raise ValueError("spin-off bases must match retained and distinct new codes")
    if len({lot.dataset_sha256, old_basis.dataset_sha256, retained_basis.dataset_sha256,
            new_basis.dataset_sha256}) != 1:
        raise ValueError("spin-off bases must use the holding's price snapshot")
    if not (lot.entered_on <= old_basis.session < effective_on <= session
            and lot.entered_on <= last_eligible_entry_on <= record_on <= effective_on
            and lot.available_on <= record_on and lot.available_on < effective_on
            and effective_on <= retained_available_on and effective_on <= new_available_on):
        raise ValueError("invalid spin-off record eligibility or entitlement dates")
    if (lot.mark_date != old_basis.session or lot.mark != old_basis.adjusted_close
            or lot.mark_date >= effective_on or lot.carried_value is not None):
        raise ValueError("spin-off old basis must reconcile an observable pre-event mark")
    if retained_basis.session != retained_available_on or new_basis.session != new_available_on:
        raise ValueError("spin-off component bases must be anchored to their availability")
    old_value = lot.marked_value
    # Multiplication/subtraction of finite Decimals can be exact, even when a
    # per-share carried mark has a repeating expansion. Preserve the value
    # separately until a valid observable mark replaces the convention.
    precision = (len(old_value.as_tuple().digits) + len(retained_carry_weight.as_tuple().digits)
                 + abs(retained_carry_weight.as_tuple().exponent) + 2)
    if precision > 4096:
        raise ValueError("bounded carry allocation precision is required")
    with localcontext() as context:
        context.prec = max(context.prec, precision)
        retained_value = old_value * retained_carry_weight
        new_value = old_value - retained_value
    old_units = old_basis.raw_shares_per_adjusted_share
    retained_shares = lot.shares * old_units * retained_ratio / retained_basis.raw_shares_per_adjusted_share
    new_shares = lot.shares * old_units * new_ratio / new_basis.raw_shares_per_adjusted_share
    retained = replace(lot, shares=retained_shares, mark=retained_value / retained_shares,
                       available_on=retained_available_on, investment_id=investment_id,
                       carried_value=retained_value)
    allotted = replace(lot, lot_id=new_lot_id, code=new_basis.code, shares=new_shares,
                       mark=new_value / new_shares, available_on=new_available_on,
                       investment_id=investment_id, carried_value=new_value)
    return retained, allotted


def observable_mark(lot: Lot, *, session: date, close: Decimal, frozen: bool) -> Lot:
    """No mark update before delivery or from frozen bars; no backward time."""
    if session < lot.mark_date:
        raise ValueError("mark date cannot move backwards")
    if session < lot.available_on or frozen:
        return lot
    _positive(close, "observable close")
    return replace(lot, mark=close, mark_date=session, carried_value=None)


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
