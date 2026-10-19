"""Pure fixed price-trend selection over an explicit complete candidate set.

The caller independently verifies each baseline as the oldest of the prior
normal-observation lookback. This module neither infers that history nor proves
source truth, comparable economic units, performance or promotion eligibility.
Every supplied baseline and formation quote is checked before any selection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Context, Decimal, MAX_EMAX, MIN_EMIN, localcontext
from functools import cmp_to_key
import hashlib
import json

from research.activity_control_selection import SEED
from research.activity_timing import AvailabilityMetadata, KST


POLICY = "BT-trend-v1"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip()


@dataclass(frozen=True)
class PriceTrendObservation:
    """One exact close and caller-declared snapshot/basis provenance.

    ``source_identity`` identifies the input snapshot; ``metadata.source``
    identifies its supplier/channel. ``basis_reference`` identifies the caller's
    evidence/declaration of comparable baseline and formation units. Agreement
    between these declarations does not certify their underlying facts.
    """

    code: str
    isin: str
    close: Decimal
    price_basis: str
    source_identity: str
    basis_reference: str
    metadata: AvailabilityMetadata

    def __post_init__(self) -> None:
        _require(all(_text(value) for value in (self.code, self.isin,
            self.price_basis, self.source_identity, self.basis_reference)),
            "explicit code/ISIN and price basis/source declarations required")
        _require(type(self.close) is Decimal and self.close.is_finite()
            and self.close > 0, "close must be a positive finite Decimal")
        _require(type(self.metadata) is AvailabilityMetadata,
            "explicit AvailabilityMetadata required")
        self.metadata.__post_init__()


@dataclass(frozen=True)
class PriceTrendSelection:
    """Immutable ranks and targets; a shortfall retains its assigned cash.

    Negative trends remain eligible for relative ranking. ``ranking_identity``
    binds all declared inputs and metadata, not independently verified sources.
    No weight renormalization, execution, book or return is produced here.
    """

    formation_on: date
    selection_at: datetime
    target_count: int
    expected_candidates: tuple[tuple[str, str], ...]
    expected_baseline_on: tuple[tuple[str, date], ...]
    ranked_codes: tuple[str, ...]
    chosen_codes: tuple[str, ...]
    eligible_shortfall: int
    ranking_identity: str
    policy: str = field(default=POLICY, init=False)
    seed: int = field(default=SEED, init=False)
    shortfall_policy: str = field(default="retain_assigned_budget_in_cash", init=False)
    source_facts_certified: bool = field(default=False, init=False)
    normal_history_certified: bool = field(default=False, init=False)
    basis_comparability_certified: bool = field(default=False, init=False)
    performance_computed: bool = field(default=False, init=False)
    strategy_edge_established: bool = field(default=False, init=False)
    promotion_permitted: bool = field(default=False, init=False)


def _product_key(left: Decimal, right: Decimal) -> tuple[int, tuple[int, ...]]:
    """Order exact positive products, including extreme finite exponents.

    Multiply only integer coefficients with enough precision for every digit;
    retain exponents as Python integers so product exponent limits cannot round
    or overflow otherwise valid finite inputs. Remove trailing coefficient
    zeroes for an exact, scale-independent significand comparison.
    """
    first, second = left.as_tuple(), right.as_tuple()
    precision = len(first.digits) + len(second.digits)
    with localcontext(Context(prec=precision, Emax=MAX_EMAX, Emin=MIN_EMIN)):
        product = (Decimal((0, first.digits, 0))
            * Decimal((0, second.digits, 0))).as_tuple().digits
    significant = product
    while significant[-1] == 0:
        significant = significant[:-1]
    return len(product) + first.exponent + second.exponent, significant


def _observation_identity(row: PriceTrendObservation) -> tuple:
    meta = row.metadata
    return (row.code, row.isin, str(row.close), row.price_basis,
        row.source_identity, row.basis_reference, meta.observation_date.isoformat(),
        meta.available_at.isoformat(), meta.retrieved_at.isoformat()
        if isinstance(meta.retrieved_at, datetime) else meta.retrieved_at,
        meta.source, meta.is_final, meta.data_vintage, meta.evidence_level,
        meta.source_public_available_at.isoformat()
        if meta.source_public_available_at is not None else None,
        meta.availability_policy, meta.evidence_reference)


def select_price_trend(*, expected_candidates: tuple[tuple[str, str], ...],
                       expected_baseline_on: tuple[tuple[str, date], ...],
                       formation_on: date, selection_at: datetime, target_count: int,
                       observations: tuple[PriceTrendObservation, ...]) -> PriceTrendSelection:
    """Choose top k by formation/baseline close, with fixed seeded tie breaks.

    Baselines must be supplied for every exact candidate, independently of the
    observed prices. Missing, duplicate, extra, mismatched or unavailable quotes
    reject the complete selection even when that candidate would rank last or
    k is zero. The caller supplies normal history and evidence declarations;
    no calendar, eligibility, unit or missing-price fallback is inferred.
    """
    _require(type(formation_on) is date and isinstance(selection_at, datetime)
        and selection_at.utcoffset() is not None
        and formation_on <= selection_at.astimezone(KST).date(),
        "explicit formation date and aware nonfuture selection cutoff required")
    _require(type(target_count) is int and target_count >= 0,
        "target_count must be a nonnegative integer")
    _require(type(expected_candidates) is tuple and all(type(row) is tuple
        and len(row) == 2 and all(_text(value) for value in row)
        for row in expected_candidates), "immutable complete code/ISIN candidates required")
    candidates = dict(expected_candidates)
    _require(len(candidates) == len(expected_candidates)
        and len(set(candidates.values())) == len(candidates),
        "candidate codes and ISINs must be unique")
    _require(type(expected_baseline_on) is tuple and all(type(row) is tuple
        and len(row) == 2 and _text(row[0]) and type(row[1]) is date
        and row[1] < formation_on for row in expected_baseline_on),
        "immutable code/baseline dates strictly before formation required")
    baselines = dict(expected_baseline_on)
    _require(len(baselines) == len(expected_baseline_on) and set(baselines) == set(candidates),
        "exactly one independently declared baseline for every candidate required")
    _require(type(observations) is tuple and all(type(row) is PriceTrendObservation
        for row in observations), "immutable typed price observations required")
    required = {(code, day) for code in candidates for day in (baselines[code], formation_on)}
    quotes: dict[tuple[str, date], PriceTrendObservation] = {}
    declarations: set[tuple[str, str, str, str]] = set()
    for row in observations:
        row.__post_init__()
        key = row.code, row.metadata.observation_date
        _require(key in required and candidates[row.code] == row.isin,
            "unexpected code/ISIN or observation date")
        _require(key not in quotes, "duplicate code/date price observation")
        row.metadata.require_available(selection_at, observation_date=key[1])
        quotes[key] = row
        declarations.add((row.price_basis, row.source_identity,
            row.basis_reference, row.metadata.source))
    _require(set(quotes) == required, "missing baseline or formation price observation")
    _require(len(declarations) <= 1,
        "common price basis, source snapshot, basis reference and supplier/channel required")
    ties = {code: (hashlib.sha256(
        f"{SEED}:{POLICY}:{formation_on.isoformat()}:{code}".encode()).digest(), code)
        for code in candidates}

    def compare(left: str, right: str) -> int:
        first = _product_key(quotes[left, formation_on].close, quotes[right, baselines[right]].close)
        second = _product_key(quotes[right, formation_on].close, quotes[left, baselines[left]].close)
        if first != second:
            return -1 if first > second else 1
        return (ties[left] > ties[right]) - (ties[left] < ties[right])

    ranked = tuple(sorted(candidates, key=cmp_to_key(compare)))
    population = tuple(sorted(candidates.items()))
    baseline_dates = tuple(sorted(baselines.items()))
    identity = {"policy": POLICY, "seed": SEED, "formation_on": formation_on.isoformat(),
        "selection_at": selection_at.isoformat(), "target_count": target_count,
        "candidates": population,
        "baselines": tuple((code, day.isoformat()) for code, day in baseline_dates),
        "observations": tuple(_observation_identity(quotes[key]) for key in sorted(quotes))}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    chosen = ranked[:target_count]
    return PriceTrendSelection(formation_on, selection_at, target_count, population,
        baseline_dates, ranked, chosen, target_count - len(chosen), digest)
