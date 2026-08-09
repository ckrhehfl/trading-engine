#!/usr/bin/env python3
"""Guardrail B's analysis step -- see vst-guardrail.sh for the full context.

Reads a Claude Code PreToolUse hook payload (Edit/Write) from stdin,
reconstructs the resulting file content this edit would produce (for Edit:
the current on-disk file with old_string replaced by new_string; for
Write: tool_input.content directly), strips comments, and checks whether
BINGX_VST_BASE_URL and getenv appear in the same semicolon-delimited
Java statement anywhere in that resulting content.

Checking the *resulting file*, not just this one edit's own new_string
fragment, is what closes a real, correctly-identified CodeRabbit review
finding on this PR: a single sensitive assignment split across two
separate Edit calls (e.g. one call introduces "System.getenv(" and a
later, separate call appends "BINGX_VST_BASE_URL");") would not contain
both substrings in either call's own new_string alone, so a check scoped
to just the diff fragment would miss it.

Checking same-STATEMENT (split on ';'), not same-line or a line-window,
is deliberate too: a fixed line-window false-positives on this project's
own real, legitimate code -- BINGX_VST_BASE_URL's own real construction
site in PaperTradingApp.java sits only 1-2 lines away from unrelated
System.getenv(...) calls for BINGX_API_KEY/BINGX_API_SECRET in the same
forBingXVst() method. Splitting on ';' keeps each real Java statement
separate (so those two unrelated statements never collide) while still
catching a single assignment/expression that was split across multiple
Edit calls before any ';' existed between the two halves.

Comment-stripping (block /* ... */ including Javadoc, and line //...) is
necessary for the same reason: this project's own Javadoc prose
legitimately discusses both "getenv" and "BINGX_VST_BASE_URL" together
(e.g. this exact safety design, in PaperTradingApp's own class Javadoc)
with no ';' in between across a comment-to-code boundary, which would
otherwise collide with real code in the naive statement split.

Deliberately regex-based, not a real Java tokenizer -- a best-effort
secondary layer (the primary safety property is that no configuration
surface for the VST host exists at all, see PaperTradingApp's own
Javadoc), not a compiler. A string literal containing "/*" could in
principle confuse the comment stripper; judged an acceptable, disclosed
limitation for a defense-in-depth guardrail, not a correctness-critical
parser.
"""

import json
import re
import sys


def main() -> None:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path")
    new_string = tool_input.get("new_string")
    content = tool_input.get("content")
    old_string = tool_input.get("old_string")

    if content is not None:
        candidate = content
    elif old_string is not None and new_string is not None:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                current = fh.read()
        except OSError:
            current = ""
        if old_string in current:
            candidate = current.replace(old_string, new_string, 1)
        else:
            # old_string not found in the current file (e.g. the file is
            # being created, or old_string targets content that doesn't
            # exist yet) -- fail toward still analyzing something rather
            # than silently skipping the check.
            candidate = current + "\n" + new_string
    else:
        candidate = new_string or ""

    no_block_comments = re.sub(r"/\*.*?\*/", "", candidate, flags=re.DOTALL)
    no_comments = re.sub(r"//[^\n]*", "", no_block_comments)
    candidate = no_comments

    found = any(
        "BINGX_VST_BASE_URL" in statement and "getenv" in statement.lower()
        for statement in candidate.split(";")
    )
    print("MATCH" if found else "NOMATCH")


if __name__ == "__main__":
    main()
