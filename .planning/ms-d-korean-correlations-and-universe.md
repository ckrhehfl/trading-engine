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
| Korea vs crypto | 0.387, borrowed from BTC/S&P 500 | **~0.10** properly aligned (KOSPI 0.159) | far lower |

Daily log returns, 1,387 common trading days, 2021-01-05 … 2026-09-01,
across the ten largest futures-eligible underlyings by 2018 traded value.

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

**Still missing: sector classification**, so the three-per-sector cap
cannot be applied yet. The current top ten is visibly concentrated in
semiconductors (3) and biotech (4), so the cap will bind and the final
KR-10 will differ from this list. **This ranking is therefore an input,
not the universe**, and nothing may be promoted on it.

## 4. §2.4 recomputed against measured inputs

Per-constituent Sharpe 1.305 and drawdown 20.135% held at `sr-ab`'s
observed values; equal weight; drawdown from the validated Monte Carlo
(§2.4's method, unchanged).

| Structure | Vol mult | Sharpe | Max DD | |
|---|---|---|---|---|
| BTC alone (today) | 1.000 | 1.305 | 20.1% | **fails** |
| §2.4 assumed, ρ=0.60 | 0.800 | 1.631 | 16.7% | passes |
| §2.4 pessimistic, ρ=0.75 | 0.880 | 1.482 | 18.2% | passes |
| **Measured, Korea ×10 at ρ=0.250** | **0.570** | **2.289** | **11.3%** | passes |
| Measured, + BTC/ETH at cross 0.10 | 0.528 | 2.470 | 10.5% | passes |

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

1. **Sector classification**, so KR-10's three-per-sector cap can be
   applied and the universe actually resolved (MS-E).
2. **Backfill** the resolved universe into the store — nothing is
   persisted yet; everything above was computed in memory.
3. **Pre-registration** committing the resolved list, the window, and the
   criteria, before MS-F scores anything.
4. The single-stock-futures **earliest bar**, which sets the tradeable
   window start (MS-C §2.1). The symbol format is now known
   (`A11610`-style, from the master), so the earlier 0-row result is
   retestable.
