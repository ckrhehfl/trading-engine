# Research Direction Task R — the universe, re-selected on futures liquidity

**Measured 2026-09-16** against the live KIS paper host. Modules:
`data/kis_futures.py`, `research/krx_futures_universe.py`. Artifact:
`runs/krx_futures_liquidity.json`. Reproducible — for a few more months —
with

```
python -m research.krx_futures_universe
```

Closes [`rd-q`](rd-q-which-korean-instrument.md) §8 item 2: *"Re-select
the universe on futures liquidity across the ~265 single-stock futures
underlyings, not on spot 거래대금. That is a selection rule and needs to
be fixed before it is applied, exactly as `ms-e` fixed KR-10's."*

---

## 1. The headline

> **The KR-10 universe and the futures-liquidity universe share two
> names.** Ranked on median daily front-month futures 거래대금 over
> 2026Q1, KR-10's members land at **1, 2, 15, 16, 31, 32, 33, 63, and
> twice nowhere at all** — 네이처셀 and HLB had **no listed futures
> contract** during the window.
>
> **Day-one selection is meaningful here, and that had to be measured
> rather than assumed**: the 2026Q1 ranking and the 2026Q2-to-date
> ranking correlate at **Spearman +0.954** across 238 names, with **7 of
> the top 10** shared. A low figure would have said no point-in-time
> futures-liquidity selection is possible at all.
>
> **The instrument is affordable, which was not obvious.** The selected
> ten need accounts of **₩42M to ₩883M** under the 2% canary limit —
> median **₩153M** — against the **₩13.3bn** rd-q computed for one
> KOSPI200 index futures contract.

### 1.1 The selected universe

Median daily front-month futures 거래대금 over 2026Q1; contract notional
and the implied account at the **latest** close (2026-09-16).

| # | code | name | median 거래대금 | contract ₩ | min account ₩ | fwd rank |
|---|---|---|---|---|---|---|
| 1 | 000660 | SK하이닉스 | 3,772.1bn | 17,650,000 | 882,500,000 | 1 |
| 2 | 005930 | 삼성전자 | 3,354.7bn | 2,500,000 | 125,000,000 | 2 |
| 3 | 005380 | 현대차 | 570.5bn | 3,605,000 | 180,250,000 | 5 |
| 4 | 034020 | 두산에너빌리티 | 283.7bn | **839,000** | **41,950,000** | 7 |
| 5 | 006400 | 삼성SDI | 180.5bn | 5,460,000 | 273,000,000 | 6 |
| 6 | 042700 | 한미반도체 | 144.1bn | 2,265,000 | 113,250,000 | 8 |
| 7 | 035420 | NAVER | 128.7bn | 2,015,000 | 100,750,000 | 13 |
| 8 | 000270 | 기아 | 128.1bn | 1,204,000 | 60,200,000 | 23 |
| 9 | 402340 | SK스퀘어 | 119.5bn | 10,120,000 | 506,000,000 | 4 |
| 10 | 012450 | 한화에어로스페이스 | 107.6bn | 10,580,000 | 529,000,000 | 11 |

All ten traded on **59 of 59** sessions in the window.

**Two independent cross-checks against rd-q**, which measured the live
book on 2026-09-16 by a completely separate endpoint: this ranking's
latest closes give 삼성전자 **₩2,500,000** and SK하이닉스 **₩17,650,000**
a contract, matching rd-q's table exactly.

### 1.2 Where KR-10 landed

| code | name | futures rank (of 241 eligible) | median 거래대금 |
|---|---|---|---|
| 000660 | SK하이닉스 | **1** | 3,772.1bn |
| 005930 | 삼성전자 | **2** | 3,354.7bn |
| 000720 | 현대건설 | 15 | 96.3bn |
| 009150 | 삼성전기 | 16 | 93.6bn |
| 051910 | LG화학 | 31 | 53.0bn |
| 068270 | 셀트리온 | 32 | 49.9bn |
| 064350 | 현대로템 | 33 | 49.1bn |
| 207940 | 삼성바이오로직스 | 63 | 17.4bn |
| 007390 | 네이처셀 | — | **no contract listed in the window** |
| 028300 | HLB | — | **no contract listed in the window** |

**rd-q's four spread-winners were 삼성전자, SK하이닉스, 현대건설 and
LG화학 — ranks 2, 1, 15 and 31 here.** Spread and turnover agree at the
very top and diverge quickly below it, which is itself worth knowing: a
tight quoted spread at one moment is not the same measurement as sustained
turnover, and rd-q had only the first.

## 2. Why the universe had to be re-selected at all

rd-q measured spot and futures round trips across the KR-10 and found
**neither instrument wins outright**: futures beat spot for four names by
~23bp each and lose for six by 10 to 118bp. The split is futures
liquidity, and the winners' futures traded **27× the cumulative volume**
of the losers' — volume, not resting depth; see rd-q §1.

