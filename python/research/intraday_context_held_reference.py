"""Independently check EI held questions and their analogue selection.

The question check reconstructs a causal prefix from a supplied session and
audited position. For selection the caller authenticates the complete history,
its source data and declared maturity. Neither check opens continuation prices.
"""

from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from fractions import Fraction
from hashlib import sha256
from math import isfinite
from typing import Literal

from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_campaign_readiness import KST
from research.intraday_chart_inputs import _clock, _need
from research.intraday_context_entry_values import _digest
from research.intraday_context_features import ContextFeatures
from research.intraday_context_features_reference import _same, reference_features
from research.intraday_context_held import ContextHeldQuestion, ContextHeldSelection, HistoricalHeldQuestion
from research.intraday_context_model_inputs import encode
from research.intraday_context_snapshot import _feature
from research.intraday_context_situations import ContextFrame
from research.intraday_context_support_reference import reference_frames
from research.intraday_ed_inputs import MINUTE, SessionInputs
from research.intraday_scenario_held_inputs import HeldDecisionInputs
from research.intraday_scenario_open_reference import _prefix_rows
from research.intraday_scenario_policy import Episode


_SCHEMA = "ei-context-held-pooled-draft-v1"
_DISTANCE = "existing_seven_band_axes_plus_time_over_30_plus_context_one_hot_unit_L1"
_CONTEXTS = ("A", "B", "other")
_MAX_NEIGHBORS = 64
_MAX_PER_DATE = 2


def _stamp(at: datetime | None) -> str | None:
    return None if at is None else at.astimezone(UTC).isoformat()


def _frame_sha(frame: ContextFrame) -> str:
    prefix = [[_stamp(row.start_at), _stamp(row.available_at), row.price_state, row.volume_state,
               None if row.prices is None else list(map(str, row.prices)),
               None if row.volume is None else str(row.volume)] for row in frame.prefix]
    return sha256(encode([frame.symbol, _stamp(frame.opening), _stamp(frame.available_at), frame.classification,
                         frame.phase, frame.reason, frame.prefix_observed_on_grid, prefix])).hexdigest()


def _held_sha(query: ContextHeldQuestion) -> str:
    held, episode = query.held, query.episode
    book = episode.book
    chart = [held.symbol, _stamp(held.decision_at), _stamp(held.entered_at), _stamp(held.planning_cap),
             held.attempt, held.status, str(held.entry_in_bands), str(held.stop_in_bands), held.half_reduced,
             str(held.remaining_fraction), held.minutes_held, held.minutes_remaining,
             [list(map(str, row)) for row in held.minutes], [list(map(str, row)) for row in held.five_minute_bars],
             [[issue.symbol, _stamp(issue.minute), issue.reason] for issue in held.issues]]
    lots = [[lot.lot_id, lot.symbol, lot.original_quantity, lot.quantity, str(lot.entry_price),
             str(lot.original_entry_cost), lot.half_reduced] for lot in book.lots]
    exits = [[leg.lot_id, leg.symbol, leg.quantity, str(leg.entry_basis), str(leg.allocated_entry_cost),
              str(leg.gross_proceeds)] for leg in book.realized_exits]
    position = [episode.symbol, *map(_stamp, (episode.start_at, episode.end_at, episode.last_at, episode.entered_at,
                episode.closed_at)), str(episode.risk_budget), episode.cost_bps, episode.first_lot_id,
                episode.attempts, str(episode.stop), book.strategy, str(book.initial_capital), str(book.cash),
                book.next_lot_id, book.inventory_uncertain, book.indeterminate_reasons, lots, exits]
    return sha256(encode([_SCHEMA, query.entry_feature_sha256, query.current_frame_sha256, query.origin_context,
        query.current_context, _stamp(query.opening), _stamp(query.closing), query.status, query.reasons,
        chart, position])).hexdigest()


