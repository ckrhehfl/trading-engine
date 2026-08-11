# GitHub issue #80: cross-statement alias/taint tracking for the VST-host guardrail

## Scope note

Fixes GitHub issue #80: `.claude/hooks/vst_guardrail_check.py`'s
`check_java_guardrail` (Guardrail B) checked a getenv-equivalent call
(`System.getenv`/`getProperty`/`getProperties`) co-occurring with the
literal `BINGX_VST_BASE_URL` only within a single semicolon-delimited
statement. This missed variable/field aliasing across statements — a
real gap CodeRabbit found during PR #79's round-4 review, deliberately
deferred at the time (self-labeled "Heavy lift" by the reviewer) rather
than rushed under review pressure, and tracked as issue #80 with a
regression test pinning the then-accepted bypassed behavior.

This task closes that gap with real, bounded cross-statement alias/taint
tracking — not a full Java parser, not general-purpose taint analysis,
matching the file's own established "best-effort secondary layer, not a
compiler" framing. Scoped strictly to `.claude/hooks/vst_guardrail_check.py`
and `.claude/hooks/test_vst_guardrail_check.py`. `vst-guardrail.sh` was
checked and needed no change — the Python interface it calls
(`check_java_guardrail(file_path, candidate) -> bool`) is unchanged in
shape, only its internal implementation grew a second, additive analysis
pass. No Java runtime code (`PaperTradingApp.java` or otherwise) was
touched, per the task's explicit out-of-scope list.

## The gap, concretely

```java
private static final String VST_HOST_PROPERTY = "BINGX_VST_BASE_URL";
String configuredHost = System.getProperty(VST_HOST_PROPERTY);
private static final String BINGX_VST_BASE_URL = configuredHost;
```

No single statement contains both a getenv-equivalent token and the
literal `BINGX_VST_BASE_URL` substring, so the old same-statement
substring check passed this as `OK` — confirmed directly (not just
reasoned about) before writing any fix, by running the exact example
through `check_java_guardrail`.

## Design

### Why not a full parser

The task brief was explicit that a bounded, conservative approach is
correct here, and the file's own history backs that up: an earlier
rushed fix (a naive `//`-comment-stripping regex) introduced its own new
bypass (stripped a real `getenv(...)` call sitting after a `"https://"`
string literal), caught by CodeRabbit and fixed with a real minimal
lexer instead. The lesson generalizes — a fast, under-designed fix is
exactly the failure mode to avoid, not just for comment-stripping but
for this alias-tracking work too. So the design deliberately stays
inside the file's existing statement model (comment-stripped text split
on `;`) rather than reaching for a Java grammar/AST library.

### The algorithm

Two new small functions plus one orchestrator, all operating on the same
comment-stripped, `;`-split statement sequence `check_java_guardrail`
already builds for the same-statement check:

