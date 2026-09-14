# Multi-Asset TSMOM Task F — what the portfolio run still needs, and why it is not written yet

**Status**: not started, deliberately. The universe, the window and the
data are in place; **two inputs are missing and neither may be invented.**
This document names them so the gap is visible rather than discovered
half-way through a run.

Nothing here has scored anything. No backtest of any kind has been run
against KRX data.

---

## 1. What is ready

| | |
|---|---|
| Universe | [`configs/research/universes/kr10.json`](../configs/research/universes/kr10.json) — 10 equities + KOSPI200 + KOSDAQ150, resolved by the rule committed at `d57a4e3` and executed at `85cdfab` |
| Window | [`configs/research/holdout_krx_1d.json`](../configs/research/holdout_krx_1d.json) — 2019-01-01 onward, whole-window holdout |
| Data | 1,882 bars per member, 2019-01-02 … 2026-09-01, `--verify` exit 0 against the index calendar |
| **Detection floor** | **0.5942** — `1.6449/√7.66`. The lowest this project has held: BingX 1d holdout 0.9567, Binance futures 1m 0.623 |

The floor matters more than it looks. At 0.5942 a real 0.6–0.8 edge is
**detectable**, which has not been true of any window this project has
used. It is also why the two missing inputs cannot be fudged — a window
this well powered will produce a confident answer either way, and a
confident answer built on invented costs is worse than no answer.

## 2. Missing input 1 — the cost constants, which must be sourced

`procedure.fee_bps` and `procedure.slippage_bps` are required fields, and
**this project has a standing rule against inventing them.** CLAUDE.md's
scalping section records `SLIPPAGE_BPS` being revised from 10 to 1 only
after `scalp-s9` measured 1.5M real direction-flips, and states plainly:
"Revising either constant needs its own justification here, never silent
per-strategy tuning to make a marginal candidate pass."

The BingX numbers (`FEE_BPS=5`, `SLIPPAGE_BPS=1`) are **not** transferable.
They are crypto perpetual taker fees on a 24/7 venue. KRX single-stock
futures have a different fee structure entirely — exchange fee plus
brokerage, no securities transaction tax on futures (unlike spot), a
different tick regime, and a 6h45m session.

What is needed, in the same shape `scalp-s9` produced:

- **KRX's own published derivatives fee schedule** for 주식선물, and KIS's
  brokerage commission for the same, both cited.
- **A tick-size-based slippage floor** per member. Single-stock futures
  tick sizes vary by price band, so one number for all ten needs
  justifying or replacing with per-member values.
- A **disclosed conservatism margin**, as `SLIPPAGE_BPS = 1` carries
  against a measured 0.015bps half-spread.

Until then the pre-registration cannot be written, because writing it
means committing numbers before the run and committing a guess is worse
than committing nothing.

## 3. Missing input 2 — a portfolio runner

Every runner in `python/research/` evaluates **one symbol**. The
pre-registration schema mostly accommodates a portfolio — probed directly
against `research.preregistration.load_preregistration`:

| Shape | Accepted? |
|---|---|
| extra keys under `data` | **yes** |
| extra keys under `procedure` | **yes** |
| `data.symbol` as a universe identifier string | **yes** |
| `data.symbol` as a **list** | **no** — must be a non-blank string |
| new **top-level** key (e.g. `portfolio`) | **no** — unknown top-level keys are rejected by design |

So the registration can carry `data.symbol = "KRX:KR-10"` plus a
`data.universe_config_path`, with no validator change. **The runner is the
real work**, and it is genuinely new:

- Load each member's series, run `daily_tsmom_ensemble.py` **unchanged**
  per member (its zero-fitted-parameter property is what the Paper
  Trading Policy Exception rests on; the portfolio layer belongs above
  it, never inside it).
- Aggregate equal-weight into one equity curve, then compute PSR,
  drawdown, profit factor and trade count **on the portfolio**, not as an
  average of per-member statistics.
- Handle a member with no position on a given day (the strategy is often
  flat) without silently reweighting the others.

`data.symbol` being a universe name also means the **standard
single-symbol holdout loader will fail** on it — deliberately, since it
would otherwise have to guess. The runner supplies its own loading.

## 4. The premise that must be stated in the registration, not discovered after

**Futures tradeability across the window is unverified and unverifiable
from KIS.** The endpoint serves only currently-listed contracts: every
2018–2025 expiry tried returns `rt_cd=0` with zero rows (MS-B §2.1). So a
result asserting futures execution over 2019–2026 rests on the assumption
that these ten names had listed, liquid futures throughout — plausible,
being the most-traded names on the exchange, and **plausible is not
verified**.

It belongs in `notes` on the registration, before the run, as a named
limitation of whatever the result turns out to be.

## 5. Criteria, which are pinned and not open

These are not decisions left to make; they are the Eligibility Bar's
single-window variant, to be copied into the registration verbatim:

- **PSR ≥ 0.95** on daily-resampled returns against a zero benchmark —
  PSR and not DSR, because the window was never searched over.
- Max drawdown ≤ 20%, profit factor ≥ 1.3.
- Trade count floor `max(30, min(100, ⌊1882/1/20⌋))` = **94**.
- Observed Sharpe must exceed **0.5942**, or the run is reported as *not
  powered to confirm* regardless of the rest.
- `total_candidates: 1`, `free_parameter_count: 0`.

## 6. Order of work

1. Source the cost constants (§2) and record the citations.
2. Write the pre-registration and commit it — **before** the runner exists,
   so the commit ordering is checkable the way MS-E's was.
3. Build the portfolio runner (§3).
4. Execute once. Report whatever comes out.
