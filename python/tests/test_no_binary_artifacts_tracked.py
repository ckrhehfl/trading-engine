"""No database or other build artifact may be tracked in git.

A 36 KB SQLite file reached `main` on 2026-09-10, in a documentation PR.
Two things had to go wrong together, and neither was noticed:

1. A `cd python && PYTHONPATH=.` invocation resolved
   `DEFAULT_DB_PATH` -- the relative path `python/data/var/klines.sqlite3`
   -- against `python/` itself, creating `python/python/data/var/`. The
   `.gitignore` rule for the kline store is anchored at `python/data/var/`
   and so did not match the doubled path.
2. A commit used `git add -A`, which swept the untracked artifact in.

The remedy for (1) is an unanchored ignore rule, now in place. This file
is the remedy for (2): an ignore rule only helps for paths someone
thought of, and `git add -A` will happily stage anything that slips
through. A check that runs on every push does not depend on either.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Extensions that are always build output or local state, never source.
FORBIDDEN_SUFFIXES = (".sqlite3", ".sqlite", ".db", ".pyc", ".class", ".jar")

# Real, deliberate exceptions, each with the reason it is not a mistake.
# An addition here should have to justify itself in review; the list is
# meant to stay very short.
ALLOWED: frozenset[str] = frozenset({
    # Committing the Gradle wrapper jar is the documented, recommended
    # practice -- it is what lets `./gradlew` bootstrap a pinned Gradle on
    # a machine that has none, which is exactly how the VPS builds. This
    # was the only pre-existing hit when the check was written.
    "java/gradle/wrapper/gradle-wrapper.jar",
})


def _tracked_files() -> list[str]:
    """`-z`, because the default output quotes unusual names.

    `git ls-files` renders a path containing a newline in C style with
    surrounding double quotes, so `weird<newline>name.sqlite3` arrives as
    the literal `"weird\\nname.sqlite3"` -- which ends in `"`, not in
    `.sqlite3`, and slips straight past the suffix test below. Verified
    directly rather than reasoned about:

        $ git ls-files            ->  "weird\\nname.sqlite3"
        $ git ls-files -z         ->  weird<NUL-separated real name>

    NUL separation removes the quoting entirely, which is the only form
    that cannot be defeated by a filename. Raised by CodeRabbit on #160.
    """
    done = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    )
    return [p for p in done.stdout.split("\0") if p]


def offending(paths: list[str]) -> list[str]:
    """The rule itself, separated so it can be tested against a fixture.

    Written this way because the last guard this repository added without
    one was inert, and the one before that was tested only against a
    passing case.
    """
    return sorted(
        p for p in paths
        if p not in ALLOWED and p.lower().endswith(FORBIDDEN_SUFFIXES)
    )


def test_the_rule_detects_a_tracked_database():
    """Fed the real path that got through, plus the shapes around it."""
    assert offending(["python/python/data/var/klines.sqlite3"]) == [
        "python/python/data/var/klines.sqlite3"
    ]
    assert offending(["a/b.db", "c/d.class", "e/f.jar"]) == [
        "a/b.db", "c/d.class", "e/f.jar"
    ]
    # Source must not be flagged -- a check that cries wolf gets switched off.
    assert offending(["python/live/generate_daily_signal.py", "README.md"]) == []
    # And a deliberate exception must actually be exempt.
    assert offending(["java/gradle/wrapper/gradle-wrapper.jar"]) == []


def test_a_quoted_path_would_have_slipped_through():
    """Pins the reason `_tracked_files` uses `-z`.

    This is what the rule receives if the default `git ls-files` output is
    used: quoted, ending in `"` rather than the suffix. The assertion is
    that it is NOT detected -- which is exactly why the caller must never
    hand it input in that form.
    """
    quoted = '"weird\\nname.sqlite3"'
    assert offending([quoted]) == [], (
        "the rule matches a C-quoted path, so this test no longer pins "
        "anything -- re-derive why `-z` is needed before deleting it"
    )
    assert offending(["weird\nname.sqlite3"]) == ["weird\nname.sqlite3"]


def test_no_binary_artifact_is_tracked():
    tracked = _tracked_files()
    assert tracked, "git ls-files returned nothing -- has the repo moved?"

    found = offending(tracked)
    assert not found, (
        "these build/state artifacts are tracked in git and should not be:\n"
        + "\n".join(f"  {p}   fix: git rm --cached {p}" for p in found)
    )


# ------------------------------------------------- the ignore rules themselves


def _is_ignored(path: str) -> bool:
    """`git check-ignore`, which answers for a path that need not exist.

    Asking git rather than re-implementing `.gitignore` matching is the
    whole point: the defect below is a property of git's own anchoring
    rule, so a hand-rolled matcher would reproduce my misunderstanding of
    it instead of catching it.
    """
    done = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", path],
        capture_output=True, text=True, check=False,
    )
    return done.returncode == 0


# Each entry is a real path a real invocation produced, not a constructed
# one -- the point of the rule is that *depth* must not defeat it, and
# depth is what a wrong working directory adds.
NESTED_RUNTIME_PATHS = (
    # 2026-09-10: `cd python && PYTHONPATH=.` resolved the relative
    # DEFAULT_DB_PATH against `python/` itself.
    "python/python/data/var/klines.sqlite3",
    "python/data/var/klines.sqlite3",
    # 2026-09-22, on the instance: the same shape one directory over.
    # `health_check --dry-run` took its lock under the relative STATE_PATH
    # and left an untracked `python/var/`, which then tripped
    # `check_deployment`'s own `uncommitted_changes` alert.
    "python/var/live/health-state.json.lock",
    "var/live/daily_signal.json",
)


@pytest.mark.parametrize("path", NESTED_RUNTIME_PATHS)
def test_runtime_state_is_ignored_at_any_depth(path):
    """**A pattern containing a slash is anchored at the repository
    root**, which is the defect both incidents share. `var/live/` reads
    like "any `var/live` anywhere" and means "`<root>/var/live`" — so the
    rule has to be written `**/var/live/` to mean what it looks like.
    """
    assert _is_ignored(path), (
        f"{path} is not ignored. A rule containing a slash is anchored at "
        f"the repo root unless written `**/...`, and a wrong working "
        f"directory adds exactly the depth that defeats the anchored form."
    )
