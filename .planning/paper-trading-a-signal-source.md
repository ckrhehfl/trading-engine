# Paper-trading bridge, Task A: `SignalSource` interface + `FileSignalSource`

## Scope note

This is **Task A** of the 5-task paper-trading bridge plan governing
`daily-tsmom-ensemble`'s human-approved move to paper trading (CLAUDE.md,
"Paper Trading Policy Exception" — see the plan file's "Context"/
"Decisions" sections for the full background). Task A is independent of
Task B (the Python daily signal runner, not built here) and is a
prerequisite for Task C (the Java scheduler/`main()` entrypoint). Nothing
in this task builds a scheduler, a `main()`, or any Python code — see
"Explicitly out of scope" in the governing brief.

R3-risk component (`java/runtime`, adjacent to `TradingLoop`/
`OrderPipeline`) — TDD discipline applied throughout (failing test →
minimum code → refactor), per CLAUDE.md's Development Methodology.

## What was built

Two new files, one small retrofit, one type-only change, all in the
existing `java/runtime` module (package `engine.runtime`), plus one new
test file. No `build.gradle.kts` change was needed — `:runtime` already
depends on `:schemas` (for `OrderIntent`) and already has
`jackson-databind` and `slf4j-api` on its classpath (added in Task B of
Priority #8, see `.planning/08b-trading-loop.md`).

- **`SignalSource`** (new interface) — `Optional<OrderIntent>
  nextSignal()`, extracted verbatim from `DummySignalSource`'s existing
  method signature. `TradingLoop` now depends only on this interface.
- **`DummySignalSource`** (retrofit) — now `implements SignalSource`, no
  behavior change. Stays `final` (see "Judgment calls" below).
- **`TradingLoop`** (type-only change) — the `signalSource` field and the
  matching constructor parameter changed from the concrete
  `DummySignalSource` type to `SignalSource`. Nothing else in the class
  changed; `tick()`'s logic, ordering, and error handling are untouched.
  The class-level Javadoc paragraph naming `DummySignalSource` as *the*
  signal source was updated to describe it as *a* pluggable
  `SignalSource`, naming both `DummySignalSource` and the new
  `FileSignalSource` as implementations — this is a direct consequence of
  the type change, not a drive-by edit.
- **`FileSignalSource`** (new class) — `implements SignalSource`.
  Constructor: `FileSignalSource(java.nio.file.Path signalFilePath)`. One
  method, `nextSignal()`, reading and deserializing the file at that path
  into an `OrderIntent` via `engine.schemas.SchemaObjectMapper` (the same
  shared Jackson configuration `SchemaCompatTest` and `SchemaObjectMapper`
  itself already use — snake_case field names, ISO-8601 timestamps,
  matching the Python/pydantic wire contract in `schemas/`).

## `FileSignalSource.nextSignal()` behavior, exactly as specified

1. File doesn't exist (`NoSuchFileException`) → `Optional.empty()`, no
   log. This is the expected steady state most ticks will see, between
   the Python runner's roughly-once-daily writes — logging it would be
   pure noise on a loop ticking every few minutes.
2. File exists but fails to read (other `IOException`) or fails to parse
   (malformed JSON, or a schema violation — e.g. a missing
   `@JsonProperty(required = true)` field, or `OrderIntent`'s own compact-
   constructor validation such as LIMIT-without-`limitPrice`) →
   `Optional.empty()`, `log.warn(...)` (SLF4J, matching `TradingLoop`'s
   own logging convention), exception never propagates.
3. File parses to a valid `OrderIntent` whose `intentId` matches the
   last-delivered one (tracked internally as a `volatile UUID` field) →
   `Optional.empty()`, no log — also an expected steady state, not an
   error.
4. File parses to a valid `OrderIntent` with a new (or first-ever)
   `intentId` → `Optional.of(intent)`, and that `intentId` is remembered
   as delivered.

`nextSignal()` itself is `synchronized` (guarding the mutable
`lastDeliveredIntentId` field) — cheap insurance matching `TradingLoop`'s
own `synchronized tick()`/`currentEquity()` pattern, even though in
practice only `TradingLoop.tick()` (already itself `synchronized`) calls
this today.

## TDD

Wrote `FileSignalSourceTest` against the not-yet-existing
`FileSignalSource` class first, confirmed the expected compile failure
(`cannot find symbol: class FileSignalSource`, 12 errors from the 6 call
sites), then implemented the class and confirmed all 6 new tests pass,
plus a regression check that `TradingLoopTest`/`DummySignalSourceTest`
still pass unchanged after the interface-extraction refactor:

1. `freshlyWrittenValidSignalIsDeliveredExactlyOnceOnFirstCall`
2. `secondCallAgainstTheSameUnchangedFileReturnsEmpty`
3. `noFilePresentReturnsEmptyWithoutThrowing`
4. `malformedFileReturnsEmptyWithoutThrowing`
5. `aFileMissingARequiredFieldReturnsEmptyWithoutThrowing` (added beyond
   the governing brief's exact list — malformed-JSON and
   missing-required-field are different code paths inside
   `nextSignal()`'s parse `catch`, worth covering separately)
6. `aSequenceOfDistinctIntentsIsEachDeliveredExactlyOnceInOrder`

Test intents are built as real `OrderIntent` records and serialized via
the same `SchemaObjectMapper` the production code uses (not hand-written
JSON strings), so the tests exercise the real wire shape rather than an
assumed one.

Two more tests were added after this initial TDD pass, in direct
response to CodeRabbit review of PR #68 (see "CodeRabbit review
findings" below): `anIntentIdThatReappearsAfterADifferentOneWasDeliveredIsRedeliveredNotSuppressed`
and `aFreshInstanceAfterARestartRedeliversAnIntentIdThePriorInstanceAlreadyDelivered`
— both make an explicit, tested contract out of a limitation that was
previously only implicit in the single-pointer implementation.

Full suite after all changes: **172 tests, 0 failures, 0 errors** across
all six `java/` modules (`schemas`, `oms`, `risk`, `execution`,
`exchange`, `runtime`).

## Judgment calls

- **`final` was not removed from `DummySignalSource`.** The governing
  brief flagged this as something to verify rather than assume. Checked
  directly: a `final` class can implement any number of interfaces in
  Java — `final` only forbids being subclassed, it says nothing about
  interface implementation. `final class DummySignalSource implements
  SignalSource` compiles as-is; no removal needed.
- **`Path`, not `String`, for the constructor argument.** The governing
  brief asked to check this codebase's existing convention before
  picking. Grepped `java/` for prior file-path-typed constructor/field
  usage in main (non-test) code: none exists yet — `BingXPriceFeed`'s
  `baseUrl` is a URL string (a genuinely different thing, not a
  filesystem path), and no other class in `java/runtime` reads a local
  file at all. The closest existing precedent is
  `engine.schemas.SchemaCompatTest`'s own `Path fixturesDir` field. Went
  with `java.nio.file.Path` on that precedent plus the general principle
  that a typed path is safer than a bare string for anything doing
  real filesystem I/O (no ambiguity about relative-vs-absolute, no
  string-concatenation path-building).
- **Reusing `SchemaObjectMapper.create()` rather than a fresh
  `ObjectMapper`.** `:runtime` already depends on `:schemas`, and
  `SchemaObjectMapper` is exactly the shared Jackson config
  (`SNAKE_CASE` naming, `JavaTimeModule`, ISO-8601 timestamps) the
  Python↔Java wire contract requires — see `schemas/README.md` and
  `SchemaCompatTest`. Using anything else here would risk silently
  failing to parse a real Python-written signal file (e.g. `intent_id`
  vs `intentId` field-name mismatch) despite passing tests built with the
  same wrong mapper. `BingXPriceFeed` builds its own bare `new
  ObjectMapper()` instead, but that's for parsing BingX's own unrelated
  wire format (untyped `JsonNode` tree-walking, not an `OrderIntent`), so
  it isn't a comparable precedent for this class.
- **Catching `IOException | RuntimeException` around the parse call, not
  just `IOException`.** Jackson normally wraps an exception thrown from a
  record's compact constructor (e.g. `OrderIntent`'s own LIMIT-without-
  `limitPrice` check) into a `ValueInstantiationException`, which *is* an
  `IOException` subtype — so `IOException` alone should already cover it.
  The broader catch is defense in depth against that wrapping behavior
  changing or not applying in some Jackson configuration, per the
  governing brief's explicit "never propagate the exception up into
  `TradingLoop.tick()`" requirement — worth the extra safety margin here
  specifically because an uncaught exception from this class would look
  like a `TradingLoop` tick failure to anyone reading logs, obscuring
  that the real cause was a malformed input file, not the loop itself.
- **No missing-file log line, by design, not by omission.** The governing
  brief's spec for the missing-file case doesn't mention logging (unlike
  the malformed-file case, which explicitly does). Read that omission as
  deliberate given the operational picture described in the plan file:
  `TradingLoop` ticks every few minutes, the Python runner writes roughly
  once a day, so "file not present yet" is the dominant, expected state
  most of the time, not a warning-worthy condition.
- **Logging verified via the real test-run log stream, not a test
  assertion.** Grepped `java/` for any existing log-capture/test-appender
  pattern (`TempDir`, `Logger`, `appender`, `slf4j` across `*Test.java`)
  before writing the malformed-file test — none exists anywhere in this
  codebase yet, and `:runtime`'s only test-time SLF4J binding is
  `slf4j-simple` (prints to stderr, no programmatic capture API). Per the
  governing brief's own fallback ("if none exists, it's fine to just
  verify no exception propagates and note the log call exists"), the
  test itself only asserts `Optional.empty()`/no exception — but the real
  `./gradlew :runtime:test` run's captured `system-err` (preserved in
  `runtime/build/test-results/test/TEST-engine.runtime.FileSignalSourceTest.xml`)
  independently confirms both `log.warn` calls actually fired, with the
  expected Jackson exception text, for both the malformed-JSON and
  missing-required-field cases — read directly, not just claimed.
