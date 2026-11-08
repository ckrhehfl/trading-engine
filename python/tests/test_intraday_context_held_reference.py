"""Invented comparisons and adversarial changes to saved EI selections."""

from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_context_held as held
from research.intraday_context_held_reference import verify_held_question, verify_held_selection
from research.intraday_context_situations import context_frame
from research.intraday_ed_inputs import MINUTE, InputRefusal
from test_intraday_context_held import OPEN, PrefixOnly, fixture, fold, historical


@pytest.mark.parametrize("options", [{}, {"reduce": True}, {"missing": 34}])
def test_question_uses_only_causal_prefix_and_original_position(options, monkeypatch):
    query, session, current, _ = fixture(**options)
    prefix = replace(session, minutes=PrefixOnly(session.minutes, current.observation_end))

    def forbidden(*args, **kwargs):
        pytest.fail("producer projection called during the reference check")

    for name in ("project_context_held", "held_decision_inputs", "context_frame", "_frame_digest", "_question_digest"):
        monkeypatch.setattr(held, name, forbidden)
    verified = verify_held_question(query, original_entry=query.entry, original_episode=query.episode,
                                    original_session=prefix)
    assert verified == query
    expected_size = Fraction(1, 2) if options.get("reduce") else Fraction(1)
    assert verified.held.remaining_fraction == expected_size
    if options.get("missing"):
        assert verified.reasons == ("current_context_unknown", "held_chart_prefix_unknown")
        assert verified.held.issues[0].reason == "missing_minute" and verified.held.minutes == ()


def test_late_price_stays_unpublished_at_held_clock():
    query, session, current, _ = fixture()
    stamp = session.opening + 34 * MINUTE
    rows = dict(session.minutes)
    rows[stamp] = replace(rows[stamp], available_at=current.available_at + MINUTE)
    session = replace(session, minutes=rows)
    delayed = held.project_context_held(query.entry, context_frame(session, decision_at=current.available_at),
        query.episode, session, entry_feature_sha256=query.entry_feature_sha256)
    verified = verify_held_question(delayed, original_entry=query.entry, original_episode=query.episode,
                                    original_session=session)
    assert verified.status == "unsupported" and verified.held.minutes == ()
    assert verified.held.issues[0].reason == "minute_available_after_cutoff"


def test_rehashed_chart_position_and_context_mutations_fail_original_binding():
    query, session, _, _ = fixture(reduce=True)
    first, *rest = query.held.minutes
    changed_minute = (first[0] + 1, *first[1:])
    changed_prices = replace(query.held, minutes=(changed_minute, *rest))
    changed_size = replace(query.held, remaining_fraction=Fraction(1), half_reduced=False)
    for changed in (replace(query, held=changed_prices), replace(query, held=changed_size),
                    replace(query, current_context="B"), replace(query, current_frame_sha256="d" * 64),
                    replace(query, entry_feature_sha256="c" * 64)):
        rehashed = replace(changed, input_sha256=held._question_digest(changed))
        with pytest.raises(InputRefusal, match="question_differs"):
            verify_held_question(rehashed, original_entry=query.entry, original_episode=query.episode,
                                 original_session=session)


def test_independent_context_distance_changes_ranking(monkeypatch):
    query = fixture(decision=57)[0]
    history = tuple(historical(fixture(day=-1, symbol=symbol, decision=57, repeated=repeated)[0])
                    for symbol, repeated in (("A", True), ("B", True), ("C", False)))
    split = fold(-1)
    pool = held.ContextHeldRoster(history, fold=split)
    chart, contextual = (pool.match(query, mode=mode) for mode in ("chart", "chart_context"))

    def forbidden(*args, **kwargs):
        pytest.fail("producer selection logic called by reference")

    for name in ("_held_state", "_bounded_episodes", "_vector", "_question_digest"):
        monkeypatch.setattr(held, name, forbidden)
    monkeypatch.setattr(held.ContextHeldRoster, "match", forbidden)
    for selection, mode, symbols, distances in (
        (chart, "chart", ("A", "B"), (0.0, 0.0)),
        (contextual, "chart_context", ("C", "A"), (0.0, 2.0)),
    ):
        audited = verify_held_selection(selection, query=query, history=reversed(history), fold=split, mode=mode)
        assert tuple(row.question.held.symbol for row in audited.sources) == symbols
        assert audited.distances == distances
        assert audited.weights == (Fraction(1, 2), Fraction(1, 2))
        assert audited.precision_reason == "single_training_date_inconclusive"
    assert chart.pool_sha256 == contextual.pool_sha256 and chart.eligible_sha256 == contextual.eligible_sha256