1. **`_split_assignment(statement)`**, via
   **`_find_top_level_assignment_index(statement)`** — recognizes exactly
   one syntactic shape: `<modifiers/type> NAME = EXPR`. Finds the first
   `=` that is (a) a plain assignment operator, not `==`/`!=`/`<=`/`>=`
   or a compound-assignment operator (`+=`, `-=`, `*=`, `/=`, `%=`, `&=`,
   `|=`, `^=`), AND (b) sits at **bracket depth 0** — not inside
   `(...)`/`[...]`/`{...}` — AND (c) outside a string/char literal, via a
   real minimal state-machine scan (three states: code, string, char,
   reusing the same escape-aware literal handling `strip_java_comments`
   already established) rather than a regex. Everything before that `=`
   is the left-hand side; the last identifier token in it (via
   `TRAILING_IDENTIFIER_RE`, `(\w+)\s*$`) is `NAME` — this discards
   modifiers/types (`private static final String`) and handles
   `this.field`-style left-hand sides too (`.` is not `\w`, so the regex
   still lands on `field`). Everything after the `=` is `EXPR`,
   unparsed. Returns `None` for any statement with no such `=` at all (a
   bare method call, a `return` statement, a class/method header, a
   `for(...)` header fragment after the `;`-split, or a statement whose
   only `=` sits inside an annotation's own parentheses — see below) —
   the same fail-toward-"can't-confirm-so-don't-assert-taint" posture the
   rest of this file already uses.

   The bracket-depth tracking is not the original design — see
   "CodeRabbit review, round 1" below for why a first, regex-only version
   (matching the first `=` character anywhere in the statement text, with
   no notion of parenthesis nesting) shipped initially and was corrected
   before merge. Depth tracking also subsumes the `String x = "a=b";`
   case the original design called out: the scan reaches the real
   assignment `=` after `x` before it ever enters the string, both
   because it's textually first *and* because entering `STRING` state via
   the `"` correctly stops the scan from ever treating an `=` inside the
   literal as a candidate.

2. **`_expr_is_tainted(expr, tainted_vars)`** — `EXPR` is tainted if it
   directly contains a getenv-equivalent token (same
   `GETENV_EQUIVALENT_TOKENS` list the same-statement check already
   uses, so `System.getProperty`/`System.getenv`/`System.getProperties`
   are all covered identically here — this is what makes both alias
   forms issue #80 asks for work through one shared code path rather
   than two parallel ones), OR if it references, as a whole identifier
   (not a substring), a name already in `tainted_vars`. Implemented by
   extracting every identifier-shaped token out of `EXPR` once via a
   single fixed, pre-compiled `IDENTIFIER_TOKEN_RE` (`\w+`) and checking
   set intersection against `tainted_vars` — not, as the original version
   did, by building and compiling a fresh alternation regex
   (`\b(?:name1|name2|...)\b`) from `tainted_vars` on every call. See
   "CodeRabbit review, round 1" below for why that changed before merge.

3. **`_detect_cross_statement_alias_bypass(statements)`** — walks
   statements in file order maintaining a `set()` of tainted variable
   names. For each statement that parses as an assignment: if `EXPR` is
   tainted and `NAME == "BINGX_VST_BASE_URL"`, return `True` immediately
   (block). If `EXPR` is tainted and `NAME` is anything else, add `NAME`
   to `tainted_vars` and keep going. This is what makes taint propagate
   *transitively* through an arbitrary-length alias chain (`a = getenv();
   b = a; c = b; BINGX_VST_BASE_URL = c;`) rather than just one hop —
   each intermediate variable that receives tainted data itself becomes
   a taint source for anything assigned from it later in the file.

`check_java_guardrail` runs the pre-existing same-statement substring
check first (unchanged, byte-for-byte the same logic) and only falls
through to `_detect_cross_statement_alias_bypass` if that check finds
nothing — additive, never narrowing. This was a deliberate choice to
minimize regression risk on the file's substantial existing test
coverage: every currently-passing direct/same-statement/split-edit test
continues to pass through the exact code path it always has.

### Judgment calls

- **Word-boundary alias matching, not substring matching.** A tainted
  variable named `host` must not falsely match an unrelated `hostName`
  reference. `re.escape` + `\b...\b` handles this; case-sensitive
  (Java identifiers are case-sensitive; only the getenv-equivalent
  token check is deliberately case-insensitive, unchanged from the
  original same-statement check).
- **Transitive propagation was not explicitly required by issue #80's
  two-hop example, but was cheap to support correctly** (the
  `tainted_vars.add(name)` step is unconditional whenever `EXPR` is
  tainted, regardless of hop count) and is directly testable, so it's
  included and pinned by
  `test_cross_statement_alias_bypass_via_transitive_chain_is_blocked`
  rather than left as an accidental side effect.
- **What's still not caught, named explicitly rather than silently
  scoped out** — matching exactly how issue #80's own predecessor
  limitation was disclosed (docstring section + a pinned regression
  test, not just a docstring mention):
  1. **Method-call-mediated aliasing.** If the getenv-equivalent call is
     hidden inside a separate helper method's body and only that
     method's *return value* reaches `BINGX_VST_BASE_URL`
     (`private static String loadHost() { return System.getenv(...); }
     ... BINGX_VST_BASE_URL = loadHost();`), the textual `NAME = EXPR`
     chain never sees the `getenv` call — `loadHost()` is just an opaque
     call expression to this analysis. There is no call-graph or
     interprocedural analysis here, deliberately (the task brief named
     this exact shape as an acceptable gap to disclose rather than
     solve). Confirmed NOT blocked; pinned by
     `test_known_disclosed_limitation_method_call_mediated_bypass_is_not_currently_blocked`.
  2. **Multi-variable declarations** (`String a = X, b = Y;`) and any
     assignment shape other than plain `NAME = EXPR` (e.g. a chained
     `a = b = EXPR`) are not parsed — `_split_assignment` returns `None`
     rather than guessing at which comma-separated name(s) `EXPR`
     belongs to.
  3. **Cross-file aliasing.** This pass only ever sees the one candidate
     file being edited in the current tool call — a value sourced from
     `getenv` in one file, referenced by name from a different file's
     `BINGX_VST_BASE_URL` assignment, is invisible to it. This is an
     inherent property of the hook's own per-tool-call, per-file
     reconstruction design (`reconstruct_candidate` only ever looks at
     one file), not something this task's scope could realistically
     close.

  None of these three weaken the primary safety property beyond what
  the file already discloses: the real, shipped `PaperTradingApp.java`
  hardcodes the VST host as a Java constant with no environment-variable
  or argument override surface at all — this hook is defense-in-depth
  on top of that structural guarantee, not the guarantee itself.

## Required test cases — results

1. **Issue #80's exact bypass example is now detected/blocked** —
   `test_issue_80_exact_bypass_example_via_getproperty_alias_is_now_blocked`
   (getProperty alias form). PASS. Also verified end-to-end through the
   real `main()` entry point (a full PreToolUse-shaped JSON payload
   piped into `vst_guardrail_check.py`), not just the unit-level
   function call — printed `BLOCK_JAVA` as expected.
2. **Both `System.getProperty` and `System.getenv` alias forms
   covered** — the issue #80 example is tested once per accessor
   (`test_issue_80_exact_bypass_example_via_getproperty_alias_is_now_blocked`,
   `test_cross_statement_alias_bypass_via_getenv_is_also_blocked`). Both
   PASS, via the same shared `GETENV_EQUIVALENT_TOKENS` code path (not
   two parallel implementations that could drift).
3. **All pre-existing tests still pass unchanged** — every test present
   before this task (direct co-occurrence, same-statement detection,
   split-edit-across-two-tool-calls, comment/string-literal handling,
   the workflow guardrail, path normalization,
   `reconstruct_candidate`'s fail-closed behavior) continues to pass
   with zero modification to their own bodies. The only test body
   change was replacing the now-superseded pinned-limitation test with
   its "now blocked" counterpart (required by the task itself — the old
   test's own docstring said exactly this: "if this assertion now fails
   ... update this test ... not silently left stale").
4. **False-positive guard: an unrelated getenv-sourced variable that
   never flows into the constant is NOT blocked** —
   `test_unrelated_getenv_assignment_that_never_flows_into_the_constant_is_not_blocked`.
   PASS. Also verified end-to-end via `main()` with the pre-existing
   `test_does_not_block_unrelated_statements_even_when_close_together`
   shape (mirrors the real, legitimate `PaperTradingApp.forBingXVst`
   pattern where an unrelated `getenv` call for `BINGX_API_KEY` sits
   right next to real `BINGX_VST_BASE_URL` usage) — confirmed `OK`.

Two additional tests beyond the four required, both proving real
properties of the new pass rather than padding coverage:
`test_cross_statement_alias_bypass_via_transitive_chain_is_blocked`
(multi-hop propagation, not just issue #80's own two-hop shape) and
`test_known_disclosed_limitation_method_call_mediated_bypass_is_not_currently_blocked`
(pinning the new, honestly-disclosed remaining gap, matching how the
issue #80 gap itself was originally pinned before being closed here).

## CodeRabbit review, round 1

CodeRabbit's first review of PR #82 (targeting commit `a6cf5ae`, the
initial push) found three issues, all addressed in a follow-up commit
before requesting re-review, per this project's own "batch fixes into
one push" convention:

1. **🔴 Critical — method-call-mediated bypass not blocked.** Re-flagged
   the exact `loadHost()` shape this design document and the module
   docstring already named and disclosed as an intentional, bounded scope
   limit (the task's own brief explicitly named this shape as acceptable
   to disclose rather than solve — "a full Java parser or
   general-purpose taint analysis" was explicitly out of scope). **Not
   fixed in this PR** — replying on the review thread explaining the
   deliberate scope decision, and opening GitHub issue #83 to track it
   for a future task, matching this project's own established "acknowledge
   a real, disclosed gap via a tracked issue rather than a rushed fix
   under review pressure" precedent (the same precedent issue #80 itself
   cites: #58, #64, #70, #74, #75). Implementing real interprocedural/
   call-graph taint analysis in response to review-cycle pressure would
   repeat exactly the mistake this file's own history already warns
   against.
2. **🔴 Critical — annotation named-element assignment hides the real
   bypass.** Real, confirmed-by-direct-testing finding: the original
   `_split_assignment` matched the first `=` character anywhere in the
   statement text with no notion of parenthesis nesting, so
   `@SuppressWarnings(value = "unused")` immediately preceding a real
   `BINGX_VST_BASE_URL = host;` in the same statement hid the real
   assignment behind the annotation's own `value = ...` syntax. **Fixed**:
   `_find_top_level_assignment_index` replaces the regex with a real
   minimal state-machine scan tracking bracket depth (`([{`/`)]}`) and
   string/char literal state, matching only a depth-0, literal-outside
   `=`. Pinned by
   `test_annotation_named_element_assignment_does_not_hide_the_real_alias_bypass`
   (written first, confirmed failing against the pre-fix code, then made
   to pass).
3. **🟠 Major — dynamic alias regex rebuilt/recompiled on every call.**
   Real finding, and separately flagged by an automated pattern-matching
   tool as a generic "regex built from non-literal data" ReDoS smell (not
   an actually exploitable ReDoS here — every alternative was
   `re.escape`d and Java identifiers can't contain regex metacharacters —
   but the underlying performance concern, an ever-growing regex
   recompiled per statement for a long alias chain, was real). **Fixed**:
   `_expr_is_tainted` now extracts identifier tokens from `EXPR` once via
   a single fixed, pre-compiled `IDENTIFIER_TOKEN_RE` and checks set
   intersection against `tainted_vars`, instead of building a fresh
   alternation regex from `tainted_vars` every call. Behaviorally
   equivalent to the prior approach (same whole-identifier semantics, same
   lack of string/char-literal awareness within `EXPR` — unchanged in
   either direction).

## Verification

Full existing Python guardrail test suite, run via the project's own
documented invocation
(`python3 .claude/hooks/test_vst_guardrail_check.py`, run from the repo
root — not `python3 -m unittest ...`, which this file's own header
comment already warns raises `ValueError: Empty module name` for a path
shaped like this one):

```
Ran 34 tests in 0.034s

OK
```

34 = the pre-existing 29 (unchanged in body) + 1 replaced
(now-blocked-instead-of-pinned-limitation) + 5 new (getenv alias,
transitive chain, false-positive guard, new pinned limitation,
annotation-bypass regression from CodeRabbit round 1) — net +5 tests, 0
regressions.

`vst-guardrail.sh` was inspected and confirmed to need no change: it
only pipes the hook payload into `vst_guardrail_check.py` and maps its
printed decision (`OK`/`BLOCK_WORKFLOW`/`BLOCK_JAVA`/
`BLOCK_UNRECOGNIZED_PAYLOAD`) to an exit code — that contract is
unchanged, since `check_java_guardrail`'s signature and possible return
values (`bool`) are unchanged; only its internal implementation gained
a second, additive analysis layer.

## What this does NOT claim

Per the task's own instruction to stay honest about scope rather than
overclaim: this closes the exact shape issue #80 named (simple
variable/field aliasing across statements, single-file, direct or
transitive) and no more. It is still, as the module docstring has said
from the start, a best-effort secondary layer defending a configuration
surface that does not exist in the real, shipped code at all — not a
compiler, not a substitute for that structural guarantee.
