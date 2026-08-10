#!/usr/bin/env python3
"""Guardrail analysis step for `.claude/hooks/vst-guardrail.sh` -- see that
file for the full PreToolUse context. Reads a Claude Code PreToolUse hook
payload (Edit/Write) from stdin and prints exactly one of:

  OK               -- neither guardrail's forbidden pattern was found.
  BLOCK_WORKFLOW    -- Guardrail A: a CI workflow file's *resulting*
                       content would reference BINGX_API_KEY/
                       BINGX_API_SECRET/the bingx-vst execution mode.
  BLOCK_JAVA       -- Guardrail B: a .java file's *resulting* content
                       would read BINGX_VST_BASE_URL from an environment
                       variable in the same statement.

Both guardrails are evaluated against the **reconstructed resulting file
content** (for Edit: the current on-disk file with `old_string` replaced
by `new_string`; for Write: `tool_input.content` directly) -- not just
this one tool call's own diff fragment. This closes a real,
correctly-identified CodeRabbit review finding on this PR: a single
forbidden pattern split across two separate Edit calls (e.g. one call
introduces "BINGX_API_KEY" as a YAML key with no value yet, a later,
separate call appends the value) would not appear complete in either
call's own fragment alone, so a check scoped to just the diff fragment
would miss it. Originally only Guardrail B had this protection; a
second, real CodeRabbit review finding on this same PR caught that
Guardrail A had not been given the identical treatment -- both now share
one candidate-reconstruction path so this class of gap cannot recur for
a hypothetical future guardrail C added the same way.

Guardrail B's own comment/string handling is a real, minimal Java lexer
(`strip_java_comments`, below), not a regex -- a **second**, separately
real CodeRabbit review finding on this PR: a naive `//[^\n]*` regex
strips everything after `//` even when it appears *inside* a string
literal (e.g. `"https://" + System.getenv(...)`), which would silently
erase the very call the guardrail exists to detect, turning the
"comment stripping" step itself into a bypass. `strip_java_comments`
tracks whether it is inside a string/char literal (respecting `\"`/`\'`
escapes) before ever treating `//`/`/*` as comment-introducing.

Guardrail B's own same-STATEMENT check (split on `;`), not same-line or
a line-window, is deliberate: a fixed line-window false-positives on
this project's own real, legitimate code -- `BINGX_VST_BASE_URL`'s own
real construction site in `PaperTradingApp.java` sits only 1-2 lines
away from unrelated `System.getenv(...)` calls for
`BINGX_API_KEY`/`BINGX_API_SECRET` in the same `forBingXVst()` method.
Splitting on `;` keeps each real Java statement separate (so those two
unrelated statements never collide) while still catching a single
assignment/expression that was split across multiple Edit calls before
any `;` existed between the two halves.

Deliberately a real lexer for comments/strings specifically (the one
part where a naive regex was proven, concretely, to create a bypass),
but still not a full Java parser overall -- e.g. it has no notion of
Java syntax beyond comments/string/char literals and `;` as a statement
delimiter. A best-effort secondary layer (the primary safety property is
that no configuration surface for the VST host exists at all, see
`PaperTradingApp`'s own Javadoc), not a compiler.
"""

import json
import re
import sys

WORKFLOW_PATH_RE = re.compile(r"\.github/workflows/.*\.ya?ml$", re.IGNORECASE)
FORBIDDEN_WORKFLOW_TOKENS_RE = re.compile(r"BINGX_API_KEY|BINGX_API_SECRET|bingx-vst", re.IGNORECASE)
JAVA_PATH_RE = re.compile(r"\.java$", re.IGNORECASE)


def normalize_path(file_path: str) -> str:
    """Backslash-to-forward-slash normalization -- a real, correctly-
    identified CodeRabbit review finding on this PR (round 1): an
    unnormalized Windows-style path (C:\\repo\\.github\\workflows\\x.yml)
    would silently bypass the forward-slash-only regexes otherwise.
    """
    return (file_path or "").replace("\\", "/")


