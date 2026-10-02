#!/usr/bin/env python3
"""Build the file an operator pastes into an external reviewer's conversation.

Two documents are the record for each phase -- a prompt and, for phase 1, the
fact package -- and the reviewer needs them as one paste. Doing that by hand
loses the boundary that matters: each prompt opens with notes addressed to US,
above a `---` rule, naming the scoring document and the phase sequence. Pasting
those tells the reviewer how its answer will be judged, which is exactly what a
blind review must not know. So the split is mechanical here rather than
remembered.

Output goes to the **repository root**, not `.planning/`, for two reasons: it is
where an operator can find it among 123 planning documents, and `.planning/` is
counted by `test_planning_index.py`, which a generated file would drift against.
It is gitignored -- regenerate rather than commit.

Usage:
    scripts/build-external-review-paste.py 1
    scripts/build-external-review-paste.py 2
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLANNING = ROOT / ".planning"

HEADER = """<!-- GENERATED -- do not edit. Built by
     scripts/build-external-review-paste.py from the documents named below;
     edits here are lost on the next build and silently diverge from the
     record. -->

"""

RULE = "=" * 78

#: Each phase: its prompt document, and the documents appended after it.
PHASES: dict[str, tuple[str, tuple[str, ...]]] = {
    "1": ("xr-b-phase1-prompt.md", ("xr-a-external-review-fact-package.md",)),
    "2": ("xr-d-phase2-prompt.md", ()),
}


def prompt_body(path: pathlib.Path) -> str:
    """The part of a prompt document addressed to the reviewer.

    Everything above the first standalone `---` rule is addressed to us and
    must not travel.
    """
    text = path.read_text(encoding="utf-8")
    marker = "\n---\n"
    if marker not in text:
        raise SystemExit(f"{path.name} has no '---' rule separating our notes from the prompt")
    return text.split(marker, 1)[1].strip()


def build(phase: str) -> tuple[pathlib.Path, str]:
    if phase not in PHASES:
        raise SystemExit(f"unknown phase {phase!r}; known: {', '.join(sorted(PHASES))}")
    prompt_name, attachments = PHASES[phase]
    parts = [HEADER, prompt_body(PLANNING / prompt_name)]
    for name in attachments:
        parts.append(
            f"\n\n{RULE}\n\nThe reference document referred to above follows in full.\n\n{RULE}\n\n"
            + (PLANNING / name).read_text(encoding="utf-8").strip()
        )
    return ROOT / f"PASTE-THIS-phase{phase}.md", "".join(parts) + "\n"


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "1"
    out, text = build(phase)
    out.write_text(text, encoding="utf-8")
    print(f"{out.name}: {len(text.splitlines())} lines, {len(text):,} chars, ~{len(text)//4:,} tokens")
