"""EI pooled held-state analogues, separate from initial neural probabilities.

This source-only component has no file, market or order reader. Callers supply
authenticated immutable sessions and source digests. Price continuation uses
the existing twenty-minute band features and close-ratio path transfer; it is
not a continuation of the initial thirty-minute context-sequence prediction.
Small samples remain exploratory rather than being silently made no-trades.
"""

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from fractions import Fraction
from hashlib import sha256
from math import isfinite
from typing import Literal

from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_campaign_readiness import KST
from research.intraday_chart_inputs import _clock, _need
from research.intraday_context_entry import context_entry_feature_sha256
from research.intraday_context_entry_values import _digest
from research.intraday_context_features import ContextFeatures, project_context_features
from research.intraday_context_model_inputs import encode
from research.intraday_context_situations import ContextFrame, context_frame
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_scenario_action_projection import ChartActionAssessment, _on_selected_paths
from research.intraday_scenario_analogue_roster import HeldAnalogueSelection, _bounded_episodes, _held_day, _held_state
from research.intraday_scenario_held_inputs import HeldDecisionInputs, held_decision_inputs
from research.intraday_scenario_management_prices_reference import audited_bar_reader, reconstruct_held_values
from research.intraday_scenario_policy import Episode


SCHEMA = "ei-context-held-pooled-draft-v1"
MODES = ("chart", "chart_context")
CONTEXTS = ("A", "B", "other")
DOWNSIDE_PENALTY = Fraction(1)
DISTANCE = "existing_seven_band_axes_plus_time_over_30_plus_context_one_hot_unit_L1"
Mode = Literal["chart", "chart_context"]
ReadPrior = Callable[[str, str, str], SessionInputs | None]


@dataclass(frozen=True)
class ContextHeldQuestion:
    entry: ContextFeatures
    entry_feature_sha256: str
    current_frame_sha256: str
    origin_context: str
    current_context: str
    held: HeldDecisionInputs
    episode: Episode
    opening: datetime
    closing: datetime
    status: Literal["ready", "unsupported"]
    reasons: tuple[str, ...]
    input_sha256: str

    @property
    def question_id(self) -> tuple:
        return (self.entry.question_id, self.episode.first_lot_id, self.held.attempt,
                self.held.decision_at, self.held.planning_cap)

    @property
    def coordinate(self) -> tuple[str, str, datetime]:
        return self.opening.astimezone(KST).date().isoformat(), self.held.symbol, self.held.decision_at.astimezone(UTC)


@dataclass(frozen=True)
class HistoricalHeldQuestion:
    question: ContextHeldQuestion
    coverage_end: datetime
    actual_mature_at: datetime | None
    source_sha256: str
    unsupported_reason: str | None = None


@dataclass(frozen=True)
class ContextHeldSelection:
    mode: Mode
    query_sha256: str
    pool_sha256: str
    eligible_sha256: str
    candidate_rows: int
    eligible_rows: int
    excluded_by_reason: tuple[tuple[str, int], ...]
    sources: tuple[HistoricalHeldQuestion, ...]
    distances: tuple[float, ...]
    weights: tuple[Fraction, ...]
    date_weights: tuple[tuple[str, Fraction], ...]
    distinct_dates: int
    effective_date_count: Fraction | None
    status: Literal["ready", "query_unsupported", "no_eligible_paths"]
    precision_reason: str


@dataclass(frozen=True)
class ContextHeldAssessment:
    selection: ContextHeldSelection
    projection: ChartActionAssessment | None
    reason: str
    unsupported_mass_by_action: tuple[tuple[str, Fraction], ...]
    unassessed_mass: Fraction
    independently_verified: bool | None


def _stamp(value: datetime | None):
    return None if value is None else value.astimezone(UTC).isoformat()


def _hash(value) -> str:
    return sha256(encode(value)).hexdigest()


def _frame_digest(frame: ContextFrame) -> str:
    return _hash([frame.symbol, _stamp(frame.opening), _stamp(frame.available_at), frame.classification,
        frame.phase, frame.reason, frame.prefix_observed_on_grid,
        [[_stamp(row.start_at), _stamp(row.available_at), row.price_state, row.volume_state,
          None if row.prices is None else list(map(str, row.prices)),
          None if row.volume is None else str(row.volume)] for row in frame.prefix]])


