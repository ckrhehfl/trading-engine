"""Spot or single-stock futures: which Korean instrument actually costs less.

CLAUDE.md has carried this as an open operator decision, and
[`rd-f`](../../.planning/rd-f-korean-cost-structure.md) §3 states plainly
that **"the futures round trip is not sourced at all"** — so the
comparison it drew was a tax comparison with one side missing. rd-f §6
also says the choice *"must be made before intraday KRX collection is
scoped."* It was not; this closes it afterwards.

## The hypothesis, and why it was wrong

Futures carry **no 증권거래세**, and the tax is 20bp of rd-f's ~30bp spot
round trip. So futures ought to be roughly a third the cost.

They are not, because **a futures order book is not the spot book**. The
tax saving is real and the spread more than eats it for most names —
measured, not argued:

    spot spread   median 12.1bp   (KR-10, quoted, at the close)
    futures       median 42.9bp

**But the median hides the finding.** Futures spread tracks *futures*
liquidity, which is wildly uneven and is **not** what the KR-10 universe
was selected on — that ranked on spot 거래대금 (`ms-e`). The four names
whose futures books are deep beat spot by ~23bp each; the six whose books
are thin lose by 10-118bp.

So the answer is neither "spot" nor "futures". It is: **futures, for the
names whose futures are liquid — and the universe has to be selected on
that, not on spot turnover.**

## What this measures and what it does not

Quoted spread is `(ask1 - bid1) / mid` from the live book, which is the
**cost of crossing once**. A round trip crosses once in each direction and
so pays one full spread, the same convention rd-f §1.2 uses.

**It is one snapshot.** rd-f's spot figure is a window median; this is the
book at a single close. A snapshot can be unrepresentative — the close in
particular — so this is a *first measurement*, not a replacement for
rd-f's method, and §6 of the write-up says what would settle it.

**Futures commission is still unsourced.** That biases the comparison
*toward* futures, and the four winners win on spread alone by ~23bp, so
the conclusion survives any plausible commission. The six losers lose by
more than a commission could rescue.

Run:

    python -m research.krx_instrument_cost
    python -m research.krx_instrument_cost --symbols 005930,000660
"""

from __future__ import annotations

import argparse
import io
import math
import os
import statistics
import sys
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass

from data.kis_klines import KisKlinesError, KisSession, _get_with_retry

#: KIS's own single-stock-futures symbol master. Same host and shape as
#: the equity masters `krx_universe.py` already uses.
STOCK_FUTURES_MASTER = (
    "https://new.real.download.dws.co.kr/common/master/fo_stk_code_mts.mst.zip"
)
MASTER_ENCODING = "cp949"

QUOTE_SPOT = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
TR_SPOT = "FHKST01010200"
QUOTE_FUTURES = "/uapi/domestic-futureoption/v1/quotations/inquire-asking-price"
TR_FUTURES = "FHMIF10010000"
PRICE_FUTURES = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
TR_PRICE_FUTURES = "FHMIF10000000"

#: rd-f §1, today's column. Both apply to **spot only** — futures pay no
#: 증권거래세 at all, which is the whole reason this comparison exists.
SPOT_TAX_BP = 20.0
SPOT_COMMISSION_BP = 3.54

#: A single-stock futures contract is 10 shares of the underlying,
#: verified in the master file's own description field (`F 202610 (  10)`)
#: for every KR-10 name.
CONTRACT_SHARES = 10


@dataclass(frozen=True)
class InstrumentCost:
    code: str
    name: str
    futures_code: str | None
    spot_spread_bp: float | None
    futures_spread_bp: float | None
    #: `None` when the name has no listed futures contract at all --
    #: distinct from `0`, which is a real observation about a contract
    #: that exists and did not trade. Nullable rather than zero because
    #: `contract_notional_krw` multiplies the price, so a missing contract
    #: would otherwise print as a **₩0 contract** -- affordable-looking,
    #: which is the direction that ruled KOSPI200 index futures out.
    futures_volume: int | None
    futures_price: float | None

    @property
    def spot_round_trip_bp(self) -> float | None:
        """Tax + one full spread crossing + commission — rd-f §1's form."""
        if self.spot_spread_bp is None:
            return None
        return SPOT_TAX_BP + self.spot_spread_bp + SPOT_COMMISSION_BP

    @property
    def futures_round_trip_bp(self) -> float | None:
        """Spread only: no 증권거래세, and commission is unsourced.

        **A lower bound, biased toward futures.** Stated that way so a
        futures win is read as the weaker claim it is and a futures loss
        as the stronger one.
        """
        return self.futures_spread_bp

    @property
    def cheaper(self) -> str | None:
        s, f = self.spot_round_trip_bp, self.futures_round_trip_bp
        if s is None or f is None:
            return None
        return "futures" if f < s else "spot"

    @property
    def contract_notional_krw(self) -> float | None:
        """One contract is 10 shares — the size a real order must clear.

        `None` when there is no contract. A missing instrument has no
        notional, and reporting ₩0 answers the question this figure exists
        to answer — *can this account hold one?* — with the most
        encouraging number available.
        """
        if self.futures_price is None:
            return None
        return self.futures_price * CONTRACT_SHARES


