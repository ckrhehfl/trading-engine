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

This was designed and initially built as an explicitly **provably inert**
refactor: extract an interface from an existing concrete class, retrofit
that class onto it, and widen two call sites' parameter types to the
interface. The same retrofit pattern was already used once in this
codebase for `SignalSource`/`DummySignalSource`
(`.planning/paper-trading-a-signal-source.md`), which served as the
template for both the mechanics and the documentation rigor here.

**That inert framing held through the original implementation and
CodeRabbit review round 1.** It stopped being fully true in round 2: a
real, narrow runtime behavior change was added to
`PaperBroker.pollFills` (per-order `RuntimeException` isolation) to fix
a genuine gap between the `OrderExecutor` contract this task itself
wrote and what `PaperBroker` actually did — flagged precisely, not
glossed over, in "Expected diff shape" and "CodeRabbit review findings"
below. The `OrderExecutor` interface extraction and the `PaperBroker`/
`TradingLoop`/`Reconciler` retype itself remain behavior-preserving;
that one later fix is the one exception, and is called out everywhere
it's relevant rather than folded silently into an "inert" claim that
would no longer be accurate.

### GSD phase status

Added explicitly on CodeRabbit review request (see "CodeRabbit review
findings" below) — not a restructuring of this doc's established
`paper-trading-*` section convention (`Scope note` / `What was built` /
`TDD` / `Judgment calls` / `CodeRabbit review findings` / `Explicitly out
of scope` / `Verification`, which every prior task doc in this series
already uses and which this doc keeps), just an explicit mapping onto
CLAUDE.md's own GSD phase loop so the status of each phase is stated
rather than left implicit:

- **Discuss**: resolved by the governing brief itself, not re-litigated
  here — the interface shape (`submit`/`pollFills`/`pendingOrders`/
  `cancel`), the exact rename, and the retype targets were all specified
  directly in the task brief handed to this session (mirroring how
  `sr-x`'s own brief "pre-decided the specific hypothesis before any code
  was written"). No open design ambiguity remained to discuss before
  starting.
- **Plan**: `.claude/plans/tender-finding-matsumoto.md` (the governing
  3-task VST integration plan) is the plan; this doc's "Scope note" and
  "Exact rename site count" record how the real code matched or deviated
  from it.
- **Execute**: this document's "What was built," "Exact rename site
  count," "TDD," and "Judgment calls" sections.
- **Verify**: this document's "Verification" section — real local test
  runs (`./gradlew clean build`, 213 tests/0 failures/0 errors as of the
  final state after CodeRabbit review) and a real observed CI run
  (including the genuine `gradlew` permission failure and its fix), not
  a claim that tests would pass.
- **Ship**: **pending.** PR #77 is open, CI is green, and a real
  CodeRabbit review has landed against the current HEAD commit — but per
  the governing brief's own explicit instruction and CLAUDE.md's
  Auto-merge Policy (Java OMS/Execution/runtime code is excluded from
  delegated auto-merge regardless of CI/CodeRabbit status), **this PR is
  not merged and must not be merged by an LLM session.** No human
  approver or approval timestamp is recorded here because none exists
  yet as of this writing — that field is intentionally left for a human
  to fill in on the PR itself (the actual source of truth for approval
  state) when they act on it, not invented here to make this section
  look more complete than the real state.

## What was built

Five changes, all in `java/execution` and `java/runtime`, plus one new CI
workflow:

- **`engine.execution.OrderExecutor`** (new interface, same package as
  `PaperBroker`/`Fill`) — `Optional<Fill> submit(Order, BigDecimal
  referencePrice)`, `List<Fill> pollFills(String symbol, BigDecimal
  referencePrice)`, `Map<UUID, Order> pendingOrders()`, `void
  cancel(Order)`. Its Javadoc states, as required by the governing brief:
  the error-contract asymmetry (`pollFills` must never throw for a
  **per-order resolution failure**; `submit` may throw and a thrown
  `submit` is an ambiguous outcome, which `TradingLoop.submitToBroker`'s
  existing orphan-handling already handles correctly and unchanged), and
  the extensibility invariant near-verbatim from the brief ("a new
  exchange/venue means writing a new `ExchangeAdapter` implementation,
  never a new `OrderExecutor` implementation... there should only ever be
  two `OrderExecutor` implementations in this codebase"). The "per-order
  resolution failure" qualifier was added after CodeRabbit review — see
  "CodeRabbit review findings" below.
- **`PaperBroker implements OrderExecutor`** (retrofit) — `final` was not
  removed (confirmed directly, same fact already established for
  `DummySignalSource implements SignalSource`: a final class can implement
  any number of interfaces, `final` only forbids subclassing).
  `onPriceUpdate(String symbol, BigDecimal price)` was renamed to
  `pollFills(String symbol, BigDecimal price)` — purely mechanical, zero
  behavior change, since the method already did exactly what the new name
  says. `@Override` added to all four interface methods (`submit`,
  `pollFills`, `pendingOrders`, `cancel`), matching this codebase's
  existing convention on `DummySignalSource.nextSignal()`. **Added in
  CodeRabbit review round 2** (see "CodeRabbit review findings" below,
  not part of the original rename): `pollFills`'s per-order loop now
  catches a `RuntimeException` from resolving any single pending order,
  logs it, and drops that one order from `pendingOrders` (for
  `Reconciler`'s `ORPHANED_IN_BROKER` check to catch), rather than
  letting it propagate and abort every other pending order's resolution
  in the same call — a real, narrow gap between `OrderExecutor`'s own
  newly-written "never throw for a per-order resolution failure"
  contract and what the implementation actually did, reachable via
  `Order`'s shared-mutable-reference design (see `Reconciler`'s own
  Javadoc), not a hypothetical.
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
  `actions/setup-java` is pinned to
  `b6effb05e454b25005698d916606bdc6ffcbf961 # v5.7.0`, confirmed as the
  real commit for that tag via the GitHub API
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

## Expected diff shape: confirmed accurate, with one disclosed exception

The governing brief predicted the test-file diff would be **zero** except
the mechanical `pollFills` rename in `PaperBrokerTest.java`, and warned
that needing to change test *logic* anywhere else would be a signal to
stop and re-investigate. This held exactly through the initial
implementation and the first CodeRabbit review round: `TradingLoopTest
.java`, `ReconcilerTest.java`, `DailyReportGeneratorTest.java`, and
`PaperTradingAppTest.java` are byte-for-byte unchanged throughout this
entire task, and `PaperBrokerTest.java`'s only change through round 1
was the rename.

**One exception, added in round 2 of CodeRabbit review** (see
"CodeRabbit review findings" below for the full reasoning): a real,
narrow per-order fault-isolation gap in `PaperBroker.pollFills` was
found and fixed, with one new test
(`pollFillsIsolatesASingleOrdersResolutionFailureAndStillResolvesOtherPendingOrders`)
added to `PaperBrokerTest.java` to cover it. This is not a rename and
not "changing test logic" in the sense the governing brief's warning was
about (no existing test's assertions changed) — it is new coverage for a
real bug the task's own newly-written `OrderExecutor` Javadoc contract
exposed, not a pre-planned feature. Flagged explicitly here rather than
folded silently into the "confirmed accurate" claim, since it is a real,
if small, deviation from a pure rename-only diff.

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

## CodeRabbit review findings

Two review rounds on PR #77 (`ASSERTIVE` profile). Disposition below,
each verified against the real current code before deciding, per each
review's own "verify each finding against current code, fix only
still-valid issues, skip the rest with a brief reason" instruction.

### Round 1

Against commit `ab91fc1` (the first commit — before the `gradlew` mode
fix and this doc's own subsequent updates were pushed): 6 actionable
comments.

**Fixed, this PR:**

- **`OrderExecutor.pollFills`'s "never throw" contract didn't actually
  match `PaperBroker.pollFills`'s real implementation.** A genuine,
  correctly-identified bug in this task's own Javadoc, not a
  pre-existing issue: `PaperBroker.pollFills` calls
  `Objects.requireNonNull(symbol, ...)`,
  `Objects.requireNonNull(price, ...)`, and `requirePositivePrice(price)`
  — all three can throw (`NullPointerException`/
  `IllegalArgumentException`), and `PaperBrokerTest`'s own
  `pollFillsRejectsZeroOrNegativePrice` test explicitly asserts this
  throwing behavior via `assertThrows`. CodeRabbit offered two
  alternative fixes: loosen the documented contract, or change
  `PaperBroker.pollFills` to swallow these exceptions instead (and
  update the test to expect an empty result). Chose the **first**
  option, not the second — the second is a real behavior change (a
  negative-price call would silently return `List.of()` instead of
  throwing), which directly contradicts this task's own "provably
  inert, zero behavior change" charter and the governing brief's
  explicit "if you find yourself needing to change test logic... stop"
  warning. Fixed by adding a new Javadoc paragraph to
  `OrderExecutor.java` (both the class-level "Error contract" section
  and the `pollFills` method Javadoc) that precisely scopes the
  "never throw" guarantee to **per-order resolution failures**
  specifically, explicitly carving out eager caller-error argument
  validation (null/non-positive arguments) as a distinct, still-legal-
  to-throw-for case — matching this codebase's existing argument-
  validation convention everywhere else. Zero behavior change; only
  the contract's own documentation became accurate. `PaperBrokerTest`
  is unaffected — still zero test-logic changes beyond the original
  rename.
- **CI concurrency control.** Added a `concurrency:` block
  (`cancel-in-progress: true`, keyed on workflow + ref) to
  `java-tests.yml` so a rapid sequence of pushes to the same branch/PR
  doesn't pile up redundant multi-minute runs. Neither existing
  workflow (`bingx-hostname-guard.yml`, `gitleaks.yml`) has this, but
  neither of those runs longer than a few seconds either — this is the
  first workflow in the repo where queue buildup is a real, practical
  concern, so the inconsistency with the other two is judged
  acceptable rather than matched.
- **Planning doc's own description of the workflow's build scope and a
  due-diligence check on the `:exchange` module in CI.** The doc
  already said "all six modules" rather than "unit-test-only," so no
  wording change was needed there — but performed and recorded the
  actual due-diligence check CodeRabbit asked for before merging:
  grepped for `LIVE_TRADING_ENABLED`/`live_trading` (zero hits
  anywhere in `java/exchange` or `java/runtime`), and read
  `BingXAdapterTest.java`/`BingXSignerTest.java` directly to confirm
  they use an in-process fake `HttpServer` (never a real BingX host)
  and hardcoded dummy credential strings (never `System.getenv`). See
  "Verification" below for the specific findings.
- **Planning doc's CI-verification note.** Was written before this
  PR's real CI had ever run (a placeholder acknowledging that limit).
  Superseded by this doc's own later update once the real first
  `java-tests` run actually happened (see "Verification" below) — the
  real observed run result is now what's recorded, not a description
  of the local-only YAML syntax check. `actionlint` was not
  additionally installed/run — a real, live GitHub Actions execution
  (which surfaced the genuine `gradlew` permission bug below) is
  strictly stronger evidence than static linting would have been for
  this specific class of failure, so it was judged unnecessary on top
  of that.

**Declined in round 1, reconsidered in round 2 (see below):**

- **"Update the R3 planning record... to document the Discuss, Plan,
  Execute, Verify, and Ship stages with their outcomes... record
  CodeRabbit's final pass status plus the human approver, approval
  time, and PR link."** Round 1's initial disposition (declined
  outright) reasoned that no planning doc in this repo's history uses a
  rigid Discuss/Plan/Execute/Verify/Ship-labeled template, and that
  inventing an approval timestamp before one exists would be dishonest.
  CodeRabbit repeated this finding in round 2 with a sharper, more
  reasonable framing ("mark Ship as pending... without inventing
  approval metadata") that directly addressed the part of round 1's
  reasoning that was actually about substance (not wanting to invent
  data), rather than the part that was about doc-convention
  consistency. Net result, applied in round 2: added a "GSD phase
  status" subsection (see near the top of this document) explicitly
  naming each phase's status, with Ship marked **pending** and no
  invented approver/timestamp — the doc's own established section
  structure (`Scope note` / `What was built` / etc.) was kept, not
  replaced, since restructuring it wholesale was never the substantive
  part of the request.

### Round 2

Against commit `b1e6b65` (after round 1's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha, not a stale/rate-limited status): 4 actionable comments.

**Fixed, this PR:**

- **GSD phase status** — see immediately above; applied this round.
- **`OrderExecutor.java`'s "both existing implementations (today just
  `PaperBroker`)" was confusing, self-contradictory wording** ("both"
  implies two, immediately followed by a clarification that there is
  only one). Genuinely introduced by round 1's own fix, not
  pre-existing. Corrected to "every implementation of this interface
  (today just `PaperBroker`)".
- **`PaperBroker.pollFills` did not actually isolate a single pending
  order's own resolution failure from every other pending order's
  resolution in the same call — a real, if narrow, gap between the
  Javadoc contract round 1 had just written and what the code actually
  did.** Verified this is genuinely reachable, not purely theoretical:
  `Order` is a shared mutable object (`Reconciler`'s own Javadoc already
  documents `OrderPipeline.submitIntent` handing `PaperBroker.submit`
  the exact same instance it registers in `OrderStore`), so a caller
  holding that same reference can drive an order `PaperBroker` still
  considers pending into a terminal state through a path other than
  `PaperBroker#cancel` (e.g. calling `order.requestCancel()`/
  `confirmCancel()` directly) — the next `pollFills` call's
  `order.fill()` for it then throws `IllegalStateException`
  (`CANCELLED` is not in `Order`'s `CAN_FILL` set), and before this fix
  that exception would propagate out of the *entire* `pollFills` call,
  losing every other pending order's fill for that tick too. Unlike the
  null/negative-argument case (round 1), this is squarely a per-order
  resolution failure, not caller-error argument validation — exactly
  what the "never throw" contract is supposed to cover, so fixing the
  implementation (not just further qualifying the Javadoc) was the
  correct call here, not scope creep: wrapped the per-order `tryFill`
  call in `pollFills`'s loop in a `try/catch (RuntimeException)`,
  logging loudly and dropping the poisoned order from `pendingOrders`
  (so `Reconciler`'s own `ORPHANED_IN_BROKER` check catches the
  resulting inconsistency on its next pass — the same disposition an
  ambiguous `submit` failure already gets, not a new pattern). Added
  one new test,
  `pollFillsIsolatesASingleOrdersResolutionFailureAndStillResolvesOtherPendingOrders`,
  using only real objects (no mocking framework exists in this
  codebase): a poisoned order is driven to `CANCELLED` directly,
  bypassing `broker.cancel(...)`, while a second, healthy pending order
  for the same symbol proves the poisoned order's failure doesn't
  prevent the healthy one from resolving in the same `pollFills` call.
  This is a small, additive, real bug fix discovered *by* writing this
  task's own Javadoc contract, not a pre-planned feature — judged
  in-scope despite the task's "provably inert" framing because leaving
  it unfixed would mean the Javadoc this task itself wrote was not
  actually true of the one implementation that exists.

**Declined, with reasoning, not attempted here:**

- **"Update the plan to block the live `ExchangeOrderExecutor`
  integration and define reconciliation for ambiguous submit outcomes:
  persist `SUBMISSION_UNKNOWN` by clientOrderId, prevent unsafe
  retries, query exchange state, and reconcile before retrying... Add
  tests covering post-acceptance timeout, pre-acceptance failure, safe
  retry, and restart deduplication."** This is real, valuable design
  work — but it is designing `ExchangeOrderExecutor`'s own submit/retry
  semantics against a real exchange, which is explicitly **Task G**,
  not Task F, per the governing brief's own "Explicitly out of scope"
  list ("Do not build `ExchangeOrderExecutor`, any BingX-specific code,
  or anything touching `ExchangeAdapter`/`BingXAdapter` — that's Task
  G, a separate later task") and the governing plan's own Task G
  section (which already names most of this: tracking pending state
  only after real acknowledgment, a status-mapping table, per-order
  failure containment in `pollFills`, TDD against a hand-written
  `ExchangeAdapter` test double). `OrderExecutor`'s own Javadoc already
  documents the mechanism that exists **today**, without needing new
  persisted state, for the ambiguous-`submit` case: a `TradingLoop`
  submit that throws leaves the order registered in `OrderStore` but
  never reaches `pendingOrders()`, which `Reconciler` already flags as
  `ORPHANED_IN_BROKER` — automatically tripping the kill switch until a
  human looks, entirely with code that already exists and is already
  tested. Designing a full persisted-`SUBMISSION_UNKNOWN`/safe-retry
  protocol now, ahead of `ExchangeOrderExecutor` actually existing,
  would be architecture work for a class that hasn't been written yet —
  exactly the kind of R3-risk design CLAUDE.md's Development
  Methodology says needs its own `Discuss` pass at the time, not
  something decided under review pressure on an unrelated PR. Tracked
  here as a real, disclosed pointer for Task G, not silently dropped.

### Round 3

Against commit `faaa371` (after round 2's fixes were pushed — a real
review confirmed via the GitHub reviews API to target this exact commit
sha): 1 inline actionable comment plus 2 "outside diff range" comments.
All three fixed, this PR:

- **Markdown MD022 (headings must be surrounded by blank lines)** on
  this document's own "### Round 1"/"### Round 2" headings — both had
  been written as long, wrapped multi-line headings (the heading text
  continuing directly onto the next line with no blank line before the
  following paragraph), which is invalid Markdown heading syntax, not
  just a style nit: everything after a heading's own line break renders
  as a separate, un-blank-line-separated paragraph rather than part of
  the heading. Fixed by shortening both headings to a single short line
  and moving the parenthetical detail into a properly blank-line-
  separated paragraph immediately below each — this "Round 3" heading
  uses that same corrected shape from the start. Verified the fix, and
  checked for further instances of the same class of problem, with a
  real linter rather than by eye: `npx markdownlint-cli2` (MD022 only)
  against this document found one additional, related issue CodeRabbit
  hadn't flagged — an inline code span (the `actions/setup-java` SHA +
  version-comment pin, in "What was built") that had been line-wrapped
  in the source such that its continuation line started with `# v5.7.0`,
  which risks being misparsed as an ATX heading by a renderer that
  classifies block structure before resolving inline code spans (a real
  rendering risk, not just a linter preference). Fixed by keeping that
  entire code span on one unwrapped line, matching the `actions/
  checkout` pin's own formatting immediately above it. Re-ran
  `markdownlint-cli2` after both fixes: **0 issues**.
- **This document's own "provably inert" framing in the Scope note no
  longer matched round 2's actual fix.** A real, correctly-identified
  inconsistency: the Scope note's opening line asserted "no new runtime
  behavior was introduced anywhere in this task" as an unqualified,
  present-tense claim, while "Expected diff shape" and "CodeRabbit
  review findings" both already honestly disclosed that round 2 added a
  real runtime behavior change (`PaperBroker.pollFills`'s per-order
  fault isolation). The disclosure existed, but the Scope note's own
  framing hadn't been updated to be consistent with it — a reader
  stopping at the Scope note alone would have come away with a false
  impression. Fixed by rewriting the Scope note to state plainly that
  the inert framing held through round 1 and stopped being fully true
  in round 2, with a pointer to where the one exception is detailed,
  rather than asserting an absolute that the rest of the document
  already contradicts.
- **The `LIVE_TRADING_ENABLED`/`live_trading` due-diligence claim in the
  Verification section overstated its own scope.** Real, correctly-
  identified: "no such flag exists anywhere in this codebase yet" is
  false as literally written — a repo-wide grep (not the `java/exchange`
  + `java/runtime`-scoped one this task actually ran) finds both strings
  in `.coderabbit.yaml` (as example text inside its own automated-review
  policy describing what a disallowed future diff would contain, not an
  active flag) and in this document itself (self-referentially, from
  round 2's version of this same sentence). Fixed by narrowing the claim
  to what was actually verified — the CI-executed build scope and the
  `:exchange` test sources specifically — and disclosing the two
  non-live-trading-enabling matches the wider grep does find, rather
  than an unqualified "anywhere in this codebase."

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
  schemas, oms, risk, execution, exchange, runtime) after the original
  implementation; **213 tests, 0 failures, 0 errors** after CodeRabbit
  review round 2 added the one new `PaperBrokerTest` fault-isolation
  test (see "Expected diff shape" and "CodeRabbit review findings"
  above) — re-ran `./gradlew clean build` again after that fix and
  confirmed the higher count with the same zero-failures result.
- `.github/workflows/java-tests.yml` YAML syntax validated locally
  (`python3 -c "import yaml; yaml.safe_load(...)"`) before pushing.
- **Confirmed, on CodeRabbit review, that `./gradlew build` running the
  `:exchange` module in CI specifically does not violate CLAUDE.md's
  "never add live exchange write-access in CI" rule** (a real,
  worth-checking question, since `:exchange` is the one module whose
  production code talks to a real BingX host). Grepped `java/exchange`
  and `java/runtime` (the workflow's own build scope + the module that
  wires it) for `LIVE_TRADING_ENABLED`/`live_trading` — zero hits in
  either. **Correction, made on CodeRabbit review round 3**: the
  original wording here ("no such flag exists anywhere in this
  codebase yet") overstated what was actually checked — a repo-wide
  grep does find `LIVE_TRADING_ENABLED`/`live_trading` in
  `.coderabbit.yaml` (as example strings inside its own
  automated-review policy text, describing what a *future* live-
  trading-enabling diff would look like, not an active flag) and in
  this very document (this sentence and its round-2 predecessor,
  self-referentially, plus CLAUDE.md's Tooling Stack row noting the
  guardrail hook is "not built yet"). Neither is a real, live,
  enableable flag anywhere in actual `java/`/`python/` source — the
  conclusion that the CI-executed test paths cannot enable live
  trading stands, scoped precisely to what was inspected: the
  `java-tests.yml` workflow's own build scope (`./gradlew build`,
  no env vars set beyond what `setup-java` needs) and the `:exchange`
  test sources, not a claim about the whole repository. Separately
  read `BingXAdapterTest.java` directly — it stands up a local
  in-process `HttpServer` bound to `127.0.0.1` on a random port (the
  same pattern `BingXPriceFeedTest`/`TradingLoopTest`/`ReconcilerTest`
  already use via `FakeBingXTradesServer`), never contacts a real
  BingX host either way. Both `BingXAdapterTest` and `BingXSignerTest`
  construct their adapter with hardcoded dummy strings
  (`"test-api-key"`/`"test-api-secret"`), never `System.getenv(...)` —
  confirmed via grep, zero `System.getenv` calls in either test file.
  Nothing in the `:exchange` test suite reads a real credential or
  could enable live trading.
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