def test_maturity_coverage_and_unsupported_query_keep_original_pool():
    query = fixture(missing=34)[0]
    history = (
        historical(fixture(day=-1, symbol="eligible")[0]),
        historical(fixture(day=-1, symbol="unmatured")[0], reason="expiry_receipt_missing"),
        historical(fixture(day=-1, symbol="short", decision=42)[0]),
        historical(fixture(day=-1, symbol="unsupported", missing=34)[0]),
    )
    split = fold(-1)
    observed = held.ContextHeldRoster(history, fold=split).match(query, mode="chart_context")
    result = verify_held_selection(observed, query=query, history=history, fold=split, mode="chart_context")
    assert result.status == "query_unsupported" and result.sources == ()
    assert result.candidate_rows == 4 and result.eligible_rows == 1
    assert dict(result.excluded_by_reason) == {"remaining_time_coverage_short": 1,
        "training_question_unsupported": 1, "unmatured: expiry_receipt_missing": 1}
    with pytest.raises(InputRefusal, match="selection_differs"):
        verify_held_selection(observed, query=query, history=history[:-1], fold=split, mode="chart_context")


def test_symbol_day_dedup_and_uneven_date_weights():
    query = fixture(decision=42)[0]
    history = (
        historical(fixture(day=-1, symbol="A", decision=42)[0]),
        historical(fixture(day=-1, symbol="A", decision=37)[0]),
        historical(fixture(day=-1, symbol="B", decision=42)[0]),
        historical(fixture(day=-1, symbol="C", decision=42)[0]),
        historical(fixture(day=-2, symbol="A", decision=42)[0]),
    )
    split = fold(-2, -1)
    observed = held.ContextHeldRoster(history, fold=split).match(query, mode="chart")
    result = verify_held_selection(observed, query=query, history=history, fold=split, mode="chart")
    assert result.sources == (history[4], history[0], history[2])
    assert result.date_weights == ((split.training_sessions[0], Fraction(1, 3)),
                                   (split.training_sessions[1], Fraction(2, 3)))
    assert result.effective_date_count == Fraction(9, 5)
    for changed in (replace(observed, sources=tuple(reversed(observed.sources))),
                    replace(observed, distances=(0, *observed.distances[1:])),
                    replace(observed, pool_sha256="e" * 64), replace(observed, eligible_sha256="d" * 64),
                    replace(observed, weights=(Fraction(1),) * 3),
                    replace(observed, effective_date_count=Fraction(2))):
        with pytest.raises(InputRefusal, match="selection_differs"):
            verify_held_selection(changed, query=query, history=history, fold=split, mode="chart")


def test_full_cap_and_chronological_tie_break():
    days = tuple(range(-33, 0))
    split = fold(*days)
    query = fixture()[0]
    history = tuple(historical(fixture(day=day, symbol=symbol)[0]) for day in days for symbol in ("B", "A"))
    observed = held.ContextHeldRoster(history, fold=split).match(query, mode="chart")
    result = verify_held_selection(observed, query=query, history=reversed(history), fold=split, mode="chart")
    assert len(result.sources) == 64 and result.distinct_dates == 32
    assert result.sources[:2] == (history[1], history[0])
    assert result.date_weights[-1][0] == split.training_sessions[-2]


def test_rejects_bound_row_with_cutoff_maturity_or_duplicate_coordinate():
    query = fixture()[0]
    source = historical(fixture(day=-1)[0])
    split = fold(-1)
    selection = held.ContextHeldRoster((source,), fold=split).match(query, mode="chart")
    for mature in (OPEN, OPEN + MINUTE, source.coverage_end + 2 * MINUTE):
        with pytest.raises(InputRefusal, match="maturity_differs"):
            verify_held_selection(selection, query=query, history=(replace(source, actual_mature_at=mature),),
                                  fold=split, mode="chart")
    with pytest.raises(InputRefusal, match="history_identity"):
        verify_held_selection(selection, query=query, history=(source, source), fold=split, mode="chart")