KR-10 was selected by `ms-e` on **spot** 거래대금. So the universe was
chosen by a statistic that does not predict the thing that decides
whether its members are tradeable at all — six of ten are not, on the
instrument that avoids Korea's 20bp sell-side tax.

## 3. What had to be established first, and none of it was documented

Four facts about KIS's single-stock futures endpoints, all measured, none
of them in CLAUDE.md before this task.

### 3.1 History exists, on an endpoint nobody here had reached

`GET /uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice`,
`tr_id` **`FHKIF03020100`**, `FID_COND_MRKT_DIV_CODE` **`JF`**. It
returns daily OHLC plus `acml_vol` and `acml_tr_pbmn` for a single-stock
futures contract.

`kis_probe.py`'s `probe_single_stock_futures` had already tried this
endpoint in Phase 0 and reported *"no (tr_id, code) combination tried
returned rows"*, correctly declining to conclude that single-stock
futures were unavailable. It was right to decline: the endpoint works.
What it had wrong was the **code**, and it said so — *"the endpoint family
and symbol format are both unconfirmed."*

Sending `F` instead of `JF` returns `rt_cd=0` with **zero rows**, which is
indistinguishable from a contract that does not trade.

### 3.2 A contract code cannot be derived from the underlying

삼성전자 `005930` is **`A11610`**. SK하이닉스 `000660` is **`A50610`**.
The two-character issue id between them is KIS's own and appears nowhere
else in this project's data.

**This trap was sprung again while writing this task.** `A11710` was
guessed for `000660`, returned `rt_cd=0` with zero rows, and was briefly
read as "this name's futures do not trade" — the same shape as rd-q §4's
six guessed index codes, one document later.

So the master file `fo_stk_code_mts.mst.zip` is the only source, and
`FuturesMaster.contract_code` refuses to construct a historical code
until the construction rule has **reproduced every expiry the master
itself lists** for that underlying. The encoding is `A` + issue id + the
year's last digit + the two-digit month; the guard is what makes relying
on it defensible rather than lucky.

### 3.3 The history decays, and a contract leaves all at once

| expiry | rows | span |
|---|---|---|
| 2026-03 | 100 (capped) | 2025-10-15 … 2026-03-12 |
| 2026-02 | 62 | 2025-11-14 … 2026-02-12 |
| **2026-01** | **62** | **2025-10-10 … 2026-01-08** |
| **2025-12** | **0** | — |
| 2025-11 | 0 | — |
| 2025-10 | 0 | — |
| 2025-09 | 0 | — |

> **Not truncation — nothing.** The 2025-12 contract traded into December
> and has bars well inside the range the 2026-01 contract is still served
> over, so this is **contract-level**, not a rolling bar window. A
> contract is served, and then its entire series is gone.

Consequences:

- the oldest single-stock futures bar reachable on 2026-09-16 is
  **2025-10-10**;
- the **front-month** series — the one a trader would actually have been
  in — is reconstructible only from **2025-12-12**, the session after the
  2025-12 contract expired, because every earlier front month is one of
  the contracts that has already gone;
- roughly one more month of it disappears every month.

**This is a second decaying Korean window**, alongside the intraday one
[`rd-o`](rd-o-krx-intraday-backfill.md) was opened to rescue. It is why
this task writes a committed artifact carrying the **raw series** and not
only the statistics computed from it: within months nobody can re-fetch
it, and a median cannot be re-derived into a different statistic.

### 3.4 The row cap is 100 and silent, and `acml_tr_pbmn` here is 원

The same silent cap as the equity daily endpoint — `rt_cd=0`, newest rows
kept, oldest dropped. A quarterly contract asked for its whole life comes
back truncated and looking complete, so every request here is narrowed to
a contract's front-month period and `daily_bars` **refuses** a response
at or over the cap.

`acml_tr_pbmn` is denominated in **원**, not the 백만원 that
투자자별 매매동향 uses (CLAUDE.md records that one explicitly). Checked
against the multiplier rather than assumed: 삼성전자's 13,935,459
contracts over 47 sessions carry ₩35.8조, and 13,935,459 × 10 shares ×
~250,000원 is ₩34.8조. **A convention is a property of an endpoint, not
of a venue** — the same lesson the 100/50 row caps already taught here.

## 4. The selection rule, fixed before it was applied

Written into `research/krx_futures_universe.py`'s module docstring and
committed before the ranking was run, on `ms-e`'s pattern:

1. **Candidate pool**: every underlying with a listed single-stock future
   in KIS's own master — **283** on 2026-09-16, every one of them **10
   shares** a contract. (rd-q estimated "~265"; the measured figure is
   283, and the master also carries options and calendar spreads for the
   same names, which a parser keyed on the code prefix alone would sweep
   in.)
2. **Statistic**: the **median daily front-month futures 거래대금** over
   the ranking window.
3. **Ranking window**: **2026Q1**, the first full quarter of the
   front-month series that still exists.
