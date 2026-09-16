# Research Direction Task Q — which Korean instrument: neither spot nor futures, but four names

**Measured 2026-09-16** against the live KIS paper host, closing books.
Module: `research/krx_instrument_cost.py`. Reproducible with

```
python -m research.krx_instrument_cost
```

Closes an operator decision CLAUDE.md has carried open since MS-A, and
which [`rd-f`](rd-f-korean-cost-structure.md) §3 named as its own largest
gap: *"the futures round trip is not sourced at all."*

**It also closes it late.** rd-f §6 states the choice *"must be made
before intraday KRX collection is scoped."* It was not — [`rd-o`](rd-o-krx-intraday-backfill.md)
collected **spot equity** minute bars without it. That ordering error is
recorded in §7 along with what it does and does not cost.

---

## 1. The headline

> **The hypothesis was wrong, and the way it was wrong is the finding.**
>
> Futures pay **no 증권거래세** — 20bp of rd-f's ~30bp spot round trip — so
> they ought to be roughly a third the cost. Measured across the KR-10,
> the futures spread is **42.9bp median against spot's 12.1bp**, and the
> tax saving does not begin to cover it.
>
> **But the median hides it.** The four names whose futures trade heavily
> beat spot by **~23bp each**, and their median round trip is **13.3bp** —
> BTC's own measured 12bp, in Korea. The six that barely trade lose by 10
> to 118bp.
>
> **The split is liquidity, and the universe was selected on the wrong
> kind of it**: `ms-e` ranked KR-10 on **spot** 거래대금, and the winners'
> futures trade **27× the cumulative volume** of the losers'.
>
> **"Volume", not "depth".** The 27× is a ratio of `acml_vol` — contracts
> that changed hands — and says nothing about resting size at the touch.
> An earlier draft of this paragraph said "books are 27× deeper", which is
> a claim about a quantity this measurement never took. Depth is on the
> same wire (`futs_askp_rsqn1..`) and is not collected here.

## 2. Measured, per name

Spot round trip is rd-f §1's form — 20bp tax + one full spread crossing +
3.54bp commission. Futures is **spread only**: no tax, and commission
unsourced, so it is a lower bound **biased toward futures**.

| name | spot spread | futures spread | spot RT | futures RT | cheaper | futures volume | contract ₩ |
|---|---|---|---|---|---|---|---|
| 삼성전자 | 19.7 | 20.0 | 43.3 | **20.0** | **futures** | 1,526,025 | 2,500,000 |
| 셀트리온 | 5.6 | 45.0 | **29.2** | 45.0 | spot | 11,160 | 1,779,000 |
| SK하이닉스 | 5.7 | 5.7 | 29.2 | **5.7** | **futures** | 368,848 | 17,650,000 |
| 삼성전기 | 7.3 | 43.6 | **30.8** | 43.6 | spot | 28,472 | 13,740,000 |
| 삼성바이오로직스 | 21.4 | 85.5 | **44.9** | 85.5 | spot | 1,491 | 13,930,000 |
| 현대건설 | 8.1 | 8.1 | 31.6 | **8.1** | **futures** | 20,378 | 1,244,000 |
| 네이처셀 | 20.1 | 100.1 | **43.6** | 100.1 | spot | 3,962 | 248,500 |
| HLB | 15.8 | 157.0 | **39.3** | 157.0 | spot | 5,319 | 324,500 |
| 현대로템 | 8.4 | 42.1 | **31.9** | 42.1 | spot | 9,163 | 1,185,000 |
| LG화학 | 18.4 | 18.5 | 41.9 | **18.5** | **futures** | 11,435 | 2,705,000 |
| **median** | **12.1** | **42.9** | **35.6** | **42.9** | | | |

Futures winners' median volume **194,613**; losers' **7,241**.

## 3. What this corrects in rd-f

**rd-f's spot figure was a floor and is now measured.** §1 used a
**one-tick** spread — 6.5bp for today's column — and said so explicitly:
*"both use a one-tick spread, which §1.2 shows is a lower bound … the true
figures are higher by however much the real quoted spread exceeds one
tick."*

The real quoted spread is **12.1bp median**, about **1.9×** that floor. So
the spot round trip is **~35.6bp**, not 30.0.

That closes rd-f §1.2's open item in the unfavourable direction, and it
matters beyond bookkeeping: every cost-ceiling figure in `rd-e` and every
required-effect figure in [`rd-p`](rd-p-what-krx-intraday-could-resolve.md)
used 30bp.

## 4. KOSPI200 index futures are not tradeable here at all

Probed first, because the project **already built a paper-trading loop for
them** (KIS Phase 1) and had never measured what it costs to trade there.

The front-month contract `A01612` quotes at **1061.15** against a KRX
multiplier of **₩250,000 per index point** — so one contract is
**₩265,287,500** of notional.