def reconstruct_candidate(tool_input: dict) -> str:
    """Reconstructs the resulting file content this tool call would
    produce -- see module docstring for why this (not just the raw diff
    fragment) is what both guardrails must check.
    """
    file_path = tool_input.get("file_path")
    new_string = tool_input.get("new_string")
    content = tool_input.get("content")
    old_string = tool_input.get("old_string")

    if content is not None:
        return content
    if old_string is not None and new_string is not None:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                current = fh.read()
        except OSError:
            current = ""
        if old_string in current:
            return current.replace(old_string, new_string, 1)
        # old_string not found in the current file (e.g. the file is being
        # created, or old_string targets content that doesn't exist yet)
        # -- fail toward still analyzing something rather than silently
        # skipping the check.
        return current + "\n" + new_string
    return new_string or ""


def strip_java_comments(text: str) -> str:
    """Removes `//` line comments and `/* */` block comments (including
    Javadoc) from Java source, WITHOUT touching content inside string
    (`"..."`) or char (`'...'`) literals -- see module docstring,
    "Guardrail B's own comment/string handling," for exactly why a plain
    regex is unsafe here (it was tried first and found to strip the very
    `getenv(...)` call a real attack payload needs, via a `//` sequence
    inside a `"https://..."` string literal).

    A real, minimal state-machine lexer over five states (code, line
    comment, block comment, string literal, char literal), including
    escape-sequence handling (`\\"`/`\\'` do not end a literal) -- not a
    full Java parser (no notion of text blocks/other Java 21 literal
    forms), but correct for the concrete bypass this was built to close
    and for every other case this file's own tests below exercise.
    """
    result = []
    i = 0
    n = len(text)
    CODE, LINE_COMMENT, BLOCK_COMMENT, STRING, CHAR = range(5)
    state = CODE
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == CODE:
            if c == "/" and nxt == "/":
                state = LINE_COMMENT
                i += 2
                continue
            if c == "/" and nxt == "*":
                state = BLOCK_COMMENT
                i += 2
                continue
            if c == '"':
                state = STRING
                result.append(c)
                i += 1
                continue
            if c == "'":
                state = CHAR
                result.append(c)
                i += 1
                continue
            result.append(c)
            i += 1
        elif state == LINE_COMMENT:
            if c == "\n":
                state = CODE
                result.append(c)  # keep the newline so statement/line structure downstream is unaffected
            i += 1
        elif state == BLOCK_COMMENT:
            if c == "*" and nxt == "/":
                state = CODE
                i += 2
                continue
            if c == "\n":
                result.append(c)  # preserve newlines crossing a block comment
            i += 1
        elif state == STRING:
            result.append(c)
            if c == "\\" and i + 1 < n:
                result.append(nxt)  # copy the escaped character verbatim, don't let it end the string
                i += 2
                continue
            if c == '"':
                state = CODE
            i += 1
        elif state == CHAR:
            result.append(c)
            if c == "\\" and i + 1 < n:
                result.append(nxt)
                i += 2
                continue
            if c == "'":
                state = CODE
            i += 1
    return "".join(result)


def check_workflow_guardrail(file_path: str, candidate: str) -> bool:
    """Guardrail A: True if this is a CI workflow file whose resulting content references a forbidden token."""
    if not WORKFLOW_PATH_RE.search(normalize_path(file_path)):
        return False
    return bool(FORBIDDEN_WORKFLOW_TOKENS_RE.search(candidate))


def check_java_guardrail(file_path: str, candidate: str) -> bool:
    """Guardrail B: True if this is a .java file whose resulting content (after comment-stripping)
    has BINGX_VST_BASE_URL and getenv co-occurring in the same semicolon-delimited statement."""
    if not JAVA_PATH_RE.search(normalize_path(file_path)):
        return False
    stripped = strip_java_comments(candidate)
    return any(
        "BINGX_VST_BASE_URL" in statement and "getenv" in statement.lower() for statement in stripped.split(";")
    )


def main() -> None:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path") or ""
    candidate = reconstruct_candidate(tool_input)

    if check_workflow_guardrail(file_path, candidate):
        print("BLOCK_WORKFLOW")
        return
    if check_java_guardrail(file_path, candidate):
        print("BLOCK_JAVA")
        return
    print("OK")


if __name__ == "__main__":
    main()
