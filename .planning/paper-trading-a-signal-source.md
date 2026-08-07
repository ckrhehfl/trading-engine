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

Full suite after the change: **170 tests, 0 failures, 0 errors** across
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
- `./gradlew :runtime:test --tests FileSignalSourceTest` — 6/6 pass,
  `system-err` in the XML report confirms both warning-log call sites
  actually fire.
- `./gradlew :runtime:test --tests TradingLoopTest --tests
  DummySignalSourceTest` — unchanged, all pass, confirming the interface
  extraction is behavior-preserving.
- `./gradlew test` (full multi-module suite) — **170 tests, 0 failures, 0
  errors**.
- PR opened, not merged — per the governing brief and CLAUDE.md's
  Auto-merge Policy, this is Java runtime code adjacent to OMS/Risk
  Gateway/Execution and requires explicit human sign-off regardless of
  CI/CodeRabbit status.
