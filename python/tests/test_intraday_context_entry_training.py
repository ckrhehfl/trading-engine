"""Invented original paths, exact cash consistency and future-reader boundaries."""

from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_chart_cash as cash
from research import intraday_context_entry_training as training
from research import intraday_context_snapshot as snapshot
from research.intraday_context_entry_values import fit_entry_values
from research.intraday_context_entry_values_reference import verify_entry_values
from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute, SessionInputs
from research.intraday_scenario_labels import label_horizons
from test_intraday_context_snapshot import BASE, DAY, OPEN, at, target


F = Fraction
SOURCE, POLICY = "a" * 64, "b" * 64
CUTOFF = OPEN + 24 * 60 * MINUTE


def source(overrides=None, *, closing=90):
    bars = (*BASE, *((114, 115, 113, 114),) * ((closing - 30) // 5))
    minutes = {at(index): Minute("invented", at(index), at(index + 3), tuple(map(Decimal, bar)), Decimal(10))
               for number, bar in enumerate(bars) for index in range(number * 5, number * 5 + 5)}
    for index, values in (overrides or {}).items():
        minutes[at(index)] = replace(minutes[at(index)], ohlc=tuple(map(Decimal, values)))
    return SessionInputs("invented", OPEN, at(closing), minutes)


def inputs(value=None):
    value = value or source()
    targets = (target(), target("S2"))
    coordinate = snapshot.project_coordinate(targets, value)
    labels = snapshot.LabeledCoordinate(DAY, "invented", "observed", tuple(
        snapshot.QuestionLabel(feature.question, value.closing + 2 * MINUTE, "observed",
            label_horizons(feature.question.path, feature.question.start_close, value,
                           cutoff_at=value.closing + 2 * MINUTE)) for feature in coordinate.questions))
    return coordinate, labels, value, targets


def build(parts=None, *, labels="original", value="original", cutoff=CUTOFF, book=None, risk=F(100), cost=33):
    coordinate, original_labels, original, targets = parts or inputs()
    opened = []

    def read_labels(day, symbol):
        opened.append(("labels", day, symbol))
        return original_labels if labels == "original" else labels

    def read_session(day, symbol):
        opened.append(("prices", day, symbol))
        return original if value == "original" else value

    result = training.build_entry_training_coordinate(coordinate, read_labels, read_session,
        expected_targets=targets, cutoff_at=cutoff, book=book or cash.new_book("invented-training"),
        risk_budget=risk, cost_bps=cost, source_sha256=SOURCE, execution_policy_sha256=POLICY)
    return result, opened


def first(result):
    key = result.original.questions[0].question_id
    row = next(row for row in result.rows if row.question.question_id == key)
    trace = result.traces[0]
    return row, trace


def test_absolute_expiry_cash_gross_and_cost_are_bound_but_separate():
    parts = inputs(source({33: (115, 115, 113, 114), 62: (118, 119, 113, 114)}))
    result, opened = build(parts)
    row, trace = first(result)
    assert row.filled is True and (row.gross_in_R, row.gap_in_R) == (F(3, 4), F(1, 4))
    assert trace.prepared.step.episode.pending.execute_at == at(33)
    assert trace.holding.episode.closed_at == at(62) == result.original.questions[0].question.path.end_at
    assert trace.actual_mature_at == row.actual_mature_at == at(65)
    leg = trace.holding.episode.book.realized_exits[-1]
    assert leg.gross_pnl == row.gross_in_R * row.risk * leg.quantity
    assert leg.net_pnl == leg.gross_pnl - leg.allocated_entry_cost < leg.gross_pnl
    assert leg.allocated_entry_cost == 115 * leg.quantity * F(33, 10_000)
    assert result.fixed_policy == training.FIXED_POLICY
    assert not result.source_authenticated and not result.contains_actual_fills
    assert opened == [("labels", DAY, "invented"), ("prices", DAY, "invented")]
    assert result.counts["original_memberships"] == 2  # Same coordinate, not two independent payoff rows.
    model = fit_entry_values(result.rows, cutoff_at=CUTOFF, execution_policy_sha256=POLICY)
    assert verify_entry_values(model, result.rows, cutoff_at=CUTOFF, execution_policy_sha256=POLICY)["verified"]
    with pytest.raises(FrozenInstanceError):
        row.gross_in_R = F(9)


def test_available_close_stop_exits_next_open_not_stop_touch_price():
    result, _ = build(inputs(source({34: (114, 114, 110, 111), 38: (110, 114, 109, 114)})))
    row, trace = first(result)
    assert row.filled and row.gross_in_R == -1
    assert trace.holding.events[0].at == at(37)
    assert trace.holding.events[0].reason == "structural_close_failure"
    assert trace.holding.episode.closed_at == at(38)
    assert row.actual_mature_at == at(41)
    assert trace.holding.episode.book.realized_exits[-1].gross_proceeds == 110 * trace.initial_fill.quantity


def test_target_touch_label_is_not_an_early_profit_order():
    result, _ = build(inputs(source({34: (114, 126, 113, 120), 62: (118, 119, 113, 114)})))
    row, trace = first(result)
    assert row.question.outcome.index == 0  # First five-minute P1 label.
    assert trace.holding.episode.closed_at == at(62)
    assert row.gross_in_R == 1  # (118 - 114) / 4, not (target 124 - entry 114) / 4.
    assert all(event.reason != "target_touch" for event in trace.holding.events)


@pytest.mark.parametrize("price,reason", [
    (112, "open_outside_frozen_entry_bounds"), (116, "open_outside_frozen_entry_bounds"),
    (124, "entry_at_or_above_target"), (125, "entry_at_or_above_target"),
])
def test_known_pending_gap_declines_are_complete_zero_payoffs(price, reason):
    parts = inputs()
    value = parts[2]
    minutes = dict(value.minutes)
    minutes[at(33)] = replace(minutes[at(33)], ohlc=tuple(map(Decimal, (price, price + 1, price - 1, price))))
    result, _ = build(parts, value=replace(value, minutes=minutes))
    row, trace = first(result)
    assert row.filled is False and row.gross_in_R == row.gap_in_R == 0
    assert row.no_fill_reason == reason and row.unsupported_reason is None
    assert trace.status == "no_fill" and trace.holding is None and row.actual_mature_at == at(36)
    assert [item.at for item in trace.prices] == [at(29), at(33)]


@pytest.mark.parametrize("index,mutation", [
    (33, "absent"), (33, "missing"), (33, "late"), (33, "no_availability"),
    (62, "absent"), (62, "missing"), (62, "late"), (62, "no_availability"),
    (34, "absent"), (34, "missing"), (34, "late"), (34, "late_for_decision"),
])
def test_unknown_entry_holding_or_exit_is_not_a_no_fill_or_zero(index, mutation):
    parts = inputs()
    value = parts[2]
    minutes = dict(value.minutes)
    if mutation == "absent":
        del minutes[at(index)]
    elif mutation == "missing":
        minutes[at(index)] = replace(minutes[at(index)], price_state="missing", ohlc=None)
    elif mutation == "late":
        minutes[at(index)] = replace(minutes[at(index)], available_at=CUTOFF)
    elif mutation == "no_availability":
        minutes[at(index)] = replace(minutes[at(index)], available_at=None)
    else:
        minutes[at(index)] = replace(minutes[at(index)], available_at=at(index + 4))
    result, _ = build(parts, value=replace(value, minutes=minutes))
    row, trace = first(result)
    assert row.filled is row.gross_in_R is row.gap_in_R is row.actual_mature_at is None
    assert row.weight == 1 and trace.status == "unknown" and row.no_fill_reason is None
    if index != 33:
        assert trace.initial_fill.filled  # Known initial fill survives in the trace despite an incomplete payoff.


def test_unneeded_later_suffix_is_never_read_after_an_early_exit():
    parts = inputs(source({34: (114, 114, 110, 111), 38: (110, 114, 109, 114)}))

    class PointOnly(Mapping):
        def __getitem__(self, key):
            assert key <= at(38), "unused future suffix was read"
            return parts[2].minutes[key]

        def __iter__(self):
            pytest.fail("raw minute session was iterated")

        def __len__(self):
            pytest.fail("raw minute count was requested")

    coordinate, labels, value, targets = parts
    assert len(coordinate.questions) == 1
    result, _ = build(parts, value=replace(value, minutes=PointOnly()))
    assert first(result)[0].gross_in_R == -1


def test_future_coordinate_never_calls_labels_or_price_readers():
    result, opened = build(cutoff=OPEN)
    assert not opened and not result.rows
    assert result.original.questions and len(result.traces) == len(result.original.questions)
    assert {trace.reason for trace in result.traces} == {"coordinate_not_before_training_cutoff"}
    assert result.counts["original_frames"] == len(result.original.frames)


def test_not_yet_mature_label_archive_does_not_open_inputs():
    result, opened = build(cutoff=at(91))  # Original archive cutoff is E92.
    assert not opened and not result.rows
    assert all(trace.status == "excluded" for trace in result.traces)


@pytest.mark.parametrize("missing", [None, "session"])
def test_label_missing_preserves_rows_weights_and_never_reads_price_path(missing):
    parts = inputs()
    labels = None if missing is None else snapshot.LabeledCoordinate(DAY, "invented", "session_missing", tuple(
        snapshot.QuestionLabel(feature.question, parts[2].closing + 2 * MINUTE, "session_missing", ())
        for feature in parts[0].questions))
    result, opened = build(parts, labels=labels)
    assert opened == [("labels", DAY, "invented")]
    assert len(result.rows) == len(result.original.questions)
    assert all(row.weight is row.question.outcome is row.filled is None for row in result.rows)
    assert all(trace.status == "unknown" for trace in result.traces)


def test_incomplete_or_ambiguous_joint_path_never_becomes_class_zero():
    result, opened = build(inputs(source({34: (114, 126, 110, 114)})))
    row, trace = first(result)
    assert row.question.outcome is None and row.weight is None and row.filled is None
    assert "same_minute_target_stop_order_unknown" in row.question.unavailable_reason
    assert trace.horizon_support[0] == (5, "unknown")
    assert opened == [("labels", DAY, "invented")]


def test_empty_no_event_and_absent_coordinates_keep_denominators_without_readers():
    parts = inputs()
    flat = source()
    flat = replace(flat, minutes={key: replace(minute, ohlc=(Decimal(100),) * 4)
                                  for key, minute in flat.minutes.items()})
    for value in (None, flat):
        coordinate = snapshot.project_coordinate(parts[3], value)
        result, opened = build((coordinate, None, value, parts[3]))
        assert not opened and not result.rows and not result.traces
        assert result.counts["original_memberships"] == 2
        assert result.original is coordinate


def test_caller_capital_risk_and_cost_are_bound_without_net_in_normalized_gross():
    parts = inputs(source({33: (115, 115, 113, 114), 62: (118, 119, 113, 114)}))
    primary, _ = build(parts)
    stress, _ = build(parts, cost=43)
    more_risk, _ = build(parts, risk=F(200))
    other_book = cash.CashBook("different-capital", F(10_000), F(10_000))
    capital, _ = build(parts, book=other_book)
    values = [first(result)[0] for result in (primary, stress, more_risk, capital)]
    assert {(row.gross_in_R, row.gap_in_R) for row in values} == {(F(3, 4), F(1, 4))}
    assert len({result.parameters_sha256 for result in (primary, stress, more_risk, capital)}) == 4
    assert first(more_risk)[1].initial_fill.quantity > first(primary)[1].initial_fill.quantity
    assert first(stress)[1].initial_fill.assumed_cost > first(primary)[1].initial_fill.assumed_cost


def test_zero_quantity_is_preparation_unsupported_not_a_zero_payoff():
    result, _ = build(risk=F(1, 100))
    row, trace = first(result)
    assert row.filled is None and trace.initial_fill is None and row.weight == 1
    assert trace.reason == "preparation_unsupported:insufficient_risk_or_cash_for_one_unit"
    assert [item.at for item in trace.prices] == [at(29)]


def test_payoff_missing_does_not_renormalize_two_same_stage_questions():
    replacements = {index: (114, 114, 113, 114) for index in range(30, 35)}
    replacements.update({index: (114, 116, 113, 114) for index in range(35, 40)})
    parts = inputs(source(replacements))
    approach = tuple(feature for feature in parts[0].questions if feature.question.stage == "approach")
    assert len(approach) == 2
    minutes = dict(parts[2].minutes)
    del minutes[at(33)]
    result, _ = build(parts, value=replace(parts[2], minutes=minutes))
    rows = tuple(row for row in result.rows if row.question.stage == "approach")
    assert [row.weight for row in rows] == [F(1, 2), F(1, 2)]
    assert rows[0].filled is None and rows[1].filled is True
    model = fit_entry_values(result.rows, cutoff_at=CUTOFF, execution_policy_sha256=POLICY)
    assert verify_entry_values(model, result.rows, cutoff_at=CUTOFF, execution_policy_sha256=POLICY)["verified"]


def test_lower_same_clock_priority_retains_supported_label_weight_without_no_fill():
    replacements = {index: (112, 120, 111, 118) for index in range(25, 30)}
    result, _ = build(inputs(source(replacements)))
    same_clock = tuple((row, trace) for row, trace in zip(result.rows, result.traces, strict=True)
                       if row.question.forecast_at == at(32))
    assert len(same_clock) == 2
    approach, breakout = same_clock
    assert approach[0].question.stage == "approach" and approach[0].question.outcome is not None
    assert approach[0].weight == 1 and approach[0].filled is None
    assert approach[1].reason == "preparation_unsupported:lower_same_clock_question_priority"
    assert approach[1].initial_fill is None
    assert breakout[0].question.stage == "breakout" and breakout[0].weight == 1


def test_joint_label_maturing_exactly_at_cutoff_remains_unavailable():
    parts = inputs(source({34: (114, 126, 113, 114)}))
    assert len(parts[1].labels) == 1
    label = parts[1].labels[0]
    cutoff = parts[2].closing + 2 * MINUTE
    changed = replace(label, results=tuple(replace(item, mature_at=cutoff) for item in label.results))
    labels = replace(parts[1], labels=(changed,))
    result, opened = build(parts, labels=labels, cutoff=cutoff)
    row, _ = first(result)
    assert row.question.outcome is None and row.question.unavailable_reason == "outcome_not_mature_before_split"
    assert row.weight is None and opened == [("labels", DAY, "invented")]


def test_existing_realized_book_history_does_not_enter_new_unit_gross():
    original = cash.new_book("invented-history")
    old = cash.enter(original, "old-symbol", entry_price=F(100), observed_prices={}, cost_bps=33).book
    closed = cash.exit(old, "old-symbol", quantity=old.lots[0].quantity, exit_price=F(110)).book
    result, _ = build(inputs(source({62: (118, 119, 113, 114)})), book=closed)
    row, trace = first(result)
    assert row.gross_in_R == 1 and len(trace.holding.episode.book.realized_exits) == 2
    assert trace.initial_episode.first_lot_id == closed.next_lot_id


@pytest.mark.parametrize("mutation", [
    {"symbol": "different"}, {"opening": OPEN + MINUTE}, {"closing": at(89)},
])
def test_original_session_substitution_fails(mutation):
    parts = inputs()
    with pytest.raises(InputRefusal):
        build(parts, value=replace(parts[2], **mutation))


@pytest.mark.parametrize("mutation", [
    {"symbol": "different"}, {"start_at": at(34)}, {"available_at": at(35)},
    {"ohlc": (Decimal(0),) * 4}, {"price_state": "missing"},
])
def test_malformed_original_minute_is_integrity_failure(mutation):
    parts = inputs()
    minutes = dict(parts[2].minutes)
    minutes[at(33)] = replace(minutes[at(33)], **mutation)
    with pytest.raises(InputRefusal):
        build(parts, value=replace(parts[2], minutes=minutes))


def test_label_coordinate_question_or_maturity_substitution_fails():
    parts = inputs()
    labels = parts[1]
    bad = (replace(labels, symbol="different"), replace(labels, labels=()),
           replace(labels, labels=(replace(labels.labels[0], cutoff_at=CUTOFF),)))
    for changed in bad:
        with pytest.raises(InputRefusal):
            build(parts, labels=changed)


@pytest.mark.parametrize("changes", [
    {"risk": 100}, {"risk": F(0)}, {"cost": 0}, {"cost": 34}, {"cost": True},
])
def test_invalid_caller_risk_or_cost_never_opens_inputs(changes):
    with pytest.raises(InputRefusal):
        build(**changes)
