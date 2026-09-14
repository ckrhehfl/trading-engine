# Multi-Asset TSMOM Task D — the correlations MS-A assumed, measured; and a timezone artifact that would have flattered them

**Status**: measured, 2026-09-13, against the live KIS paper host from the
GCP instance. No universe committed to a pre-registration yet, no strategy
run, nothing promoted.

Closes the debt MS-A §2.4 took on: *"Both are measured at MS-C, before
MS-F scores anything, and this table is recomputed against the real
figures then."*

---

## 1. What was assumed, and what is true

| Quantity | MS-A §2.4 assumed | Measured | |
|---|---|---|---|
| Korean internal correlation | 0.60, pessimistic 0.75 | **0.250** (median 0.216, range 0.104–0.726) | far lower |
| Korea vs crypto | 0.387, borrowed from BTC/S&P 500 | **~0.20** (see §2.1 — an earlier figure of ~0.10 was an artefact of daily-bar alignment) | lower |

### 1.1 Exactly how each figure is computed, so it can be reproduced

- **Series**: adjusted daily closes (`FID_ORG_ADJ_PRC=0`) from
  `inquire-daily-itemchartprice`; returns are close-to-close natural logs.
- **Constituents** — the ten largest futures-eligible underlyings by 2018
  traded value, **before** the sector cap (§3), i.e. the *pre-measurement*
  set, not a committed universe:
  `005930` 삼성전자, `068270` 셀트리온, `000660` SK하이닉스, `009150` 삼성전기,
  `207940` 삼성바이오로직스, `000720` 현대건설, `007390` 네이처셀,
  `028300` HLB, `064350` 현대로템, `051910` LG화학.
- **Korean internal 0.250** = the arithmetic mean of all **45** pairwise
  correlations (`C(10,2)`) over the **1,387** dates common to all ten
  series, 2021-01-05 … 2026-09-01. Median 0.216, min 0.104, max 0.726.
  Both series in every pair are Korean, so no cross-timezone alignment
  applies.
- **Korea vs crypto ~0.20** = the mean of the ten individual
  stock-vs-BTC correlations under the **exact 06:30–06:30 UTC** alignment
  of §2.1, over the **419** dates where minute data exists to construct
  it. The KOSPI-index equivalent is 0.338.
- **§4's inputs are assumptions applied to these measurements**, not
  measurements of a portfolio: each constituent is given `sr-ab`'s Sharpe
  and drawdown, and the correlation structure above. §5 says why that
  distinction is the whole point.

**Both assumptions were conservative, and the direction is stronger than
MS-A claimed** — which is the safe direction to be wrong in, and is
stated here rather than quietly enjoyed.

## 2. The timezone artifact, which nearly became a finding

The first measurement returned **Korea vs BTC = −0.016**, and KOSPI vs
BTC = −0.007. Two risk assets with a correlation of exactly zero is not a
result, it is a symptom. The cause is alignment: the Korean session closes
15:30 KST = **06:30 UTC**, while a BTC daily bar spans 00:00–24:00 UTC. A
Korean close therefore reflects the *previous* complete UTC day, not the
one it shares a date label with.

Shifting the UTC-day series by one day and re-measuring, with two controls:

| Pair | Same day | UTC series lagged +1 |
|---|---|---|
| KOSPI vs BTC | −0.007 | **+0.159** |
| S&P 500 vs BTC | **+0.395** | +0.037 |
| KOSPI vs S&P 500 | +0.111 | **+0.309** |

The controls are what make this conclusive rather than a story. The US
close (21:00 UTC) overlaps the BTC UTC day, so S&P 500 peaks at lag 0 and
collapses at lag 1 — **the opposite pattern**, from the same code. And
KOSPI reacting to the previous US session at +0.309 is the textbook
result any Korean market participant would predict.

**Why this mattered.** The spurious zero pointed the wrong way for
safety: it would have made cross-asset diversification look *perfect* and
inflated every projection built on it. A number that flatters the
conclusion and is too clean deserves the same suspicion as one that
breaks it.

### 2.1 The one-day shift is an approximation, and it understates by a third

