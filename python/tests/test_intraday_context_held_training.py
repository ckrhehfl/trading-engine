"""Invented EI prior-pool construction and time/support boundary regressions."""

from dataclasses import replace
from decimal import Decimal

import pytest

from research import intraday_chart_cash as cash
from research.intraday_context_entry_roster import CoordinateOpportunities, EntryRoster, Opportunity
from research.intraday_context_held_training import build_held_training_pool
from research.intraday_ed_inputs import MINUTE, InputRefusal
from test_intraday_context_held import F, OPEN, POLICY, SOURCE, PrefixOnly, assess, fixture, fold


def training_fixture():
    query, source, _, _ = fixture(day=-1)
    test_query = fixture()[0]
    first, later = fold(-1).training_sessions[0], fold(-1).test_sessions[0]
    eligible = Opportunity(query.entry, "opportunity", (), None)
    test_op = Opportunity(test_query.entry, "opportunity", (), None)
    empty = CoordinateOpportunities(first, "absent", "session_missing", 0, (), ())
    train = CoordinateOpportunities(first, source.symbol, "observed_session", 72, (), (eligible,))
    evaluation = CoordinateOpportunities(later, source.symbol, "observed_session", 72, (), (test_op,))
    roster = EntryRoster("S1", SOURCE, (first, later), (empty, train, evaluation), 3, (("opportunity", 2),))
    return roster, train, source


def build(roster, read):
    return build_held_training_pool(roster, fold=fold(-1), read_session=read,
        book=cash.new_book("invented-prior-pool"), risk_budget=F(100), cost_bps=33,
        source_sha256=SOURCE, execution_policy_sha256=POLICY)


def replace_bar(source, offset, **change):
    at = source.opening + offset * MINUTE
    return replace(source, minutes={**source.minutes, at: replace(source.minutes[at], **change)})


def test_prior_roster_keeps_empty_coordinates_and_never_reads_evaluation_or_suffix():
    roster, train, source = training_fixture()
    cap = train.questions[0].feature.question.path.end_at
    calls = []

    def read(day, symbol):
        calls.append((day, symbol))
        assert (day, symbol) == (train.session, train.symbol)
        return replace(source, minutes=PrefixOnly(source.minutes, cap + MINUTE))

    pool = build(roster, read)
    assert calls == [(train.session, train.symbol)]
    assert pool.original_coordinates == roster.coordinates[:2]
    assert len(pool.cases) == 1 and pool.cases[0].status == "held_closed"
    assert [row.question.held.decision_at for row in pool.roster.rows] == [
        source.opening + offset * MINUTE for offset in (37, 42, 47, 52, 57)]
    assert all(row.coverage_end == cap and row.actual_mature_at == cap + 3 * MINUTE
               and row.source_sha256 == SOURCE and row.question.held.remaining_fraction == 1
               for row in pool.roster.rows)


def test_earlier_questions_keep_original_cap_after_fixed_structural_exit():
    roster, train, source = training_fixture()
    source = replace_bar(source, 39, ohlc=(Decimal(100),) * 4)
    pool = build(roster, lambda *_: source)
    row, = pool.roster.rows
    assert pool.cases[0].status == "held_closed"
    assert row.question.held.decision_at == source.opening + 37 * MINUTE
    assert row.coverage_end == train.questions[0].feature.question.path.end_at
    assert row.actual_mature_at == row.coverage_end + 3 * MINUTE


@pytest.mark.parametrize(("offset", "missing", "expected"), [
    (39, True, None),
    (62, True, "expiry_receipt_missing"),
    (39, False, "continuation_not_mature_before_cutoff"),
])
def test_later_missing_or_late_rows_never_erase_earlier_held_questions(offset, missing, expected):
    roster, _, source = training_fixture()
    if missing:
        rows = dict(source.minutes)
        del rows[source.opening + offset * MINUTE]
        source = replace(source, minutes=rows)
    else:
        source = replace_bar(source, offset, available_at=OPEN)
    pool = build(roster, lambda *_: source)
    assert pool.cases[0].status == "held_unresolved"
    first = pool.roster.rows[0]
    assert first.question.held.decision_at == source.opening + 37 * MINUTE
    assert first.unsupported_reason == expected
    assert (first.actual_mature_at is None) == (expected is not None)


def test_mature_path_with_interior_gap_keeps_its_selected_unknown_mass():
    roster, train, source = training_fixture()
    rows = dict(source.minutes)
    del rows[source.opening + 39 * MINUTE]
    source = replace(source, minutes=rows)
    pool = build(roster, lambda *_: source)
    query, _, _, visible = fixture()
    outcome = assess(pool.roster, query, visible, {(train.session, train.symbol): source})
    assert outcome.selection.weights == (F(1),)
    assert outcome.reason == "unresolved_path_mass"
    assert dict(outcome.unsupported_mass_by_action)["hold"] == 1


def test_missing_open_and_known_no_fill_have_distinct_case_states():
    roster, _, source = training_fixture()
    rows = dict(source.minutes)
    del rows[source.opening + 33 * MINUTE]
    missing = build(roster, lambda *_: replace(source, minutes=rows))
    declined = build(roster, lambda *_: replace_bar(source, 33, ohlc=(Decimal(150),) * 4))
    assert (missing.cases[0].status, declined.cases[0].status) == ("unresolved", "declined")
    assert not missing.roster.rows and not declined.roster.rows


def test_known_late_geometry_and_suppressed_questions_have_no_source_callback():
    roster, train, _ = training_fixture()
    original, = train.questions
    feature = original.feature
    late = replace(feature.question.path, end_at=OPEN - 3 * MINUTE)
    candidate = replace(original, feature=replace(feature, question=replace(feature.question, path=late)))
    suppressed = replace(original, status="overlap_suppressed", reasons=("earlier_shared_reservation",))
    changed = replace(train, questions=(candidate, suppressed))
    pool = build(replace(roster, coordinates=(roster.coordinates[0], changed, roster.coordinates[2])),
                 lambda *_: pytest.fail("premature source read"))
    assert not pool.roster.rows
    assert [(case.status, case.reason) for case in pool.cases] == [
        ("unmatured", "expiry_not_mature_before_cutoff"), ("overlap_suppressed", "earlier_shared_reservation")]


def test_duplicate_original_training_coordinate_is_refused_before_read():
    roster, train, _ = training_fixture()
    with pytest.raises(InputRefusal, match="original_roster_differs"):
        build(replace(roster, coordinates=(train, train)), lambda *_: pytest.fail("read from invalid roster"))
