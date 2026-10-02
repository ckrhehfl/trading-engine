# External Review Phase 0 — the resources, measured facts and rules, with our own conclusions deliberately withheld

**Purpose.** A self-contained description of this project's *resources, measured
facts and evaluation rules*, written to be handed to an external reviewer (a
different model, or a person) who will design a strategy-search system without
being told what we think the answer is.

**What makes this document unusual, and why it is built this way.** It
deliberately **withholds our own conclusions**: what we tried, what we believe
went wrong, and what we think is promising. The reasoning is this project's own,
from CLAUDE.md's change-check section — *a verification that shares an
assumption with its implementation confirms the misunderstanding rather than
catching it.* An external design that is first shown our diagnosis can only
agree with it, and agreement under those conditions is not evidence. So the
reviewer gets the inputs and must derive its own conclusions, which we then
compare against ours.

**The rule applied throughout: give the inputs, withhold the inferences.** Where
a number is measured, it is here. Where a number follows from those measurements
by arithmetic, it is **not** here — whether the reviewer performs that arithmetic
is itself one of the things being tested. Two concrete instances, named so a
future session does not "fix" them by adding the missing halves:

- Detection floors and the formula producing them are given. Whether that floor
  is high or low *relative to a realistic edge* is not.
- The selection-trial count `N` and the DSR definition are given. The Sharpe a
  result would need in order to clear DSR 0.95 at that `N` is not.

**Sequence this belongs to.** Phase 0 is this document. Phase 1 hands it over
and asks for a search-system architecture. Phase 2 reveals what we actually
tried and asks whether the proposed design predicts those outcomes. Phase 3
reveals our own diagnosis and compares. Each phase's prompt is its own document.

**Measured 2026-10-01** unless a line says otherwise. Figures taken from the
databases of record, not from CLAUDE.md, because several of CLAUDE.md's carry
earlier observation dates.

---

## 1. What the system is

A personal, single-operator, institution-style automated trading system. Two
planes: a **Java trading plane** (order management, risk gateway, execution,
venue adapters) and a **Python research plane** (data collection, backtesting,
walk-forward validation, metrics). They communicate through versioned JSON
schemas with cross-language compatibility tests.

It currently trades nothing. Two paper-trading loops exist and are stopped by
operator decision; a third (Korean) has a kill switch that trips
unconditionally at construction.

**Current instrument scope: Korean domestic equities (KRX).** BTC/USDT futures
was the original scope and is set aside, not abandoned — all of its data and
code is retained.

### Permanent non-goals

These are architectural commitments, not current limitations. A design that
requires one of them is out of scope:

- High-frequency trading, co-location, tick-level or trade-level strategies.
  The finest bar this project will use is **1 minute**.
- Multi-user SaaS; Kubernetes; Kafka/Aeron/Chronicle Queue.
- Any path where Python places a live order directly, or where the Java risk
  gateway can be bypassed.

### Infrastructure that exists and can be built on

- **`ExchangeAdapter`** — an interface. Two implementations (BingX, KIS). A new
  venue is a new adapter and touches nothing else.
- **`OrderExecutor`** — exactly two implementations, by invariant: an internal
  simulator (`PaperBroker`) and a venue-agnostic `ExchangeOrderExecutor`.
- **`RiskGateway`** — every order passes through it. Notional limits, leverage
  limits, quantity rejection hooks.
- **Backtest engine** — bar-by-bar, look-ahead safe by construction (a strategy
  is only ever shown bars up to and including the current one). Supports
  single-intent and multi-intent-per-bar strategies.
- **Position model with legs** (`metrics.book`) — multiple independent legs,
  closed by explicit id rather than FIFO, gross and net exposure reported
  separately. Built because a single net position cannot express "close only the
  short and keep the core".
- **Walk-forward harness** — rolling train/validate folds, configurable
  geometry, per-fold metrics.
- **Metrics** — Sharpe, Sortino, Calmar, max drawdown, profit factor,
  expectancy, MAE/MFE distributions, turnover, equity curve reconstruction,
  funding P&L.
- **Experiment log** — every backtest run appended to `runs/experiments.jsonl`
  with parameters, results and timestamp. 1,883 run records, 9 holdout-access
  records.
