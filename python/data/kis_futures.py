"""KIS single-stock futures client -- the instrument rd-q says to trade.

[`rd-q`](../../.planning/rd-q-which-korean-instrument.md) measured that
Korean single-stock futures beat spot for the names whose futures books
are deep, and lose badly for the rest -- and that the KR-10 universe was
selected on **spot** turnover, which does not predict futures liquidity.
Re-selecting on futures liquidity needs a futures series, which is what
this module fetches.

Everything here is shaped by what was measured against the live paper
host on 2026-09-16, not by documentation:

- **Contract codes cannot be derived from the underlying code.** 삼성전자
  `005930` is `A11610`; SK하이닉스 `000660` is `A50610`. There is no
  arithmetic between them -- the two-character issue id is KIS's own and
  appears nowhere else. Guessing `A11710` for `000660` returned `rt_cd=0`
  with zero rows, which is indistinguishable from "this contract does not
  trade". That is the same trap rd-q §4 recorded for six guessed index
  codes, re-sprung while writing this module, so the master file is the
  only source here and `contract_code` refuses to construct a code whose
  pattern it has not verified against a listed one.
- **An expired contract is served until it is not, and then its whole
  series disappears at once.** Expiries 2026-01 and later answer; 2025-12
  and earlier return zero rows -- not a truncated series, nothing at all.
  So this is a **decaying window** like `inquire-time-dailychartprice`,
  and the oldest futures bar reachable on 2026-09-16 was **2025-10-10**.
- **Zero rows is not an error.** It is how KIS reports a contract it does
  not serve, with `rt_cd=0`. `daily_bars` returns an empty list and the
  caller decides; a backfill that treats `rt_cd=0` as success and records
  nothing is the failure mode rd-o already hit once.
- **The 100-row cap is silent**, exactly as for equities. A quarterly
  contract lives longer than 100 sessions, so a full-life request comes
  back truncated with `rt_cd=0`. `daily_bars` reports the cap being hit
  rather than letting a caller read a truncated series as a complete one.
- **`acml_tr_pbmn` here is 원, not 백만원.** Checked against the contract
  multiplier: 삼성전자's 13,935,459 contracts over 47 sessions carry
  ₩35.8조, and 13,935,459 x 10 shares x ~250,000원 is ₩34.8조. The
  투자자별 매매동향 endpoint's 백만원 convention (CLAUDE.md, KIS section)
  does **not** apply to this one.
- **A contract's last trading day is read from its own last bar**, never
  computed. The expiry is the second Thursday of the month -- verified on
  five contracts -- but a holiday moves it, and KRX's moving lunar
  holidays are a known unresolved gap in `KrxMarketCalendar`. The data
  already knows the answer.

**This module cannot place an order and must stay that way.** Quotation
TR ids only, no account number, nothing imported from the trading path.
`tests/test_kis_probe_cannot_trade.py` enforces that for this file too.
"""

from __future__ import annotations

import datetime as dt
import io
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass

from data.kis_klines import KisKlinesError, KisSession, _get_with_retry, validated_output2

#: KIS's own single-stock-futures symbol master. Same host, encoding and
#: pipe-delimited shape as the equity masters `krx_universe.py` reads.
STOCK_FUTURES_MASTER = (
    "https://new.real.download.dws.co.kr/common/master/fo_stk_code_mts.mst.zip"
)
MASTER_ENCODING = "cp949"

DAILY_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice"
TR_DAILY = "FHKIF03020100"

#: `JF` is stock futures, `F` is index futures. Sending `F` for a stock
#: contract returns `rt_cd=0` with zero rows -- a wrong division looks
#: exactly like a contract that does not trade.
DIVISION_STOCK_FUTURES = "JF"

#: Silent, and the same figure as the equity daily endpoint. A request
#: wide enough to reach it comes back truncated with `rt_cd=0`.
ROW_CAP = 100

#: Every one of the 283 underlyings in the master carries `(  10)` in its
#: description field. Verified across the whole file rather than rd-q's
#: ten, because a differently-sized contract would break every notional.
CONTRACT_SHARES = 10

#: `삼성전자   F 202610 (  10)` -- the futures line. Options are `C`/`P`
#: and calendar spreads are `SP`, all of which share the file.
_FUTURES_DESCRIPTION = re.compile(r"\bF (\d{6}) \(\s*(\d+)\)")


@dataclass(frozen=True)
class FuturesBar:
    """One daily bar of one contract. `value` is 거래대금 in 원."""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    value: int