Caught on review of this document's own PR, and it is a real correction
to a number published above. A KOSPI close-to-close return spans
`[D−1 06:30, D 06:30]` UTC, which **straddles two BTC UTC days**. Shifting
whole daily bars by one day therefore approximates *information
availability*; it does not align the observation windows. The controls
establish that the shift direction is right — they say nothing about the
magnitude.

BTC was re-aggregated to true 06:30→06:30 windows from local 1-minute
bars, and measured on the **same 419-day sub-sample** so alignment is the
only thing that varies:

| BTC alignment | KOSPI vs BTC | Individual ten, mean |
|---|---|---|
| **Exact 06:30–06:30** | **+0.3377** | **+0.1976** |
| Daily bars, shifted +1 | +0.2305 | +0.1287 |
| Daily bars, unshifted | −0.0568 | — |

**The approximation understates the true correlation by about a third**,
and it does so in the direction that flatters diversification. The
corrected input is therefore **~0.20** for individual names, not ~0.10.

Disclosed limits: the exact figure rests on 419 days (minute data begins
2024-11-30) against 1,297 for the approximation, so period and alignment
are not fully separated. The approximation over the full 5.7 years gives
+0.159 for KOSPI. §4 carries both 0.20 and a conservative 0.34 so the
conclusion can be read against either.

**Consequence for MS-F, and it is not cosmetic.** A portfolio holding
Korean and crypto legs together must respect *which information existed
when*. Sizing a Korean position on "today's BTC return" is lookahead —
that return is not complete until 17.5 hours after the Korean close. Any
combined-portfolio construction has to state its alignment explicitly and
be checked against it.

## 3. The universe ranking

KR-10's ranking input, per MS-A §4.2: 2018 full-calendar-year traded
value (거래대금), the year *before* the window opens, so nothing from the
scored window enters the selection.

**Pool**: every outright single-stock-futures underlying — 265, from
`fo_stk_code_mts.mst`. **229 have 2018 data**; the other 36 did not trade
in 2018 and are excluded by the rule itself rather than by a judgement.
244 trading days in 2018, matching the real KRX calendar.

Top 20, before the three-per-sector cap:

| # | Code | Name | 2018 traded value |
|---|---|---|---|
| 1 | 005930 | 삼성전자 | ₩144.3 tn |
| 2 | 068270 | **셀트리온** | ₩92.6 tn |
| 3 | 000660 | SK하이닉스 | ₩73.0 tn |
| 4 | 009150 | 삼성전기 | ₩32.5 tn |
| 5 | 207940 | 삼성바이오로직스 | ₩29.7 tn |
| 6 | 000720 | 현대건설 | ₩27.4 tn |
| 7 | 007390 | **네이처셀** | ₩26.6 tn |
| 8 | 028300 | **HLB** | ₩25.9 tn |
| 9 | 064350 | 현대로템 | ₩22.9 tn |
| 10 | 051910 | LG화학 | ₩21.1 tn |
| 11–20 | | 현대엘리베이터, 삼성SDI, POSCO홀딩스, 현대차, NAVER, LG전자, 카카오, KB금융, 삼성물산, 롯데케미칼 | |

**The rule earns its keep in the names a person would not have picked.**
셀트리온 second, 네이처셀 seventh and HLB eighth are speculative,
retail-heavy names that any hand-assembled "Korean blue chips" list —
including the one this task started from — would have excluded. Their
presence is not a flaw in the rule; it is the rule refusing to encode
hindsight about which names turned out to be respectable.

**This ranking is an input, not the universe**, and nothing may be
promoted on it.

### 3.1 Sector classification — solved for KOSPI, unmapped for KOSDAQ

Both equity masters carry three `지수업종` code fields (major/mid/minor) in
their fixed-width tails — at the same relative offsets, though the tails
themselves differ in width (KOSPI 227 bytes, KOSDAQ 220). A third public
master, **`idxcode.mst`**, names 486 of those codes, which turns the
numbers into something checkable:

| Code | Name | KR-10 candidate |
|---|---|---|
| `00027` | 제조 | the *major* code for most manufacturers |
| `00013` | 전기·전자 | 삼성전자, SK하이닉스, 삼성전기 |
| `00009` | 제약 | 셀트리온, 삼성바이오로직스 |
| `00008` | 화학 | LG화학 |
| `00011` | 금속 | POSCO홀딩스 |
| `00012` | 기계·장비 | 현대엘리베이터 |
| `00015` | 운송장비·부품 | 현대차, 현대로템 |
| `00018` | 건설 | 현대건설 |
| `00029` | IT 서비스 | NAVER, 카카오 |

The resolution rule falls out cleanly: **the mid code when non-zero,
otherwise the major**. Every KOSPI name lands on a sensible sector.

**KOSDAQ does not resolve.** 네이처셀 and HLB carry `(00091, 00240)`, and
neither code appears in `idxcode.mst` — its 486 entries are KOSPI-side.
The two markets use disjoint numbering with no published mapping between
them, so a shared code space would count KOSPI pharma `(00027, 00009)` and
KOSDAQ pharma `(00091, 00240)` as **different** sectors, and a
three-per-sector cap could admit six biotechs. **The cap cannot be
applied across markets with the data available.**

### 3.2 The capped basket measures worse — but two things changed at once

MS-A §3.2 argued for the cap because a semiconductor-heavy basket would
push internal correlation toward 0.8. That reasoning is sound in the
abstract and turns out to be wrong here:

| Basket | Mean pairwise ρ | Vol multiplier |
|---|---|---|
| Top ten by turnover, **no cap** (4 biotech, 3 semis) | **0.2500** | 0.570 |
| Sector-capped ten, **KOSPI-only fallback** | **0.3005** | 0.609 |

**This is a comparison of two changes, not one, and the effects cannot be
separated from it.** The capped basket is KOSPI-only *by construction* —
§3.1 shows KOSDAQ sectors cannot be mapped, so applying the cap at all
forces the KOSDAQ names out. So the 0.2500 → 0.3005 move mixes (a) the
sector cap and (b) the loss of 네이처셀 and HLB, two speculative KOSDAQ
names that barely co-move with anything. Their replacements,
현대엘리베이터 and POSCO홀딩스, are ordinary industrials that track the rest
more closely.

The honest reading is therefore only that **the KOSPI-only capped
fallback is worse on this measure than the uncapped cross-market ten** —
not that the cap alone causes it. A cleaner argument, computed on 2018
and free of this confound, appears in
[`ms-e-kr10-universe-rule.md`](ms-e-kr10-universe-rule.md) §4.2.

**This comparison must not be used to choose the universe.** It is
computed over 2021–2026, which is the window MS-F would score on; picking
a basket because it measures better there is selection on the scored
window, the precise thing this whole design exists to avoid. It is
reported as a diagnostic.

A legitimate version exists and is offered as an option, not adopted:
impose the correlation constraint **on 2018 data** — the same pre-window
year the ranking already uses — greedily in rank order. That replaces a
proxy with a direct measurement of the property the proxy stands for,
using data outside the scored window.

**Three ways forward, and the choice is the operator's** because it
modifies a committed rule. **Decided 2026-09-13: option 2.** The rule and
its thresholds are committed in
[`ms-e-kr10-universe-rule.md`](ms-e-kr10-universe-rule.md) §2–3 and the
resolved universe in its §4; this list is the pre-decision record.


1. **Keep the cap, KOSPI-only.** Follows MS-A §4.2 as written, at the
   cost of a market-wide exclusion the rule never specified.
2. **Replace the cap with a 2018-measured correlation constraint.**
   Measures the property directly; needs the threshold committed before
   use.
3. **Drop the cap, rank on turnover alone.** Simplest, concentration
   unguarded — and §3.2 is not sufficient grounds for it.

## 4. §2.4 recomputed against measured inputs