def front_month_futures(codes: set[str], expiry: str) -> dict[str, str]:
    """`{underlying: futures code}` for one expiry, from KIS's master file.

    The master is the only source for these codes: `scripts/kis-paper.sh`
    says outright that it "does not know or validate real KIS contract
    codes". Guessing them is how six probes came back `rt_cd=0` with an
    empty `output1` — KIS's "nothing here" convention, which is *not* an
    error and would let a caller conclude futures are unavailable.
    """
    request = urllib.request.Request(
        STOCK_FUTURES_MASTER, headers={"User-Agent": "trading-engine/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
    except (urllib.error.URLError, OSError) as exc:  # type: ignore[attr-defined]
        raise KisKlinesError(f"could not download the futures master: {exc}") from exc

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            raw = archive.read(archive.namelist()[0])
    except (zipfile.BadZipFile, IndexError) as exc:
        raise KisKlinesError("the futures master is not a readable zip") from exc

    out: dict[str, str] = {}
    marker = f"F {expiry}"
    for line in raw.decode(MASTER_ENCODING, errors="replace").splitlines():
        fields = line.split("|")
        # 1: KIS short code, 3: description, 7: underlying stock code.
        if len(fields) >= 8 and fields[7] in codes and marker in fields[3]:
            out.setdefault(fields[7], fields[1])
    if not out:
        raise KisKlinesError(
            f"no {marker} contract found for any of {sorted(codes)}. The expiry "
            f"may have rolled — check the master rather than guessing a code."
        )
    return out


def _best_quote(
    session: KisSession, path: str, tr: str, division: str, code: str,
    ask_field: str, bid_field: str,
) -> tuple[float, float] | None:
    """`(ask1, bid1)`, or `None` when the book is empty.

    `None` rather than an exception because an empty book is how KIS
    answers for an expired or unlisted contract — `rt_cd=0` with nothing
    in it — and that is a fact about the contract, not a failure.
    """
    params = {"FID_COND_MRKT_DIV_CODE": division, "FID_INPUT_ISCD": code}
    url = f"{session.host}{path}?{urllib.parse.urlencode(params)}"
    payload = _get_with_retry(url, session.headers(tr))
    if payload.get("rt_cd") != "0":
        raise KisKlinesError(
            f"KIS rejected the book for {code}: rt_cd={payload.get('rt_cd')}"
        )
    for key in ("output1", "output2"):
        block = payload.get(key)
        if isinstance(block, dict) and block.get(ask_field) not in (None, "", "0"):
            if isinstance(block.get(ask_field), bool) or isinstance(
                block.get(bid_field), bool
            ):
                raise KisKlinesError(
                    f"KIS returned a boolean quote for {code}; float(True) is 1.0, "
                    f"so it would have been priced rather than rejected"
                )
            try:
                ask, bid = float(block[ask_field]), float(block[bid_field])
            except (TypeError, ValueError):
                return None
            # `spread_bp`'s own checks cannot catch these: every comparison
            # against `nan` is False, so a nan quote passes both the
            # positivity and the crossed-book test and returns a nan
            # spread, which then makes `cheaper` answer without a number.
            if not (math.isfinite(ask) and math.isfinite(bid)):
                raise KisKlinesError(
                    f"KIS returned a non-finite quote for {code}: "
                    f"ask={ask} bid={bid}"
                )
            return ask, bid
    return None


def spread_bp(ask: float, bid: float) -> float:
    """`(ask - bid) / mid` in basis points — the cost of crossing once."""
    if ask <= 0 or bid <= 0:
        raise ValueError(f"a quote must be positive, got ask={ask} bid={bid}")
    if ask < bid:
        raise ValueError(f"crossed book: ask={ask} < bid={bid}")
    return (ask - bid) / ((ask + bid) / 2.0) * 1e4


def _futures_price(session: KisSession, code: str) -> tuple[int, float]:
    """`(cumulative volume, last price)` for a futures contract.

    **Fails rather than returning zero.** A rejected response, a missing
    `output1` or an absent `futs_prpr` would otherwise be recorded as a
    price of 0 — and `contract_notional_krw` multiplies it, so a failed
    fetch would report a **₩0 contract**. That is not merely wrong, it is
    wrong in the direction that makes an instrument look affordable, and
    the notional is exactly what ruled KOSPI200 index futures out at ₩265M.

    Distinct from `_best_quote`, which returns `None` for an empty book on
    purpose: an empty *book* is a real fact about a contract nobody is
    quoting, while an absent *price* is a failed measurement.
    """
    params = {"FID_COND_MRKT_DIV_CODE": "JF", "FID_INPUT_ISCD": code}
    url = f"{session.host}{PRICE_FUTURES}?{urllib.parse.urlencode(params)}"
    payload = _get_with_retry(url, session.headers(TR_PRICE_FUTURES))
    if payload.get("rt_cd") != "0":
        raise KisKlinesError(
            f"KIS rejected the futures price for {code}: "
            f"rt_cd={payload.get('rt_cd')} msg_cd={payload.get('msg_cd')}"
        )
    block = payload.get("output1")
    if isinstance(block, dict) and (
        isinstance(block.get("futs_prpr"), bool) or isinstance(block.get("acml_vol"), bool)
    ):
        # `float(True)` is 1.0 and `int(True)` is 1, so a JSON `true` would
        # pass every check below and become a plausible measurement --
        # ₩10 of contract notional, or one contract of volume. bool is the
        # only type that converts silently like this, and rejecting it is
        # the same contract as the rest of this function rather than a new
        # one: a value that was never measured must not become a number.
        raise KisKlinesError(
            f"KIS returned a boolean where {code}'s price or volume belongs; "
            f"float(True) is 1.0, so this would have been recorded as a real "
            f"quote rather than rejected"
        )
    if not isinstance(block, dict) or block.get("futs_prpr") in (None, ""):
        raise KisKlinesError(
            f"KIS returned no futures price for {code}. An empty output1 is "
            f"how KIS answers for an expired or unlisted contract -- check the "
            f"master file for the current expiry rather than treating this as a "
            f"price of zero."
        )
    try:
        price = float(block["futs_prpr"])
    except (TypeError, ValueError) as exc:
        raise KisKlinesError(
            f"futures price for {code} is not a number: {block['futs_prpr']!r}"
        ) from exc
    # `nan` and `inf` both survive `float()` and both survive `<= 0`, and
    # `nan` then propagates silently through every comparison downstream --
    # `cheaper` would return "spot" for a nan futures cost without anything
    # looking wrong. Finiteness is checked before positivity for that
    # reason, not as a formality.
    if not math.isfinite(price) or price <= 0:
        raise KisKlinesError(
            f"futures price for {code} is not a finite positive number: {price}"
        )
    # The same fail-closed argument as the price, for the same reason: a
    # missing volume recorded as 0 reads as an ILLIQUID name, and the
    # liquidity split is rd-q's entire finding. `"0"` stays valid -- a
    # listed contract that did not trade that day is a real observation.
    try:
        volume = int(block["acml_vol"])
    except (KeyError, TypeError, ValueError) as exc:
        raise KisKlinesError(
            f"futures volume for {code} is missing or unparseable: "
            f"{block.get('acml_vol')!r}"
        ) from exc
    if volume < 0:
        raise KisKlinesError(f"futures volume for {code} is negative: {volume}")
    return volume, price


def measure(
    session: KisSession, universe: dict[str, str], expiry: str
) -> list[InstrumentCost]:
    futures = front_month_futures(set(universe), expiry)
    out = []
    for code, name in universe.items():
        fcode = futures.get(code)
        spot = _best_quote(session, QUOTE_SPOT, TR_SPOT, "J", code, "askp1", "bidp1")
        fut = None
        volume, price = None, None
        if fcode:
            fut = _best_quote(
                session, QUOTE_FUTURES, TR_FUTURES, "JF", fcode,
                "futs_askp1", "futs_bidp1",
            )
            volume, price = _futures_price(session, fcode)
        out.append(
            InstrumentCost(
                code=code,
                name=name,
                futures_code=fcode,
                spot_spread_bp=spread_bp(*spot) if spot else None,
                futures_spread_bp=spread_bp(*fut) if fut else None,
                futures_volume=volume,
                futures_price=price,
            )
        )
    return out


def report(rows: list[InstrumentCost]) -> None:
    if not rows:
        print("no instruments measured")
        return
    print(
        f"spot round trip = {SPOT_TAX_BP}bp tax + quoted spread + "
        f"{SPOT_COMMISSION_BP}bp commission (rd-f §1)\n"
        f"futures round trip = quoted spread only -- no tax, commission "
        f"UNSOURCED, so biased toward futures\n"
    )
    print(
        f"{'name':<14} {'spot bp':>9} {'fut bp':>9} {'spot RT':>9} {'fut RT':>9} "
        f"{'cheaper':>9} {'fut volume':>12} {'contract ₩':>12}"
    )
    print("-" * 92)
    for r in rows:
        s = f"{r.spot_spread_bp:.1f}" if r.spot_spread_bp is not None else "-"
        f = f"{r.futures_spread_bp:.1f}" if r.futures_spread_bp is not None else "-"
        # `is not None`, not truthiness: a touching book is a real 0.0bp
        # cost and the *best* one there is, so printing it as missing would
        # hide the most favourable measurement the module can make.
        srt = f"{r.spot_round_trip_bp:.1f}" if r.spot_round_trip_bp is not None else "-"
        frt = (
            f"{r.futures_round_trip_bp:.1f}"
            if r.futures_round_trip_bp is not None
            else "-"
        )
        vol = f"{r.futures_volume:,}" if r.futures_volume is not None else "-"
        notional = (
            f"{r.contract_notional_krw:,.0f}"
            if r.contract_notional_krw is not None
            else "-"
        )
        print(
            f"{r.name:<14} {s:>9} {f:>9} {srt:>9} {frt:>9} {r.cheaper or '-':>9} "
            f"{vol:>12} {notional:>12}"
        )

    wins = [r for r in rows if r.cheaper == "futures"]
    loses = [r for r in rows if r.cheaper == "spot"]
    print("-" * 92)
    if wins:
        vol = statistics.median(r.futures_volume or 0 for r in wins)
        rt = statistics.median(r.futures_round_trip_bp for r in wins)  # type: ignore[misc]
        print(
            f"futures cheaper for {len(wins)}: median round trip **{rt:.1f}bp**, "
            f"median futures volume {vol:,.0f}"
        )
    if loses:
        vol = statistics.median(r.futures_volume or 0 for r in loses)
        print(f"spot cheaper for {len(loses)}: median futures volume {vol:,.0f}")
    if wins and loses:
        ratio = statistics.median(r.futures_volume or 0 for r in wins) / max(
            statistics.median(r.futures_volume or 0 for r in loses), 1
        )
        print(
            f"\n**The split is liquidity.** The winners trade {ratio:.0f}x the "
            f"CUMULATIVE VOLUME of the losers.\nThat is acml_vol -- volume that "
            f"changed hands -- and NOT resting size at the touch. Depth is on "
            f"the\nwire (futs_askp_rsqn1..) and is not collected here, so no "
            f"claim about book\ndepth is made. The universe was selected on SPOT "
            f"turnover (ms-e), which\npredicts neither."
        )
    print(
        "\nOne snapshot of one session's book. rd-f's spot figure is a window "
        "median;\nthis is not a replacement for that method."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--symbols",
        default="005930,068270,000660,009150,207940,000720,007390,028300,064350,051910",
        help="comma-separated 6-digit underlying codes (default: KR-10)",
    )
    ap.add_argument("--expiry", default="202610", help="futures expiry, YYYYMM")
    args = ap.parse_args(argv)

    key, secret = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not key or not secret:
        print("KIS_APP_KEY / KIS_APP_SECRET are not set", file=sys.stderr)
        return 2
    universe = {c.strip(): c.strip() for c in args.symbols.split(",") if c.strip()}
    report(measure(KisSession(key, secret), universe, args.expiry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
