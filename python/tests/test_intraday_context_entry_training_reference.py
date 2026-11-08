"""Small invented execution audits; no native/archive reader or full campaign."""

from collections.abc import Mapping
from dataclasses import asdict, replace
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256

import pytest

from research import intraday_chart_cash as cash
from research import intraday_context_entry as entry
from research import intraday_context_entry_training as training
from research import intraday_context_snapshot as snapshot
from research import intraday_scenario_management_compare as management
from research import intraday_scenario_policy as policy
from research.intraday_context_entry_training_reference import verify_entry_training_coordinate
from research.intraday_context_model_inputs import encode
from research.intraday_ed_inputs import InputRefusal
from test_intraday_context_entry_training import CUTOFF, POLICY, SOURCE, at, build, first, inputs, source


F = Fraction


def verify(result, parts, **changes):
    coordinate, labels, session, targets = parts
    kwargs = dict(expected_targets=targets, cutoff_at=CUTOFF, book=cash.new_book("invented-training"),
                  risk_budget=F(100), cost_bps=33, source_sha256=SOURCE, execution_policy_sha256=POLICY)
    kwargs.update(changes)
    return verify_entry_training_coordinate(result, coordinate, labels, session, **kwargs)


@pytest.mark.parametrize("overrides,gross,gap,closed", [
    ({33: (115, 115, 113, 114), 62: (118, 119, 113, 114)}, F(3, 4), F(1, 4), 62),
    ({34: (114, 114, 110, 111), 38: (110, 114, 109, 114)}, F(-1), F(0), 38),
    ({34: (114, 126, 113, 120), 62: (118, 119, 113, 114)}, F(1), F(0), 62),
])
def test_original_prices_reconstruct_gap_gross_stop_or_expiry_and_single_cost(overrides, gross, gap, closed):
    parts = inputs(source(overrides))
    result, _ = build(parts)
    report = verify(result, parts)
    row, trace = first(result)
    assert report["verified"] and report["market_payoffs_reconstructed"]
    assert not report["source_authenticated"] and not report["snapshot_science_reconstructed"]
    assert (row.gross_in_R, row.gap_in_R) == (gross, gap)
    assert trace.holding.episode.closed_at == at(closed)
    sale = trace.holding.episode.book.realized_exits[-1]
    assert sale.allocated_entry_cost == sale.entry_basis * F(33, 10_000)
    assert report["original_memberships"] == 2


