# BingX VST integration, Task F: `OrderExecutor` interface extraction + retrofit

## Scope note

This is **Task F** of the 3-task BingX VST integration plan (see
`.claude/plans/tender-finding-matsumoto.md` — not yet mirrored into
`.planning/`, referenced here as the governing brief), which follows and
depends on the now-complete 5-task paper-trading bridge (`paper-trading-a`
through `-e`). Task F is the **foundation** the other two tasks (G —
`ExchangeOrderExecutor`, H — VST wiring/safety/real verification) depend
on; neither of those is touched here. R3-risk component (`java/execution`,
`java/runtime`, adjacent to `PaperBroker`/`TradingLoop`/`Reconciler`) — TDD
discipline applied throughout, per CLAUDE.md's Development Methodology.

This is explicitly a **provably inert** refactor: extract an interface
from an existing concrete class, retrofit that class onto it, and widen
two call sites' parameter types to the interface. No new runtime behavior
was introduced anywhere in this task. The same retrofit pattern was
already used once in this codebase for `SignalSource`/`DummySignalSource`
(`.planning/paper-trading-a-signal-source.md`), which served as the
template for both the mechanics and the documentation rigor here.

## What was built

Five changes, all in `java/execution` and `java/runtime`, plus one new CI
workflow:

- **`engine.execution.OrderExecutor`** (new interface, same package as
  `PaperBroker`/`Fill`) — `Optional<Fill> submit(Order, BigDecimal
  referencePrice)`, `List<Fill> pollFills(String symbol, BigDecimal
  referencePrice)`, `Map<UUID, Order> pendingOrders()`, `void
  cancel(Order)`. Its Javadoc states, as required by the governing brief:
  the error-contract asymmetry (`pollFills` must never throw; `submit` may
  throw and a thrown `submit` is an ambiguous outcome, which
  `TradingLoop.submitToBroker`'s existing orphan-handling already handles
  correctly and unchanged), and the extensibility invariant near-verbatim
  from the brief ("a new exchange/venue means writing a new
  `ExchangeAdapter` implementation, never a new `OrderExecutor`
  implementation... there should only ever be two `OrderExecutor`
  implementations in this codebase").
- **`PaperBroker implements OrderExecutor`** (retrofit) — `final` was not
  removed (confirmed directly, same fact already established for
  `DummySignalSource implements SignalSource`: a final class can implement
  any number of interfaces, `final` only forbids subclassing).
  `onPriceUpdate(String symbol, BigDecimal price)` was renamed to
  `pollFills(String symbol, BigDecimal price)` — purely mechanical, zero
  behavior change, since the method already did exactly what the new name
  says. `@Override` added to all four interface methods (`submit`,
  `pollFills`, `pendingOrders`, `cancel`), matching this codebase's
  existing convention on `DummySignalSource.nextSignal()`.
- **`TradingLoop`** (type-level substitution) — the field and constructor
  parameter were retyped from `PaperBroker` to `OrderExecutor`. The field
  was also **renamed** from `paperBroker` to `orderExecutor` — see
  "Judgment calls" below for why this went beyond a pure type change.
  `tick()`'s call site changed from `paperBroker.onPriceUpdate(symbol,
  price)` to `orderExecutor.pollFills(symbol, price)`;
  `submitToBroker`'s changed from `paperBroker.submit(order, price)` to
  `orderExecutor.submit(order, price)`. No other line of control-flow
  logic changed. Every Javadoc paragraph that named `PaperBroker`
  specifically as *the* broker was updated to describe it as *a*
  pluggable `OrderExecutor` (mirroring exactly what Task A did for
  `SignalSource`), while paragraphs describing `PaperBroker` as the one
  concrete implementation that exists *today* were left referencing it by
  name, since that remains factually accurate.
- **`Reconciler.check(...)`** (type-level substitution) — the third
  parameter was retyped from `PaperBroker` to `OrderExecutor` and renamed
  from `paperBroker` to `orderExecutor` (`pendingOrders()` is the only
  method called on it, already covered by the interface). The class
  Javadoc's "fill-or-nothing (no partial fill)" note — which the
  governing brief flagged as becoming inaccurate the moment a second
  `OrderExecutor` implementation can partially fill — was corrected to
  state explicitly that fill-or-nothing is a property of `PaperBroker`'s
  own current implementation, not a guarantee the `OrderExecutor`
  interface itself makes, and that a future exchange-backed implementation
  is expected to produce genuine partial fills. The two runtime mismatch-
  detail log strings (`ORPHANED_IN_BROKER`, `UNTRACKED_IN_BROKER`) that
  hardcoded the literal word "PaperBroker" were also generalized to "the
  OrderExecutor" — see "Judgment calls" below.
- **`.github/workflows/java-tests.yml`** (new CI workflow) — runs
  `./gradlew build` (full compile + test, all six modules) on every push
  and PR. Confirmed before adding it that no Java CI existed in this
  repository at all: `.github/workflows/` had exactly two files
  (`bingx-hostname-guard.yml`, `gitleaks.yml`), neither of which builds or
  tests Java. Matches both existing workflows' style: header comment block
  explaining purpose and scope, `on: push` / `on: pull_request` (no path
  filtering, same as the other two), `runs-on: ubuntu-latest`,
  `permissions: contents: read`, and pinned third-party actions (SHA +
  version comment) rather than a floating tag — `actions/checkout` reuses
  the exact same pin already used by the other two workflows
  (`3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`);
  `actions/setup-java` is pinned to `b6effb05e454b25005698d916606bdc6ffcbf961
  # v5.7.0`, confirmed as the real commit for that tag via the GitHub API
  (`GET /repos/actions/setup-java/git/refs/tags/v5.7.0`) rather than typed
  from memory. Java 21 / Temurin (matches every `build.gradle.kts`
  toolchain declaration), `cache: gradle` enabled (a single built-in
  `setup-java` input, not a second action) for faster subsequent runs.