def verify_held_question(query: ContextHeldQuestion, *, original_entry: ContextFeatures,
                         original_episode: Episode, original_session: SessionInputs) -> ContextHeldQuestion:
    """Check a scheduled held question against the original causal prefix.

    The caller binds the initial question, current evaluation clock and
    position to the audited opportunity and fill/decision ledger.
    """
    _need(type(query) is ContextHeldQuestion and type(query.held) is HeldDecisionInputs
          and type(original_entry) is ContextFeatures and type(original_episode) is Episode
          and type(original_session) is SessionInputs, "EI_held_reference_original_inputs_required")
    _coordinate(query)
    entry, episode, session = original_entry, original_episode, original_session
    question = entry.question
    origin_at, at = question.episode_origin_at, query.held.decision_at
    entered, cap = episode.entered_at, episode.end_at
    for clock in (origin_at, entry.forecast_at, entered, cap, episode.last_at, session.opening, session.closing):
        _clock(clock)
    symbol, anchor = question.anchor_id
    _need(_same(query.entry, entry) and _same(query.episode, episode) and entry.status == "ready"
          and question.economic_priority and not question.unsupported_reasons
          and session.symbol == episode.symbol == symbol and session.opening == anchor.start_at == query.opening
          and session.closing == query.closing
          and session.opening <= origin_at <= episode.start_at == entry.forecast_at < entered <= at - 3 * MINUTE
          and entered == episode.start_at + MINUTE and entered <= episode.last_at < at
          and at < cap == question.path.end_at < session.closing and cap <= entered + 30 * MINUTE
          and type(episode.attempts) is int and episode.attempts == 1 and episode.pending is None
          and episode.closed_at is None and not episode.book.indeterminate and not episode.book.inventory_uncertain
          and len(episode.book.lots) == 1, "EI_held_reference_original_entry_or_position_differs")
    lot, = episode.book.lots
    level, band = question.path.level, question.path.band
    _need(type(level) is Fraction and type(band) is Fraction and level > 0 and band > 0
          and lot.symbol == symbol and lot.lot_id == episode.first_lot_id
          and 0 < episode.stop == question.path.stop < lot.entry_price < question.path.target
          and lot.entry_price <= question.start_close + band, "EI_held_reference_original_lot_or_basis_differs")

    frames = {frame.available_at: frame for frame in reference_frames(session, at)
              if frame.available_at in (origin_at, entry.forecast_at, at)}
    _need(all(clock in frames for clock in (origin_at, entry.forecast_at, at)), "EI_held_reference_clock_off_grid")
    origin, current = frames[origin_at], frames[at]
    _need(_same(reference_features(frames[entry.forecast_at], question), entry)
          and origin.classification == question.episode_origin_context and _same(origin.anchor, anchor)
          and (current.anchor is None or _same(current.anchor, anchor)),
          "EI_held_reference_original_context_or_feature_differs")

    end = (at - session.opening) // MINUTE - 2
    rows, issues = _prefix_rows(session, range(end - 20, end), at)
    minutes = tuple(tuple((Fraction(price) - level) / band for price in row.ohlc)
                    for row in rows) if not issues else ()
    coarse = tuple((group[0][0], max(bar[1] for bar in group), min(bar[2] for bar in group), group[-1][3])
                   for group in (minutes[i:i + 5] for i in range(0, len(minutes), 5)))
    held = HeldDecisionInputs(symbol, at, entered, cap, 1, "unknown" if issues else "ready",
        (lot.entry_price - level) / band, (episode.stop - level) / band, lot.half_reduced,
        Fraction(lot.quantity, lot.original_quantity), (at - entered) // MINUTE, (cap - at) // MINUTE,
        minutes, coarse, issues)
    reasons = tuple(reason for condition, reason in (
        (current.classification not in _CONTEXTS, "current_context_" + current.classification),
        (current.metrics is not None and current.metrics.invalidated_at is not None, "original_anchor_invalidated"),
        (bool(issues), "held_chart_prefix_unknown"),
    ) if condition)
    expected = ContextHeldQuestion(entry, sha256(encode(_feature(entry))).hexdigest(), _frame_sha(current),
        origin.classification, current.classification, held, episode, session.opening, session.closing,
        "unsupported" if reasons else "ready", reasons, query.input_sha256)
    _need(_same(query, expected) and query.input_sha256 == _held_sha(expected), "EI_held_reference_question_differs")
    return expected