@dataclass(frozen=True)
class FuturesMaster:
    """Every listed single-stock futures contract, by underlying.

    `contracts` maps `underlying -> {expiry: code}` for expiries KIS
    currently lists. Historical expiries are **not** in here -- they have
    to be constructed, which is what `contract_code` is for.
    """

    contracts: dict[str, dict[str, str]]

    @property
    def underlyings(self) -> list[str]:
        return sorted(self.contracts)

    def contract_code(self, underlying: str, expiry: str) -> str:
        """The contract code for one `underlying` and `YYYYMM` expiry.

        Constructed, because the ranking window needs contracts that have
        already expired and the master lists only live ones -- and then
        **verified against the master's own listed codes** before it is
        returned. The encoding is `A` + a two-character issue id + the
        last digit of the year + the two-digit month (`A11610` is 삼성전자
        October 2026, `A11703` the same name March 2027).

        The verification is the point. A construction rule that is wrong
        for one underlying would produce a plausible code that returns
        `rt_cd=0` with zero rows, which this module's own contract says
        means "not served" -- so the name would silently drop out of a
        liquidity ranking for looking illiquid. Reproducing every listed
        expiry from the same prefix is what rules that out.
        """
        listed = self.contracts.get(underlying)
        if not listed:
            raise KisKlinesError(f"{underlying} has no listed single-stock future")
        if len(expiry) != 6 or not expiry.isdigit():
            raise KisKlinesError(f"not a YYYYMM expiry: {expiry!r}")
        if not 1 <= int(expiry[4:]) <= 12:
            # Shape is not validity, the same reason `parse_row` checks the
            # calendar as well as the digits. `202613` would otherwise build
            # `A11613`, a well-formed code for a month that does not exist,
            # and a well-formed wrong code returns zero rows -- which this
            # module reports as "not served".
            raise KisKlinesError(f"month {expiry[4:]} is not a month: {expiry!r}")

        prefixes = {code[:-3] for code in listed.values()}
        if len(prefixes) != 1:
            raise KisKlinesError(
                f"{underlying}'s listed contracts do not share one prefix: "
                f"{sorted(listed.values())}. The code encoding is not what this "
                f"module assumes, so a constructed historical code would be a guess."
            )
        prefix = prefixes.pop()
        for listed_expiry, listed_code in listed.items():
            if prefix + _expiry_suffix(listed_expiry) != listed_code:
                raise KisKlinesError(
                    f"the code encoding does not reproduce {underlying}'s own listed "
                    f"{listed_expiry} contract ({listed_code}); refusing to construct "
                    f"a historical code from a rule the master contradicts"
                )
        return prefix + _expiry_suffix(expiry)


def _expiry_suffix(expiry: str) -> str:
    """`202610` -> `610`: the year's last digit, then the month.

    Ambiguous across decades by construction -- 2016 and 2026 share a
    suffix -- which is KIS's encoding and not a choice made here. It is
    harmless for this module because the served window is under a year.
    """
    return expiry[3] + expiry[4:]


