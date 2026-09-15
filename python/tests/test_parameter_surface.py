"""Tests for `research.parameter_surface`.

Two properties carry the module, and each exists because of a real
incident rather than a style preference:

- **Coverage.** S16 concluded "the signal is not there" from
  `entry_z=5.0` while `|z|>=6` was never run, and running it reversed the
  sign. A hole in the grid is invisible in a heatmap, so the count is
  asserted rather than drawn.
- **Spike versus plateau.** CLAUDE.md's rule is *"never conclude about a
  domain from one parameter setting"*; this turns the rule into a number,
  so the tests can pin what "a spike" means instead of leaving it to
  whoever is squinting at the picture.
"""

from __future__ import annotations

import json

import pytest

from research.parameter_surface import (
    DEFAULT_PLATEAU_RATIO,
    build_surface,
    load_records,
    render,
    report,
    varying_parameters,
)


def _rec(sid="s", metric="sharpe_ratio", value=1.0, key="params", **params):
    return {
        "record_type": "backtest_run",
        "strategy_id": sid,
        key: params,
        "aggregate_metrics": {metric: value},
    }


def _grid(values: dict[tuple[int, int], float], sid="s"):
    return [_rec(sid=sid, value=v, fast=f, slow=s) for (f, s), v in values.items()]


# ------------------------------------------------------------- coverage


def test_coverage_reports_the_fraction_of_the_grid_actually_run():
    """Two of four cells run, so the implied 2x2 grid is half covered."""
    surf = build_surface(_grid({(1, 10): 1.0, (2, 20): 2.0}), "s", ("fast", "slow"))
    assert surf.grid_size == 4
    assert len(surf.cells) == 2
    assert surf.coverage == pytest.approx(0.5)


def test_a_diagonal_sweep_is_reported_as_sparse():
    """The real shape of two of this project's own sweeps: `fast` and
    `slow` moved together as pairs, so a 5x5 grid holds 5 cells and there
    is no surface at all — only a line through it."""
    surf = build_surface(
        _grid({(f, f * 5): 1.0 for f in (3, 4, 5, 6, 8)}), "s", ("fast", "slow")
    )
    assert surf.grid_size == 25 and len(surf.cells) == 5
    assert surf.coverage == pytest.approx(0.2)


def test_a_full_grid_reports_complete_coverage():
    surf = build_surface(
        _grid({(f, s): 1.0 for f in (1, 2) for s in (10, 20)}), "s", ("fast", "slow")
    )
    assert surf.coverage == 1.0


def test_unrun_cells_render_as_a_question_mark():
    """Loud on purpose — a blank would read as 'low', which is a
    different claim from 'never measured'."""
    surf = build_surface(_grid({(1, 10): 1.0, (2, 20): 2.0}), "s", ("fast", "slow"))
    assert "?" in render(surf)


# -------------------------------------------------------- spike vs plateau


def test_a_broad_hill_is_a_plateau():
    values = {(f, s): 1.0 for f in (1, 2, 3) for s in (10, 20, 30)}
    values[(2, 20)] = 1.2
    surf = build_surface(_grid(values), "s", ("fast", "slow"))
    assert surf.verdict == "PLATEAU"
    assert surf.plateau_ratio == pytest.approx(1.0 / 1.2)


def test_an_isolated_peak_is_a_spike():
    """The real case: `single-lookback-momentum`'s best cell has a mean of
    2.696 with neighbours averaging 0.090 — a ratio of 0.03."""
    values = {(f, s): 0.09 for f in (1, 2, 3) for s in (10, 20, 30)}
    values[(2, 20)] = 2.70
    surf = build_surface(_grid(values), "s", ("fast", "slow"))
    assert surf.verdict == "SPIKE"
    assert surf.plateau_ratio is not None and surf.plateau_ratio < 0.05


def test_the_threshold_is_what_separates_them():
    """Same surface, two thresholds — so the verdict is a stated
    convention rather than a property of the data."""
    values = {(f, s): 0.6 for f in (1, 2, 3) for s in (10, 20, 30)}
    values[(2, 20)] = 1.0
    assert build_surface(_grid(values), "s", ("fast", "slow"), plateau_ratio_threshold=0.5).verdict == "PLATEAU"
    assert build_surface(_grid(values), "s", ("fast", "slow"), plateau_ratio_threshold=0.8).verdict == "SPIKE"