- **Statistical tooling** — PSR, Deflated Sharpe, detection floor, permutation
  tests, information coefficient with non-overlapping sampling, block
  bootstrap, Benjamini-Hochberg, Stouffer combination.
- **Conclusion and change checks** — nine automated checks on research
  conclusions (overlapping windows, unswept parameters, unattainable criteria,
  cross-sectional clustering, …) and eight on engineering changes. Each raises
  rather than warns.

**Infrastructure that could be built but does not exist**, listed because a
design may legitimately require it: per-candidate equity-curve retention;
CSCV/PBO; a canonical options symbol format; a scheduled retraining pipeline;
wire-level price guards on market orders; timestamped price feeds.

---

## 2. Data inventory, measured

### Korean equities — the current scope

The database of record is on a GCP instance running UTC.

| series | coverage | size |
|---|---|---|
| **Full-universe daily panel, 2019+** | 3,043 codes complete; 434 never served, 1,152 outside window, 9 failed | **4,598,643 bars** |
| Daily, 19-name focus set | 2019-01-02 .. 2026-10-01 | 35,420 bars |
| **1-minute, 18 names** | 2025-09-04 .. 2026-10-01 | 1,772,879 bars |
| Delisted-issue register | 4,191 distinct codes | 37,680 snapshot rows |
| Listed-universe snapshots | 4,430 distinct codes, 13 snapshot dates | 57,244 rows |
| 투자자별 매매동향 (per-stock investor flow) | 2026-08-03 .. 2026-10-01, **41 trading days**, 18 names | 26,758 rows |
| Futures order-book samples (L1) | 2026-09-18 .. 2026-10-01, 18 underlyings + 16 futures codes | ~100 samples per name |

**Which windows are still available for selection.** This is a necessary fact
rather than a conclusion, and without it a design cannot be executed here at all:
under §5's rules a window that has already been selected on cannot produce a
promotable result, so a procedure pointed at one is unrunnable. What is given is
only *whether* a window has been selected on — not what was run there or what
came of it.

| window | selected on? |
|---|---|
| BingX 1h, 15m, 1d | **yes** |
| Binance 1d (2017–2021) | **yes** |
| Binance futures 1m | **yes** |
| BingX 1m | **yes** |
| KRX daily 2019–2026 | **yes** |
| KRX intraday (1m) | **yes** |
| KRX investor flow, first 33 of its 41 days | **yes** |
| KRX investor flow, the 8 most recent days | no |
| KRX futures order-book samples | no |

A window that has been selected on remains valid for **reproducing** a logged
result, **diagnosing** a mechanism, and **infrastructure** testing. It is closed
only to selecting something for promotion.

**KIS daily history reaches back to 1991-08-28.** The store currently holds
2019-01-02 onward for the focus set and the 2019+ full-universe panel above.
Probes confirmed the pre-2019 era is served, for delisted names as well as
survivors — 삼성전자 1996 Q1 returned 97 bars, 조흥은행 (merged away 2006)
returned 72 bars in 1998 Q1, 한진해운 (delisted 2017) returned full depth back
to 2010. Each probe carried a nonsense-code negative control that returned zero
in the same run.

**Data quality before 2000 is unverified** beyond those probes.

### BTC/USDT and macro — the set-aside scope

| series | coverage | bars |
|---|---|---|
| Binance futures BTCUSDT 1m | 2019-09-08 .. 2026-08-25 (6.96y) | 3,661,780 |
| BingX BTC-USDT 1m | 2024-11-30 .. 2026-08-24 (1.73y) | 910,040 |
| BingX BTC-USDT 1h | 2024-04-27 .. 2026-07-26 (2.24y) | 19,678 |
| BingX BTC-USDT 15m | 2025-11-16 .. 2026-07-26 | 24,191 |
| BingX BTC-USDT 1d | 2021-05-14 .. 2026-09-04 | 1,940 |
| Binance BTCUSDT 1d | 2017-08-17 .. 2021-05-13 | 1,366 |
| ETH-USDT 1d | 2021-05-14 .. 2026-08-28 | 1,930 |
| Funding rates | BTC-USDT | 6,199 |
| Positioning (OI, long/short ratios, taker flow — 10 metrics × 2 symbols) | 2026-08-08 .. 2026-09-17, **collection stopped** | 165,446 rows |
| Macro (FRED) | DGS10 16,848 · DFII10 6,151 · DTWEXBGS 5,365 · SP500 2,610 | 30,974 |

