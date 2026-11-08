"""EI branch accounting against independent opens and the original first lot."""

from dataclasses import replace
from fractions import Fraction

import pytest

from research import intraday_context_management as management
from research.intraday_context_management_execution_reference import verify_management_case, verify_management_execution
from research.intraday_ed_inputs import InputRefusal, MINUTE
from test_intraday_context_management import case_fixture


def managed(*, missing_open=False):
    opportunity, prepared, session, roster, read_prior, _ = case_fixture()
    if missing_open:
        late = session.opening + 62 * MINUTE
        session = replace(session, minutes={at: row for at, row in session.minutes.items() if at != late})
    opens = {at: Fraction(row.ohlc[0]) for at, row in session.minutes.items() if row.ohlc is not None}
    case = management.replay_context_management(opportunity, prepared, session, opens, roster,
                                               read_prior_session=read_prior, include_reentry=True)
    return case, session


@pytest.mark.parametrize("missing_open", [False, True])
def test_execution_audit_uses_original_open_and_preserves_unknown(missing_open, monkeypatch):
    case, session = managed(missing_open=missing_open)

    def forbidden(*args, **kwargs):
        pytest.fail("production replay used during independent execution check")

    monkeypatch.setattr(management, "replay_context_management", forbidden)
    monkeypatch.setattr(management, "_position_replay", forbidden)
    report = verify_management_execution(case, original_initial=case.initial, original_session=session)
    expected = None if missing_open else -case.initial_fill.assumed_cost
    assert report["net_by_arm"] == dict.fromkeys(("fixed", "chart", "chart_context", "reentry"), expected)
    assert report["original_open_count"] == 2


def test_cash_change_without_a_recorded_fill_is_rejected():
    case, session = managed()
    terminal = case.chart.episode
    changed = replace(case, chart=replace(case.chart, episode=replace(terminal,
        book=replace(terminal.book, cash=terminal.book.cash + 1))))
    with pytest.raises(InputRefusal, match="audit_cash_inventory"):
        verify_management_execution(changed, original_initial=case.initial, original_session=session)


def test_missing_assessment_and_extra_open_receipt_are_rejected():
    case, session = managed()
    with pytest.raises(InputRefusal, match="assessment_clocks"):
        verify_management_execution(replace(case, decisions=case.decisions[1:]),
                                    original_initial=case.initial, original_session=session)
    extra = (session.opening + 45 * MINUTE, Fraction(114))
    changed = replace(case, modeled_open_receipts=tuple(sorted((*case.modeled_open_receipts, extra))))
    with pytest.raises(InputRefusal, match="original_open_receipts"):
        verify_management_execution(changed, original_initial=case.initial, original_session=session)


def test_case_audit_checks_discretionary_action_and_fresh_reset_against_the_source():
    case, session = managed()
    opportunity, _, _, pool, read_prior, _ = case_fixture()
    arguments = {"original_initial": case.initial, "original_entry": opportunity.feature, "original_session": session,
                 "original_history": pool.rows, "fold": pool.fold, "read_prior_session": read_prior, "include_reentry": True}
    audit = verify_management_case(case, **arguments)
    assert audit["discretionary_decisions_checked"] == len(case.decisions) == 10
    assert audit["reentry_reset_verified"]
    original, *rest = case.chart.events
    changed = replace(case, chart=replace(case.chart, events=(replace(original, reason="wrong_value_decision"), *rest)))
    # The same cash and order ledger alone cannot expose the changed rationale.
    verify_management_execution(changed, original_initial=case.initial, original_session=session)
    with pytest.raises(InputRefusal, match="reference_action_differs"):
        verify_management_case(changed, **arguments)
