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

**Cross-statement alias/taint tracking** (added to close a real,
disclosed gap tracked as GitHub issue #80 -- originally found on a real
CodeRabbit review of PR #79, round 4, self-labeled by the reviewer as a
"Heavy lift", i.e. not waved away, it was weighed and deliberately
deferred rather than rushed under review pressure). The same-statement
check above cannot see a config-lookup call and `BINGX_VST_BASE_URL`
connected only through intermediate variable/field assignments, e.g.:

```java
private static final String VST_HOST_PROPERTY = "BINGX_VST_BASE_URL";
String configuredHost = System.getProperty(VST_HOST_PROPERTY);
private static final String BINGX_VST_BASE_URL = configuredHost;
```

-- confirmed for real (not just reasoned about) that this candidate did
NOT get blocked by `check_java_guardrail` before this fix.
`_detect_cross_statement_alias_bypass` (with its two helpers,
`_split_assignment` and `_expr_is_tainted`) closes this specific shape
with a real, bounded def-use pass over the same comment-stripped,
`;`-delimited statement sequence Guardrail B already uses -- not a full
Java parser and not general-purpose taint analysis, matching this
file's existing "best-effort secondary layer, not a compiler" framing.
It walks statements in file order and recognizes only one syntactic
shape: `<modifiers/type> NAME = EXPR;` (the first top-level `=` in the
statement -- not `==`/`!=`/`<=`/`>=`/a compound-assignment operator --
splits the statement; `NAME` is the last identifier token before it).
A variable/field becomes "tainted" the moment its own `EXPR` either
directly contains a getenv-equivalent token (the same
`GETENV_EQUIVALENT_TOKENS` list the same-statement check uses) or
references, as a whole identifier, a name already tainted by an
earlier statement in the file -- so taint propagates transitively
through an arbitrary-length chain of simple aliases, not just one hop.
The moment a statement assigns to (or otherwise defines/reassigns)
`BINGX_VST_BASE_URL` from a tainted expression, this is flagged exactly
as the direct same-statement co-occurrence already is. Both the
`System.getProperty` and `System.getenv` alias forms are covered (same
shared token list), per issue #80's own acceptance criteria.

Deliberately bounded, and honest about it -- three concrete gaps this
pass does NOT close, named so they are not silently assumed covered:

1. **Method-call-mediated aliasing**: if the getenv-equivalent call is
   wrapped inside a separate helper method (even in the same file) and
   only that method's *return value* reaches `BINGX_VST_BASE_URL`
   (`private static String loadHost() { return System.getenv(...); }
   ... BINGX_VST_BASE_URL = loadHost();`), this pass does not follow
   the call into the method body -- there is no call-graph/
   interprocedural analysis, only textual taint through `NAME = EXPR`
   assignment chains. Confirmed NOT blocked, pinned by
   `test_known_disclosed_limitation_method_call_mediated_bypass_is_not_currently_blocked`.
2. **Multi-variable declarations** (`String a = X, b = Y;`) and any
   assignment shape other than the single `NAME = EXPR` pattern (e.g. a
   chained `a = b = EXPR` form) are not parsed -- `_split_assignment`
   returns `None` for anything it doesn't confidently recognize, the
   same fail-toward-"can't confirm, don't assert taint" posture as the
   rest of this file's conservative design, rather than guessing.
3. **Cross-file aliasing**: this pass (like the rest of Guardrail B)
   only ever sees the one candidate file's own reconstructed content
   for this one tool call -- a value assigned from a getenv-equivalent
   call in one file and referenced by name (e.g. a shared constant
   imported from another class) in a different file's
   `BINGX_VST_BASE_URL` assignment is invisible to it.

None of these three weaken the primary safety property any further than
the file's existing framing already accounts for (see above: no
configuration surface exists in the real, shipped code at all,
independent of this best-effort layer's coverage) -- they are the same
class of "did not attempt full interprocedural/cross-file analysis"
scoping this file has disclosed from the start, just enumerated
concretely now that the single-file, single-hop gap issue #80 tracked
is closed.
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


