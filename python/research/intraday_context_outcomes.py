"""Pure exploratory EA price labels for explicitly authenticated caller inputs.

No IO, source authentication, experiment authorization or trading happens here.
The caller must establish its execution boundary before supplying these inputs;
metadata and an explicit acknowledgement retain that boundary, not prove it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
import re

from research import intraday_context_preparation as preparation


@dataclass(frozen=True)
class OutcomeSource:
    """Caller-authenticated identity and provenance for the supplied minute map."""

    symbol: str
    session_date: date
    policy: str
    price_basis: str
    price_source: preparation.PriceSource
    input_sha256: str
    authorization_reference: str


def exploratory_outcome(
    candidate: preparation.PreparedCandidate,
    minutes: Mapping[datetime, tuple[Decimal, Decimal, Decimal, Decimal] | None],
    *,
    source: OutcomeSource,
    inputs_authenticated: bool,
    opening_prices: Mapping[datetime, Decimal | None] | None = None,
) -> dict:
    """Apply frozen EA arithmetic with assumed fills, never synthetic evidence.

    Source provenance is retained separately from label evidence: even observed
    prices do not certify historical availability, account fills or permission
    to trade. Acknowledgement is a caller assertion, not a cryptographic check.
    All arms share this price label; arm selection and missingness stay intact.

    Optional OPENs must come from the same authenticated raw buckets as minutes.
    The caller retains unique, valid OPENs even when HLC made an OHLC bucket
    unknown; absent/duplicate/conflicting buckets map to None, never a chosen
    duplicate or substituted minute. When supplied, this map is authoritative:
    missing/conflicting OPENs cannot fall back to OHLC. Held minutes still need
    validated complete OHLC and agreement with their corresponding OPEN.
    """
    require = preparation._require
    require(inputs_authenticated is True, "authenticated_outcome_inputs_required")
    require(isinstance(candidate, preparation.PreparedCandidate)
            and isinstance(minutes, Mapping), "invalid_exploratory_inputs")
    require(isinstance(source, OutcomeSource), "outcome_source_required")
    preparation._target(candidate.target)
    target = candidate.target
    require(source.symbol == target.symbol
            and type(source.session_date) is date
            and source.session_date == target.session_date
            and source.policy == target.policy
            and source.price_basis == target.price_basis, "outcome_source_identity_changed")
    require(isinstance(source.input_sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", source.input_sha256) is not None,
            "outcome_input_digest_required")
    require(preparation._text(source.authorization_reference), "outcome_authorization_reference_required")
    preparation._target(preparation.Target(
        target.symbol, target.session_date, target.policy, target.opening,
        target.closing, target.price_basis, source.price_source))
    require(source.price_source.evidence_level != "synthetic", "exploratory_source_must_not_be_synthetic")
    result = preparation._price_outcome(
        candidate, minutes, evidence_level="assumed", error_context="exploratory",
        retrieved_at=source.price_source.retrieved_at, opening_prices=opening_prices)
    return {
        **result,
        "exploratory_only": True,
        "assumed_fill": True,
        "historical_timing_certified": False,
        "inputs_authenticated_by_caller": True,
        "source": asdict(source),
    }
