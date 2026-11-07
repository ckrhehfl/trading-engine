"""Rebase one selected prior-session path onto the current decision clock."""

from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN
from fractions import Fraction
from functools import lru_cache
from types import MappingProxyType

from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs, _clock, _require, _session, window
from research.intraday_scenario_rollout import AnaloguePath


_KST = timezone(timedelta(hours=9))
_PUBLICATION_LAG = 3 * MINUTE


def rebase_analogue_path(
    source: SessionInputs, source_decision_at: datetime, fold: ChronologicalFold, *,
    key: str, weight: Fraction, decision_at: datetime, visible_minute: Minute,
    query_symbol: str, query_opening: datetime, query_closing: datetime, cap: datetime,
) -> AnaloguePath:
    """Translate historical offsets and rescale by the two visible close marks.

    The caller selects the analogue from predecision evidence and authenticates
    the original source. Missing rows in the selected suffix stay missing,
    including any portion outside its original session. No query suffix or
    volume is needed for these synthetic price paths.
    """
    _session(source)
    _require(type(fold) is ChronologicalFold and fold.training_sessions and fold.test_sessions,
             "eh_analogue_requires_chronological_fold")
    cutoff = fold.first_test_opening
    for at in (source.opening, source.closing, source_decision_at, query_opening, query_closing,
               decision_at, cap, cutoff):
        _clock(at)
        _require(at.second == at.microsecond == 0, "eh_analogue_requires_minute_clock")
    _require(type(key) is str and bool(key) and type(weight) is Fraction and 0 < weight <= 1
             and type(query_symbol) is str and bool(query_symbol), "eh_analogue_invalid_branch_identity")

    source_day = source.opening.astimezone(_KST).date().isoformat()
    query_day = query_opening.astimezone(_KST).date().isoformat()
    first_test_day = cutoff.astimezone(_KST).date().isoformat()
    _require(first_test_day == fold.test_sessions[0] and source_day in fold.training_sessions
             and query_day in fold.test_sessions and source_day < first_test_day <= query_day
             and source_day == source.closing.astimezone(_KST).date().isoformat()
             and query_day == query_closing.astimezone(_KST).date().isoformat()
             and query_opening >= cutoff and (query_day != first_test_day or query_opening == cutoff),
             "eh_analogue_requires_distinct_prior_and_test_sessions")

    source_anchor = source_decision_at - _PUBLICATION_LAG
    query_anchor = decision_at - _PUBLICATION_LAG
    horizon = cap - decision_at
    _require(source.opening <= source_anchor < source_decision_at < source.closing
             and query_opening <= query_anchor < decision_at < cap < query_closing
             and MINUTE <= horizon <= 30 * MINUTE, "eh_analogue_invalid_path_geometry")
    _require(source_decision_at + horizon + _PUBLICATION_LAG < cutoff, "eh_analogue_suffix_not_mature")
    _require(type(visible_minute) is Minute and visible_minute.start_at == query_anchor
             and visible_minute.symbol == query_symbol and visible_minute.available_at == decision_at,
             "eh_analogue_requires_same_clock_query_mark")
    query_mark = window(SessionInputs(query_symbol, query_anchor, query_anchor + MINUTE,
                                    {query_anchor: visible_minute}), 0, 1, decision_at)
    source_mark = window(source, (source_anchor - source.opening) // MINUTE, 1, source_decision_at)
    _require(not query_mark.issues and not source_mark.issues
             and source_mark.minutes[0].available_at == source_decision_at, "eh_analogue_mark_unsupported")
    ratio = Fraction(query_mark.minutes[0].ohlc[3]) / Fraction(source_mark.minutes[0].ohlc[3])
    context = Context(prec=28, rounding=ROUND_HALF_EVEN)

    @lru_cache(maxsize=128)
    def scale(price: Decimal) -> Decimal:
        exact = Fraction(price) * ratio
        return context.divide(Decimal(exact.numerator), Decimal(exact.denominator))

    minutes, opens = {}, {}
    for offset in range(-3, horizon // MINUTE + 1):
        original_at = source_decision_at + offset * MINUTE
        if original_at >= source.closing:
            break
        original = source.minutes.get(original_at)
        if original is None:
            continue
        _require(isinstance(original, Minute) and original.symbol == source.symbol
                 and original.start_at == original_at, "eh_analogue_source_minute_identity_differs")
        _clock(original.available_at)
        _require(original.available_at >= original_at + _PUBLICATION_LAG, "eh_analogue_early_source_publication")
        _require(original.available_at < cutoff, "eh_analogue_suffix_not_mature")
        checked = window(source, (original_at - source.opening) // MINUTE, 1, original.available_at)
        ohlc = None if checked.issues else tuple(scale(price) for price in original.ohlc)
        at = decision_at + offset * MINUTE
        available_at = at + (original.available_at - original_at)
        minutes[at] = Minute(query_symbol, at, available_at, ohlc, None, original.price_state, "missing")
        if offset > 0:
            # Match the requested-open evidence cutoff while retaining late bars
            # for structural decisions after their actual publication.
            opens[at] = Fraction(ohlc[0]) if ohlc is not None and available_at == at + _PUBLICATION_LAG else None

    projected = SessionInputs(query_symbol, query_opening, query_closing, MappingProxyType(minutes))
    return AnaloguePath(key, weight, projected, MappingProxyType(opens))
