"""Invented EI report cases with paired returns and explicit unsupported rows."""

from dataclasses import replace
from fractions import Fraction

import pytest

from research.intraday_context_entry_report import summarize_entry_comparisons
from research.intraday_ed_inputs import InputRefusal
from test_intraday_context_entry_compare import change_minute, data, first_pair, prepare, replay, value_model


def report(result):
    return summarize_entry_comparisons((result,), expected_folds=(result.prepared.fold,))


def test_report_preserves_weekly_denominator_and_paired_skip_value(data):
    plan, _ = prepare(data, model=value_model(data[2], Fraction(-1)))
    result, _ = replay(plan, data[-1])
    pair = first_pair(result)
    summary = report(result)
    original = summary["overall"]
    assert summary["original_evaluation_coordinates"] == 3
    assert summary["coordinate_statuses"]["session_missing"] == 1
    assert original["original_opportunities"] == original["both_known_opportunities"] == 2
    assert original["paired_selected_minus_baseline_net_pnl"] == -pair.baseline.payoff.net
    assert original["baseline"]["mean_closed_trade_net_bps"] == pair.baseline.payoff.net_return * 10000
    assert original["selected"]["mean_closed_trade_net_bps"] is None
    assert summary["weekly"][0]["original_coordinates"] == 3
    assert summary["weekly"][0]["baseline_modeled_fills"] == 1
    assert summary["weekly"][0]["selected_modeled_fills"] == 0
    assert summary["mean_of_daily_paired_opportunity_net_deltas"] == pair.net_delta / 2
    assert summary["by_episode_origin"]["A"]["paired_selected_minus_baseline_net_pnl"] == pair.net_delta
    assert summary["by_episode_origin"]["B"]["paired_selected_minus_baseline_net_pnl"] == 0
    assert summary["by_episode_origin"]["B"]["baseline"]["status_counts"] == {"no_fill": 1}


def test_report_distinguishes_unknown_exit_from_zero_profit(data):
    plan, _ = prepare(data, model=value_model(data[2], Fraction(-1)))
    initial, _ = replay(plan, data[-1])
    close_at = first_pair(initial).baseline.holding.episode.closed_at
    session = change_minute(data[-1], close_at, ohlc=None, price_state="halt")
    result, _ = replay(plan, session)
    summary = report(result)
    assert summary["overall"]["baseline"]["fills_with_unknown_outcome"] == 1
    assert summary["overall"]["both_known_opportunities"] == 1
    assert summary["overall"]["unpaired_opportunities"] == 1
    assert summary["overall"]["paired_selected_minus_baseline_net_pnl"] == 0
    assert summary["by_episode_origin"]["A"]["paired_selected_minus_baseline_net_pnl"] is None
    assert summary["by_episode_origin"]["B"]["paired_selected_minus_baseline_net_pnl"] == 0
    assert summary["date_contributions"][0]["paired_net_delta"] == 0
    assert summary["dates_with_paired_opportunities"] == 1


def test_report_rejects_a_removed_original_coordinate_and_overlapping_dates(data):
    plan, _ = prepare(data)
    result, _ = replay(plan, data[-1])
    with pytest.raises(InputRefusal, match="profile_or_coordinate_roster"):
        report(replace(result, coordinates=result.coordinates[:-1]))
    with pytest.raises(InputRefusal, match="evaluation_dates_overlap"):
        summarize_entry_comparisons((result, result), expected_folds=(plan.fold, plan.fold))