def test_execution_and_producer_validators_are_not_reference_oracles(monkeypatch):
    parts = inputs(source({34: (114, 114, 110, 111), 38: (110, 114, 109, 114)}))
    result, _ = build(parts)

    def forbidden(*args, **kwargs):
        pytest.fail("producer execution/validation was called")

    for module, names in (
        (training, ("build_entry_training_coordinate", "_path_payoff", "prepare_context_entry", "fill_context_entry",
                    "_position_replay", "_validate_coordinate", "_validate_labels", "training_outcome",
                    "weighted_training_rows", "_eligible")),
        (entry, ("prepare_context_entry", "fill_context_entry", "decide", "apply_modeled_open", "_validate_feature")),
        (policy, ("decide", "apply_modeled_open", "start_episode")),
        (management, ("_position_replay", "_held_step")),
        (snapshot, ("_validate_coordinate", "_validate_labels", "_validate_feature")),
        (cash, ("enter", "exit", "reduce_half", "freeze")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    assert verify(result, parts)["verified"]


@pytest.mark.parametrize("offset,mutation", [
    (33, "missing"), (33, "late"), (33, "no_availability"), (34, "late_for_decision"),
    (34, "absent"), (62, "missing"), (62, "late"), (62, "no_availability"),
])
def test_unknown_original_price_never_becomes_known_no_fill(offset, mutation):
    coordinate, labels, session, targets = inputs()
    minutes = dict(session.minutes)
    if mutation == "missing":
        minutes[at(offset)] = replace(minutes[at(offset)], price_state="missing", ohlc=None)
    elif mutation == "absent":
        del minutes[at(offset)]
    else:
        availability = {"late": CUTOFF, "no_availability": None, "late_for_decision": at(offset + 4)}[mutation]
        minutes[at(offset)] = replace(minutes[at(offset)], available_at=availability)
    changed = replace(session, minutes=minutes)
    parts = coordinate, labels, changed, targets
    result, _ = build(parts)
    report = verify(result, parts)
    row, _ = first(result)
    assert row.filled is row.actual_mature_at is row.gross_in_R is row.gap_in_R is None
    assert report["known_no_fill_rows"] == 0 and report["unknown_payoff_rows"] > 0


@pytest.mark.parametrize("price,reason", [(112, "open_outside_frozen_entry_bounds"),
    (116, "open_outside_frozen_entry_bounds"), (124, "entry_at_or_above_target")])
def test_known_gap_decline_has_complete_zero_and_two_logical_reads(price, reason):
    coordinate, labels, session, targets = inputs()
    minutes = dict(session.minutes)
    minutes[at(33)] = replace(minutes[at(33)], ohlc=tuple(map(Decimal, (price, price + 1, price - 1, price))))
    parts = coordinate, labels, replace(session, minutes=minutes), targets
    result, _ = build(parts)
    report = verify(result, parts)
    row, trace = first(result)
    assert row.filled is False and row.gross_in_R == row.gap_in_R == 0
    assert trace.reason == reason and report["original_price_reads"] == report["logical_price_uses"] == 2


def test_raw_minutes_read_once_and_unneeded_suffix_never_opened():
    parts = inputs(source({34: (114, 114, 110, 111), 38: (110, 114, 109, 114)}))
    result, _ = build(parts)
    read = set()

    class PointOnce(Mapping):
        def __getitem__(self, at_):
            assert at_ not in read and at_ <= at(38)
            read.add(at_)
            return parts[2].minutes[at_]

        def __iter__(self):
            pytest.fail("raw original session iterated")

        def __len__(self):
            pytest.fail("raw original session sized")

    changed = (*parts[:2], replace(parts[2], minutes=PointOnce()), parts[3])
    report = verify(result, changed)
    assert report["original_price_reads"] == len(read)
    assert report["logical_price_uses"] > len(read)  # Entry minute later supplies a close.


def test_excluded_coordinate_never_touches_labels_or_prices_and_counters_are_bound():
    parts = inputs()
    result, opened = build(parts, cutoff=at(0))
    assert not opened
    blocked = parts[0], object(), object(), parts[3]
    report = verify(result, blocked, cutoff_at=at(0))
    assert report["opened_inputs"] == () and report["original_price_reads"] == 0
    with pytest.raises(InputRefusal):
        verify(replace(result, opened_inputs=("labels",)), blocked, cutoff_at=at(0))


def test_missing_labels_never_reads_price_source():
    parts = inputs()
    result, _ = build(parts, labels=None)
    report = verify(result, (parts[0], None, object(), parts[3]))
    assert report["opened_inputs"] == ("labels",) and report["original_price_reads"] == 0
    assert report["unknown_payoff_rows"] == report["training_rows"]


def test_label_mature_exactly_at_cutoff_has_no_weight_or_price_access():
    parts = inputs(source({34: (114, 126, 113, 114)}))
    label = parts[1].labels[0]
    cutoff = parts[2].closing + 2 * (at(1) - at(0))
    changed = replace(label, results=tuple(replace(item, mature_at=cutoff) for item in label.results))
    labels = replace(parts[1], labels=(changed,))
    result, _ = build(parts, labels=labels, cutoff=cutoff)
    report = verify(result, (parts[0], labels, object(), parts[3]), cutoff_at=cutoff)
    assert report["label_supported_rows"] == report["original_price_reads"] == 0
    assert first(result)[0].weight is None


def test_zero_quantity_and_missing_session_are_unknown_without_fabricated_no_fill():
    parts = inputs()
    result, _ = build(parts, risk=F(1, 100))
    report = verify(result, parts, risk_budget=F(1, 100))
    assert report["known_no_fill_rows"] == 0 and report["original_price_reads"] == 1
    result, _ = build(parts, value=None)
    report = verify(result, (parts[0], parts[1], None, parts[3]))
    assert report["known_no_fill_rows"] == report["original_price_reads"] == 0
    assert report["unknown_payoff_rows"] == report["training_rows"]


def test_no_session_and_no_question_coordinates_retain_original_denominators():
    parts = inputs()
    flat = replace(parts[2], minutes={key: replace(row, ohlc=(Decimal(100),) * 4)
                                     for key, row in parts[2].minutes.items()})
    for session in (None, flat):
        coordinate = snapshot.project_coordinate(parts[3], session)
        original = coordinate, None, session, parts[3]
        result, _ = build(original)
        report = verify(result, (coordinate, object(), object(), parts[3]))
        assert report["original_memberships"] == 2 and report["training_rows"] == 0
        assert report["opened_inputs"] == () and report["original_price_reads"] == 0


def test_original_stage_weights_survive_missing_payoff_and_priority():
    overrides = {index: (114, 114, 113, 114) for index in range(30, 35)}
    overrides.update({index: (114, 116, 113, 114) for index in range(35, 40)})
    coordinate, labels, session, targets = inputs(source(overrides))
    minutes = dict(session.minutes)
    del minutes[at(33)]
    parts = coordinate, labels, replace(session, minutes=minutes), targets
    result, _ = build(parts)
    assert verify(result, parts)["verified"]
    rows = tuple(row for row in result.rows if row.question.stage == "approach")
    assert len(rows) == 2 and all(row.weight == F(1, 2) for row in rows)
    changed = replace(result, rows=tuple(replace(row, weight=F(1)) for row in result.rows))
    with pytest.raises(InputRefusal):
        verify(changed, parts)
    priority = inputs(source({index: (112, 120, 111, 118) for index in range(25, 30)}))
    result, _ = build(priority)
    assert verify(result, priority)["verified"]
    assert result.rows[0].weight == 1 and result.rows[0].filled is None


def test_coherent_rehash_cannot_replace_caller_source():
    parts = inputs()
    result, _ = build(parts)
    changed_source = "c" * 64

    def exact(value):
        if type(value) is F:
            return [value.numerator, value.denominator]
        if type(value) is dict:
            return {key: exact(item) for key, item in value.items()}
        if type(value) in (list, tuple):
            return [exact(item) for item in value]
        return value

    digest = sha256(encode([training.SCHEMA, training.FIXED_POLICY, changed_source, POLICY,
                           exact(asdict(result.book)), exact(result.risk_budget), result.cost_bps])).hexdigest()
    changed = replace(result, source_sha256=changed_source, parameters_sha256=digest,
                      rows=tuple(replace(row, source_sha256=changed_source) for row in result.rows))
    assert changed.parameters_sha256 != result.parameters_sha256
    with pytest.raises(InputRefusal):
        verify(changed, parts)


def test_coherent_forged_fill_trace_is_rejected_by_original_prices():
    parts = inputs()
    coordinate, labels, session, targets = parts
    minutes = dict(session.minutes)
    minutes[at(62)] = replace(minutes[at(62)], ohlc=tuple(map(Decimal, (118, 119, 113, 114))))
    forged, _ = build((coordinate, labels, replace(session, minutes=minutes), targets))
    assert first(forged)[0].gross_in_R == 1
    with pytest.raises(InputRefusal):
        verify(forged, parts)


@pytest.mark.parametrize("change", [
    {"risk_budget": F(200)}, {"cost_bps": 43}, {"source_sha256": "d" * 64},
    {"execution_policy_sha256": "e" * 64}, {"book": cash.CashBook("other", F(10000), F(10000))},
])
def test_caller_metadata_is_not_taken_from_producer(change):
    parts = inputs()
    result, _ = build(parts)
    with pytest.raises(InputRefusal):
        verify(result, parts, **change)


def test_prior_realized_history_and_43bps_are_audited_without_contaminating_new_gross():
    parts = inputs(source({62: (118, 119, 113, 114)}))
    original = cash.new_book("invented-history")
    old = cash.enter(original, "old", entry_price=F(100), observed_prices={}, cost_bps=33).book
    book = cash.exit(old, "old", quantity=old.lots[0].quantity, exit_price=F(110)).book
    result, _ = build(parts, book=book, cost=43)
    assert verify(result, parts, book=book, cost_bps=43)["verified"]
    assert first(result)[0].gross_in_R == 1