**Binance geo-blocks the GCP instance entirely** (HTTP 451 on every endpoint),
so Binance collection can only run from a Korean IP on a local machine. KRX/KIS
works from the instance. A collector's home is therefore chosen per venue.

**Binance positioning cannot be backfilled** — rolling ~30-day windows — so the
gap from 2026-09-17 is permanent. 투자자별 매매동향 likewise: the endpoint
serves a rolling 30-row horizon with **no date parameter at all**. An order book
cannot be backfilled at any price: there is no historical endpoint, so a sample
not taken is gone the same second.

---

## 3. Costs, measured

### Korean equities

**Round trip ≈ 30–33 bp**, of which **20 bp is 거래세** (transaction tax, sell
side, statutory). The remainder is commission plus measured half-spread on
liquid names.

**No retail short selling** on cash equities for the era this project can reach.
Single-stock futures exist from the 2000s and allow short exposure, but with a
contract-based instrument whose per-stock multiplier this project has not yet
confirmed, and whose contract codes bear no arithmetic relation to the
underlying.

### BTC/USDT perpetual futures

- **Taker fee 5 bp** per side (both venues' published VIP0 for USDT-M
  perpetuals). Their *spot* VIP0 taker fee is twice that.
- **Measured half-spread ≈ 1 bp**, from real L1 samples. An earlier assumption
  of 10 bp was calibrated against a cited "typical spread" that proved two to
  three orders of magnitude too wide for this instrument.
- **Market impact is a real venue difference**: negligible on Binance at canary
  size, about one tick on BingX, whose best ask is thin enough for a canary
  order to clear it.
- Funding is paid or received on perpetuals; the sign convention is
  `payment = -sign(qty) × |qty| × markPrice × fundingRate`, computed against the
  funding row's own historical mark price.

### What the fill model does and does not simulate

`GUARDED_MARKET` orders receive slippage. **`LIMIT` orders fill 100% at the
exact limit price the instant a bar's high or low touches it** — unaffected by
any slippage parameter. The engine has a working limit branch; this project
currently restricts validation to market orders because the fill-on-touch model
is optimistic and has not been hardened with order-book depth, partial fills or
queue position.

---

## 4. Market facts, measured — each one cost a real error to learn

Grouped by what they constrain.

### Data integrity

- **A zero-row answer is ambiguous by construction.** A nonexistent code, a
  delisted name, an out-of-range date and an expired contract all return the
  same success status with zero rows. **Any probe therefore needs a
  nonsense-code negative control in the same run**, or a backfill records
  nothing and reports a clean run.
- **Row caps are per-endpoint, not per-venue, and truncate silently** — success
  status, newest rows kept. One pipeline whose guard assumed a single shared cap
  let a truncated window through. A response at or over the cap that claims to
  cover a whole range is a failure, not data.
- **The split-adjustment parameter is the most dangerous input here, and the
  venue's own published sample defaults to the wrong value.** Unadjusted, a 50:1
  split reads as a near-total single-day collapse — which a momentum signal takes
  for a crash. The adjustment flag is therefore a required argument with no
  default. Separately: **splits are adjusted, dividends are not**, so what comes
  back is a price return, not a total return.
- **Unit conventions are undocumented and differ between endpoints** on the same
  venue: turnover on one series is denominated in millions of won, on another in
  won. Reading the first as the second is off by a factor of a million — an error
  that does not look wrong, merely small.
- **Retry only from an allowlist.** One endpoint intermittently returns a real
  success status carrying an internal error code for a window that answers
  normally next attempt, and at the measured rate a multi-call backfill cannot
  complete without retrying it. **Every other non-zero code met here is
  permanent** — wrong endpoint id, wrong market division, nonexistent code. A
  temporary-IP-ban signal is explicitly non-retryable.
