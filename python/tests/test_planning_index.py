"""`.planning/README.md`'s index must match what is actually on disk.

An index that silently goes stale is worse than no index: it sends a
reader to a file that moved, or hides one that exists, and it does both
with the confidence of a maintained document. Three ways it can rot, all
checked here — a document added and not listed, a document listed and
deleted, and a title edited without the index following.

The same reasoning as every other guard in this repository: the one
thing a check must not do is quietly pass while the thing it guards has
drifted.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLANNING = REPO_ROOT / ".planning"
README = PLANNING / "README.md"

# `- [`name.md`](name.md) — Title`
ENTRY = re.compile(r"^- \[`(?P<name>[^`]+)`\]\((?P<href>[^)]+)\) — (?P<title>.+)$", re.M)


def entries() -> list[tuple[str, str]]:
    """Every row as it literally appears, duplicates included.

    `indexed()` collapses by filename, which is what the other checks
    want and is also how a duplicate row hides: two entries for one
    document become one, and every check passes while the README shows
    the reader two lines -- with two different descriptions, if someone
    edited one of them.
    """
    return [
        (m.group("name"), m.group("title").strip())
        for m in ENTRY.finditer(README.read_text(encoding="utf-8"))
    ]


def indexed() -> dict[str, str]:
    return dict(entries())


def on_disk() -> list[Path]:
    return sorted(p for p in PLANNING.glob("*.md") if p.name != "README.md")


def title_of(path: Path) -> str:
    match = re.search(r"^#\s+(.+)", path.read_text(encoding="utf-8"), re.M)
    assert match, f"{path.name} has no `# ` heading to index"
    return re.sub(r"\s+", " ", match.group(1)).strip()


def test_every_planning_document_is_indexed():
    missing = sorted({p.name for p in on_disk()} - set(indexed()))
    assert not missing, (
        f"{len(missing)} document(s) missing from .planning/README.md's index: "
        f"{missing}. Add them — the index is how anyone finds these."
    )


def test_the_index_lists_nothing_that_has_been_deleted():
    stale = sorted(set(indexed()) - {p.name for p in on_disk()})
    assert not stale, (
        f"the index points at {len(stale)} file(s) that no longer exist: {stale}"
    )


def test_each_entry_matches_its_documents_own_title():
    """The description is the document's own `# ` heading, so it says
    what that document concluded rather than what someone once summarised
    it as. A drifting copy is how an index starts lying."""
    listed = indexed()
    drifted = [
        (p.name, listed[p.name], title_of(p))
        for p in on_disk()
        if p.name in listed and listed[p.name] != title_of(p)
    ]
    assert not drifted, "index descriptions no longer match their titles:\n" + "\n".join(
        f"  {name}\n    index: {was}\n    file:  {now}" for name, was, now in drifted
    )


def test_the_link_target_matches_the_filename():
    bad = [
        (m.group("name"), m.group("href"))
        for m in ENTRY.finditer(README.read_text(encoding="utf-8"))
        if m.group("name") != m.group("href")
    ]
    assert not bad, f"link text and target disagree: {bad}"


def test_no_document_is_indexed_twice():
    """A duplicate row is invisible to every other check here, because
    they all read the collapsed mapping. Counted from the raw matches
    instead."""
    from collections import Counter

    counts = Counter(name for name, _ in entries())
    duplicated = sorted(name for name, n in counts.items() if n > 1)
    assert not duplicated, (
        f"listed more than once in .planning/README.md: {duplicated}. Two rows "
        f"for one document show the reader two descriptions and hide each "
        f"other from every other check in this file."
    )


def test_the_index_is_not_empty():
    """Without this, a regex that stopped matching would make every test
    above pass on an empty set -- the inert-guard failure this project
    keeps hitting."""
    assert len(entries()) >= 70, (
        f"only {len(entries())} entries parsed; the ENTRY regex has probably "
        f"stopped matching the README's format"
    )