def _question_digest(question: ContextHeldQuestion) -> str:
    held, episode = question.held, question.episode
    book = episode.book
    return _hash([SCHEMA, question.entry_feature_sha256, question.current_frame_sha256,
        question.origin_context, question.current_context, _stamp(question.opening), _stamp(question.closing),
        question.status, question.reasons,
        [held.symbol, *map(_stamp, (held.decision_at, held.entered_at, held.planning_cap)), held.attempt, held.status,
         str(held.entry_in_bands), str(held.stop_in_bands), held.half_reduced, str(held.remaining_fraction),
         held.minutes_held, held.minutes_remaining, [[str(x) for x in row] for row in held.minutes],
         [[str(x) for x in row] for row in held.five_minute_bars],
         [[item.symbol, _stamp(item.minute), item.reason] for item in held.issues]],
        [episode.symbol, *map(_stamp, (episode.start_at, episode.end_at, episode.last_at, episode.entered_at, episode.closed_at)),
         str(episode.risk_budget), episode.cost_bps, episode.first_lot_id, episode.attempts, str(episode.stop),
         book.strategy, str(book.initial_capital), str(book.cash), book.next_lot_id, book.inventory_uncertain,
         book.indeterminate_reasons,
         [[lot.lot_id, lot.symbol, lot.original_quantity, lot.quantity, str(lot.entry_price),
           str(lot.original_entry_cost), lot.half_reduced] for lot in book.lots],
         [[leg.lot_id, leg.symbol, leg.quantity, str(leg.entry_basis), str(leg.allocated_entry_cost),
           str(leg.gross_proceeds)] for leg in book.realized_exits]]])


def _validate_question(question: ContextHeldQuestion) -> None:
    _need(type(question) is ContextHeldQuestion and type(question.entry) is ContextFeatures
          and type(question.held) is HeldDecisionInputs and type(question.episode) is Episode,
          "EI_held_typed_question_required")
    for digest in (question.entry_feature_sha256, question.current_frame_sha256, question.input_sha256):
        _digest(digest)
    _need(question.entry_feature_sha256 == context_entry_feature_sha256(question.entry)
          and question.input_sha256 == _question_digest(question), "EI_held_question_digest_differs")
    entry, held, episode = question.entry, question.held, question.episode
    _held_day(held)
    for at in (question.opening, question.closing, entry.forecast_at, episode.last_at):
        _clock(at)
    _need(entry.status == "ready" and question.origin_context == entry.question.episode_origin_context
          and entry.question.economic_priority and not entry.question.unsupported_reasons
          and question.origin_context in CONTEXTS and question.current_context in (*CONTEXTS, "unknown", "unclassified")
          and episode.symbol == held.symbol == entry.question.anchor_id[0]
          and question.opening == entry.question.anchor_id[1].start_at
          and episode.start_at == entry.forecast_at and episode.end_at == held.planning_cap == entry.question.path.end_at
          and episode.entered_at == held.entered_at == episode.start_at + MINUTE
          and episode.entered_at <= episode.last_at < held.decision_at < held.planning_cap < question.closing
          and question.opening.astimezone(KST).date() == question.closing.astimezone(KST).date()
          == held.decision_at.astimezone(KST).date()
          and episode.attempts == held.attempt == 1 and episode.pending is None and episode.closed_at is None
          and not episode.book.indeterminate and len(episode.book.lots) == 1,
          "EI_held_original_entry_or_position_binding_differs")
    lot, = episode.book.lots
    anchor = entry.question.anchor_id[1]
    _need(lot.symbol == episode.symbol and lot.lot_id == episode.first_lot_id
          and episode.stop == entry.question.path.stop and 0 < episode.stop < lot.entry_price < entry.question.path.target
          and lot.entry_price <= entry.question.start_close + anchor.band
          and held.entry_in_bands == (lot.entry_price - anchor.price) / anchor.band
          and held.stop_in_bands == (episode.stop - anchor.price) / anchor.band
          and held.half_reduced == lot.half_reduced
          and held.remaining_fraction == Fraction(lot.quantity, lot.original_quantity),
          "EI_held_inventory_or_price_basis_differs")
    _need(question.status in ("ready", "unsupported") and type(question.reasons) is tuple
          and all(type(reason) is str and reason for reason in question.reasons)
          and ((question.status == "ready" and not question.reasons and held.status == "ready"
                and question.current_context in CONTEXTS)
               or (question.status == "unsupported" and bool(question.reasons))), "EI_held_support_state_differs")
    if question.status == "ready":
        _held_state(held)


