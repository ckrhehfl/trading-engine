"""Invented-only causal entry pairing and fixed-policy cash checks."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_chart_cash as cash
from research.intraday_context_entry_compare import prepare_entry_comparison, replay_entry_comparison
from research.intraday_context_entry_roster import build_entry_roster
from research.intraday_context_entry_values import fit_entry_values
from research.intraday_context_forecast_fold import forecast_fold
from research.intraday_context_forecast_seal import read_forecasts, seal_forecasts
from research.intraday_context_snapshot import FEATURE_SCHEMA, LABEL_SCHEMA, project_coordinate
from research.intraday_ed_inputs import MINUTE
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_snapshot import SnapshotSeal
from test_intraday_context_entry_values import POLICY, historical
from test_intraday_context_forecast_fold import DAYS, adapters, fixture, inputs, label_reader


F = Fraction


class RequestedMinutes(Mapping):
    def __init__(self, minutes):
        self.minutes = minutes
        self.reads = Counter()

    def __getitem__(self, at):
        self.reads[at] += 1
        return self.minutes[at]

    def __iter__(self):
        pytest.fail("session suffix was enumerated")

    def __len__(self):
        pytest.fail("session suffix length was queried")


@pytest.fixture
def data(tmp_path):
    targets, coordinates, sessions, fold = fixture(extra=True)
    # Preserve one missing coordinate and one genuine no-event coordinate.
    missing = project_coordinate(coordinates[3].targets, None)
    quiet = inputs(DAYS[1], "quiet", ((100, 100, 100, 100),) * 20)
    quiet_targets = tuple(TargetCoordinate(scenario, "quiet", DAYS[1], 3, 100, "observed", b"{}")
                          for scenario in ("S1", "S2"))
    targets += quiet_targets
    quiet_coordinate = project_coordinate(quiet_targets, quiet)
    coordinates = (*coordinates[:3], missing, quiet_coordinate, *coordinates[4:])
    result = forecast_fold(coordinates, fold, "S1", label_reader(sessions, coordinates, []),
                           expected_targets=targets, adapters=adapters([]))
    feature = SnapshotSeal(FEATURE_SCHEMA, "a" * 64, 1024, len(coordinates))
    label = SnapshotSeal(LABEL_SCHEMA, "b" * 64, 2048, len(coordinates))
    path = tmp_path / "invented-forecasts.jsonl"
    seal = seal_forecasts(path, result, feature, label)
    stored = read_forecasts(path, seal, feature_seal=feature, label_seal=label,
        expected_coordinates=result.original_test_coordinates, expected_fold=fold, expected_scenario="S1",
        expected_adapter_mode="injected_diagnostic")
    roster = build_entry_roster(coordinates, expected_targets=targets, scenario="S1", source_sha256=feature.sha256)
    return roster, stored, fold, sessions[(DAYS[1], "invented")]


def value_model(fold, gross=F(2), *, unavailable_class=False):
    n = 9 if unavailable_class else 10
    rows = [historical(outcome=index, stage=stage, episode=index + 1, gross=gross, weight=F(1, n))
            for stage in ("approach", "breakout") for index in range(n)]
    return fit_entry_values(rows, cutoff_at=fold.first_test_opening, execution_policy_sha256=POLICY)


def prepare(data, *, model=None, read_visible=None, **changes):
    roster, stored, fold, session = data
    calls = []

    def causal(day, symbol, at):
        assert (day, symbol) == (DAYS[1], "invented")
        calls.append(at)
        return session.minutes.get(at)

    values = {"book": cash.new_book("invented-EI"), "risk_budget": F(100), "cost_bps": 33,
              "execution_policy_sha256": POLICY, "read_visible": read_visible or causal}
    return prepare_entry_comparison(roster, stored, fold, model or value_model(fold), **(values | changes)), calls


def replay(plan, session):
    calls = []

    def session_reader(day, symbol):
        assert (day, symbol) == (DAYS[1], "invented")
        calls.append((day, symbol))
        return session

    return replay_entry_comparison(plan, read_session=session_reader), calls


def first_pair(result):
    return next(row for row in result.coordinates[0].outcomes if row.decisions.opportunity.status == "opportunity")


def change_minute(session, at, **changes):
    minutes = dict(session.minutes)
    minutes[at] = replace(minutes[at], **changes)
    return replace(session, minutes=minutes)


def test_sealed_forecast_and_train_only_values_share_exact_fixed_fill_and_keep_denominator(data):
    plan, causal_reads = prepare(data)
    session = data[-1]
    observed = RequestedMinutes(session.minutes)
    result, session_reads = replay(plan, replace(session, minutes=observed))
    pair = first_pair(result)
    assert pair.baseline is pair.selected and pair.baseline.status == "closed"
    assert pair.net_delta == 0 and pair.baseline.fill.quantity > 0
    selected = pair.decisions.selected
    assert selected.entry_value.query.forecast_model_key == "context_sequence"
    assert selected.entry_value.query.forecast_source_sha256 == data[1].seal.sha256
    assert selected.entry_value.query.numeric_boundary.original == (0.1,) * 10
    payoff = pair.baseline.payoff
    lot, = pair.baseline.initial.book.lots
    expected_exit, = pair.baseline.holding.episode.book.realized_exits
    assert payoff.entry_notional == lot.entry_price * lot.original_quantity
    assert payoff.cost == payoff.entry_notional * F(33, 10_000)
    assert payoff.gross == expected_exit.gross_proceeds - payoff.entry_notional
    assert payoff.net_return == payoff.net / payoff.entry_notional
    assert pair.baseline.holding.episode.book.cash == selected.book_before.cash + payoff.net
    expiry_open = next(row.at for row in pair.baseline.holding.events if row.fill_status == "modeled")
    assert observed.reads[expiry_open] == 1
    assert len(session_reads) == 1
    assert len(causal_reads) == len([row for row in result.coordinates[0].outcomes
                                    if row.decisions.opportunity.status == "opportunity"])
    assert [row.prepared.roster.symbol for row in result.coordinates] == ["invented", "other", "quiet"]
    assert result.coordinates[1].prepared.roster.coordinate_status == "session_missing"
    assert result.coordinates[1].outcomes == result.coordinates[2].outcomes == ()
    assert result.coordinates[1].prepared.roster.original_frames != result.coordinates[2].prepared.roster.original_frames
    suppressed = [row for row in result.coordinates[0].outcomes if row.baseline.status == "overlap_suppressed"]
    assert suppressed and all(row.selected.status == "overlap_suppressed" and row.net_delta is None for row in suppressed)


def test_selected_wait_uses_zero_pnl_but_only_against_a_known_baseline(data):
    plan, _ = prepare(data, model=value_model(data[2], F(-1)))
    result, _ = replay(plan, data[-1])
    pair = first_pair(result)
    assert pair.selected.status == "no_entry" and pair.selected.payoff.net == 0
    assert pair.selected.payoff.net_return is None and pair.selected.fill is None
    assert pair.baseline.status == "closed" and pair.net_delta == -pair.baseline.payoff.net
    assert pair.decisions.selected.step.action == "wait"


@pytest.mark.parametrize("failure", ("unknown_open", "late_open", "unknown_exit", "known_no_fill", "session_missing"))
def test_missing_fill_or_exit_does_not_turn_into_a_zero_return(data, failure):
    plan, _ = prepare(data, model=value_model(data[2], F(-1)))
    original, _ = replay(plan, data[-1])
    pair = first_pair(original)
    entry_at = pair.baseline.initial.entered_at
    exit_at = pair.baseline.holding.episode.closed_at
    session = data[-1]
    if failure == "unknown_open":
        session = change_minute(session, entry_at, ohlc=None, price_state="missing")
    elif failure == "late_open":
        session = change_minute(session, entry_at, available_at=entry_at + 4 * MINUTE)
    elif failure == "unknown_exit":
        session = change_minute(session, exit_at, ohlc=None, price_state="halt")
    elif failure == "known_no_fill":
        target = pair.decisions.opportunity.feature.question.path.target
        high = Decimal(target.numerator) / Decimal(target.denominator)
        session = change_minute(session, entry_at, ohlc=(high,) * 4)
    else:
        session = None
    result, _ = replay(plan, session)
    pair = first_pair(result)
    assert pair.selected.status == "no_entry"
    if failure == "known_no_fill":
        assert pair.baseline.status == "no_fill" and pair.baseline.reason == "entry_at_or_above_target"
        assert pair.baseline.payoff.net == pair.net_delta == 0
    else:
        assert pair.baseline.status == "unresolved" and pair.baseline.payoff is pair.net_delta is None
        if failure == "unknown_open":
            assert (entry_at, None, "price_missing") in result.coordinates[0].modeled_opens
        elif failure == "late_open":
            assert (entry_at, None, "minute_available_after_cutoff") in result.coordinates[0].modeled_opens
        elif failure == "unknown_exit":
            assert pair.baseline.fill.filled and pair.baseline.fill.assumed_cost > 0
            assert (exit_at, None, "price_halt") in result.coordinates[0].modeled_opens


def test_a_positive_mass_class_without_training_payoff_abstains(data):
    plan, _ = prepare(data, model=value_model(data[2], unavailable_class=True))
    result, _ = replay(plan, data[-1])
    pair = first_pair(result)
    assert pair.baseline.status == "closed" and pair.selected.status == "unsupported"
    assert pair.decisions.selected.entry_value.unsupported_probability_mass == F(1, 10)
    assert pair.selected.payoff is pair.net_delta is None


def test_missing_causal_point_does_not_open_any_test_outcome(data):
    plan, _ = prepare(data, read_visible=lambda *_: None)
    result = replay_entry_comparison(plan, read_session=lambda *_: pytest.fail("outcome read after abstention"))
    pair = first_pair(result)
    assert pair.baseline.status == pair.selected.status == "unsupported"
    assert pair.baseline.reason == pair.selected.reason == "causal_close_missing"
    assert pair.baseline.payoff is pair.selected.payoff is pair.net_delta is None


def test_neither_policy_opens_test_outcomes_if_one_unit_is_unaffordable(data):
    plan, _ = prepare(data, risk_budget=F(1, 1000))
    result = replay_entry_comparison(plan, read_session=lambda *_: pytest.fail("outcome read without pending entry"))
    pair = first_pair(result)
    assert pair.baseline.status == pair.selected.status == "no_entry"
    assert pair.baseline.reason == pair.selected.reason == "insufficient_risk_or_cash_for_one_unit"
    assert pair.net_delta == 0 and pair.baseline.payoff.net_return is None


@pytest.mark.parametrize("corruption", ("drop_coordinate", "forecast_membership", "wrong_cutoff", "reservation"))
def test_forecast_training_and_reservation_must_match_before_any_causal_price_read(data, corruption):
    roster, stored, fold, session = data
    model = value_model(fold)
    if corruption == "drop_coordinate":
        roster = replace(roster, coordinates=tuple(row for row in roster.coordinates if row.symbol != "quiet"))
    elif corruption == "forecast_membership":
        stored = replace(stored, forecast=replace(stored.forecast, predictions=stored.forecast.predictions[:-1]))
    elif corruption == "wrong_cutoff":
        model = replace(model, cutoff_at=fold.first_test_opening - MINUTE)
    else:
        coordinates = list(roster.coordinates)
        target = next(index for index, row in enumerate(coordinates) if row.session == DAYS[1] and row.symbol == "invented")
        row = coordinates[target]
        coordinates[target] = replace(row, questions=tuple(replace(item, status="opportunity", reserved_by=None)
                                                           for item in row.questions))
        roster = replace(roster, coordinates=tuple(coordinates))
    with pytest.raises(ValueError, match="EI_entry_compare_"):
        prepare((roster, stored, fold, session), model=model,
                read_visible=lambda *_: pytest.fail("read price before binding complete roster"))


def test_replay_requires_original_causal_minute_before_the_first_open(data):
    plan, _ = prepare(data)
    first = next(pair for pair in plan.coordinates[0].decisions if pair.baseline is not None)
    at = first.baseline.visible_minute.start_at
    source = change_minute(data[-1], at, ohlc=(Decimal(113),) * 4)
    minutes = RequestedMinutes(source.minutes)
    with pytest.raises(ValueError, match="original_causal_minute_differs"):
        replay(plan, replace(source, minutes=minutes))
    assert minutes.reads == {at: 1}