## Exact rename site count

The governing brief estimated "~10 sites" in `PaperBrokerTest.java` and
asked that the real count be verified rather than assumed. **Confirmed
exactly 10** (`grep -c onPriceUpdate` before this task's changes): three
test method names (`onPriceUpdateFillsPendingLimitOrderOnceMarketable`,
`onPriceUpdateIgnoresPendingOrdersForOtherSymbols`,
`onPriceUpdateRejectsZeroOrNegativePrice`), five real invocation call
sites (`broker.onPriceUpdate(...)`), and two `AssertionError` message
strings inside the concurrency test. The estimate was accurate.
`PaperBroker.java` itself had 2 sites (the method declaration plus one
comment mentioning the old name), and `TradingLoop.java` had 2 sites (one
Javadoc mention, one real call) — both exactly matching the governing
brief's own count.

## TDD

Followed the same test-first discipline as Task A, adapted for a
mechanical rename rather than new behavior:

1. Wrote the `OrderExecutor` interface first (the new contract other files
   would compile against).
2. Renamed every `onPriceUpdate` occurrence in `PaperBrokerTest.java` to
   `pollFills` **before** touching `PaperBroker.java` itself.
3. Ran `./gradlew :execution:compileTestJava` and confirmed the expected
   compile failure: 5 errors, "cannot find symbol: method
   pollFills(String,BigDecimal)" — one per real invocation call site (the
   other 5 renamed occurrences were test-method names and error-message
   strings, which don't affect compilation, so they didn't appear as
   separate compiler errors).
4. Implemented the `PaperBroker` retrofit (`implements OrderExecutor`,
   rename, `@Override` annotations). Ran `./gradlew :execution:test` —
   green.
5. Retyped `TradingLoop`'s field/constructor/call-sites. Ran
   `./gradlew :runtime:compileJava` — green, confirming the retype is
   source-compatible with the rest of `:runtime`'s main sources.
6. Retyped `Reconciler.check(...)`'s third parameter and corrected its
   Javadoc. Ran `./gradlew :runtime:test` — green, with **zero** changes
   needed to `TradingLoopTest.java`, `ReconcilerTest.java`,
   `DailyReportGeneratorTest.java`, or `PaperTradingAppTest.java` — every
   one of them constructs a real `PaperBroker` directly (no mocking
   framework exists anywhere in this codebase) and passes it into a
   now-`OrderExecutor`-typed parameter, which is source-compatible by
   Java's own widening-reference-conversion rules.