> Against this project's own canary limit of **2% max order notional**,
> holding **one** contract requires an account of **₩13.3 billion**.

**No mini contract exists in KIS's own index master** (`fo_idx_code_mts.mst`,
8,347 rows: KOSPI200, KSQ150, VKOSPI, KRX300 and sector indices — no
₩50,000-multiplier line). So the instrument the paper loop was built for
is unusable at any plausible personal account size, and that is a fact
about contract size rather than about strategy.

Single-stock futures are **10 shares** a contract — ₩248,500 to ₩17.65M
across the KR-10 — which is the range a real account can hold.

**A side finding with an operational consequence**: `A01609`, the symbol
the KIS paper loop actually ran, is the **September 2026** contract and
expired on **2026-09-10**. Six guessed codes returned `rt_cd=0` with an
empty `output1` before the master file was read — KIS's "nothing here"
convention again, and indistinguishable from "futures are unavailable" if
a caller treats a successful call as success.

## 5. What it does to rd-p's arithmetic

rd-p priced a KRX intraday event study at a **30bp** floor. At the
futures-liquid names' **13bp**, every cell moves — and in both directions
at once, which is the trade-off rd-p already named:

| h | at 30bp | | at 13bp | |
|---|---|---|---|---|
| | effect/σ | n (band) | effect/σ | n (band) |
| 15 | 0.465 | 98 – 656 | **0.202** | **524 – 3,494** |
| 60 | 0.260 | 314 – 2,097 | **0.113** | 1,673 – 11,166 |
| 240 | 0.149 | 958 – 6,391 | 0.065 | 5,100 – 34,035 |

> **h = 15 becomes the interesting cell**, where at 30bp it was
> implausible. A **0.20σ** conditional shift is inside the range rd-b's
> framing calls detectable, and h=15 is where the most events exist.

The cross-section shrinks with the universe, though: four names × 251
sessions is **1,004 symbol-sessions**, against h=15's 524–3,494 band. A
situation firing once per symbol-session clears the optimistic end and not
the pessimistic one — the same "depends on the unmeasured event
dispersion" as rd-p §5.

**And four names is an artifact of asking the KR-10.** There are ~265
single-stock futures underlyings; a universe ranked on **futures**
liquidity would be larger and is the obvious next selection.

## 6. What this does not establish

**One snapshot, one close.** rd-f's spot figure is a window median; this
is a single session's book at 15:45, and the close is a particular moment.
**This is a first measurement, not a replacement for rd-f's method**, and
it must be re-measured intraday across days before anything is registered
against it.

**Futures commission is still unsourced.** It biases toward futures, and
the four winners win on spread alone by ~23bp, so the direction survives
any plausible commission — but the 13.3bp figure itself is a lower bound.

**Nothing about whether an edge exists.** No situation, no forward return,
no conditioning. This is a cost measurement.

**Nothing about the per-era tax schedule**, which rd-f §1.1 item 3 records
as unsourced with **the direction of its bias unestablished**. That was a
prerequisite for a Korean registration before this document and still is —
and it now blocks a live question rather than a hypothetical one.

**Nothing about futures data availability.** Expired contracts return
`rt_cd=0` with zero rows, so a futures-based backtest reads the
**underlying** and treats the basis as a disclosed approximation. `basis`
is on the wire (삼성전자: 125 on a 250,000 price, ~5bp) and has not been
studied over time.

## 7. The ordering error, recorded

rd-f §6 said this decision *"must be made before intraday KRX collection
is scoped."* rd-o scoped and ran that collection — 944,763 **spot equity**
bars — without it.

**What it cost: less than it might have.** A single-stock future tracks
its underlying, so spot minute bars are the right research series for a
futures-executed strategy; what changes is the **cost floor applied to
them**, not the data. The collection is not wasted.

**What it did cost**: rd-p was computed at 30bp, and §5 re-prices it. And
the universe is wrong — KR-10 was selected on spot turnover, and only four
of its ten have futures worth using.

**The transferable part**: rd-f wrote its own prerequisite down, in the
document, and the next task read that document and did not act on it. A
prerequisite recorded in prose is not a gate. The closest thing to a gate
this project has is a test, and there is no test that a planning
document's stated prerequisite has been satisfied.

## 8. What follows

1. **Re-measure intraday, across days.** One closing snapshot cannot carry
   a registration. The module takes `--symbols`, so a scheduled sampler is
   the whole of it.
2. **Re-select the universe on futures liquidity** across the ~265
   single-stock futures underlyings, not on spot 거래대금. That is a
   selection rule and needs to be fixed before it is applied, exactly as
   `ms-e` fixed KR-10's.
3. **Then rd-p's h = 15 cell**, at a measured floor rather than an assumed
   one, with its own event dispersion reported — rd-p §7 item 2.
4. **The per-era tax schedule is still the blocking prerequisite** for any
   Korean registration, and it is now on the critical path.
