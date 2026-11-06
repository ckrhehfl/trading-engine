"""Past-only model rows from authenticated, separately sealed EG frames."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from math import isfinite

from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_chart_folds import Key, ObservedLabel
from research.intraday_ed_inputs import MINUTE, _require
from research.intraday_highvol_folds import CandidateSupport, FoldSelection, Support, select_fold
from research.intraday_highvol_inputs import CHART_CHANNELS, MINUTE_SLOTS, PEER_CHANNELS
from research.intraday_highvol_models import ARMS, ModelInput, TrainingRow, decision_state
from research.intraday_highvol_snapshot import HOLD_STEPS, PRIMARY_HORIZON, TIMINGS, _fraction
from research.intraday_pattern_calibration import session_close, session_open


MINIMUM_ROWS = 30
MINIMUM_DAYS = 10
DecisionKey = tuple[str, str, int, int]


@dataclass(frozen=True)
class InferenceRow:
    key: DecisionKey
    decision_at: datetime
    support: Support
    entry_support: Support
    chart: ModelInput | None
    peers: tuple[tuple[float, ...], ...] | None

    def for_arm(self, arm: str) -> ModelInput | None:
        _require(arm in ARMS, "unregistered_eg_model_arm")
        if self.chart is None or self.entry_support not in ("common", "chart_only"):
            return None
        if arm != "TCN_CHART_PEER":
            return self.chart
        if self.entry_support != "common" or self.peers is None:
            return None
        minute = tuple(chart + peer for chart, peer in zip(self.chart.minute, self.peers, strict=True))
        return ModelInput(minute, self.chart.state)


@dataclass(frozen=True)
class FitRow:
    context: InferenceRow
    gross30: Fraction | None = None
    target_first: bool | None = None
    hold_advantage: Fraction | None = None


@dataclass(frozen=True)
class ModelDataset:
    branch: str
    timing: str
    selection: FoldSelection
    training: tuple[FitRow, ...]
    inference: tuple[InferenceRow, ...]
    counts: tuple[tuple[str, int], ...]

    def training_for(self, arm: str) -> tuple[tuple[DecisionKey, TrainingRow], ...]:
        _require(arm in ARMS, "unregistered_eg_model_arm")
        if not self.selection.supported:
            return ()
        rows = []
        for item in self.training:
            model_input = item.context.for_arm(arm)
            _require(model_input is not None, "eg_training_lost_common_support")
            rows.append((item.context.key, TrainingRow(model_input, item.gross30, item.target_first,
                                                      item.hold_advantage)))
        return tuple(rows)

    def predictions_for(self, arm: str) -> tuple[tuple[DecisionKey, ModelInput], ...]:
        _require(arm in ARMS, "unregistered_eg_model_arm")
        return tuple((row.key, value) for row in self.inference if (value := row.for_arm(arm)) is not None)


def _clock(raw: str) -> datetime:
    _require(type(raw) is str, "eg_snapshot_clock_string_required")
    value = datetime.fromisoformat(raw)
    _require(value.utcoffset() is not None and value.isoformat() == raw, "eg_snapshot_clock_not_canonical")
    return value


def _sequence(projection: dict, key: Key, decision: datetime, width: int):
    _require(projection["symbol"] == key[1] and _clock(projection["opening"]) == session_open(key[0])
             and _clock(projection["decision_at"]) == decision
             and _clock(projection["bar_end"]) == decision - 2 * MINUTE,
             "eg_snapshot_context_identity_changed")
    status, sequence = projection["status"], projection["sequence"]
    if status == "unavailable":
        _require(sequence is None, "eg_snapshot_unavailable_sequence_present")
        return None
    _require(status == "available" and type(sequence) is list and len(sequence) == MINUTE_SLOTS
             and all(type(row) is list and len(row) == width
                     and all(type(value) is float and isfinite(value) for value in row) for row in sequence),
             "eg_snapshot_sequence_shape_or_values_changed")
    return tuple(map(tuple, sequence))


def _inputs(frame: dict, timing: str, key: Key) -> tuple[InferenceRow, ...]:
    confirmation = frame["attempt"]["confirmation"]
    decision = _clock(confirmation["decision_at"])
    entry_at = _clock(confirmation["intended_entry_at"])
    stop, target = _fraction(confirmation["stop"]), _fraction(confirmation["target"])
    _require(entry_at == decision + MINUTE and 0 < stop < target, "eg_snapshot_confirmation_changed")
    entry = frame["entry"]
    _require(type(entry) is dict and type(entry["primary_clock_supported"]) is bool,
             "eg_snapshot_entry_context_required")
    inside = entry["primary_clock_supported"]
    _require(inside == (entry_at + PRIMARY_HORIZON * MINUTE < session_close(key[0]) - 15 * MINUTE),
             "eg_snapshot_primary_clock_support_changed")
    holds = frame["holding"]
    _require(type(holds) is list and tuple(row["step_minutes"] for row in holds) == (HOLD_STEPS if inside else ()),
             "eg_snapshot_holding_feature_roster_changed")
    contexts = []
    for step, context in zip((0, *(HOLD_STEPS if inside else ())), (entry, *holds), strict=True):
        clock = _clock(context["decision_at"])
        _require(clock == decision + step * MINUTE and timing in context["peers"],
                 "eg_snapshot_decision_or_timing_changed")
        chart = _sequence(context["chart"], key, clock, len(CHART_CHANNELS))
        peers = _sequence(context["peers"][timing], key, clock, len(PEER_CHANNELS))
        if not inside:
            status = "outside_clock"
        elif chart is None:
            status = "chart_unavailable"
        else:
            status = "common" if peers is not None else "chart_only"
        model_input = None if chart is None or not inside else ModelInput(chart, decision_state(
            holding=bool(step), remaining_minutes=PRIMARY_HORIZON - step,
            close=_fraction(context["last_close"]), stop=stop, target=target,
        ))
        contexts.append(InferenceRow((*key, step), clock, status,
                                     status if step == 0 else contexts[0].support, model_input, peers))
    return tuple(contexts)


def _primary(label: dict, key: Key, context: InferenceRow) -> ObservedLabel | None:
    _require(tuple(label["key"]) == key, "eg_snapshot_training_label_key_changed")
    path = label["primary"]
    _require(type(path) is dict and _clock(path["entry_at"]) == context.decision_at + MINUTE
             and _clock(path["exit_at"]) == context.decision_at + (PRIMARY_HORIZON + 1) * MINUTE,
             "eg_snapshot_primary_label_clock_changed")
    gross, maturity = path["fixed_gross"], path["fixed_mature_at"]
    _require((gross is None) == (maturity is None), "eg_snapshot_primary_label_maturity_missing")
    if gross is None:
        return None
    _require(path["status"] == "admitted", "eg_snapshot_gross_for_unadmitted_entry")
    return ObservedLabel(_fraction(gross), _clock(maturity))


def _target_first(path: dict, cutoff: datetime) -> bool | None:
    touch = path["first_touch"]
    if touch is None or touch["status"] in ("same_minute_unknown", "unavailable"):
        return None
    status = touch["status"]
    _require(status in ("target_first", "stop_first", "time"), "eg_snapshot_target_first_status_changed")
    entry, exit_at = _clock(path["entry_at"]), _clock(path["exit_at"])
    if status == "time":
        _require(touch["minute"] is None, "eg_snapshot_time_exit_has_touch_minute")
        last = exit_at - MINUTE
    else:
        last = _clock(touch["minute"])
        _require(entry <= last < exit_at, "eg_snapshot_touch_outside_path")
    maturity = _clock(touch["mature_at"])
    _require(maturity >= last + 3 * MINUTE, "eg_snapshot_touch_maturity_changed")
    return status == "target_first" if maturity < cutoff else None


def _hold_value(label: dict, context: InferenceRow, primary_maturity: datetime,
                cutoff: datetime) -> tuple[Fraction | None, str]:
    next_open = context.decision_at + MINUTE
    _require(label["step_minutes"] == context.key[3] and _clock(label["decision_at"]) == context.decision_at
             and _clock(label["next_open_at"]) == next_open, "eg_snapshot_hold_label_clock_changed")
    status, value = label["status"], label["value"]
    if status == "next_open_unavailable":
        _require(value is None and label["next_open"] is None and label["mature_at"] is None,
                 "eg_snapshot_unavailable_hold_has_value")
        return None, status
    # The caller is already an admitted training entry with an observed cap.
    _require(status == "available" and _fraction(label["next_open"]) > 0, "eg_snapshot_hold_quote_missing")
    maturity = _clock(label["mature_at"])
    quote_maturity = _clock(label["next_open_mature_at"])
    _require(quote_maturity >= next_open + 3 * MINUTE and maturity == max(primary_maturity, quote_maturity),
             "eg_snapshot_hold_label_maturity_changed")
    return (None, "immature") if maturity >= cutoff else (_fraction(value), "used")


def assemble_fold(
    source_dates: Sequence[str], branch: str, timing: str, fold_index: int,
    feature_frames: Iterable[dict], training_label: Callable[[Key], dict | None],
) -> ModelDataset:
    """Project a fold in session, onset-offset, symbol order with a past-label callback.

    The callback is invoked once per common-support training entry. Test
    outcomes are outside this interface. Holding rows use the same prospective
    context whether or not their next OPEN happens to be known afterward.
    """
    calendar = fixed_calendar(source_dates)
    _require(branch in ("A", "B") and timing in TIMINGS and type(fold_index) is int
             and 1 <= fold_index <= len(calendar.folds) and callable(training_label),
             "eg_registered_dataset_fold_required")
    fold = calendar.folds[fold_index - 1]
    relevant = set(fold.training_sessions) | set(fold.test_sessions)
    source = set(calendar.source_reference_sessions)
    previous = None
    contexts: dict[Key, tuple[InferenceRow, ...]] = {}
    for frame in feature_frames:
        day, symbol, offset = key = tuple(frame["key"])
        order = day, offset, symbol
        _require(type(day) is str and day in source and type(symbol) is str and type(offset) is int
                 and (previous is None or previous < order), "eg_snapshot_feature_order_or_identity_changed")
        previous = order
        timings = frame["timings"]
        _require(type(timings) is list and bool(timings)
                 and timings == [name for name in TIMINGS if name in timings],
                 "eg_snapshot_original_membership_changed")
        if (day not in relevant or timing not in timings or frame["attempt"]["scenario"] != branch
                or frame["attempt"]["state"] != "confirmed"):
            continue
        contexts[key] = _inputs(frame, timing, key)

    candidates = []
    for key, (entry, *_) in contexts.items():
        candidates.append(CandidateSupport(key, entry.decision_at, entry.chart is not None, entry.peers is not None))
    labels: dict[Key, dict] = {}

    def read_primary(key: Key) -> ObservedLabel | None:
        label = training_label(key)
        if label is None:
            return None
        labels[key] = label
        return _primary(label, key, contexts[key][0])

    selection = select_fold(source_dates, fold_index, candidates, read_primary,
                            min_train_rows=MINIMUM_ROWS, min_train_days=MINIMUM_DAYS)
    inference = []
    for row in selection.evaluation:
        entry, *holding = contexts[row.key]
        _require(row.status == entry.support, "eg_snapshot_primary_clock_support_changed")
        inference.extend((entry, *holding))

    training, counts = [], Counter()
    cutoff = fold.first_test_opening
    for row in selection.training:
        entry, *holding = contexts[row.key]
        label = labels[row.key]
        path = label["primary"]
        target = _target_first(path, cutoff)
        counts["training_target_first_known"] += target is not None
        training.append(FitRow(entry, gross30=row.outcome.value, target_first=target))
        _require(tuple(item["step_minutes"] for item in label["holding"]) == HOLD_STEPS,
                 "eg_snapshot_holding_label_roster_changed")
        for context, hold in zip(holding, label["holding"], strict=True):
            if context.support != "common":
                counts[f"training_hold_{context.support}"] += 1
                continue
            value, status = _hold_value(hold, context, row.outcome.mature_at, cutoff)
            counts[f"training_hold_{status}"] += 1
            if value is not None:
                training.append(FitRow(context, hold_advantage=value))
    return ModelDataset(branch, timing, selection, tuple(training), tuple(inference), tuple(sorted(counts.items())))