def download_master(*, timeout_s: int = 60) -> bytes:
    request = urllib.request.Request(
        STOCK_FUTURES_MASTER, headers={"User-Agent": "trading-engine/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise KisKlinesError(f"could not download the futures master: {exc}") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            return archive.read(archive.namelist()[0])
    except (zipfile.BadZipFile, IndexError) as exc:
        raise KisKlinesError("the futures master is not a readable zip") from exc


def parse_master(raw: bytes) -> FuturesMaster:
    """Futures lines only, from a file that also holds options and spreads.

    The file carried 15,719 rows on 2026-09-16 for **283** underlyings
    with a future; the rest are call (`B`), put (`C`) and calendar-spread
    (`D`) lines. A parser that keyed on the code prefix alone would sweep
    all of them in, and an option's turnover is not this instrument's.
    """
    contracts: dict[str, dict[str, str]] = {}
    sizes: set[int] = set()
    for line in raw.decode(MASTER_ENCODING, errors="replace").splitlines():
        fields = line.split("|")
        # 1: KIS short code, 3: description, 7: underlying stock code.
        if len(fields) < 8:
            continue
        match = _FUTURES_DESCRIPTION.search(fields[3])
        if not match or not fields[1].startswith("A"):
            continue
        contracts.setdefault(fields[7], {})[match.group(1)] = fields[1]
        sizes.add(int(match.group(2)))

    if not contracts:
        raise KisKlinesError(
            "no single-stock futures line parsed out of the master. The file's "
            "layout has changed -- fail rather than report an empty universe, "
            "which reads as 'Korea has no single-stock futures'."
        )
    if sizes != {CONTRACT_SHARES}:
        raise KisKlinesError(
            f"contract sizes in the master are {sorted(sizes)}, not just "
            f"{CONTRACT_SHARES}. Every notional in this project's Korean work "
            f"assumes 10 shares, so a second size must be handled explicitly "
            f"rather than averaged over."
        )
    return FuturesMaster(contracts=contracts)


def parse_row(row: dict[str, object]) -> FuturesBar:
    date = str(row.get("stck_bsop_date") or "")
    if len(date) != 8 or not date.isdigit():
        raise KisKlinesError(f"not a YYYYMMDD trading date: {date!r}")
    try:
        # Shape is not validity: `20261332` is eight digits and not a
        # date. It matters here because `front_month_series` assigns a bar
        # to a contract by comparing these strings against the expiry
        # calendar, so an impossible date sorts into a real window.
        dt.date(int(date[:4]), int(date[4:6]), int(date[6:]))
    except ValueError as exc:
        raise KisKlinesError(f"not a real calendar date: {date!r}") from exc
    # `float(True)` is 1.0 and `int(True)` is 1, so a JSON `true` would
    # survive every check below as a plausible price or volume. bool is
    # the only type that converts this silently.
    boolean = sorted(k for k, v in row.items() if isinstance(v, bool))
    if boolean:
        raise KisKlinesError(f"futures bar for {date} has boolean {boolean}")
    try:
        prices = {
            name: float(row[key])  # type: ignore[arg-type]
            for name, key in (
                ("open", "futs_oprc"), ("high", "futs_hgpr"),
                ("low", "futs_lwpr"), ("close", "futs_prpr"),
            )
        }
        # `acml_vol` and `acml_tr_pbmn` are REQUIRED, not coalesced to 0.
        # This module exists to rank names by liquidity, so a missing
        # figure recorded as zero reads as "nobody trades this" -- the one
        # error that would silently remove a name from a universe. `"0"`
        # itself stays valid: a listed contract can genuinely not trade.
        volume = int(row["acml_vol"])  # type: ignore[arg-type]
        value = int(row["acml_tr_pbmn"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError) as exc:
        raise KisKlinesError(
            f"unparseable futures bar for {date}: {sorted(row)}"
        ) from exc
    # `float()` accepts "nan" and "inf", and neither is caught by a
    # positivity check -- a nan close propagates through every downstream
    # comparison without ever looking wrong.
    bad = [n for n, v in prices.items() if not math.isfinite(v) or v <= 0]
    if bad:
        raise KisKlinesError(
            f"futures bar for {date} has non-finite or non-positive {sorted(bad)}"
        )
    if volume < 0 or value < 0:
        raise KisKlinesError(
            f"futures bar for {date} has negative volume={volume} value={value}"
        )
    return FuturesBar(date=date, volume=volume, value=value, **prices)


def daily_bars(
    session: KisSession, code: str, start: str, end: str
) -> list[FuturesBar]:
    """Daily bars for one contract, **oldest first**, or `[]` if unserved.

    `[]` is a real answer and not a failure: KIS returns `rt_cd=0` with an
    empty `output2` both for a date range outside a contract's life and
    for a contract it no longer serves at all. The caller distinguishes
    the two -- this module cannot, and pretending otherwise would put a
    guess where a measurement belongs.
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": DIVISION_STOCK_FUTURES,
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": "D",
    }
    url = f"{session.host}{DAILY_PATH}?{urllib.parse.urlencode(params)}"
    payload = _get_with_retry(url, session.headers(TR_DAILY))
    if payload.get("rt_cd") != "0":
        raise KisKlinesError(
            f"KIS rejected the futures history for {code}: "
            f"rt_cd={payload.get('rt_cd')} msg_cd={payload.get('msg_cd')}"
        )
    if "output2" not in payload:
        # MEASURED, not assumed (2026-09-16): KIS sends `output2` on every
        # successful response, as `[]` both for a contract it has dropped
        # and for a live contract asked outside its life. So an ABSENT key
        # is not a shape this endpoint produces, and reading it as an empty
        # series is precisely the failure rd-o hit once -- a backfill that
        # treats `rt_cd=0` as success, records nothing, and reports a clean
        # run.
        raise KisKlinesError(
            f"no output2 in a successful response for {code}. An empty series "
            f"arrives as output2: [] -- an absent key means the response shape "
            f"has changed, not that the contract has no bars."
        )
    # This reader already had the cap-before-filter order right, unlike the
    # two it now shares an implementation with. It is routed through the
    # same contract anyway, because the defect D1 addresses is the
    # DUPLICATION -- the SPAC filter reached one copy of a rule and not the
    # other for exactly this reason, and a fourth reader written next year
    # should be correct without having to rediscover the ordering.
    rows = validated_output2(payload, cap=ROW_CAP, what=f"{code} {start}-{end}")
    bars = [parse_row(row) for row in rows]
    bars.sort(key=lambda bar: bar.date)
    return bars


def last_trading_day(bars: list[FuturesBar]) -> str | None:
    """A contract's own last bar -- its real last trading day.

    Read rather than computed. The expiry is the second Thursday of the
    expiry month (verified on the 2026-01/02/03/06/09 삼성전자 contracts),
    but a holiday moves it and KRX's moving lunar holidays are the gap
    `KrxMarketCalendar` still lists as unresolved. The series knows.
    """
    return bars[-1].date if bars else None


def fetch_contracts(
    session: KisSession,
    master: FuturesMaster,
    underlying: str,
    expiries: list[str],
    start: str,
    end: str,
    *, pause_s: float = 0.35,
) -> dict[str, list[FuturesBar]]:
    """`{expiry: bars}` for one underlying, skipping unserved contracts.

    An unserved expiry is simply absent from the result. That is the
    decaying-window fact, not an error, and the caller reports how much
    of its requested window it actually got.
    """
    out: dict[str, list[FuturesBar]] = {}
    for expiry in expiries:
        bars = daily_bars(session, master.contract_code(underlying, expiry), start, end)
        if bars:
            out[expiry] = bars
        time.sleep(pause_s)
    return out