def test_a_peak_whose_neighbours_were_never_run_is_called_out_separately():
    """Not 'SPIKE' and not 'PLATEAU'. This is exactly S16's error shape —
    the cells that would have settled it were never evaluated — and
    collapsing it into either verdict would hide that."""
    surf = build_surface(
        _grid({(f, f * 5): 1.0 for f in (3, 4, 5)} | {(4, 20): 3.0}), "s", ("fast", "slow")
    )
    assert surf.verdict.startswith("PEAK ISOLATED")
    assert surf.plateau_ratio is None


def test_an_all_negative_surface_yields_no_verdict_rather_than_a_ratio():
    """A ratio against a negative peak reads as a verdict and is not one."""
    surf = build_surface(
        _grid({(f, s): -1.0 for f in (1, 2) for s in (10, 20)}), "s", ("fast", "slow")
    )
    assert surf.verdict == "NO POSITIVE CELL"
    assert surf.plateau_ratio is None


def test_an_empty_selection_is_empty_not_an_error():
    surf = build_surface([], "nothing-here")
    assert surf.verdict == "EMPTY" and surf.notes


# ------------------------------------------------------------- plumbing


def test_both_parameter_spellings_are_read():
    """The real log carries `params` on scored records and `parameters`
    on some older ones. Reading only one silently halves the sweep."""
    recs = [
        _rec(key="params", value=1.0, fast=1),
        _rec(key="parameters", value=2.0, fast=2),
    ]
    assert varying_parameters(recs) == {"fast": 2}


def test_repeated_cells_are_averaged_and_counted():
    recs = _grid({}) + [
        _rec(value=1.0, fast=1, slow=10),
        _rec(value=3.0, fast=1, slow=10),
    ]
    surf = build_surface(recs, "s", ("fast", "slow"))
    cell = surf.cells[(1, 10)]
    assert (cell.n, cell.mean, cell.best, cell.worst) == (2, 2.0, 3.0, 1.0)


def test_a_record_without_the_metric_is_skipped():
    recs = [_rec(value=1.0, fast=1, slow=10), {"strategy_id": "s", "params": {"fast": 2, "slow": 20}}]
    assert len(build_surface(recs, "s", ("fast", "slow")).cells) == 1


def test_a_non_finite_metric_is_skipped():
    bad = _rec(value=1.0, fast=2, slow=20)
    bad["aggregate_metrics"]["sharpe_ratio"] = float("nan")
    assert len(build_surface([_rec(value=1.0, fast=1, slow=10), bad], "s", ("fast", "slow")).cells) == 1


def test_axes_default_to_the_two_most_varied_parameters():
    recs = [_rec(value=1.0, fast=f, slow=s, fixed=7) for f in (1, 2, 3) for s in (10, 20)]
    assert set(build_surface(recs, "s").axes) == {"fast", "slow"}


def test_a_truncated_final_line_does_not_break_the_reader(tmp_path):
    """An append-only log can end mid-write."""
    p = tmp_path / "experiments.jsonl"
    p.write_text(json.dumps(_rec(value=1.0, fast=1)) + "\n{\"strategy_id\": \"s\"", encoding="utf-8")
    assert len(load_records(p)) == 1


def test_a_missing_log_is_empty_rather_than_an_error(tmp_path):
    assert load_records(tmp_path / "nope.jsonl") == []


def test_the_report_names_coverage_and_the_verdict():
    values = {(f, s): 0.09 for f in (1, 2, 3) for s in (10, 20, 30)}
    values[(2, 20)] = 2.70
    text = report(build_surface(_grid(values), "s", ("fast", "slow")))
    assert "COVERAGE" in text and "VERDICT" in text and "SPIKE" in text


def test_the_default_threshold_is_the_documented_one():
    assert DEFAULT_PLATEAU_RATIO == pytest.approx(0.5)