def reconstruct_candidate(tool_input: dict):
    """Reconstructs the resulting file content this tool call would
    produce -- see module docstring for why this (not just the raw diff
    fragment) is what both guardrails must check.

    Returns `None` -- not `""` -- for any payload shape this function does
    not confidently recognize (real, correctly-identified CodeRabbit review
    finding on this PR): the only two shapes it understands are Write's
    `content` and Edit's `old_string`+`new_string` pair. Anything else
    (e.g. a hypothetical multi-edit `edits` array, or a `tool_input` with
    none of these keys at all) previously fell through to `new_string or
    ""`, silently returning an empty string that both guardrails then check
    and always pass against -- i.e. the guardrail silently no-opped for a
    payload shape it could not actually analyze, rather than failing
    closed. `main()` treats `None` as a hard block, matching this
    project's own established fail-closed convention elsewhere (e.g.
    `engine.runtime.SubmissionMarkerStore.load()`).
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
    return None


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


GETENV_EQUIVALENT_TOKENS = ("getenv", "getproperty", "getproperties")

TARGET_CONSTANT_NAME = "BINGX_VST_BASE_URL"

# Matches a single top-level `=` (a plain assignment/declaration
# operator), NOT `==`/`!=`/`<=`/`>=`/a compound-assignment operator
# (`+=`, `-=`, `*=`, `/=`, `%=`, `&=`, `|=`, `^=`) -- see
# `_split_assignment`'s own docstring for why only the first such match
# per statement is used as the split point.
ASSIGNMENT_OPERATOR_RE = re.compile(r"(?<![=!<>+\-*/%&|^])=(?!=)")

# Matches the trailing identifier token in a left-hand side like
# "private static final String BINGX_VST_BASE_URL" -> "BINGX_VST_BASE_URL",
# or "this.field" -> "field" (`.` is not a `\w` character).
TRAILING_IDENTIFIER_RE = re.compile(r"(\w+)\s*$")


def _split_assignment(statement: str):
    """Splits a single semicolon-delimited STATEMENT into `(name, expr)` if
    it looks like a simple `<modifiers/type> NAME = EXPR` declaration or
    plain reassignment -- returns `None` if the statement contains no
    top-level `=` operator at all (e.g. a bare method call, a `return`
    statement, a class/method declaration header, a `for(...)` clause
    fragment after splitting on `;`). `NAME` is the last identifier token
    immediately before the first top-level `=` (so modifiers/types such as
    `private static final String` are discarded, keeping just the
    variable/field name being declared or reassigned); `EXPR` is
    everything after it, unparsed.

    Only the FIRST top-level `=` is used as the split point (a Java
    declaration/assignment has exactly one) -- e.g. for
    `String x = "a=b";`, the first match is the real assignment operator
    after `x`, not the `=` inside the string literal, so `EXPR` correctly
    captures the full `"a=b"` right-hand side. See module docstring,
    "Cross-statement alias/taint tracking," for the honest, bounded scope
    this narrow shape does NOT attempt to cover (multi-variable
    declarations, chained assignments, method-call return values).
    """
    match = ASSIGNMENT_OPERATOR_RE.search(statement)
    if not match:
        return None
    lhs = statement[: match.start()]
    expr = statement[match.end():]
    name_match = TRAILING_IDENTIFIER_RE.search(lhs)
    if not name_match:
        return None
    return name_match.group(1), expr


def _expr_is_tainted(expr: str, tainted_vars: set) -> bool:
    """True if EXPR either directly contains a getenv-equivalent call
    (same `GETENV_EQUIVALENT_TOKENS` list as the same-statement check), or
    references -- as a whole identifier, not a substring -- a variable
    name already known to be tainted from an earlier statement in file
    order (TAINTED_VARS)."""
    if any(token in expr.lower() for token in GETENV_EQUIVALENT_TOKENS):
        return True
    if not tainted_vars:
        return False
    alias_re = re.compile(r"\b(?:" + "|".join(re.escape(name) for name in tainted_vars) + r")\b")
    return bool(alias_re.search(expr))


def _detect_cross_statement_alias_bypass(statements) -> bool:
    """Real, bounded cross-statement alias/taint tracking closing the
    GitHub issue #80 gap -- see module docstring, "Cross-statement
    alias/taint tracking," for the full design and its honest limits.

    Walks STATEMENTS (already comment-stripped and split on `;`, same
    sequence the same-statement check above uses) in file order, tracking
    which simple variable/field names are "tainted" -- their value derives,
    directly or through a chain of plain `NAME = EXPR` assignments, from a
    getenv-equivalent call earlier in the same file. Returns True the
    moment a statement assigns to (or otherwise defines/reassigns)
    BINGX_VST_BASE_URL using a tainted expression.
    """
    tainted_vars = set()
    for statement in statements:
        parsed = _split_assignment(statement)
        if parsed is None:
            continue
        name, expr = parsed
        if _expr_is_tainted(expr, tainted_vars):
            if name == TARGET_CONSTANT_NAME:
                return True
            tainted_vars.add(name)
    return False


def check_java_guardrail(file_path: str, candidate: str) -> bool:
    """Guardrail B: True if this is a .java file whose resulting content (after comment-stripping)
    reads BINGX_VST_BASE_URL from a getenv-equivalent accessor, either directly (both appear in the
    same semicolon-delimited statement) or via cross-statement variable/field aliasing (the value
    flows from a getenv-equivalent call into BINGX_VST_BASE_URL through one or more intermediate
    `NAME = EXPR` assignments -- see `_detect_cross_statement_alias_bypass`).

    Checks `System.getenv(...)`, `System.getProperty(...)` (singular), and
    `System.getProperties()` (plural, Map-style -- `.get("KEY")` reads a
    single property out of the full snapshot it returns; a real,
    independently-verified gap, found and fixed alongside a separate,
    inaccurate claim about this same file that did not hold up under
    direct testing -- see `.planning/paper-trading-h-vst-integration.md`'s
    "round 5" section for the full account of both). "getproperty" is
    deliberately NOT a substring of "getproperties" (they diverge at the
    11th character, `y` vs `i`), so the plural form needed its own token,
    not just a broader existing one. The pre-fix check only looked for the
    literal substring "getenv", so `System.getProperty(...)` -- a real,
    different JVM configuration surface, settable via a `-D` flag, that
    CLAUDE.md's own "no environment variable, argument, or OTHER
    CONFIGURATION SURFACE" wording already covers -- was not textually
    caught at all either, until an earlier round's fix. The chained
    `System.getenv().get("BINGX_VST_BASE_URL")` form was already covered
    before that fix (both substrings already co-occur in that one
    statement) -- verified by its own regression test, not assumed.

    The cross-statement alias pass (added to close GitHub issue #80) runs
    as a second, additive layer whenever the direct same-statement check
    doesn't already find a match -- it never narrows or replaces the
    direct check, only extends coverage to the aliasing shape the direct
    check structurally cannot see.
    """
    if not JAVA_PATH_RE.search(normalize_path(file_path)):
        return False
    stripped = strip_java_comments(candidate)
    statements = stripped.split(";")
    direct_hit = any(
        TARGET_CONSTANT_NAME in statement
        and any(token in statement.lower() for token in GETENV_EQUIVALENT_TOKENS)
        for statement in statements
    )
    if direct_hit:
        return True
    return _detect_cross_statement_alias_bypass(statements)


def main() -> None:
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path") or ""
    candidate = reconstruct_candidate(tool_input)

    if candidate is None:
        # Fail closed (real, correctly-identified CodeRabbit review finding
        # on this PR): a payload shape reconstruct_candidate cannot
        # confidently analyze must never be silently treated as OK -- see
        # that function's own docstring. vst-guardrail.sh's own catch-all
        # case already fails closed on any decision string other than OK/
        # BLOCK_WORKFLOW/BLOCK_JAVA, so this needs no shell-side change.
        print("BLOCK_UNRECOGNIZED_PAYLOAD")
        return
    if check_workflow_guardrail(file_path, candidate):
        print("BLOCK_WORKFLOW")
        return
    if check_java_guardrail(file_path, candidate):
        print("BLOCK_JAVA")
        return
    print("OK")


if __name__ == "__main__":
    main()