- **Gap-blindness is an open exposure.** Neither fold generation nor bar
  iteration detects a timestamp gap — both are positional arithmetic — so the bar
  after a gap is silently treated as one interval later. Bounded: exactly one
  signal position per gap is affected. Daily and hourly data have no gaps;
  1-minute data introduces this fresh.

### Universe construction

- **A halted name's bar looks like a quiet day, not a stoppage**: open = high =
  low = close, zero volume, zero turnover, for as long as the halt lasts. They
  are stored, because they are what the tape said, and counted separately.
- **Counting bars measures listing, not liquidity.** A bar prints for every
  session a contract is listed, carrying zero volume if nobody traded. "No bars
  in the window" means *not yet listed*.
- **The exchange's security-group code does not separate common stock from
  preferred.** What separates them is a position within the 12-character ISIN,
  whose third character also gives the instrument class (stock/ETF/REIT vs ETN vs
  fund vs depositary receipt). The country prefix is part of the rule: a
  foreign-domiciled listing carries an ISIN where that position means nothing.
- **Three filters and none subsumes the others**: issue type does not separate
  stock from ETF; the group code does not separate common from preferred; **and a
  SPAC passes both**, being legally an ordinary corporation. SPAC and REIT
  detection falls back to anchored name matching, which is weaker evidence and is
  kept structurally separate for that reason. An unbranded delisted ETF would
  still pass.
- **Instrument type is unknown for delisted names.** The register publishes no
  type field, so a historical pool carries preferred shares and SPACs. The
  delisted-code list is a floor, not a filter.
- **Stock codes are no longer all numeric** — alphanumeric codes are now issued,
  so any digit-only validation is wrong. The delisted side still filters on
  digits, which is correct today and will silently drop the first alphanumeric
  code that delists.
- **Delisting is not failure.** Measured exits: 36,700 and 8,660 won
  (take-privates), and two bank mergers. Booking −100% on delisting is wrong in
  the opposite direction to survivorship bias, and by more on a tender offer than
  survivorship bias costs. **An exit price comes from the last bar, never from
  the delisting event.**
- **Membership comes from the price series, never from a delisting date**, and an
  absent bar is not evidence of absence unless the fetch that produced it is
  known complete.
- **Expected trading days must come from an index series, not arithmetic.** A
  market trading ~245 days a year shows false gaps by the hundred against a
  calendar sequence. An index prints exactly when the market is open, needs no
  holiday table, covers moving lunar holidays, and separates a market closure
  from a stock-specific halt. **The check must run both directions** — a symbol
  printing on a day the reference lacks means the reference is truncated.

### Microstructure

- **Intraday bar timestamps are not uniformly on the minute grid.** One probed
  date returned second-offset stamps where every other returned :00.
- **A quote request outside market hours returns the LAST book, not an empty
  one**, so any spread or depth collection must gate on the continuous session
  rather than trusting the response.
- **The futures book's field names are asymmetric** — prices carry a prefix the
  quantities do not — and the symmetric guess returns null at every level, so a
  caller that coalesces records a book with prices and no size.
- **A Korean trading date maps exactly onto UTC midnight** (session opens 09:00
  KST, KST is UTC+9), so no special case is needed for daily grid alignment.
- **Session structure, which bounds when anything can execute.** Cash equities
  trade 09:00–15:30 KST, of which **09:00–15:20 is the continuous session** and
  15:20–15:30 is a closing call auction with **no continuous order book**. Index
  futures run 08:45–15:45, shortened to 08:45–15:20 on a contract's final trading
  day. The night session (18:00–06:00 KST) is not supported here. In UTC — which
  the instance runs — the continuous cash session is 00:00–06:20.
- **The trading calendar fails closed**: any date whose holiday or
  final-trading-day status cannot be positively confirmed from a committed static
  fixture resolves to **closed**, never open. **Moving lunar-calendar holidays
  are a known unresolved gap** — no JDK chronology expresses them — which is part
  of why expected trading days are taken from an index series rather than a
  holiday table.
- **Turnover is returned on the same call as the prices.** `close × volume` is
  not a substitute.
- **The three investor types do not sum to market turnover** — the residual is
  other corporations, domestic residents and government, which this endpoint does
  not break out. A "share of volume" from the three alone is overstated.