Per-constituent Sharpe 1.305 and drawdown 20.135% held at `sr-ab`'s
observed values; equal weight; drawdown from the validated Monte Carlo
(§2.4's method, unchanged).

| Structure | Vol mult | Sharpe | Max DD | |
|---|---|---|---|---|
| BTC alone (today) | 1.000 | 1.305 | 20.1% | **fails** |
| §2.4 assumed, ρ=0.60 | 0.800 | 1.631 | 16.7% | passes |
| §2.4 pessimistic, ρ=0.75 | 0.880 | 1.482 | 18.2% | passes |
| **Measured, Korea ×10 at ρ=0.250** | **0.570** | **2.289** | **11.8%** | passes |
| + BTC/ETH, cross 0.20 (exact alignment) | 0.554 | 2.356 | 11.2% | passes |
| + BTC/ETH, cross 0.34 (KOSPI-index, conservative) | 0.588 | 2.219 | 12.8% | passes |

**The headline row does not use the cross-correlation at all**, so §2.1's
correction leaves it untouched. Only the combined rows move, and every
one of them still clears both gates.

**The combined rows' full covariance input**, since a `cross` number
alone does not determine one — this is a two-block structure:

- **Korea block**: 10 members, internal ρ = 0.250 (§1, measured).
- **Crypto block**: 2 members, internal ρ = **0.8454** — the measured
  BTC/ETH figure from MS-A §2.3.
- **Between blocks**: the single `cross` value shown in the row, applied
  **uniformly to every Korea-crypto pair**, i.e. Korea-vs-BTC and
  Korea-vs-ETH alike.

Applying one number to both crypto legs **is an assumption, not a
measurement**: only Korea-vs-BTC was measured (§2.1). ETH's own
Korea-correlation was not, and ETH's 0.845 co-movement with BTC makes a
similar value plausible without making it observed. The combined rows are
therefore **scenarios**; the Korea ×10 row is the one built only on
measurements.

Trade count scales with K: 64 → 640 at ten constituents, against a floor
near 100.

## 5. What this does **not** show, stated before the numbers get quoted

**The per-constituent Sharpe of 1.305 is `sr-ab`'s BTC figure, assumed to
carry over. Nothing here measures whether TSMOM has any edge on Korean
equities at all.** That is the entire question, and it is MS-F's, not
this task's.

The distinction matters because low correlation cuts both ways. It
genuinely reduces portfolio volatility and drawdown — that part is now
measured. But diversifying across ten instruments on which the signal
does not work produces ten times the trades and no edge, and the
correlation structure would look exactly this good either way.

So the honest reading of §4 is narrow and worth keeping narrow:
**the portfolio arithmetic that made single-asset BTC fail its two gates
is now measured rather than assumed, and it clears them with room. The
premise those gates are applied to remains untested.**

## 6. Method notes

- Both correlation figures come from adjusted (`FID_ORG_ADJ_PRC=0`) daily
  closes, so the 삼성전자-style split artefact cannot enter them.
- The Monte Carlo is the one validated in MS-A §2.4 against real BTC/ETH
  data (simulated drawdown ratio 0.977 against a measured 0.978).
- The ranking pull is 229 successful symbol-years with zero errors; the
  36 gaps are genuine absences, not failures.
- **A 20-minute job must not be tied to an interactive SSH session.** The
  first ranking run was silently restarted from scratch by a `gcloud
  compute ssh` reconnect after an `exit 255`, and was only caught because
  the remote process's elapsed time read 3m58s when the job had been
  "running" for forty minutes. Re-run detached with `setsid nohup`. The
  same shape as the deployment lesson already in CLAUDE.md: a thing that
  looks like it is running is not evidence that it is.

## 7. Next

1. **The §3.2 decision** — cap / correlation constraint / neither — then
   resolve the universe (MS-E). KOSPI sectors are now resolvable; KOSDAQ
   is not.
2. **Backfill** the resolved universe into the store — nothing is
   persisted yet; everything above was computed in memory.
3. **Pre-registration** committing the resolved list, the window, and the
   criteria, before MS-F scores anything.
4. The single-stock-futures **earliest bar**, which sets the tradeable
   window start (MS-C §2.1). The symbol format is now known
   (`A11610`-style, from the master), so the earlier 0-row result is
   retestable.