def _coordinate(question: ContextHeldQuestion) -> tuple[str, str, datetime]:
    _need(type(question) is ContextHeldQuestion and question.status in ("ready", "unsupported"),
          "EI_held_reference_bound_question_required")
    held = question.held
    for at in (question.opening, question.closing, held.entered_at, held.decision_at, held.planning_cap):
        _clock(at)
    _digest(question.input_sha256)
    day = held.decision_at.astimezone(KST).date()
    _need(question.opening.astimezone(KST).date() == question.closing.astimezone(KST).date() == day
          and question.opening <= held.entered_at < held.decision_at < held.planning_cap < question.closing
          and type(held.symbol) is str and bool(held.symbol)
          and type(held.attempt) is int and held.attempt == 1
          and type(held.minutes_held) is int and type(held.minutes_remaining) is int
          and held.minutes_held >= 3 and held.minutes_remaining >= 1
          and held.minutes_held + held.minutes_remaining <= 30
          and held.decision_at - held.entered_at == held.minutes_held * MINUTE
          and held.planning_cap - held.decision_at == held.minutes_remaining * MINUTE
          and question.origin_context in _CONTEXTS
          and (question.status != "ready" or (held.status == "ready" and question.current_context in _CONTEXTS)),
          "EI_held_reference_question_clock_or_context_differs")
    return day.isoformat(), held.symbol, held.decision_at.astimezone(UTC)


def _axes(question: ContextHeldQuestion, mode: str) -> tuple[float, ...]:
    held = question.held
    bars = held.minutes
    _need(type(bars) is tuple and len(bars) == 20
          and all(type(bar) is tuple and len(bar) == 4 and all(type(p) is Fraction for p in bar) for bar in bars)
          and type(held.entry_in_bands) is Fraction and type(held.stop_in_bands) is Fraction,
          "EI_held_reference_chart_axes_differs")
    latest = bars[-1][3]
    values = tuple(float(value) for value in (
        latest, latest - held.entry_in_bands, latest - held.stop_in_bands,
        Fraction(sum(bar[3] > 1 for bar in bars), len(bars)),
        max(bar[1] for bar in bars) - min(bar[2] for bar in bars), latest - bars[0][0], latest - bars[-5][0],
    )) + (held.minutes_held / 30, held.minutes_remaining / 30)
    if mode == "chart_context":
        values += tuple(float(context == name) for context in (question.origin_context, question.current_context)
                        for name in _CONTEXTS)
    _need(all(isfinite(value) for value in values), "EI_held_reference_nonfinite_distance_axis")
    return values