---

## 5. Evaluation rules — these are binding, not advisory

A design that cannot be evaluated against these cannot be run here.

### Walk-forward is mandatory

No strategy is eligible for paper trading without **rolling train/validate
windows** — not a single train/test split. A single split cannot distinguish a
real edge from a result that fit one historical window.

### The Eligibility Bar

Fold geometry in use, each derived from that timeframe's measured retention:

| timeframe | train / validate / step (bars) | folds |
|---|---|---|
| 15m | 8640 / 2880 / 2880 | 3 |
| 1h | 2160 / 720 / 720 | 19 |
| 1d | 90 / 60 / 60 | 12 |

Criteria:

1. **Fold consistency** — 80–90% of folds with positive annualized Sharpe. Not
   literal 100%: a genuinely strong 80%-true-edge strategy clears a 19/19 sweep
   only ~1.4% of the time, so demanding 100% measures luck.
2. **Aggregate significance — both** a binomial sign test on fold win/loss
   against p=0.5, **and** a **Deflated Sharpe Ratio ≥ 0.95**. DSR is computed on
   daily-resampled returns against the **project-level selection-trial count**
   and the variance of that trial set's Sharpe estimates. A one-sample t-test
   may be reported but is not a pass criterion — it has no notion of how much
   searching produced the result.
3. **Minimum 8–10 folds** for credibility.
4. **Max drawdown ≤ 20–25%**, per-fold and aggregate.
5. **Profit factor ≥ 1.3–1.5.** The mean is what is scored; the median is
   reported beside it and flagged when they disagree.
6. **Minimum trade count, scaled to the strategy's own frequency**:
   `max(30, min(100, floor(evaluated_days / 20)))`. A run below the floor is
   reported `INCONCLUSIVE-DATA-LIMITED` — neither a pass nor a fail, and not
   evidence against the strategy.

**The selection-trial count is `N` = 129** research trials (plus 19
infrastructure trials and 66 reproductions, which do not count). By family:
trend-momentum 97, funding 8, mean-reversion 8, btc-scalping 5, macro-conditioned
4, volume 4, trade-management 2, and **one single-member family that is a test
artifact** (a suite once appended to the live log) — summing to 129. The
artifact is counted anyway, because an inflated `N` can only lower a later DSR
and the log is append-only.

**The standard deviation of those trial Sharpes is 1.2665**, over the **126 of
those 129 trials that have a defined Sharpe** (three do not: two have no
computable fold Sharpe and the artifact has none). Annualized units. A selection
correction needs both that dispersion and `N`, computed over **the same
population** — this figure therefore excludes the separate `infrastructure`
purpose, exactly as `N` does.

**DSR must fail closed on `N`.** A strategy whose family cannot be resolved
resolves to its own single-member family, which *understates* `N` — and a smaller
`N` inflates DSR, making the gate weaker than intended. Such a run is reported
unevaluable for aggregate significance, never passed on a fallback count.

### Holdout discipline

- **A holdout is accessed exactly once, ever**, and must be the only access on
  record for that strategy id.
- **The specification is committed before access** — every parameter fixed.
  Anything added after the data is seen voids the registration.
- **The criteria are also pinned before access**, with the revision date they
  come from. A later change to a threshold is not applied retroactively. *A
  holdout judged against criteria selected after seeing it is not a holdout.*
- **Single-window variant of the bar** (fold clauses have no meaning on one
  window, and must not be simulated by chopping it into pseudo-folds): **PSR ≥
  0.95** on daily-resampled returns; drawdown, trade-count and profit-factor
  floors unchanged; and the observed Sharpe must exceed **that window's own
  detection floor**, stated explicitly, or the run is reported **not powered to
  confirm**.

PSR rather than DSR, deliberately: a holdout was never searched over, so there
is no selection bias to deflate.

### Detection floor

`floor = 1.6449 / sqrt(years)` on daily-resampled returns — the annualized
Sharpe below which a result cannot be distinguished from noise at one-sided
α=0.05. **It depends on calendar span, not bar count**, so a finer timeframe
buys no statistical power.

Measured floors, by window:

