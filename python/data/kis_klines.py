"""KIS daily-bar client for KRX equities and indices -- Multi-Asset Task C.

The durable counterpart to `kis_probe.py`, which was the one-off Phase 0
investigation (`.planning/ms-b-kis-history-probe-result.md`). Everything
here is shaped by what that probe actually measured against the live
paper host, not by documentation:

- **The 100-row cap is silent.** A ~5-year request returned exactly 100
  rows with `rt_cd=0` -- BingX's behaviour, not Binance futures', which
  returns a real HTTP 400. So this module never issues a request wide
  enough to reach the cap, and **fails loud if one ever does** rather
  than quietly returning a truncated series.
- **`HTTP 500` is transient.** The probe's first run concluded the
  KOSPI200 index code was unsupported; a retry returned it correctly. So
  transport errors get a bounded retry, and a conclusion is never drawn
  from a single failed call.
- **The adjusted-price flag is the trap.** `FID_ORG_ADJ_PRC` is `0` for
  수정주가 and `1` for 원주가, and KIS's own published sample defaults to
  `1`. An unadjusted series shows Samsung dropping ~98% in a day across
  its 2018 50:1 split, which a momentum signal reads as a crash. This
  module therefore takes `adjusted` as a **required, explicit** argument
  with no default -- a caller must state which series it wants.
- **The token endpoint rate-limits after about three issuances**, on an
  allowance shared with the live `kis-paper` JVM. One token per process,
  cached to an owner-only file, keyed on (host, app-key fingerprint).

**This module cannot place an order and must stay that way.** Quotation
TR ids only, no account number, nothing imported from the trading path.
`tests/test_kis_probe_cannot_trade.py` enforces that for this file as
well as for the probe, and enforces that a credential never reaches an
output sink.

Storage symbols are namespaced by venue, following the
`BINANCE:BTCUSDT` / `BINANCE-FUTURES:BTCUSDT` convention already in the
store: `KRX:005930` for an equity, `KRX-INDEX:2001` for an index. The two
are separate namespaces because they are fetched from different
endpoints, and conflating them would make a six-digit equity code and a
four-digit index code collide in principle.

**A KRX trading date maps exactly onto UTC midnight.** KRX's continuous
session opens 09:00 KST and KST is UTC+9, so a bar dated `20240502`
genuinely opens at `2024-05-02T00:00:00Z`. This is an equality, not an
approximation, which is why `1d`'s existing 86,400,000 ms grid alignment
holds for KRX without a special case.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from typing import Any

from data.bingx_klines import KlineRow

PAPER_HOST = "https://openapivts.koreainvestment.com:29443"
REAL_HOST = "https://openapi.koreainvestment.com:9443"

TOKEN_PATH = "/oauth2/tokenP"
DAILY_ITEM_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
DAILY_INDEX_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"

TR_DAILY_ITEM = "FHKST03010100"
TR_DAILY_INDEX = "FHKUP03500100"

ADJUSTED = "0"  # 수정주가 -- split-adjusted
RAW = "1"  # 원주가 -- what KIS's own sample defaults to; see the module docstring

KRX_EQUITY_PREFIX = "KRX:"
KRX_INDEX_PREFIX = "KRX-INDEX:"

# The row caps, per endpoint, and they are NOT the same -- measured
# 2026-09-13 against the live paper host, after a first version of this
# module assumed one number for both and let a silently truncated index
# series through:
#
#   equities  100 rows/call
#   indices    50 rows/call, keeping the NEWEST rows and dropping the
#              oldest -- BingX's direction, not Binance futures'
#
# A 120-day request for KOSPI returned exactly 50 bars spanning only the
# last 73 days of it, with `rt_cd=0` and nothing else to say so. The stock
# endpoint answered the identical range with all 81. `FID_PW_DATA_INCU_YN`
# makes no difference either way.
EQUITY_ROWS_PER_CALL_CAP = 100
INDEX_ROWS_PER_CALL_CAP = 50
KIS_ROWS_PER_CALL_CAP = EQUITY_ROWS_PER_CALL_CAP  # backwards-compatible alias

# Sized so a page cannot reach its endpoint's cap. KRX trades on roughly
# 68% of calendar days, so 120 days is about 82 bars (under 100) and 60
# days about 41 (under 50).
DEFAULT_WINDOW_DAYS = 120
DEFAULT_INDEX_WINDOW_DAYS = 60


def rows_per_call_cap(*, is_index: bool) -> int:
    return INDEX_ROWS_PER_CALL_CAP if is_index else EQUITY_ROWS_PER_CALL_CAP


def default_window_days(*, is_index: bool) -> int:
    return DEFAULT_INDEX_WINDOW_DAYS if is_index else DEFAULT_WINDOW_DAYS

TIMEOUT_S = 20.0  # real observed KIS paper latency is 7-10s
INTER_REQUEST_DELAY_S = 0.2
_MAX_RETRIES = 4
_RETRY_BASE_DELAY_S = 1.0

MS_PER_DAY = 86_400_000


class KisKlinesError(RuntimeError):
    """A request could not be completed.

    Never carries a response body: a real KIS response embeds account
    numbers and balances, and this project already has a standing rule
    against putting those into an exception message, which lands in a
    persisted log.
    """


# ------------------------------------------------------------------ auth

TOKEN_CACHE_DIR = pathlib.Path(
    os.environ.get("XDG_RUNTIME_DIR") or pathlib.Path.home() / ".cache"
) / "kis_klines"
TOKEN_CACHE = TOKEN_CACHE_DIR / "token.json"
TOKEN_REUSE_S = 3000.0


def _app_key_fingerprint(app_key: str) -> str:
    """Identifies *which* app key a cached token belongs to, without
    storing the key. KIS binds a token to its issuing key, so reusing
    another key's token sends a mismatched pair and is rejected."""
    return hashlib.sha256(app_key.encode("utf-8")).hexdigest()[:32]