7. Ran the full multi-module suite (`./gradlew clean build`) — green.

## Expected diff shape: confirmed accurate

The governing brief predicted the test-file diff would be **zero** except
the mechanical `pollFills` rename in `PaperBrokerTest.java`, and warned
that needing to change test *logic* anywhere else would be a signal to
stop and re-investigate. This held exactly: `git diff --stat` on the
final change set shows only `PaperBrokerTest.java` (rename-only, 10
lines) among test files; `TradingLoopTest.java`, `ReconcilerTest.java`,
`DailyReportGeneratorTest.java`, and `PaperTradingAppTest.java` are
byte-for-byte unchanged. No point in this task required stopping to
re-investigate.

## Judgment calls

- **Renamed `TradingLoop`'s/`Reconciler.check`'s `paperBroker` field/
  parameter to `orderExecutor`, not just its type.** The governing brief's
  wording ("retype the field/constructor parameter") is ambiguous about
  whether the identifier itself should change. Chose to rename it because
  leaving a field literally named `paperBroker` while declared
  `OrderExecutor` would be actively misleading the moment Task G's
  `ExchangeOrderExecutor` exists and could be constructed there instead —
  and because the brief explicitly called out updating "any Javadoc that
  names `PaperBroker` specifically as the broker" as an expected, in-scope
  consequence of the type change, which is the same underlying concern
  applied to the field name itself. This is safe: Java has no named
  arguments, so renaming a field or constructor-parameter identifier
  cannot affect any caller's ability to compile, which the full green test
  suite (constructing `TradingLoop`/`Reconciler.check` positionally
  throughout) confirms directly rather than by argument alone.
- **Generalized `Reconciler`'s two runtime mismatch-detail log strings**
  (`ORPHANED_IN_BROKER`, `UNTRACKED_IN_BROKER`) from hardcoded
  `"PaperBroker"` to `"the OrderExecutor"`. The governing brief's item 4
  named only the "fill-or-nothing" Javadoc note explicitly, but these two
  strings are runtime-visible diagnostic text describing the exact
  parameter that was just retyped — the same category of staleness the
  brief's own reasoning about the Javadoc note targets, just in log text
  instead of a doc comment. Verified safe before making the change:
  `grep`ed every test file in `java/runtime/src/test` for `.detail()`
  assertions and found none — no test in this codebase asserts on the
  exact mismatch-detail string content, only on `.type()` and
  `.orderId()`, so this change carries zero test-breakage risk and was
  confirmed green by the same `:runtime:test` run as everything else in
  this task.
- **CI action pinning verified against the live GitHub API, not typed
  from memory.** This project's existing two workflows (`bingx-hostname-
  guard.yml`, `gitleaks.yml`) both pin third-party actions to a full
  commit SHA with a version-tag comment rather than a floating tag or
  major-version ref. Matched that convention for `actions/setup-java` by
  querying `https://api.github.com/repos/actions/setup-java/git/refs/tags/v5.7.0`
  directly and using the real returned commit SHA
  (`b6effb05e454b25005698d916606bdc6ffcbf961`), rather than guessing or
  reusing a possibly-stale SHA from training data.
- **Did not touch `PaperTradingApp.java`.** It constructs a concrete
  `PaperBroker` and stores it in a field still typed `PaperBroker`
  (`this.paperBroker`), passed into both the now-`OrderExecutor`-typed
  `TradingLoop` constructor and `Reconciler.check(...)` — both compile
  unchanged via widening reference conversion. The governing brief's task
  list names only `OrderExecutor`, `PaperBroker`, `TradingLoop`, and
  `Reconciler` as in-scope; `PaperTradingApp` choosing which concrete
  `OrderExecutor` to construct (e.g. via
  `PAPER_TRADING_EXECUTION_MODE`) is explicitly Task H's job per the
  governing plan's own "Decisions" section, not this one's. Confirmed
  `PaperTradingApp.java` and `PaperTradingAppTest.java` are both
  byte-for-byte unchanged in the final diff.