def verify_held_selection(selection: ContextHeldSelection, *, query: ContextHeldQuestion,
                          history: Iterable[HistoricalHeldQuestion], fold: ChronologicalFold,
                          mode: Literal["chart", "chart_context"]) -> ContextHeldSelection:
    """Reject a saved selection unless all rows, hashes and weights match."""
    _need(type(selection) is ContextHeldSelection and mode in ("chart", "chart_context")
          and type(fold) is ChronologicalFold and fold.training_sessions and fold.test_sessions,
          "EI_held_reference_typed_selection_and_fold_required")
    _clock(fold.first_test_opening)
    cutoff = fold.first_test_opening
    training, test = fold.training_sessions, fold.test_sessions
    _need(tuple(sorted(set(training))) == training and tuple(sorted(set(test))) == test
          and training[-1] < test[0] == cutoff.astimezone(KST).date().isoformat(),
          "EI_held_reference_chronological_fold_differs")
    day, _, decision = _coordinate(query)
    _need(day in test and decision >= cutoff, "EI_held_reference_query_outside_evaluation_fold")

    coordinates = {}
    source_pins = {}
    training_days = set(training)
    for row in history:
        _need(type(row) is HistoricalHeldQuestion, "EI_held_reference_bound_history_required")
        key = _coordinate(row.question)
        held = row.question.held
        _clock(row.coverage_end)
        _digest(row.source_sha256)
        _need(key not in coordinates and key[0] in training_days and key[2] < cutoff
              and held.decision_at < row.coverage_end <= held.planning_cap
              and held.half_reduced is False and held.remaining_fraction == 1,
              "EI_held_reference_history_identity_or_coverage_differs")
        _need(source_pins.setdefault(key[:2], row.source_sha256) == row.source_sha256,
              "EI_held_reference_original_source_changed")
        if row.actual_mature_at is None:
            _need(type(row.unsupported_reason) is str and bool(row.unsupported_reason.strip())
                  and len(row.unsupported_reason) <= 128, "EI_held_reference_missing_maturity_reason")
        else:
            _clock(row.actual_mature_at)
            _need(row.unsupported_reason is None and row.coverage_end + 3 * MINUTE <= row.actual_mature_at < cutoff,
                  "EI_held_reference_maturity_differs")
        coordinates[key] = row

    pool_rows, eligible = [], []
    excluded: Counter[str] = Counter()
    required = query.held.planning_cap - query.held.decision_at
    for key, row in sorted(coordinates.items()):
        pool_rows.append([row.question.input_sha256, _stamp(row.coverage_end), _stamp(row.actual_mature_at),
                          row.source_sha256, row.unsupported_reason])
        if row.question.status != "ready":
            excluded["training_question_unsupported"] += 1
        elif row.actual_mature_at is None:
            excluded["unmatured: " + row.unsupported_reason] += 1
        elif row.coverage_end - key[2] < required:
            excluded["remaining_time_coverage_short"] += 1
        else:
            eligible.append((key, row))
    pool_sha = sha256(encode([_SCHEMA, _DISTANCE, list(training), list(test), _stamp(cutoff), pool_rows])).hexdigest()
    eligible_sha = sha256(encode([pool_sha, [row.question.input_sha256 for _, row in eligible]])).hexdigest()

    picked, distances = [], []
    counts: Counter[str] = Counter()
    if query.status == "ready" and eligible:
        query_axes = _axes(query, mode)
        ranking = []
        for key, row in eligible:
            distance = sum(abs(a - b) for a, b in zip(_axes(row.question, mode), query_axes, strict=True))
            _need(isfinite(distance), "EI_held_reference_nonfinite_distance")
            ranking.append((distance, key, row))
        seen = set()
        for distance, key, row in sorted(ranking):
            source_day = key[0]
            if key[:2] in seen or counts[source_day] >= _MAX_PER_DATE:
                continue
            seen.add(key[:2])
            counts[source_day] += 1
            picked.append(row)
            distances.append(distance)
            if len(picked) == _MAX_NEIGHBORS:
                break

    weights = (Fraction(1, len(picked)),) * len(picked) if picked else ()
    date_weights = tuple((day, Fraction(count, len(picked))) for day, count in sorted(counts.items()))
    dates = len(date_weights)
    status = "query_unsupported" if query.status != "ready" else "ready" if picked else "no_eligible_paths"
    precision = ("no_supported_training_dates" if not dates else "single_training_date_inconclusive" if dates == 1
                 else "exploratory_date_concentration_not_independent_trials")
    effective_dates = Fraction(len(picked) ** 2, sum(count * count for count in counts.values())) if picked else None
    expected = ContextHeldSelection(mode, query.input_sha256, pool_sha, eligible_sha, len(coordinates), len(eligible),
        tuple(sorted(excluded.items())), tuple(picked), tuple(distances), weights, date_weights, dates,
        effective_dates, status, precision)
    _need(selection == expected and all(type(value) is int for value in (
              selection.candidate_rows, selection.eligible_rows, selection.distinct_dates,
              *(count for _, count in selection.excluded_by_reason)))
          and all(type(distance) is float for distance in selection.distances)
          and all(type(weight) is Fraction for weight in (*selection.weights, *(w for _, w in selection.date_weights)))
          and (selection.effective_date_count is None or type(selection.effective_date_count) is Fraction),
          "EI_held_reference_selection_differs")
    return expected