def _read_cached_token(host: str, app_key: str) -> str | None:
    try:
        fd = os.open(TOKEN_CACHE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            os.close(fd)
            return None
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            os.close(fd)
            return None
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, ValueError):
        return None
    try:
        if (
            blob.get("host") == host
            and blob.get("key") == _app_key_fingerprint(app_key)
            and time.time() - float(blob["at"]) < TOKEN_REUSE_S
        ):
            return str(blob["token"])
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _write_cached_token(host: str, app_key: str, token: str) -> None:
    """Owner-only directory, `O_NOFOLLOW`, `O_EXCL`, mode 0600.

    A bearer token on disk is a credential on disk. A world-writable path
    is unsafe because an ordinary write follows an existing symlink, so
    anyone able to pre-create the path has the token written into a file
    they own -- and a later `chmod` cannot help, because they are already
    the owner.
    """
    try:
        TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(TOKEN_CACHE_DIR, 0o700)
        try:
            os.unlink(TOKEN_CACHE)
        except FileNotFoundError:
            pass
        fd = os.open(
            TOKEN_CACHE,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "token": token,
                    "at": time.time(),
                    "host": host,
                    "key": _app_key_fingerprint(app_key),
                },
                fh,
            )
    except OSError:
        pass  # an uncacheable token still works for this run