| window | span | floor |
|---|---|---|
| 15m research | 0.57y | 2.18 |
| 1h research | 1.84y | 1.21 |
| 1h trailing holdout | — | 2.57 |
| 1d early-window holdout | 2.95y | 0.96 |
| Binance spot 1d | — | 0.85 |
| BingX 1m | 1.73y | 1.25 |
| Binance futures 1m | 6.96y | 0.62 |
| KRX daily 2019–2026 | 7.71y | 0.592 |
| KRX intraday | 1.04y | ~1.6 |
| KRX futures quotes | 4 sessions | 14.06 |

### Statistical rules that bind any measurement here

Each was adopted after this project made the error it prevents.

- **A statistic over overlapping windows is not a statistic over independent
  observations.** Deduplicate to non-overlapping samples before reporting any t,
  p or standard error — or state explicitly that the figures are uncorrected.
- **A p-value over observations that share a session is not a significance
  test.** Ten names measured at the same instant share that instant's
  market-wide move, so pooling them as ten draws understates the standard error
  however disjoint their holding windows are. Compute over **sessions**, or
  block-bootstrap whole sessions, and **report the ratio of corrected to naive
  standard error**. The correction is **not** a uniform haircut: it can move a
  p-value in either direction.
- **A permutation null's spread is the standard error of its own statistic, not
  of the arm it is compared against.** They coincide only when the null is
  calibrated to that arm, and "matched on a prior-window statistic" does not
  calibrate a null to an event defined by a burst in that same statistic.
  **Report null_sd / se on every permutation test.**
- **Compute the family's detectable effect before specifying it**, from the event
  arm's own dispersion, and decide before any data is seen whether the family can
  detect an effect of the size that would matter.
- **Name the unit that is actually independent before quoting any figure that
  assumes independence.** The general form of the three rules above.
- **A criterion that cannot be satisfied is not a criterion.** A bar unreachable
  at a given sample size is evidence about the criterion, not the strategy, and
  is reported UNINFORMATIVE rather than FAIL.
- **Never conclude about a domain from one parameter setting — sweep it first.**
- **Report stability by year, not only in aggregate**, and treat an edge
  concentrated in one regime as unconfirmed until shown outside it.

### Risk parameters (changing these needs operator approval)

Canary tier: base leverage 1x, max 2x, max order notional 2% of capital, daily
loss limit −0.5%, weekly −1.5%, monthly −3%, hard stop −4%.

Stable tier: base 2x, max 3x, max notional 5%, daily −1%, weekly −3%, monthly
−6%, hard stop −8%, emergency −10%.

### Operational gates before anything trades real money

Two gates, both required, plus separate live-entry criteria on top.

**Gate A — operational readiness** (proves the system, not the strategy; mock
signals explicitly allowed): 15 consecutive days of operation at ≥99% uptime
measured from the loop's own tick counters; ≥200 order events through the full
intent → pipeline → risk gateway → order → executor path; zero critical crashes,
duplicate orders, position mismatches or risk-gateway bypasses; no missing daily
reports; kill switch verified by a deliberate trip and recovery; ambiguous-
submission recovery verified.

**Gate B — strategy edge**: not calendar-bound, because calendar time cannot
manufacture trades a strategy does not take. Evidence from pre-registered
holdout confirmations, walk-forward folds meeting the Eligibility Bar, or
accumulated paper trades. **A strategy is never blocked here for a trade count
its own frequency makes impossible to reach.**

---

## 6. What is being asked

Design the **system** by which this project should search for a tradeable
strategy — the procedure, not a strategy.

The framing is deliberate. What matters is how candidates are generated, in what
order, against which data, with what stopping rules, and how a result is judged
— including how the search itself is accounted for, given that every look costs
something under the rules in §5.

Constraints to respect: the non-goals in §1, the costs in §3, the market facts
in §4, and the evaluation rules in §5. Resources available are in §1 and §2, and
infrastructure that does not yet exist may be assumed buildable if the design
says what it needs and why.

**What this document withholds, stated plainly so its absence is not mistaken
for its not existing**: this project's strategy attempts and their outcomes, its
own diagnosis of why those outcomes happened, and what it currently believes is
worth trying next. Those come in later phases, against which the design produced
here will be compared.
