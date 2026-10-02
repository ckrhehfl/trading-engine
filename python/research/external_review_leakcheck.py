"""Does the external-review fact package leak this project's own conclusions?

`.planning/xr-a-external-review-fact-package.md` is handed to an outside
reviewer who is asked to design a strategy-search system **without** being told
what this project tried, what happened, or what it believes went wrong. The
reasoning is CLAUDE.md's own: *a verification that shares an assumption with its
implementation confirms the misunderstanding rather than catching it.* A
reviewer shown the diagnosis can only agree with it, and agreement under those
conditions is not evidence.

So the document must carry **inputs** (measurements, rules, resources) and
withhold **inferences** (attempts, outcomes, diagnoses). This module checks
that mechanically, because the boundary is easy to cross by accident — and was
crossed on the first version.

**What the first version of this check missed, which is why MEASURED_OUTCOME
exists.** It scanned for strategy identities and verdict vocabulary, and passed
a §5 that contained *"this moved one p-value from 0.016 to 0.182"* and *"took
the count surviving Benjamini-Hochberg from 1 to 0"*. Both are past results.
Neither names a strategy or prints a verdict word. CodeRabbit caught them on
PR #215 — after the reviewer had already read them, so Phase 1 ran with a
partial leak, recorded in `xr-e`.

The lesson generalises past this file: **a result does not need a name attached
to be a result.** What gives it away is the shape — a measured quantity with a
concrete value, or a transition between two values.

Over-reports by design. A hit is a line to read, not a verdict: the document
legitimately defines verdict *categories* (`INCONCLUSIVE-DATA-LIMITED` is a
rule, not an outcome) and lists available tooling (Stouffer is a method, not a
finding). Read each hit; keep the check strict.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FACT_PACKAGE = REPO_ROOT / ".planning" / "xr-a-external-review-fact-package.md"

#: Strategy and work-arc identities. Naming one reveals what was attempted.
IDENTITIES = (
    "daily-tsmom", "tsmom", "ensemble-momentum", "configuration c", "ma-crossover",
    "funding-extremity", "ofi-momentum", "regime-momentum", "single-lookback",
    "hourly_momentum", "kr-10", "kr10", "moskowitz", "zarattini", "grinold",
    "larry williams", "barber", "alexander & fabozzi",
)

#: Work-arc document prefixes, which index the record of what was run.
ARC_PREFIXES = (
    "ms-e", "ms-f", "sr-v", "sr-ab", "sr-ac", "sr-t", "sr-r", "sr-j", "sr-q",
    "rd-a", "rd-c", "rd-k", "rd-n", "rd-q", "rd-r", "rd-t", "rd-u", "rd-w", "rd-y",
    "scalp-", "tm-a", "tm-b", "tm-c", "tm-d", "tm-e", "s8 ", "s11", "s13", "s14",
    "s15", "s16",
)

#: Verdict vocabulary applied to a *run*. The document may define these as
#: categories; it may not report one as having happened.
VERDICTS = (
    "0 of 18", "0 of 12", "0 of 13", "zero candidates", "no candidate produced",
    "did not clear", "failed to clear", "cleared gate a", "cost-disqualified",
    "rejected-underpowered", "spent twice",
)

#: This project's own diagnosis -- what the reviewer must derive independently.
DIAGNOSIS = (
    "the real finding", "the finding is", "most important finding",
    "management axis", "selection filter", "the filter is the strategy",
    "every strategy_id", "asks which formula", "situations rather than formulas",
    "reserved for confirmation", "best-powered", "cannot clear",
    "no realistic edge", "0.4-0.8", "0.4–0.8", "institutional trend-following",
    "indistinguishable from luck", "adverse excursion", "is the edge",
    "diversification reduces the variance",
)

#: Arithmetic deliberately withheld so we can see whether the reviewer performs
#: it. Deriving the required Sharpe, or the reserved window's floor, is the test.
#:
#: **The numeric entries carry digit boundaries**, because a bare "4.00" matches
#: inside "14.00" and "0.315" inside "10.315" -- the same substring trap that
#: `figure_survival.py` exists to avoid, where "4374" once "survived" inside a
#: row id. Flagged on review of PR #215.
WITHHELD_ARITHMETIC_LITERAL = ("required sharpe", "would have needed")
WITHHELD_ARITHMETIC_NUMERIC = (
    r"(?<![\d.])0\.315(?![\d])",
    r"(?<![\d.])4\.00(?![\d])",
    r"(?<![\d.])4\.6(?![\d])",
)

#: **A past result, by shape rather than by name.** See the module docstring.
MEASURED_OUTCOME = (
    r"\bp[- ]?values?\s+(?:from|of)\s+0\.\d+",
    r"\bfrom\s+0\.\d+\s+to\s+0\.\d+",
    r"\bfrom\s+\d+\s+to\s+\d+\b",
    r"\bran\s+0?\.\d+\s*[-–]\s*0?\.\d+\s*(?:times|×|x)\b",
    # Verb-independent on purpose: the first draft keyed on "fell ...fold" and
    # missed the real sentence, which was "cut t roughly threefold".
    r"\b(?:two|three|four|five|six|seven|eight|nine|ten|\d+)[- ]?fold\b",
    r"\b(?:t|Z)\s*=\s*[+-]?\d",
    r"\bSharpe\s+of\s+[+-]?\d",
    r"\bmean\s+(?:annualized\s+)?Sharpe\s+[+-]\d",
    r"\bDSR\s*=\s*\d",
    r"\bPSR\s+0\.\d",
    r"\bsurviv\w+\s+Benjamini",
    r"\bΦ\(Z\)",
)

#: Facts the design cannot be executed without. Their absence is the opposite
#: failure from a leak and is just as disqualifying: a reviewer missing these
#: produces generalities, which is what a fully blind package was shown to do.
#: **Needles are phrases, not bare numbers**, because a short one fails OPEN: a
#: document that had lost its trial count entirely would still contain "129"
#: somewhere, and "0.95" appears in any number of unrelated thresholds. The check
#: would then report the fact as present. Tightened on review of PR #215; the
#: synthetic test removes each needle and asserts it is reported missing, so a
#: needle that cannot be removed cleanly fails that test rather than passing
#: quietly.
REQUIRED_FACTS = {
    "detection floor formula": "1.6449 / sqrt",
    "selection trial count": "`N` = 129",
    "trial Sharpe dispersion": "1.2665",
    "DSR threshold": "Deflated Sharpe Ratio ≥ 0.95",
    "trade count formula": "evaluated_days / 20",
    "Korean round trip": "30–33 bp",
    "transaction tax": "거래세",
    "taker fee": "Taker fee 5 bp",
    "KIS reach": "1991-08-28",
    "full universe panel": "4,598,643",
    "short selling constraint": "No retail short selling",
    "holdout single access": "accessed exactly once",
    "walk-forward mandatory": "without **rolling train/validate",
    "window availability": "selected on?",
    "session hours": "09:00–15:20 is the continuous session",
    "calendar fails closed": "trading calendar fails closed",
}


@dataclass(frozen=True)
class Hit:
    bucket: str
    term: str
    line_number: int
    line: str


def _preamble_end(lines: list[str]) -> int:
    """The document's own preamble describes what it withholds, legitimately.

    Scanning it would flag the very sentences that explain the design, so the
    scan starts at the first numbered section.
    """
    for i, line in enumerate(lines):
        if line.startswith("## 1."):
            return i
    return 0


def find_leaks(text: str) -> list[Hit]:
    """Every line that looks like a withheld conclusion, bucket-labelled."""
    lines = text.splitlines()
    start = _preamble_end(lines)
    buckets: tuple[tuple[str, tuple[str, ...], bool], ...] = (
        ("IDENTITY", IDENTITIES, False),
        ("ARC", ARC_PREFIXES, False),
        ("VERDICT", VERDICTS, False),
        ("DIAGNOSIS", DIAGNOSIS, False),
        ("WITHHELD-ARITH", WITHHELD_ARITHMETIC_LITERAL, False),
        ("WITHHELD-ARITH", WITHHELD_ARITHMETIC_NUMERIC, True),
        ("MEASURED-OUTCOME", MEASURED_OUTCOME, True),
    )
    hits: list[Hit] = []
    for name, terms, is_regex in buckets:
        for term in terms:
            pattern = re.compile(term if is_regex else re.escape(term), re.I)
            for i, line in enumerate(lines):
                if i < start:
                    continue
                if pattern.search(line):
                    hits.append(Hit(name, term, i + 1, line.strip()))
    return hits


def missing_required_facts(text: str) -> list[str]:
    """Required facts absent from the document, by label."""
    lowered = text.lower()
    return [label for label, needle in REQUIRED_FACTS.items() if needle.lower() not in lowered]


def main() -> int:
    text = FACT_PACKAGE.read_text(encoding="utf-8")
    hits = find_leaks(text)
    missing = missing_required_facts(text)

    if hits:
        print(f"누출 후보 {len(hits)}건 -- 각각 손으로 읽을 것:")
        for hit in hits:
            print(f"  [{hit.bucket}] {hit.term!r}")
            print(f"    L{hit.line_number}: {hit.line[:104]}")
    else:
        print("누출 후보 없음")

    print()
    if missing:
        print(f"필수 사실 누락 {len(missing)}건: {', '.join(missing)}")
    else:
        print(f"필수 사실 {len(REQUIRED_FACTS)}건 전부 존재")
    return 1 if (hits or missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
