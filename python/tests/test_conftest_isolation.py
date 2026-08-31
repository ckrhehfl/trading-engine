"""The experiment-log isolation in `conftest.py` must actually isolate.

A guard nobody verifies is worse than no guard: it converts "we might be
polluting the research log" into "we are certain we are not", while the
pollution continues. `runs/experiments.jsonl` really does carry two
`test-ofi-momentum` records from before that fixture existed, and its
`research_selection_trials` is the `N` behind every Deflated Sharpe
Ratio this project reports -- so "the fixture silently stopped working"
is a failure mode with a real, gating consequence.

These tests exist so the fixture fails loudly instead.
"""

from __future__ import annotations

import ast
import inspect
import json
from decimal import Decimal
from pathlib import Path

import conftest
import research.experiment_log as experiment_log
import research.walkforward as walkforward


def _log_one(**overrides):
    payload = dict(
        run_id="isolation-check",
        strategy_id="test-conftest-isolation",
        strategy_version="v1",
        params={},
        fold_results=[],
        aggregate_metrics={},
        data_range={"start_ms": 0, "end_ms": 1, "num_bars": 1},
        fee_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
        walk_forward_config={"train_bars": 1, "validate_bars": 1, "step_bars": 1, "fold_count": 0},
        is_holdout_run=False,
    )
    payload.update(overrides)
    return experiment_log.log_run(**payload)


def test_bound_defaults_are_deliberately_left_alone():
    """Documents *why* the fixture patches writes rather than defaults.

    A default argument is evaluated at `def` time, so `log_run`'s and
    `run_walk_forward`'s bound `runs_path` still hold the real path and
    are expected to. Patching them was the first design and it missed
    `OfiMomentumTrainable.__init__`; the wrapper catches all three. This
    asserts the premise so a future reader does not "fix" the unpatched
    defaults and conclude the wrapper is redundant.
    """
    for func in (experiment_log.log_run, walkforward.run_walk_forward):
        # `experiment_log.log_run` is the fixture's wrapper; unwrap to
        # reach the real function whose default is under test.
        assert inspect.unwrap(func).__kwdefaults__["runs_path"] == conftest.REAL_RUNS_PATH


def test_the_module_constant_points_somewhere_isolated_and_absolute():
    value = experiment_log.DEFAULT_RUNS_PATH
    assert value != conftest.REAL_RUNS_PATH
    assert Path(value).is_absolute(), (
        "an isolated path must be absolute -- a relative one would still "
        "resolve against whatever directory pytest happened to start in, "
        "which is exactly how python/runs/ got created"
    )


def test_a_default_path_write_lands_in_the_temp_dir_and_reads_back():
    _log_one()
    written = Path(experiment_log.DEFAULT_RUNS_PATH)
    assert written.exists(), "log_run did not write where the fixture pointed it"
    records = [json.loads(line) for line in written.read_text().splitlines() if line.strip()]
    assert [r["strategy_id"] for r in records] == ["test-conftest-isolation"]


def test_each_test_gets_a_fresh_log(tmp_path):
    """`tmp_path` is per-test, so a previous test's record must not be here.

    Without this, one test's write could be read back as another's and a
    count assertion elsewhere would pass for the wrong reason.
    """
    written = Path(experiment_log.DEFAULT_RUNS_PATH)
    assert not written.exists() or written.read_text().strip() == ""


def test_an_explicit_runs_path_still_wins(tmp_path):
    """The fixture changes the default only; a test asserting on log
    contents at its own path must keep working unchanged."""
    explicit = tmp_path / "explicit.jsonl"
    _log_one(runs_path=explicit)
    assert explicit.exists()
    assert not Path(experiment_log.DEFAULT_RUNS_PATH).exists()


def test_a_trainables_own_bound_default_is_redirected():
    """The case that defeated the first version of the fixture.

    Every `Trainable.__init__` in `research/strategies/` binds
    `runs_path = experiment_log.DEFAULT_RUNS_PATH` at import time and
    passes that *explicit* string down to `log_run`. Patching callers
    could never catch all 25 of them; patching the write function does.
    """
    _log_one(runs_path=conftest.REAL_RUNS_PATH)
    assert not Path(conftest.REAL_RUNS_PATH).is_absolute()
    written = Path(experiment_log.DEFAULT_RUNS_PATH)
    assert written.exists(), "a call carrying the real default path was not redirected"
    assert json.loads(written.read_text().splitlines()[0])["strategy_id"] == (
        "test-conftest-isolation"
    )


def test_every_write_function_in_experiment_log_is_covered():
    """Exhaustiveness: a newly added writer must not slip through.

    `WRITE_FUNCTIONS` is a hand-maintained tuple, so this asserts it
    still names every public callable in `experiment_log` that takes a
    `runs_path` and is not a read. Adding a writer without listing it
    fails here rather than silently appending to the real log.
    """
    reads = {"read_records"}
    writers = {
        name
        for name, obj in vars(experiment_log).items()
        if not name.startswith("_")
        and inspect.isfunction(obj)
        and obj.__module__ == experiment_log.__name__
        and "runs_path" in inspect.signature(obj).parameters
        and name not in reads
    }
    assert writers == set(conftest.WRITE_FUNCTIONS), (
        f"experiment_log's writers are {sorted(writers)} but conftest patches "
        f"{sorted(conftest.WRITE_FUNCTIONS)}. Add the new one to WRITE_FUNCTIONS."
    )


def test_no_module_binds_a_writer_directly():
    """The fixture patches `experiment_log.log_run` and
    `log_holdout_access` as module attributes, so a caller that did
    `from research.experiment_log import log_run` would hold its own
    reference and write straight past the redirect.

    Scans **all of `python/`**, not just `research/` — an earlier version
    checked only the latter and so missed
    `tests/test_experiment_log.py`, which really does bind both writers
    at module level.

    Parsed with `ast` rather than matched as text, and narrowed to the
    write functions: `read_records` and the module constants are safe to
    import directly, and flagging them would make this fail for reasons
    that carry no risk.
    """
    root = Path(__file__).resolve().parent.parent
    writers = set(conftest.WRITE_FUNCTIONS)
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if ".venv" in path.parts or path.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "research.experiment_log":
                continue
            bound = {alias.name for alias in node.names} & writers
            if bound:
                offenders.append(
                    f"{path.relative_to(root)}:{node.lineno} binds {sorted(bound)}"
                )
    assert not offenders, (
        "these bind an experiment_log writer directly, bypassing the autouse "
        "isolation in conftest.py:\n  " + "\n  ".join(offenders) +
        "\nUse `from research import experiment_log` and call "
        "`experiment_log.log_run(...)`, or pass an absolute runs_path."
    )
