# Research Direction Task AJ — implement the bounded price-basis diagnostic

Date: 2026-10-06. This implements Task AI's fixed acquisition specification;
it does not report an acquisition result. The operator authorized this order:
implement/test/review the diagnostic, acquire the six anchors, finish dated
instrument/action evidence, then consider corrected accounting and sizing.
CLAUDE.md's research guards and Task AG's input gates remain binding.

## Scope and decisions

`data.kis_basis_probe` has only `--scan-db` and `--output-dir`. The six
code/date pairs, two quotation bases, three negative controls, positive
control, and paper host are fixed in code exactly as specified in Task AI.
For each date, both bases' controls must pass before either target is read.
The twelve target pages are repeated after the initial sixty-page matrix:
72 logical page calls when successful. No date expansion, return calculation,
comparison, sizing, order endpoint, or holdout access is implemented.

The existing `KisSession`, quotation reader, pacing and retry limits are
reused. An optional callback in `kis_klines` checks the protected KRX session
and persists an attempt record before every quotation GET, including both
transport and application retries. Existing callers without the callback
keep their behavior. Counts explicitly exclude authentication/token-cache
activity; they are not represented as total network requests. A guard refusal
is outside the transport retry handler and cannot be retried as a network
error. Serial pacing and the session guard apply before every such attempt.
An additional callback checks the session before each application attempt's
header/token preparation, so entering the protected session during a retry
also prevents starting another token refresh. An already in-flight network
request is not cancelled at the market boundary.

Prices must be finite and positive, OHLC consistent, and the response must
contain exactly the requested day once. Existing unfiltered row-cap and
malformed-payload checks also apply. A target's empty response is unresolved,
never zero recovery. **Volume and turnover must also be strictly positive**:
this is a conservative refinement of the specification for these six
observed/traded anchors, not a rule that halted holdings are worthless.
A zero-volume/stale anchor needs investigation instead of certification.
Equivalent decimal padding is normalized without rounding values.

## Evidence and input boundary

A new output directory outside the source checkout is required. An existing
directory is refused, preserving earlier evidence. Relevant acquisition
sources and lockfiles must be committed and clean. After the initial session
and source checks, `started.json` is flushed and fsynced **before any price
read or authentication**. The source SQLite is opened with `mode=ro` and
`query_only=ON`, within one read transaction. Its panel must be exactly the
spent discovery panel, checked before any `scan_bars` query, and each of the
six codes must have a completed scan and one exact archived anchor.

Artifacts:

- `started.json`: fixed matrix, source commit, host, purpose, source path,
  start time and canonical plan SHA-256.
- `archive.json`: the six normalized archived OHLC/volume/turnover records
  and their canonical SHA-256; this is **not** the full portfolio dataset hash.
- `events.jsonl`: request starts, quotation GET attempts and validated
  quotation responses, with UTC times and fixed request parameters.
- `failure.json`: sanitized fixed refusal reason/current request/counters
  when a run stops after its start record; no upstream exception content.
- `result.json`: written only after all controls, archived-adjusted matches
  and exact repeats pass, with source/plan/input/output hashes and counters.

Every evidence write is flushed and fsynced. The final result is first synced
as `.result.pending`, then renamed to `result.json`; a failed final sync does
not publish success. Headers, credentials, tokens,
raw vendor payloads and exception strings are not persisted. CLI failures
print a fixed message rather than a chained traceback. Credentials come
only from the operator-entered environment; the existing session's protected
token-cache mechanism is unchanged. The new module is included in the
credentialed-client inventory of `test_kis_probe_cannot_trade.py`.

Success is deliberately `basis_certified: false`. Agreement of a fresh
raw/adjusted pair, its repeat, and these archived anchors is necessary but
insufficient: independently verify the vendor's adjustment convention and
that the actual held lot used the identified full dataset. A mismatch stops
the diagnostic. Repeated vendor answers are not independent evidence.

## Operator acquisition step after review

This is a manual action, not a command already executed. First place the
reviewed commit and its own synchronized environment in the **isolated** GCP
research checkout, and verify its clean source version. Never update the
collector checkout for this step. The operator opens a shell as
`minjun4897` outside the protected KRX session and enters credentials there,
following the existing manual procedure in `docs/paper-trading-runbook.md`:

```bash
(
  read -rs -p 'KIS_APP_KEY: ' KIS_APP_KEY
  echo
  read -rs -p 'KIS_APP_SECRET: ' KIS_APP_SECRET
  echo
  export KIS_APP_KEY KIS_APP_SECRET
  cd /home/minjun4897/research-checkouts/activity-discovery-20261005/python || exit
  .venv/bin/python -m data.kis_basis_probe \
    --scan-db /home/minjun4897/trading-engine/python/data/var/krx_scan.sqlite3 \
    --output-dir "/home/minjun4897/research-evidence/activity-basis-$(date -u +%Y%m%dT%H%M%SZ)"
)
```