4. **Membership floor**: the name must **trade** on ≥ 90% of the window's
   sessions.

   **In the event this removed exactly one name**, and it is worth
   reporting precisely because the raw count is misleading: 42 of 283
   candidates failed, but **41 of them had no contract listed during the
   window at all**. KRX lists these in batches — 24 arrived 2026-04-27 and
   17 more on 2026-09-14, two days before this ran — so their ineligibility
   is a fact about their listing date and not about their liquidity. The
   one genuine case is 001570, listed 2026-02-13, which printed 16 bars
   and traded on **none** of them. `report` now names the two groups
   separately; one number for both would have read as "42 names are too
   illiquid", which is false.
5. **Size**: the top 10, so the result is directly comparable with KR-10.
6. **Exit rule**: a member leaves on a delisting announcement or a
   failure to resume — never on "its futures got less liquid later",
   which would be selection on the outcome.

**Why the median across days rather than the sum.** A sum lets one
expiry-day volume spike decide a membership. Note this is the median
*within* a name; rd-q's misleading median was *across* names, and they
are different statistics doing different jobs.

**Why "traded" and not "printed", which is the whole content of clause
4.** KIS returns a bar for every session a contract is **listed**,
carrying `acml_vol` 0 when nobody traded it. Counting bars therefore
gives all 283 names 100% coverage and filters nothing whatever. This was
found by watching the first 22 names of a real run report *186 of 186
sessions each* — not by reading the code, and not by the tests, which
were written from the same wrong model. It is the Change-checks pattern
again: *a verification that shares an assumption with its implementation
confirms the misunderstanding.*

## 5. Does day-one selection mean anything here? Measured, not assumed

A ranking window is only worth having if the ranking persists. So the
same statistic is computed over the **forward** window (2026-04-01 to
date) and the two are compared by Spearman rank correlation and top-10
overlap.

A low correlation would not be an inconvenience to work around — it would
say that **no point-in-time futures-liquidity selection is possible at
all**, which is a finding about the instrument and would have to be
reported as one.

**Spearman +0.954 across 238 names**, top-10 overlap **7 of 10**. The
three that left the top 10 went to ranks 13, 23 and 11 — a reshuffle
inside the top quarter, not a different universe.

So a rule fixed on 2026Q1 information selects substantially the same
names a rule fitted with hindsight would, which is the property day-one
selection needs and the one `ms-e` had to assume for spot.

**What it does not license.** +0.954 is a statement about *this* nine
months, during which nothing in the instrument changed structurally. It is
evidence that the ranking is persistent over a quarter, not over years,
and the series is not long enough to ask the longer question.

## 6. Contract notional is a second filter, and it may bind harder

rd-q ruled KOSPI200 index futures out at **₩265M** a contract against
this project's **2% max-order-notional** canary limit — an account of
₩13.3bn to hold one.

A single-stock future is 10 shares, which is far smaller, but *10 shares
of an expensive name is still not small*. SK하이닉스 at ₩1,765,000 is
**₩17.65M** a contract and therefore **₩882.5M** of account under the same
limit. So a name can be the most liquid single-stock future in Korea and
still be unholdable, and the ranking reports the implied account beside
the liquidity rather than leaving it to be discovered later.

## 7. What this does not establish

**Nothing about whether an edge exists.** No situation, no forward
return, no conditioning. A universe is a precondition for a study, not a
result.

**Nothing about the cost floor.** rd-q §8 item 1 — re-measure the spread
intraday across days — is still open, and one closing snapshot still
cannot carry a registration. The universe this task selects is the right
set of names to sample, which is why it comes first.

**Nothing about the per-era tax schedule**, which rd-f §1.1 item 3
records as unsourced with the direction of its bias unestablished. It
remains the blocking prerequisite for any Korean registration.

**Survivorship is bounded, not removed.** The candidate pool is the
**currently-listed** futures master, so a name whose single-stock future
was delisted between 2025-12 and now is absent. Over a nine-month window
that is a small exposure, and it is the same shape as KR-10's — bounded
by the window's length rather than by an argument that it does not
happen. A longer study reopens it properly.

**The intraday bars collected so far are for the wrong names.**
`scripts/collect-krx-intraday.sh` collects KR-10. Every session not
collected for the newly selected names is permanently lost on the same
~250-trading-day clock rd-o measured, so this is the urgent consequence
of the result rather than a tidy follow-up.

## 8. What follows

1. **Point the intraday collector at the new universe**, today rather
   than after the next task. The rolling window means the cost of waiting
   is measured in sessions, not in effort.
2. **Then rd-q §8 item 1**: sample the quoted spread intraday across days
   for these names, and compute a real cost floor for them.
3. **Then rd-p's h = 15 cell** at that measured floor, with its own event
   dispersion reported — rd-p §7 item 2.
4. **Keep the futures history from decaying further.** The artifact
   freezes what exists today; a scheduled collector is what stops the
   next nine months going the way the last twelve did.