def project_context_held(entry: ContextFeatures, current: ContextFrame, episode: Episode, session: SessionInputs,
                         *, entry_feature_sha256: str) -> ContextHeldQuestion:
    """Bind a fresh held question to the original entry and a causal current frame.

    Only prefix keys through current.available_at - two minutes are read. Initial
    path labels are not accepted. A resolved initial path therefore cannot stop
    or fabricate this remaining-time question.
    """
    _need(type(entry) is ContextFeatures and type(current) is ContextFrame and type(episode) is Episode
          and type(session) is SessionInputs, "EI_held_typed_projection_required")
    _digest(entry_feature_sha256)
    _need(entry.status == "ready" and entry_feature_sha256 == context_entry_feature_sha256(entry),
          "EI_held_entry_feature_binding_differs")
    _need(current.symbol == session.symbol == entry.question.anchor_id[0]
          and current.opening == session.opening == entry.question.anchor_id[1].start_at
          and entry.forecast_at < current.available_at, "EI_held_current_clock_or_symbol_differs")
    original = context_frame(session, decision_at=entry.forecast_at)
    origin = context_frame(session, decision_at=entry.question.episode_origin_at)
    _need(project_context_features(original, entry.question) == entry
          and origin.classification == entry.question.episode_origin_context
          and origin.anchor == entry.question.anchor_id[1], "EI_held_original_context_or_feature_differs")
    _need(context_frame(session, decision_at=current.available_at) == current
          and (current.anchor is None or current.anchor == entry.question.anchor_id[1]),
          "EI_held_current_frame_or_frozen_anchor_differs")
    held = held_decision_inputs(session, episode, level=entry.question.path.level, band=entry.question.path.band,
                               decision_at=current.available_at, planning_cap=entry.question.path.end_at)
    reasons = []
    if current.classification not in CONTEXTS:
        reasons.append("current_context_" + current.classification)
    if current.metrics is not None and current.metrics.invalidated_at is not None:
        reasons.append("original_anchor_invalidated")
    if held.status != "ready":
        reasons.append("held_chart_prefix_unknown")
    result = ContextHeldQuestion(entry, entry_feature_sha256, _frame_digest(current),
        entry.question.episode_origin_context, current.classification, held, episode, session.opening, session.closing,
        "unsupported" if reasons else "ready", tuple(reasons), "")
    result = replace(result, input_sha256=_question_digest(result))
    _validate_question(result)
    return result


def _vector(question: ContextHeldQuestion, mode: Mode) -> tuple[float, ...]:
    _, values = _held_state(question.held)  # Discard EH's exact bucket/group; keep its seven axes.
    values += (question.held.minutes_held / 30, question.held.minutes_remaining / 30)
    if mode == "chart_context":
        values += tuple(float(context == name) for context in (question.origin_context, question.current_context)
                        for name in CONTEXTS)
    _need(all(isfinite(value) for value in values), "EI_held_distance_not_finite")
    return values