def issue_token(host: str, app_key: str, app_secret: str, *, use_cache: bool = True) -> str:
    """`POST /oauth2/tokenP`, reusing a cached token where possible.

    The cache is not a convenience. `EGW00133` fires after roughly three
    issuances in a few minutes, and the allowance is per app key -- shared
    with the live `kis-paper` JVM, which needs it to renew its own token.
    """
    if use_cache:
        cached = _read_cached_token(host, app_key)
        if cached:
            return cached
    body = json.dumps(
        {"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret}
    ).encode("utf-8")
    req = urllib.request.Request(host + TOKEN_PATH, data=body, method="POST")
    req.add_header("content-type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise KisKlinesError(
            f"token issuance failed with HTTP {exc.code}. HTTP 403 here is normally "
            f"EGW00133, the token endpoint's own rate limit -- wait 60-90s."
        ) from None
    except (urllib.error.URLError, OSError) as exc:
        raise KisKlinesError(f"token issuance failed: {type(exc).__name__}") from None
    token = payload.get("access_token")
    if not token:
        raise KisKlinesError(
            f"no access_token in the token response (code={payload.get('error_code')})"
        )
    if use_cache:
        _write_cached_token(host, app_key, str(token))
    return str(token)


class KisSession:
    """One authenticated session: one token, reused for every call.

    Deliberately an object rather than free functions taking a token, so
    that a whole backfill is structurally one issuance. Passing a token
    around invites a caller to re-issue per symbol, which is what exhausts
    the shared rate limit.
    """

    def __init__(self, app_key: str, app_secret: str, *, host: str = PAPER_HOST) -> None:
        if not app_key or not app_secret:
            raise KisKlinesError("app_key and app_secret are both required")
        self._app_key = app_key
        self._app_secret = app_secret
        self.host = host
        self._token = issue_token(host, app_key, app_secret)

    def headers(self, tr_id: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }


# ------------------------------------------------------------------ http


def _get_with_retry(url: str, headers: dict[str, str]) -> dict[str, Any]:
    """One GET, retried on transport failure with exponential backoff.

    `HTTP 500` from KIS is transient -- measured, not assumed: the Phase 0
    probe concluded the KOSPI200 index code was unsupported on the
    strength of a single 500, and a retry returned it correctly. Drawing a
    conclusion from one failed call is the mistake this exists to prevent.
    """
    last: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        req = urllib.request.Request(url, method="GET")
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as exc:
            last = exc
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BASE_DELAY_S * (2**attempt))
    raise KisKlinesError(
        f"request failed after {_MAX_RETRIES} attempts: {type(last).__name__}"
    ) from None


# -------------------------------------------------------------- symbols


def equity_storage_symbol(code: str) -> str:
    """`005930` -> `KRX:005930`."""
    return f"{KRX_EQUITY_PREFIX}{code}"


def index_storage_symbol(code: str) -> str:
    """`2001` -> `KRX-INDEX:2001`."""
    return f"{KRX_INDEX_PREFIX}{code}"


def trading_date_to_ms(yyyymmdd: str) -> int:
    """A KRX trading date to the UTC-midnight instant of that date.

    Exact rather than approximate: KRX's continuous session opens 09:00
    KST and KST is UTC+9, so the bar genuinely opens at UTC midnight of
    its own date. That equality is why `1d`'s 86,400,000 ms grid
    alignment holds for KRX with no special case.
    """
    if len(yyyymmdd) != 8 or not yyyymmdd.isdigit():
        raise KisKlinesError(f"not a YYYYMMDD trading date: {yyyymmdd!r}")
    date = dt.date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:]))
    return int(
        dt.datetime(date.year, date.month, date.day, tzinfo=dt.timezone.utc).timestamp() * 1000
    )