- **`.planning/` filename.** Followed the governing brief's own suggested
  name (`paper-trading-a-signal-source.md`) rather than the numeric
  `NN-*.md` convention used for top-level Implementation Priorities
  (`00`–`09`) — this task is a sub-task of a 5-task bridge plan, not a new
  top-level priority, so it doesn't fit that numbering scheme cleanly;
  the strategy-research line's own `sr-*.md` lettered convention is the
  closer precedent for a multi-task effort under one banner.

## CodeRabbit review findings

One review round on PR #68 (`ASSERTIVE` profile), two actionable findings,
both `🟠 Major`. Final disposition, ahead of the detail below: the
dedup-scope finding's own thread is fully resolved — CodeRabbit's own
follow-up caught one more real thing worth recording here: the fix's
first pass (commit `43f63d1`) left the class Javadoc's *opening*
paragraph still asserting "deliver each distinct `intentId` exactly
once", contradicting the new, precise "Dedup scope, stated precisely"
section added two paragraphs below it. Fixed in commit `abb55a8` —
reworded the opening paragraph to describe the actual behavior instead
of a stronger guarantee than the code provides. A subsequent CodeRabbit
re-review of that exact commit came back with zero actionable comments.
The symbol-validation finding's thread was, by CodeRabbit's own explicit
choice, left unresolved as a tracking marker rather than a request for a
fix in this PR — its own reply says as much ("이 리뷰 의견은 미해결 상태로
유지하겠습니다... 이 PR에서 수정을 요구하지는 않겠습니다", roughly "I'll
keep this review comment unresolved... I won't require a fix in this
PR") and separately confirmed the underlying reasoning (pre-existing
gap, needs a design decision, correctly out of scope for an interface-
extraction PR). That one open thread is why GitHub's native
`reviewDecision`/`mergeStateStatus` still read `CHANGES_REQUESTED`/
`BLOCKED` even though every actual code-level finding has been resolved
or explicitly deferred with reasoning both sides agree on — a real,
disclosed distinction between "the CodeRabbit commit-status check is
green against the exact HEAD sha" (true, verified) and "GitHub's
native PR-review UI shows a fully clean state" (not true, by mutual,
documented choice) — left for the human to resolve when merging, not
force-closed here.