class ContextHeldRoster:
    """One immutable mature pool; two fixed distances, no date-count trading gate."""

    def __init__(self, history: Iterable[HistoricalHeldQuestion], *, fold: ChronologicalFold):
        _need(type(fold) is ChronologicalFold and fold.training_sessions and fold.test_sessions,
              "EI_held_chronological_fold_required")
        _clock(fold.first_test_opening)
        _need(tuple(sorted(set(fold.training_sessions))) == fold.training_sessions
              and tuple(sorted(set(fold.test_sessions))) == fold.test_sessions
              and max(fold.training_sessions) < min(fold.test_sessions)
              and fold.first_test_opening.astimezone(KST).date().isoformat() == min(fold.test_sessions),
              "EI_held_training_test_dates_overlap")
        self.fold = fold
        rows, seen, sources = [], set(), {}
        for row in history:
            _need(type(row) is HistoricalHeldQuestion, "EI_held_typed_history_required")
            _validate_question(row.question)
            _digest(row.source_sha256)
            coordinate = row.question.coordinate
            _clock(row.coverage_end)
            _need(coordinate[0] in fold.training_sessions and coordinate[2] < fold.first_test_opening
                  and coordinate not in seen and row.question.held.decision_at < row.coverage_end
                  <= row.question.held.planning_cap, "EI_held_history_identity_or_coverage_differs")
            _need(not row.question.held.half_reduced and row.question.held.remaining_fraction == 1,
                  "EI_held_training_requires_baseline_full_position")
            if row.actual_mature_at is None:
                _need(type(row.unsupported_reason) is str and bool(row.unsupported_reason.strip())
                      and len(row.unsupported_reason) <= 128, "EI_held_unavailable_maturity_reason_required")
            else:
                _clock(row.actual_mature_at)
                _need(row.unsupported_reason is None and row.coverage_end + 3 * MINUTE <= row.actual_mature_at
                      < fold.first_test_opening, "EI_held_path_not_actually_mature")
            source = coordinate[:2]
            _need(source not in sources or sources[source] == row.source_sha256, "EI_held_same_source_digest_changed")
            sources[source] = row.source_sha256
            seen.add(coordinate)
            rows.append(row)
        self.rows = tuple(sorted(rows, key=lambda row: row.question.coordinate))
        self.pool_sha256 = _hash([SCHEMA, DISTANCE, list(fold.training_sessions), list(fold.test_sessions),
            _stamp(fold.first_test_opening), [[row.question.input_sha256, _stamp(row.coverage_end),
            _stamp(row.actual_mature_at), row.source_sha256, row.unsupported_reason] for row in self.rows]])

    def match(self, query: ContextHeldQuestion, *, mode: Mode) -> ContextHeldSelection:
        _validate_question(query)
        _need(mode in MODES and query.coordinate[0] in self.fold.test_sessions
              and query.held.decision_at >= self.fold.first_test_opening, "EI_held_query_mode_or_fold_differs")
        excluded, eligible = Counter(), []
        for row in self.rows:
            if row.question.status != "ready":
                excluded["training_question_unsupported"] += 1
            elif row.actual_mature_at is None:
                excluded["unmatured: " + row.unsupported_reason] += 1
            elif row.coverage_end - row.question.held.decision_at < query.held.planning_cap - query.held.decision_at:
                excluded["remaining_time_coverage_short"] += 1
            else:
                eligible.append(row)
        eligible_digest = _hash([self.pool_sha256, [row.question.input_sha256 for row in eligible]])
        selected, distances, date_weights = (), (), ()
        if query.status == "ready" and eligible:
            target = _vector(query, mode)
            ranked = sorted((sum(abs(a - b) for a, b in zip(_vector(row.question, mode), target, strict=True)),
                             row.question.coordinate, row) for row in eligible)
            selected, _ = _bounded_episodes((coordinate[2].astimezone(KST).date(), coordinate[1], row)
                                             for _, coordinate, row in ranked)
            scores = {row.question.coordinate: distance for distance, _, row in ranked}
            distances = tuple(scores[row.question.coordinate] for row in selected)
            counts = Counter(row.question.coordinate[0] for row in selected)
            date_weights = tuple((day, Fraction(count, len(selected))) for day, count in sorted(counts.items()))
        dates = len(date_weights)
        status = "query_unsupported" if query.status != "ready" else "ready" if selected else "no_eligible_paths"
        return ContextHeldSelection(mode, query.input_sha256, self.pool_sha256, eligible_digest, len(self.rows),
            len(eligible), tuple(sorted(excluded.items())), selected, distances,
            (Fraction(1, len(selected)),) * len(selected) if selected else (), date_weights, dates,
            1 / sum((weight * weight for _, weight in date_weights), Fraction()) if dates else None,
            status, "no_supported_training_dates" if not dates else "single_training_date_inconclusive" if dates == 1
            else "exploratory_date_concentration_not_independent_trials")