The key values are typed into hidden prompts, not supplied in chat, process
arguments or shell history. Do not source/copy the collector's `.env`, add a
credential helper, stop collectors, alter cron or use the pre-2019 database.
A refusal is retained as evidence and investigated; it does not authorize
changing dates, ignoring a failed control or retrying a broader window.

## Remaining gates

No actual quotation acquisition or corrected research run is established by
this implementation. Task AI's eleven identity chains still have unresolved
point-in-time availability, the other 104 name candidates need classification,
and the full eligible pool and all held intervals (including closed and
successor lots) need dated instrument/action coverage. Net liquidation amounts,
actual payment dates, the Softcamp date discrepancy and vendor price bases
remain unresolved. Task AD's cost-floor stop before the comparison family
remains in force. Corrected accounting, preregistration and a durable trial
record must precede any later authorized sizing experiment.

## Local verification before review

The focused suite passed **210 tests, one existing skip**, covering the new
diagnostic, existing quotation client, no-trading/credential guards and
planning index. The diagnostic contributes 64 synthetic tests, including
real parser/transport code with mocked HTTP, 72 logical versus 74 retried
quotation attempts, protection before token preparation, all control/date/
repeat/archive failures, read-only SQLite and sanitized exceptions. Final
fsync and rename failure injections both leave no published `result.json`.
Removing the exact-date guard in memory made its regression test fail;
restoring the original function made the same case pass. No network request
or real price acquisition was used for these checks.

Independent review also found that the original local secret variable name
was outside the existing name-based print guard. It was renamed to
`app_secret`, so the new credential consumer is covered by that guard as
well as the synthetic secret-bearing exception tests. Full-suite/CI and
CodeRabbit outcomes are recorded by the PR; these focused checks alone do
not establish completion of those gates.

## CodeRabbit review corrections

Review of the first implementation identified two remaining boundaries.
Checking before `headers()` was insufficient if a cache read straddled the
session boundary: initial issuance and later refresh must also check
immediately before the token POST. The diagnostic binds the same guard to
its `KisSession` for its lifetime; `issue_token` invokes it after cache lookup
and request preparation but outside its network-error handler. Other callers
omit the optional callback and retain their existing token/cache behavior.
This still cannot cancel a request already in flight.

A failed final sync or rename also left a success-shaped temporary record.
Publication failures now attempt to remove `.result.pending` before
re-raising the original error. An `OSError` during this cleanup must not mask
that error. The absence of published `result.json` remains the success gate;
cleanup and `failure.json` cannot be guaranteed if the filesystem itself
refuses further writes. Never interpret a pending file as a completed run.

Six revision regression cases failed against the first implementation before
these fixes. Afterwards, the diagnostic's 70 tests and the existing 11 token
session tests all passed (81 total). The token-boundary tests exercise the real
CLI/session/issuance path with mocked cache and HTTP: initial issuance and
expired-token renewal both sent zero POSTs/GETs when cache lookup crossed into
the protected session. Valid cache hits and permitted token POSTs still work.
Cleanup-failure tests preserve the original exception object and successfully
sync the sanitized failure record when that independent write remains possible.

The second review found a filesystem durability gap: syncing file contents
does not durably create their directory entries. Evidence writes therefore
also sync the containing directory, and final rename is followed by a directory
sync before success is returned. The output directory must now have an existing
parent (the dedicated research-evidence root); recursive parent creation is
refused, avoiding unsynced ancestor directories. Creation of the fresh output
directory syncs that existing parent before any source price or authentication.

If final publication or its directory sync fails, both owned temporary and
final paths are removed on a best-effort basis and the directory is resynced,
without masking the original error. A failing filesystem may still prevent
cleanup or failure recording: accept only a successfully completed CLI run
with its result, and reject any evidence directory carrying a failure record.
The directory-descriptor sync implementation targets the supported Linux
environments (Ubuntu WSL and GCP), not native Windows Python.

The added sync boundary also prompted a final session check after writing each
quotation-attempt event. The GET callback now runs after request/header
preparation and immediately before the network call. If its evidence sync or
last session check fails, that unsent attempt is removed from the final HTTP
counter and a sanitized cancellation event is attempted. Thus an intent event
may be followed by cancellation; interpret final counters together with those
events, not by counting intent lines alone.

Five directory-durability regression cases failed before the second-review
fix and passed afterwards. Two additional tests move into the protected
session during the attempt event's file/directory fsync and verify zero GETs,
zero final HTTP attempts and a cancellation event. The resulting diagnostic
suite has 77 passing tests; together with the existing token-session suite,
88 tests passed. Original publication/cleanup fault injections also still pass.