def ms_to_trading_date(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y%m%d")


# --------------------------------------------------------------- parsing


def _decimal(raw: object, field: str, date: str) -> Decimal:
    if raw is None or raw == "":
        raise KisKlinesError(f"{field} missing on the {date} bar")
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        raise KisKlinesError(f"{field} is not a number on the {date} bar") from None
    if not value.is_finite():
        raise KisKlinesError(f"{field} is not finite on the {date} bar")
    return value


def _parse_row(row: dict[str, Any], *, is_index: bool) -> KlineRow:
    """One `output2` entry to a `KlineRow`.

    Fails closed on a missing or unparseable field rather than
    substituting a zero. KIS returns field names in per-endpoint casing
    and Jackson-style silent-null parsing is exactly how this project's
    KIS integration got three endpoints wrong once already; the Python
    equivalent is a `.get()` that quietly yields `None`.

    Index bars carry `bstp_nmix_*` names and no traded value, so
    `quote_volume` is `None` for them -- absent, not zero.
    """
    date = str(row.get("stck_bsop_date") or "")
    if not date:
        raise KisKlinesError("a row arrived with no stck_bsop_date")

    if is_index:
        o = _decimal(row.get("bstp_nmix_oprc"), "bstp_nmix_oprc", date)
        h = _decimal(row.get("bstp_nmix_hgpr"), "bstp_nmix_hgpr", date)
        low = _decimal(row.get("bstp_nmix_lwpr"), "bstp_nmix_lwpr", date)
        c = _decimal(row.get("bstp_nmix_prpr"), "bstp_nmix_prpr", date)
        volume = _decimal(row.get("acml_vol") or "0", "acml_vol", date)
        quote_volume = None
    else:
        o = _decimal(row.get("stck_oprc"), "stck_oprc", date)
        h = _decimal(row.get("stck_hgpr"), "stck_hgpr", date)
        low = _decimal(row.get("stck_lwpr"), "stck_lwpr", date)
        c = _decimal(row.get("stck_clpr"), "stck_clpr", date)
        volume = _decimal(row.get("acml_vol"), "acml_vol", date)
        # 거래대금 -- what the KR-10 universe rule ranks on. close * volume
        # is not a substitute: this is price times quantity summed over the
        # day's trades, not the closing price times the day's total.
        quote_volume = _decimal(row.get("acml_tr_pbmn"), "acml_tr_pbmn", date)

    if not (h >= o and h >= c and h >= low and low <= o and low <= c):
        raise KisKlinesError(
            f"OHLC is internally inconsistent on {date}: o={o} h={h} l={low} c={c}"
        )

    return KlineRow(
        open_time_ms=trading_date_to_ms(date),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=volume,
        quote_volume=quote_volume,
    )


# --------------------------------------------------------------- fetching


def fetch_daily_page(
    session: KisSession,
    code: str,
    start: str,
    end: str,
    *,
    adjusted: str,
    is_index: bool = False,
) -> list[KlineRow]:
    """One `inquire-daily-*chartprice` call, ascending by date.

    `adjusted` is required and has no default -- see the module docstring.
    Raises if the response reaches the row cap, because a capped response
    is silently truncated and this module's contract is that a page is
    complete.
    """
    if adjusted not in (ADJUSTED, RAW):
        raise KisKlinesError(f"adjusted must be {ADJUSTED!r} or {RAW!r}, got {adjusted!r}")
    if is_index and adjusted != ADJUSTED:
        # An index has no split to adjust for; accepting RAW here would
        # imply a distinction the endpoint does not make.
        raise KisKlinesError("an index series has no raw/adjusted distinction")

    params = {
        "FID_COND_MRKT_DIV_CODE": "U" if is_index else "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": "D",
    }
    if not is_index:
        params["FID_ORG_ADJ_PRC"] = adjusted

    path = DAILY_INDEX_PATH if is_index else DAILY_ITEM_PATH
    tr = TR_DAILY_INDEX if is_index else TR_DAILY_ITEM
    url = f"{session.host}{path}?{urllib.parse.urlencode(params)}"
    payload = _get_with_retry(url, session.headers(tr))

    if payload.get("rt_cd") != "0":
        raise KisKlinesError(
            f"KIS rejected the request for {code} {start}..{end}: "
            f"rt_cd={payload.get('rt_cd')} msg_cd={payload.get('msg_cd')}"
        )

    raw_rows = payload.get("output2") or []
    if not isinstance(raw_rows, list):
        raise KisKlinesError(f"output2 is not a list for {code} {start}..{end}")
    rows = [r for r in raw_rows if isinstance(r, dict) and r.get("stck_bsop_date")]

    cap = rows_per_call_cap(is_index=is_index)
    if len(rows) >= cap:
        raise KisKlinesError(
            f"{code} {start}..{end} returned {len(rows)} rows, at or over this "
            f"endpoint's {cap}-row cap. KIS truncates silently and keeps the "
            f"NEWEST rows, so this page cannot be assumed complete -- narrow "
            f"the window."
        )

    parsed = [_parse_row(r, is_index=is_index) for r in rows]
    parsed.sort(key=lambda k: k.open_time_ms)
    return parsed


def iter_daily_range(
    session: KisSession,
    code: str,
    start: str,
    end: str,
    *,
    adjusted: str,
    is_index: bool = False,
    window_days: int | None = None,
) -> Iterator[KlineRow]:
    """Every bar in the inclusive date range `[start, end]`, ascending.

    Pages forward in `window_days` calendar-day slices, each sized to stay
    below **this endpoint's** silent row cap -- which differs between
    equities (100) and indices (50), so the default window differs too.
    Duplicate dates across a page boundary are dropped, so an
    inclusive-boundary endpoint cannot double-count.
    """
    if window_days is None:
        window_days = default_window_days(is_index=is_index)
    if window_days < 1:
        raise KisKlinesError("window_days must be positive")
    first = dt.date(int(start[:4]), int(start[4:6]), int(start[6:]))
    last = dt.date(int(end[:4]), int(end[4:6]), int(end[6:]))
    if first > last:
        raise KisKlinesError(f"start {start} is after end {end}")

    seen: set[int] = set()
    cursor = first
    while cursor <= last:
        window_end = min(cursor + dt.timedelta(days=window_days - 1), last)
        page = fetch_daily_page(
            session,
            code,
            cursor.strftime("%Y%m%d"),
            window_end.strftime("%Y%m%d"),
            adjusted=adjusted,
            is_index=is_index,
        )
        for row in page:
            if row.open_time_ms not in seen:
                seen.add(row.open_time_ms)
                yield row
        cursor = window_end + dt.timedelta(days=1)
        if cursor <= last:
            time.sleep(INTER_REQUEST_DELAY_S)


# ------------------------------------------------- calendar-aware gaps


class ReferenceCalendarError(KisKlinesError):
    """The reference series cannot be a trading calendar.

    Raised when a symbol has bars on dates the reference does not. An
    index prints on every day the market is open, so a stock trading on a
    day the index did not is impossible -- it means the reference series
    is itself incomplete, and every "no gaps" verdict computed against it
    is worthless.

    This exists because that happened. A 120-day KOSPI request came back
    silently truncated to its newest 50 bars, and the first version of
    `missing_trading_days` compared only `reference - present`: it
    reported **zero** missing days for a Samsung series while quietly
    discarding the 31 dates that proved its own reference was broken.
    Half a diff is not a check.
    """


def missing_trading_days(
    reference_days: set[int], present_days: set[int]
) -> list[int]:
    """Trading days the reference has and the series does not, ascending.

    KRX trades roughly 245 days a year, so `store.find_missing_ranges` --
    which diffs against an *arithmetic* sequence -- reports every weekend
    and holiday as a gap, on the order of 120 false positives per symbol
    per year. That function is correct for crypto and unusable here.

    The reference is an **index** series: an index prints on exactly the
    days the market is open, so it is a true trading calendar that needs
    no holiday table and cannot go stale. It also distinguishes a market
    closure from a stock-specific halt -- a date the index has and a
    single name does not is a halt, which a calendar table cannot see.

    **Both directions are checked.** Days the series has and the reference
    does not raise `ReferenceCalendarError`, because that is proof the
    reference is not a valid calendar rather than a fact about the series.

    Full reasoning, including the two rejected alternatives:
    `.planning/ms-b-kis-history-probe-result.md` §6.1.
    """
    impossible = present_days - reference_days
    if impossible:
        sample = [ms_to_trading_date(m) for m in sorted(impossible)[:5]]
        raise ReferenceCalendarError(
            f"the series has {len(impossible)} bars on dates the reference "
            f"series lacks (e.g. {sample}). A market index prints whenever the "
            f"market is open, so the reference is incomplete -- most likely "
            f"silently truncated at its row cap. Any gap verdict against it "
            f"would be meaningless."
        )
    return sorted(reference_days - present_days)