def assess_context_held(roster: ContextHeldRoster, query: ContextHeldQuestion, *, mode: Mode,
                        visible_minute: Minute, read_prior_session: ReadPrior) -> ContextHeldAssessment:
    """Select first, then reuse matched action paths and independent raw-price payoff arithmetic.

    The reader receives only selected (day, symbol, expected source digest).
    Missing selected paths keep their weight; there is no replacement or second
    nearest search. Current quantity/reduction state applies to every action.
    """
    _need(type(roster) is ContextHeldRoster and callable(read_prior_session), "EI_held_roster_reader_required")
    selection = roster.match(query, mode=mode)
    at, cap = query.held.decision_at, query.held.planning_cap
    _need(type(visible_minute) is Minute and visible_minute.symbol == query.held.symbol
          and visible_minute.start_at == at - 3 * MINUTE and visible_minute.available_at == at,
          "EI_held_visible_minute_clock_differs")
    if selection.status != "ready":
        return ContextHeldAssessment(selection, None, selection.status, (), Fraction(1), None)
    _need(visible_minute.price_state == "observed" and visible_minute.ohlc is not None
          and query.held.minutes[-1] == tuple((Fraction(value) - query.entry.question.path.level)
              / query.entry.question.path.band for value in visible_minute.ohlc), "EI_held_visible_price_basis_differs")
    coordinates = tuple(row.question.coordinate for row in selection.sources)
    pins = {row.question.coordinate[:2]: row.source_sha256 for row in selection.sources}
    originals = {row.question.coordinate[:2]: row for row in selection.sources}
    checked = {}

    def prior(day, symbol):
        _need((day, symbol) in pins, "EI_held_unselected_source_read")
        key = day, symbol
        if key not in checked:
            source = read_prior_session(day, symbol, pins[key])
            if source is not None:
                record = originals[key]
                original = record.question
                current = context_frame(source, decision_at=original.held.decision_at)
                rebuilt = project_context_held(original.entry, current, original.episode, source,
                                               entry_feature_sha256=original.entry_feature_sha256)
                _need(rebuilt == original, "EI_held_selected_source_prefix_changed")
                # Bind the declared actual maturity to metadata of the whole
                # declared continuation. Missing rows remain missing paths.
                start = original.held.decision_at - 3 * MINUTE
                for offset in range((record.coverage_end - start) // MINUTE + 1):
                    clock = start + offset * MINUTE
                    row = source.minutes.get(clock)
                    if row is not None:
                        _need(type(row) is Minute and row.symbol == symbol and row.start_at == clock,
                              "EI_held_selected_path_row_identity_differs")
                        _clock(row.available_at)
                        _need(clock + 3 * MINUTE <= row.available_at <= record.actual_mature_at
                              < roster.fold.first_test_opening, "EI_held_selected_actual_maturity_differs")
            checked[key] = source
        return checked[key]

    legacy = HeldAnalogueSelection(query.coordinate, coordinates, selection.distinct_dates,
                                   selection.eligible_rows, "ready")
    projection = _on_selected_paths(legacy, coordinates, roster.fold, query.episode, at=at, visible_minute=visible_minute,
        query_opening=query.opening, query_closing=query.closing, cap=cap, read_prior_session=prior,
        downside_penalty=DOWNSIDE_PENALTY)
    query_session = SessionInputs(query.held.symbol, query.opening, query.closing, {visible_minute.start_at: visible_minute})

    def audited(day, symbol):
        return query_session if (day, symbol) == query.coordinate[:2] else prior(day, symbol)

    lot, = query.episode.book.lots
    reason, values = reconstruct_held_values(day=query.coordinate[0], symbol=query.held.symbol, at=at, cap=cap,
        cutoff=roster.fold.first_test_opening, entered_at=query.held.entered_at, quantity=lot.quantity,
        half_reduced=lot.half_reduced, stop=query.episode.stop, downside_penalty=DOWNSIDE_PENALTY,
        sources=coordinates, read_session=audited, read_bar=audited_bar_reader(audited))
    _need(reason == projection.reason and values == (projection.rollout.values if projection.rollout else None),
          "EI_held_independent_payoff_differs")
    unknown = Counter()
    if projection.rollout is not None:
        for outcome in projection.rollout.outcomes:
            if outcome.net_change is None:
                unknown[outcome.action] += outcome.weight
    return ContextHeldAssessment(selection, projection, projection.reason, tuple(sorted(unknown.items())),
                                 Fraction(1) if projection.rollout is None else Fraction(), True)
