"""Pure KRX raw-row decoding for the separately planned LG/LX episode.

Future IO may reuse activity_price_parity.cache_receipts/raw_coordinate/load_raw;
this module only accepts already parsed rows and declared response provenance.
It never replaces BM/BP quotes, supplies KIS storage labels, models historical
availability, builds a quote view or runs accounting/replay.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import date, datetime
from decimal import Decimal
import re

from data import krx_openapi_probe as probe

TARGET_CODES = frozenset({"003550", "383800"})
RAW_FIELDS = ("TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC", "ACC_TRDVOL", "ACC_TRDVAL")
assert set(RAW_FIELDS) <= set(probe.TRADE_FIELDS)


@dataclass(frozen=True)
class RawQuoteObservation:
    """Immutable raw units; unresolved/zero-price bars expose no usable prices."""

    code: str
    requested_bas_dd: str
    returned_bas_dd: str | None
    row_position: int | None
    row_positions: tuple[int, ...]
    status: str
    state: str
    issues: tuple[str, ...]
    original_texts: tuple[tuple[str, str | None], ...]
    canonical_decimal_strings: tuple[tuple[str, str | None], ...]
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal | None
    turnover: Decimal | None
    observation_date: str | None
    available_at: None
    retrieved_at: str
    source: str
    is_final: None
    data_vintage: str
    evidence_level: str
    response_sha256: str
    source_public_available_at: None = None
    modeled_available_at: None = None
    availability_assumption: str = "not_applied_by_raw_decoder"
    price_unit: str = "KRW_per_raw_share"
    volume_unit: str = "raw_shares"
    turnover_unit: str = "KRW"
    quotation_basis: str = "KRX_unadjusted_raw"
    market_state_cause: str = "unknown"
    source_truth_certified: bool = False
    historical_publication_certified: bool = False

    @property
    def provenance(self) -> dict:
        return {name: getattr(self, name) for name in ("observation_date", "available_at", "retrieved_at", "source",
            "is_final", "data_vintage", "evidence_level", "response_sha256", "source_public_available_at",
            "modeled_available_at", "availability_assumption")}


def _coordinates(code: str, bas_dd: str, service: str, retrieved_at: str, response_sha256: str) -> None:
    if type(code) is not str or code not in TARGET_CODES or type(service) is not str or service not in probe.SERVICES[:2]:
        raise ValueError("registered LG/LX raw trading coordinate required")
    if type(bas_dd) is not str or not re.fullmatch(r"[0-9]{8}", bas_dd) or date.fromisoformat(bas_dd).strftime("%Y%m%d") != bas_dd:
        raise ValueError("canonical request date required")
    if type(retrieved_at) is not str or datetime.fromisoformat(retrieved_at).utcoffset() is None:
        raise ValueError("timezone-aware retrieval receipt required")
    if type(response_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", response_sha256):
        raise ValueError("declared retained response SHA-256 required")


def _empty(code, bas_dd, service, retrieved_at, response_sha256, issues, positions=()):
    return RawQuoteObservation(code=code, requested_bas_dd=bas_dd, returned_bas_dd=None,
        row_position=positions[0] if len(positions) == 1 else None, row_positions=positions, status="unresolved", state="unresolved",
        issues=tuple(issues), original_texts=tuple((field, None) for field in RAW_FIELDS),
        canonical_decimal_strings=tuple((field, None) for field in RAW_FIELDS), open=None, high=None, low=None, close=None,
        volume=None, turnover=None, observation_date=None, available_at=None, retrieved_at=retrieved_at,
        source="KRX OpenAPI " + service, is_final=None, data_vintage="current_retrieval_sha256:" + response_sha256,
        evidence_level="observed_current_api_not_historical", response_sha256=response_sha256)


def _number(value, field):
    if value is None:
        return None, None, "null:" + field
    if type(value) is not str or not probe.NUMBER.fullmatch(value):
        return None, None, "invalid_number_text:" + field
    number = Decimal(value.replace(",", ""))
    if not number.is_finite() or number.is_signed():
        return None, None, "negative_or_nonfinite:" + field
    # String conversion/comparison does not use ambient Decimal arithmetic.
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return number, text, None


def decode_raw_episode_row(row: dict, *, code: str, bas_dd: str, retrieved_at: str,
                           response_sha256: str, row_position: int, service: str = "stk_bydd_trd") -> RawQuoteObservation:
    """Decode one declared coordinate; data errors are unresolved, never zero fills."""
    _coordinates(code, bas_dd, service, retrieved_at, response_sha256)
    if type(row) is not dict or type(row_position) is not int or row_position < 0:
        raise ValueError("parsed row and nonnegative row position required")
    original, canonical, numbers, issues = [], [], [], []
    for field in RAW_FIELDS:
        value = row.get(field)
        original.append((field, value if type(value) is str else None))
        number, text, issue = _number(value, field)
        numbers.append(number); canonical.append((field, text))
        if issue:
            issues.append(issue)
    if row.get("ISU_CD") != code:
        issues.append("issue_code_mismatch")
    if row.get("BAS_DD") != bas_dd:
        issues.append("observation_date_mismatch")
    result = replace(_empty(code, bas_dd, service, retrieved_at, response_sha256, issues, (row_position,)),
        returned_bas_dd=row.get("BAS_DD") if type(row.get("BAS_DD")) is str else None,
        original_texts=tuple(original), canonical_decimal_strings=tuple(canonical))
    if issues:
        return result
    opening, high, low, close, volume, turnover = numbers
    prices = opening, high, low, close
    if all(value == 0 for value in prices) and volume == turnover == 0:
        return replace(result, status="observed", state="observed_no_trade_zero_prices", observation_date=date.fromisoformat(bas_dd).isoformat(),
            volume=volume, turnover=turnover)
    if any(value == 0 for value in prices):
        return replace(result, issues=("mixed_or_active_zero_prices",))
    if not low <= min(opening, close) <= max(opening, close) <= high:
        return replace(result, issues=("invalid_ohlc_bounds",))
    flat = opening == high == low == close
    if volume > 0 and turnover > 0:
        state = "locked" if flat else "observed"
    elif flat and volume == turnover == 0:
        state = "frozen"
    else:
        return replace(result, issues=("inconsistent_volume_turnover_or_nonflat_no_trade",))
    return replace(result, status="observed", state=state, observation_date=date.fromisoformat(bas_dd).isoformat(),
        open=opening, high=high, low=low, close=close, volume=volume, turnover=turnover)


def extract_raw_episode_targets(rows: list[dict], item: dict, retrieved_at: str,
                                response_sha256: str) -> tuple[RawQuoteObservation, ...]:
    """Select fixed LG/LX targets without choosing a duplicate or inventing a date."""
    if type(rows) is not list or any(type(row) is not dict for row in rows):
        raise ValueError("parsed raw response rows required")
    if type(item) is not dict or set(item) != {"service", "bas_dd", "targets"} or type(item["targets"]) is not list:
        raise ValueError("exact raw target request required")
    targets = item["targets"]
    if not targets or any(type(code) is not str or code not in TARGET_CODES for code in targets) or len(set(targets)) != len(targets):
        raise ValueError("unique registered LG/LX targets required")
    result = []
    for code in targets:
        _coordinates(code, item["bas_dd"], item["service"], retrieved_at, response_sha256)
        matches = [(position, row) for position, row in enumerate(rows) if row.get("ISU_CD") == code]
        if len(matches) == 1:
            position, row = matches[0]
            result.append(decode_raw_episode_row(row, code=code, bas_dd=item["bas_dd"], service=item["service"],
                retrieved_at=retrieved_at, response_sha256=response_sha256, row_position=position))
        else:
            reason = "duplicate_target" if matches else "target_absent_cause_unknown"
            result.append(_empty(code, item["bas_dd"], item["service"], retrieved_at, response_sha256,
                (reason,), tuple(position for position, _ in matches)))
    return tuple(result)


def _same_typed(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is tuple:
        return len(left) == len(right) and all(_same_typed(a, b) for a, b in zip(left, right))
    if type(left) is Decimal:
        return left.as_tuple() == right.as_tuple()
    return left == right


def validate_raw_observation(value: RawQuoteObservation) -> RawQuoteObservation:
    """Recompute the immutable decoder contract, without certifying response bytes.

    Unresolved records retain no usable prices. For a rejected non-string field,
    its original text is None; its issue distinguishes rejection from null.
    """
    if (type(value) is not RawQuoteObservation or type(value.source) is not str
            or type(value.issues) is not tuple or any(type(issue) is not str for issue in value.issues)):
        raise ValueError("raw observation required")
    service = value.source.removeprefix("KRX OpenAPI ")
    _coordinates(value.code, value.requested_bas_dd, service, value.retrieved_at, value.response_sha256)
    positions = value.row_positions
    if type(positions) is not tuple or any(type(position) is not int or position < 0 for position in positions):
        raise ValueError("immutable raw row positions required")
    if value.issues in (("duplicate_target",), ("target_absent_cause_unknown",)):
        duplicate = value.issues == ("duplicate_target",)
        if (duplicate and (len(positions) < 2 or tuple(sorted(set(positions))) != positions)) or (not duplicate and positions):
            raise ValueError("raw target selection positions disagree")
        expected = _empty(value.code, value.requested_bas_dd, service, value.retrieved_at, value.response_sha256, value.issues, positions)
    else:
        if type(value.row_position) is not int or positions != (value.row_position,):
            raise ValueError("unique raw row position required")
        if type(value.original_texts) is not tuple or len(value.original_texts) != len(RAW_FIELDS):
            raise ValueError("exact immutable raw source texts required")
        row = {"ISU_CD": "mismatched" if "issue_code_mismatch" in value.issues else value.code, "BAS_DD": value.returned_bas_dd}
        for field, pair in zip(RAW_FIELDS, value.original_texts):
            if type(pair) is not tuple or len(pair) != 2 or pair[0] != field or (pair[1] is not None and type(pair[1]) is not str):
                raise ValueError("exact immutable raw source texts required")
            row[field] = 0 if pair[1] is None and "invalid_number_text:" + field in value.issues else pair[1]
        expected = decode_raw_episode_row(row, code=value.code, bas_dd=value.requested_bas_dd, service=service,
            retrieved_at=value.retrieved_at, response_sha256=value.response_sha256, row_position=value.row_position)
    if any(not _same_typed(getattr(value, field.name), getattr(expected, field.name)) for field in fields(value)):
        raise ValueError("raw observation differs from source-text recomputation")
    return value
