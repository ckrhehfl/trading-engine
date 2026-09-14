"""Every module must mean the same database by `DEFAULT_DB_PATH`.

**This exists because on 2026-09-14 they did not.** Two relative
conventions were coexisting -- `"python/data/var/klines.sqlite3"` (correct
only from the repo root) and `"data/var/klines.sqlite3"` (correct only from
`python/`) -- and neither *fails* when wrong, because `connect()` creates
whatever path it is given. Running `scripts/collect-krx-flow.sh`, which
cds to the repo root, against a module using the second convention created
a **second, parallel database** and wrote 3,600 rows into it. The collector
reported success, exited 0, and logged a clean run.

It surfaced only from an external observable: a re-run that should have
written zero rows wrote 3,600 again. That is the same lesson
`python/tests/conftest.py` records for `runs/experiments.jsonl` -- *"a
relative path resolves against whatever directory pytest started in"*.

So the rule is not "pick one convention" but **"the default may not depend
on the working directory at all."**
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

# Every module that offers a `--db-path` default. A new one belongs here;
# the whole failure mode is a module quietly meaning a different file.
MODULES_WITH_A_DB_DEFAULT = (
    "data._paths",
    "data.backfill",
    "data.backfill_binance",
    "data.backfill_kis",
    "data.collect_positioning",
    "data.krx_universe",
    "data.kis_investor_flow",
    "research.run_kr10_portfolio",
)


def _default(module_name: str) -> str:
    module = importlib.import_module(module_name)
    value = getattr(module, "DEFAULT_DB_PATH", None)
    assert value is not None, f"{module_name} has no DEFAULT_DB_PATH"
    return str(value)


@pytest.mark.parametrize("module_name", MODULES_WITH_A_DB_DEFAULT)
def test_the_default_is_absolute(module_name):
    """A relative default is the bug itself, whichever direction it leans."""
    value = _default(module_name)
    assert os.path.isabs(value), (
        f"{module_name}.DEFAULT_DB_PATH is relative ({value!r}), so it means a "
        f"different file depending on where the process started"
    )


def test_every_module_means_the_same_file():
    defaults = {name: _default(name) for name in MODULES_WITH_A_DB_DEFAULT}
    assert len(set(defaults.values())) == 1, (
        f"modules disagree about the database: {defaults}"
    )


def test_the_default_points_inside_the_data_package():
    """Guards against an absolute path that is absolute and still wrong."""
    expected = Path(__file__).resolve().parents[1] / "data" / "var" / "klines.sqlite3"
    assert Path(_default("data._paths")) == expected


def test_the_default_does_not_move_with_the_working_directory(tmp_path, monkeypatch):
    """The property the incident violated, asserted directly rather than
    inferred from the string being absolute."""
    before = _default("data.krx_universe")
    monkeypatch.chdir(tmp_path)
    importlib.reload(importlib.import_module("data._paths"))
    after = _default("data.krx_universe")
    assert before == after


def test_no_module_still_hardcodes_a_relative_database_path():
    """A new module could reintroduce the literal without importing the
    canonical one, and nothing above would notice."""
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in list(root.glob("data/*.py")) + list(root.glob("research/*.py")):
        if path.name == "_paths.py":
            continue
        text = path.read_text(encoding="utf-8")
        for literal in ('"data/var/klines.sqlite3"', '"python/data/var/klines.sqlite3"'):
            # A mention inside a comment or docstring is documentation of the
            # incident, not a reintroduction of it; only an assignment counts.
            if f"DEFAULT_DB_PATH = {literal}" in text or f"default={literal}" in text:
                offenders.append(f"{path.name}: {literal}")
    assert offenders == [], f"relative database defaults reintroduced: {offenders}"