**Partially addressed, in this PR** (CodeRabbit itself offered this as an
explicit alternative to the finding's primary "heavy lift" suggestion —
see below):

- **`FileSignalSource`'s dedup is a single in-memory pointer, not a
  durable, all-history set of delivered `intentId`s.** Two concrete
  consequences the finding named: (1) an `intentId` that reappears after a
  different one was delivered in between ("A, then B, then A again") is
  redelivered, not suppressed; (2) the tracking doesn't survive a process
  restart. CodeRabbit's primary suggestion was durable, cross-restart
  idempotent storage of every processed `intentId`, with `OrderPipeline`/
  `OrderStore` also enforcing dedup — its own severity tag on that
  suggestion was `🏗️ Heavy lift`. Its own comment named a lighter
  alternative in the same breath: "reduce the contract to 'only suppress
  a consecutive identical file' and make the A→B→A and restart cases
  explicit in tests." Took that path, not the heavy one:
  - `FileSignalSource`'s class Javadoc now states the single-pointer
    contract precisely (see "Dedup scope, stated precisely" in the
    class's own Javadoc) rather than implying a stronger guarantee than
    the code actually provides.
  - Two new tests make both consequences explicit and asserted, not just
    described:
    `anIntentIdThatReappearsAfterADifferentOneWasDeliveredIsRedeliveredNotSuppressed`
    and
    `aFreshInstanceAfterARestartRedeliversAnIntentIdThePriorInstanceAlreadyDelivered`.
  - The heavier fix (durable storage, cross-component `OrderPipeline`/
    `OrderStore` dedup) was **not** implemented here, and not because it
    lacks merit — reasoning: (a) it directly contradicts the governing
    brief's own exact spec for this class ("track the last-delivered
    `intentId` internally"); (b) `OrderStore`/`PaperBroker` are both
    in-memory-only today (see `TradingLoop`'s own Javadoc, "does not
    assume any prior state... 'start clean' is the only state there
    is") — durable dedup in `FileSignalSource` alone, with no durable
    order/position state anywhere else in the system, would be a
    partial, inconsistent fix, not a real one; a real fix needs durable
    `OrderStore`/reconciliation, which doesn't exist yet anywhere in this
    codebase; (c) `OrderPipeline`/`OrderStore` are both on this task's
    explicit out-of-scope list. This is the same "fix what's cheap and
    real, decline and document what needs its own design pass" pattern
    `.planning/08b-trading-loop.md`'s own CodeRabbit findings section
    already used for the `OrderStore` orphan-on-broker-failure finding —
    real follow-on work, most naturally paired with the governing plan's
    own Task E ("minimal internal reconciliation") or a dedicated
    durable-state effort, not a silent scope expansion of this task.
    Replied to the review comment with this reasoning; not implemented,
    tracked here instead.

**Declined in this PR, with reasoning, tracked as an open item:**

- **No symbol-match validation between a signal's `OrderIntent.symbol()`
  and the price/order symbol anywhere in the
  `TradingLoop`→`OrderPipeline`→`RiskGateway`→`PaperBroker.submit()`
  chain.** CodeRabbit's own investigation (it ran a real repo-wide script
  to check this before writing the finding, visible in the review
  comment's analysis trace) confirmed the gap is real and confirmed it
  predates this PR: `OrderPipeline`, `RiskGateway`, and `PaperBroker` all
  already lacked any `intent.symbol().equals(...)` check before this
  task's diff — `DummySignalSource` never exercised the gap because it's
  constructed with a fixed, caller-chosen symbol that every existing test
  happens to keep in sync with `TradingLoop`'s own `symbol` field, not
  because anything enforces that they match. `FileSignalSource` makes the
  gap easier to hit in practice (a real external file could plausibly
  carry a different symbol than intended) but does not introduce it.
  **Not fixed here** — the governing brief's "Explicitly out of scope"
  list names exactly this boundary: "Do not touch `RiskGateway`,
  `OrderPipeline`, `PaperBroker`, or `KillSwitch` internals — this task
  only adds the new interface/implementation and retypes `TradingLoop`'s
  field." A correct fix touches `TradingLoop.submitToBroker`/`tick()`
  logic (or `PaperBroker.submit()` itself) beyond a type-only change, and
  per CLAUDE.md's Development Methodology, a behavioral change to
  R3-risk components needs its own `Discuss` pass, not a fix folded into
  an unrelated task under review pressure. Concretely, the more natural
  place to decide *where* this validation should live (inside
  `FileSignalSource` itself, guarding against an untrusted external file
  specifically; or centrally in `PaperBroker`/`OrderPipeline`, guarding
  every signal source uniformly) is the governing plan's own Task C,
  which is what actually decides the real configured symbol and wires
  `FileSignalSource` to it — deciding it here, without that wiring
  context, risks guessing wrong about which layer should own the check.
  Replied to the review comment with this reasoning; flagged here as a
  real, disclosed gap for Task C (or a dedicated follow-up) to resolve,
  not silently dropped.

## Explicitly out of scope (per the governing brief, not attempted here)

- The Python side that will actually write these files (Task B).
- A scheduler or `main()` entrypoint driving `TradingLoop.tick()`
  periodically (Task C, depends on this task).
- `OrderStore.createOrder`'s visibility (CLAUDE.md Priority #10, a known,
  deliberately deferred gap, unrelated to this task).
- Any change to `RiskGateway`, `OrderPipeline`, `PaperBroker`, or
  `KillSwitch` internals.

## Verification

- `./gradlew :runtime:compileTestJava` against the test-first
  `FileSignalSourceTest` failed with the expected "cannot find symbol"
  errors before `FileSignalSource` existed.
- `./gradlew :runtime:test --tests FileSignalSourceTest` — 8/8 pass (6
  from the original TDD list plus the 2 dedup-scope tests added in
  response to CodeRabbit review, see "CodeRabbit review findings"),
  `system-err` in the XML report confirms both warning-log call sites
  actually fire.
- `./gradlew :runtime:test --tests TradingLoopTest --tests
  DummySignalSourceTest` — unchanged, all pass, confirming the interface
  extraction is behavior-preserving.
- `./gradlew test` (full multi-module suite) — **172 tests, 0 failures, 0
  errors**.
- PR opened, not merged — per the governing brief and CLAUDE.md's
  Auto-merge Policy, this is Java runtime code adjacent to OMS/Risk
  Gateway/Execution and requires explicit human sign-off regardless of
  CI/CodeRabbit status.