- **`.planning/` filename and numbering.** Followed the same lettered
  sub-task convention as Tasks A-E (`paper-trading-f-order-executor.md`)
  rather than treating this as a new top-level numbered priority, since
  it's a sub-task of the governing 3-task VST integration plan — same
  reasoning Task A's own doc already recorded for this choice.

## Explicitly out of scope (per the governing brief, not attempted here)

- `ExchangeOrderExecutor`, any BingX-specific execution code, or anything
  touching `ExchangeAdapter`/`BingXAdapter` (Task G).
- `OrderStore`, `RiskGateway`, `KillSwitch`, `DailyReportGenerator`
  internals.
- `FileSignalSource`/`SignalSource` (unrelated to this task).
- `.env`/credentials — none needed here.
- CLAUDE.md edits. The governing plan's own "Decisions" section mentions
  writing the `OrderExecutor`/`ExchangeAdapter` layering rule into
  CLAUDE.md's Architecture section, but the task-specific brief handed to
  this session lists CLAUDE.md changes nowhere in Task F's own itemized
  scope — left for whichever later task's brief actually calls for it
  (plausibly Task H, which the governing plan's own "CLAUDE.md updates"
  bullet is written under), rather than added here as a guess.

## Verification

- `./gradlew :execution:compileTestJava` against the test-first renamed
  `PaperBrokerTest.java` failed with the expected 5 "cannot find symbol:
  pollFills" errors before `PaperBroker.pollFills` existed.
- `./gradlew :execution:test` — green after the `PaperBroker` retrofit.
- `./gradlew :runtime:compileJava` — green after the `TradingLoop` retype.
- `./gradlew :runtime:test` — green after the `Reconciler` retype, with
  zero changes to any `:runtime` test file.
- `./gradlew clean build` (full multi-module suite, all six modules,
  clean — not incremental) — **BUILD SUCCESSFUL**, **212 tests, 0
  failures, 0 errors** (summed from every module's JUnit XML report:
  schemas, oms, risk, execution, exchange, runtime).
- `.github/workflows/java-tests.yml` YAML syntax validated locally
  (`python3 -c "import yaml; yaml.safe_load(...)"`) before pushing.
- **Real first CI run failed, and the cause was a genuine pre-existing
  repo bug this task's CI was the first thing to ever surface**:
  `./gradlew: Permission denied` (exit 126). `git ls-files --stage
  java/gradlew` showed the file tracked at mode `100644` (non-executable)
  since it was first committed (#9, "shared schemas skeleton") — masked
  locally the entire time because this session's dev environment has
  `core.fileMode=false` (common on a Windows/WSL-mounted filesystem),
  so no local `git status`/`ls -la` ever showed a problem, and no
  human had ever run `./gradlew` from a strict-mode checkout before
  this PR's CI. Fixed at the root, not worked around per-workflow:
  `git update-index --chmod=+x java/gradlew` (content/blob sha
  unchanged, only the tracked tree-entry mode moved to `100755`),
  committed separately from the main Task F change set so the fix and
  its reasoning are visible in isolation. Chose this over a `chmod +x`
  step inside `java-tests.yml` itself because the underlying bug is
  general, not CI-specific — it would also break a plain `git clone` +
  `./gradlew ...` on any ordinary Linux/macOS machine with normal
  `core.fileMode=true` behavior, not just this one workflow, so fixing
  only the workflow would leave the same landmine for the next
  workflow, script, or human clone that ever invokes `gradlew` directly.
  Second CI run (after the fix) passed in ~1m1s.
- Full PR check suite, final state: `bingx-hostname-guard` pass,
  `gitleaks` pass, `java-tests` pass (both push events on the PR
  branch), `CodeRabbit` — see PR thread for its own status.
- PR opened, not merged — per the governing brief and CLAUDE.md's
  Auto-merge Policy, this is Java OMS/Execution/runtime code and requires
  explicit human sign-off regardless of CI/CodeRabbit status.
