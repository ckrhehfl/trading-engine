"""Test-suite-wide guards.

## Why this file exists

`research.experiment_log.DEFAULT_RUNS_PATH` is the relative string
`"runs/experiments.jsonl"`, and `log_run` is called automatically as
`run_walk_forward`'s last action. Any test that exercises a `Trainable`'s
`fit()` -- or anything else reaching a real walk-forward -- therefore
appends to whatever `runs/experiments.jsonl` resolves to **from the
current working directory**.

That is not a hypothetical. It really happened, twice, and the evidence
is in the repository: `runs/experiments.jsonl` carries two
`strategy_id: "test-ofi-momentum"` records over 20 synthetic bars, logged
2026-08-26 and 2026-08-27 by `tests/test_ofi_momentum.py::
TestTrainableFit` run from the repository root. A third leak created a
stray `python/runs/` when the same suite was run from `python/`.

**This is not cosmetic. That log is the sole input to
`research.overfitting_check.check_project_combination_count`, whose
`research_selection_trials` is the `N` every Deflated Sharpe Ratio in
this project is computed against** -- an Eligibility Bar gate. The two
leaked records made `test-ofi-momentum` its own single-member family
contributing 1 selection trial, so the project's stated `N` of 127 is
really 126 real trials plus one test artifact.

The direction of that error is the safe one -- an inflated `N` can only
*lower* a DSR, never raise it, so nothing was ever wrongly passed
because of it. That is why the two records are left in place rather than
edited out: the log is append-only by design, rewriting history to fix a
conservative error would be a worse precedent than carrying a documented
one, and `.planning/tm-c-confluence-hedge-result.md` records the exact
figure. **The leak itself is what gets fixed, here, so the next one is
not a coin flip on which direction it errs in.**

## Why an autouse fixture rather than fixing the one test

Fixing `test_ofi_momentum.py` would fix `test_ofi_momentum.py`. Every
future test that touches a `Trainable`, a walk-forward, or a
preregistration would be free to leak again, and would do so silently --
an appended line looks exactly like a real research record. Redirecting
the default for the whole suite makes the safe behaviour the one you get
without thinking about it, which is the only kind that survives.

A test that genuinely wants to assert on log contents still passes its
own `runs_path=tmp_path / ...` and is unaffected -- see the rule at the
bottom of this docstring for exactly what is and is not redirected.

## Patching the constant does nothing, and patching the callers does not scale

Reassigning `experiment_log.DEFAULT_RUNS_PATH` alone is **inert**. A
default argument is evaluated once, when the `def` executes, so the
module attribute and every bound default become separate objects from
that moment on. A fixture that only reassigns the constant looks like it
isolates the log while isolating nothing -- worse than no fixture.

Redirecting the *callers* instead does not scale either. **25 sites bind
`= experiment_log.DEFAULT_RUNS_PATH` as a default**, including a
`runs_path` parameter on the `__init__` of every `Trainable` in
`research/strategies/`. Enumerating them by hand rots the moment someone
adds the twenty-sixth, and it rots silently -- which was proven in
practice: a first version of this fixture listed `log_run` and
`run_walk_forward`, and `OfiMomentumTrainable.__init__`'s own bound
default leaked straight past it on the very first verification run.

So this patches the **write functions themselves**, which is the one
place every path converges. That works because no module in this
codebase does `from research.experiment_log import log_run`; all 10
importers use `from research import experiment_log` and call through the
module attribute. `test_conftest_isolation.py` asserts that property
holds, so the day someone adds a direct import, a test fails rather than
a research record quietly appears.

**Writes only.** `read_records` and the read-side defaults are left
pointing at the real log: reading it is not destructive, and a test that
deliberately checks the committed log's integrity should keep being able
to.

## What counts as "needs redirecting": any relative path

Not "the default was omitted", and not "it equals
`runs/experiments.jsonl`". Both are too narrow, and the second was tried
and leaked: `live/generate_daily_signal.py` writes to a **different**
committed file, `runs/live_signals.jsonl`, through the same `log_run`,
passing that path explicitly. A rule keyed to the experiments log let it
straight through, and the full suite recreated `python/runs/` on the very
next run.

The property that actually matters is the one that caused every
incident: **a relative path resolves against whatever directory pytest
was started in**, so the same test writes to `runs/` at the repository
root or `python/runs/` depending on the invocation, and both are real
files this project commits. An absolute path cannot do that and is
always deliberate — every `tmp_path`-based test supplies one. So:
relative in, isolated out; absolute in, honoured exactly.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

import research.experiment_log as experiment_log

# Every function in `experiment_log` that appends to the log. Anything
# added here must also be added to `test_conftest_isolation.py`'s
# exhaustiveness check, which fails if a new writer appears.
WRITE_FUNCTIONS = ("log_run", "log_holdout_access")

# Captured at import, before anything is patched. This is the value the
# 25 bound defaults across `research/` are holding -- they took their
# copy at *their* import time, so a later reassignment of
# `experiment_log.DEFAULT_RUNS_PATH` never reaches them and this is the
# string their calls actually arrive carrying.
REAL_RUNS_PATH = experiment_log.DEFAULT_RUNS_PATH


@pytest.fixture(autouse=True)
def _isolate_experiment_log(tmp_path, monkeypatch):
    """Force every experiment-log write to a per-test temp dir.

    An **absolute** `runs_path=` from the caller is honoured exactly, so
    a test asserting on log contents at its own `tmp_path` keeps working.
    An omitted or **relative** one is redirected into this test's temp
    dir, keeping its filename so the experiments log and the
    live-signals log stay distinguishable.

    `monkeypatch` restores the originals after each test, so nothing here
    leaks into a later test, a later session, or another tool's import.
    """
    isolated_dir = tmp_path / "runs"
    monkeypatch.setattr(
        experiment_log, "DEFAULT_RUNS_PATH", str(isolated_dir / "experiments.jsonl")
    )

    for name in WRITE_FUNCTIONS:
        original = getattr(experiment_log, name)

        def redirected(*args, __original=original, **kwargs):
            given = kwargs.get("runs_path")
            if given is None:
                kwargs["runs_path"] = isolated_dir / "experiments.jsonl"
            elif not Path(given).is_absolute():
                # Keep the filename so a test distinguishing the
                # experiments log from the live-signals log still can --
                # only the directory is relocated.
                kwargs["runs_path"] = isolated_dir / Path(given).name
            return __original(*args, **kwargs)

        monkeypatch.setattr(experiment_log, name, functools.wraps(original)(redirected))
