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


def test_the_documents_count_in_the_prose_is_the_real_count():
    """The README says "This index is checked, not maintained by hope" and
    then carried a hand-maintained document count two lines above it.

    On PR #159 that count read 77 against a real 81 — stale by four,
    because three separate PRs had added a document and updated the list
    without touching the number. Anything maintained by hope eventually
    is not maintained, including a sentence in the paragraph that says so.
    """
    import re

    prose = (PLANNING / "README.md").read_text(encoding="utf-8")
    match = re.search(r"^(\d+) documents and counting", prose, re.M)
    assert match, "the README no longer states a document count in the expected form"

    claimed = int(match.group(1))
    actual = len(on_disk())
    assert claimed == actual, (
        f"the index prose claims {claimed} documents; there are {actual}. "
        f"Update the number in .planning/README.md."
    )


def test_claude_md_s_document_count_is_the_real_count_too():
    """CLAUDE.md states the same count, and nothing was checking it.

    It read **77** against a real **105** on 2026-09-15 — stale by 28,
    because twenty-eight documents had been added across many PRs and the
    sentence two lines from "verify every figure ... then remove it" was
    never one of the figures anyone verified.

    The README's own count has had this guard since PR #159. Extending it
    here rather than writing a second one, because the failure is
    identical and so is the fix.
    """
    import re

    claude = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    match = re.search(r"an index of all (\d+) documents", claude)
    assert match, (
        "CLAUDE.md no longer states the .planning document count in the "
        "expected form; update this test with it, do not delete the check"
    )
    claimed, actual = int(match.group(1)), len(on_disk())
    assert claimed == actual, (
        f"CLAUDE.md claims {claimed} planning documents; there are {actual}."
    )


def _unspent_claimed_in_claude_md() -> set[str]:
    """The window names CLAUDE.md currently calls unspent."""
    import re

    claude = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    match = re.search(
        r"Unspent and therefore \*not\* available\s*\n?\s*for discovery: (.+?)\.",
        claude,
        re.S,
    )
    assert match, (
        "CLAUDE.md no longer states which windows are unspent in the "
        "expected form; update this test with it, do not delete the check"
    )
    claimed = {n for n in WINDOW if n in match.group(1)}
    assert claimed, (
        f"no known window name found in {match.group(1)!r}; the mapping in "
        f"this test needs the new name adding"
    )
    return claimed


#: CLAUDE.md's English name for a window -> the (symbol substring,
#: interval) a holdout access against it carries in the log.
#:
#: Written out rather than inferred: those are two different vocabularies,
#: and guessing between them is how a guard goes quietly inert.
WINDOW = {
    "Binance spot 1m": ("BINANCE:BTCUSDT", "1m"),
    "Binance spot 1d": ("BINANCE:BTCUSDT", "1d"),
    "KRX daily": ("KRX:", "1d"),
    "BingX 1m": ("BTC-USDT", "1m"),
    "Binance futures 1m": ("BINANCE-FUTURES:BTCUSDT", "1m"),
}


def test_claude_md_s_unspent_windows_are_really_unspent():
    """CLAUDE.md names which windows are still available for a
    confirmation run, and the project knows which have been spent.
    Nothing compared the two.

    On 2026-09-15 the clause listed **KRX daily** as unspent. It had been
    spent on **2026-09-13** by `daily-tsmom-kr10-portfolio`, three
    recorded `holdout_access` entries, and the sentence was written the
    following day in a different PR — so no rule was broken, the claim was
    simply never checked. It is the most load-bearing kind of stale fact
    this file can carry: it tells a future session a fresh window exists
    when it does not.

    **Checked against the committed ledger, not the log**, because
    `runs/experiments.jsonl` is gitignored — the first version of this
    test read it directly and so could only ever fail locally, passing in
    CI for want of a file. `runs/spent_windows.json` is the derived,
    committed artifact; `test_the_spent_window_ledger_matches_the_log`
    keeps it honest wherever the log exists.
    """
    from research.spent_windows import load

    spent = set()
    for row in load():
        for name, (sym_part, interval) in WINDOW.items():
            if row["interval"] == interval and sym_part in row["symbol"]:
                spent.add(name)

    wrongly_claimed = sorted(_unspent_claimed_in_claude_md() & spent)
    assert not wrongly_claimed, (
        f"CLAUDE.md calls {wrongly_claimed} unspent, but "
        f"runs/spent_windows.json records a holdout access against each. "
        f"A spent window is not available for confirmation."
    )


def test_the_spent_window_ledger_matches_the_log():
    """The ledger is derived, so it can drift from what it summarises.

    Skipped where the log is absent — CI, a fresh clone — and that is
    deliberate rather than a hole: the check above runs there instead,
    against the ledger. Each tier is non-inert in the environment it runs
    in, which is the property the first version of this pair lacked.
    """
    import pytest

    from research.spent_windows import build, load

    # Anchored at the repo root, not `DEFAULT_RUNS_PATH`, which is relative
    # and so resolves against whatever directory pytest started in —
    # `python/runs/experiments.jsonl` from here. That made this tier skip
    # **locally too**, leaving the pair with no environment where it ran:
    # the same inert-guard shape it was written to remove, one level up.
    log = REPO_ROOT / "runs" / "experiments.jsonl"
    if not log.exists():
        pytest.skip(f"{log} is gitignored and absent here; the ledger check covers it")

    fresh = {(w["symbol"], w["interval"]) for w in build(log)["windows"]}
    committed = {(w["symbol"], w["interval"]) for w in load()}
    assert fresh == committed, (
        f"runs/spent_windows.json is stale. Only in the log: "
        f"{sorted(fresh - committed)}; only in the ledger: "
        f"{sorted(committed - fresh)}. Regenerate with "
        f"`python -m research.spent_windows --write`."
    )


def test_the_ledger_is_not_empty_so_the_claim_check_cannot_pass_vacuously():
    """A broken generator would empty the ledger and make every window
    look available — the inert-guard failure, one layer down."""
    from research.spent_windows import load

    assert len(load()) >= 5
