"""Binance historical klines client -- public, unauthenticated
`GET /api/v3/klines` (spot) and `GET /fapi/v1/klines` (USDT-M perpetual
futures). Stdlib-only (`urllib`), no `requests`/`httpx` -- same
discipline as `bingx_klines.py`/`fred_client.py`. See
`.planning/sr-z-binance-data-research.md` for the full investigation this
task ran, and CLAUDE.md's "Strategy Research Methodology" for why a
second exchange's data matters here: BingX's own retention (max ~5.2
years at `1d`) gives a detectable-Sharpe floor of ~0.72 at one-sided
α=0.05, which may be unable to distinguish a real edge in the credible
institutional range (0.4-0.8 Sharpe) from noise. Binance spot BTCUSDT
goes back to 2017-08-17 -- roughly 9 years, materially improving that
floor.

`base_url` and `path` are always caller-supplied arguments, never
literals used directly inside a request -- same "caller decides the
host" pattern as `bingx_klines.py`, though for a different reason here
(mirroring `fred_client.py`'s own reasoning, not `bingx_klines.py`'s):
this project builds no live-trading surface against Binance at all (no
`ExchangeAdapter`, no credentials, no order placement -- this is
read-only historical market data only), so there is no
`bingx-hostname-guard.yml`-style live/demo safety distinction to
enforce. The sole reason `base_url`/`path` are threaded through as
parameters is testability against a local fake server;
`backfill_binance.py` is the only place a real default Binance hostname
literal lives (mirroring where `DEFAULT_FRED_BASE_URL` lives in
`backfill_macro.py`, not where `BINGX_BASE_URL` is forced through an
env var).

`fetch_klines_page`/`iter_klines_range` return/yield
`bingx_klines.KlineRow` (imported, not redefined) -- the core OHLCV wire
row shape (`open_time_ms`, `open`, `high`, `low`, `close`, `volume`) is
identical between BingX and Binance, so `store.py`'s existing
`upsert_klines`/`fetch_klines`/`find_missing_ranges` work against
Binance data completely unchanged; no new schema or new row type was
needed for the original (Task Z) version of this module. Scalping
Strategy Research Task S5 later added two further, optional
`KlineRow` fields this module now also populates from the wire (see
below) -- see `bingx_klines.KlineRow`'s own docstring for why they are
additive and don't disturb this paragraph's original claim for the
6 core fields. `symbol` values passed to `store.py` are
namespaced (`"BINANCE:BTCUSDT"`/`"BINANCE-FUTURES:BTCUSDT"`) by
`backfill_binance.py`, not here -- this module always uses Binance's own
raw wire symbol (`"BTCUSDT"`, no dash, no exchange prefix) in requests,
since that's what the real API expects.

Real API findings, verified directly against the live production
endpoints (`api.binance.com` spot, `fapi.binance.com` USDT-M futures)
2026-08-05 -- full rigor matching CLAUDE.md's "Exchange API Facts --
BingX" bar:

- **Response shape is a bare JSON array of arrays, by POSITION, not an
  object with a `code`/`msg`/`data` envelope the way every BingX
  endpoint in this pipeline works.** Each row:
  `[open_time_ms, open, high, low, close, volume, close_time_ms,
  quote_asset_volume, num_trades, taker_buy_base_volume,
  taker_buy_quote_volume, ignore]` -- 12 elements. `open_time_ms`/
  `close_time_ms`/`num_trades` are bare (unquoted) JSON integers; every
  OHLCV/volume field (including the two taker-buy fields) is a quoted
  JSON string, confirmed via `type()` on every field checked,
  identically for both spot and futures. `json.loads(..., parse_float=
  Decimal)` is used anyway, same defensive-in-depth reasoning as
  `bingx_klines.py`'s own module docstring. **`taker_buy_base_volume`
  (index 9) and `taker_buy_quote_volume` (index 10) are parsed and
  populated (Scalping Strategy Research Task S5)** -- the volume of this
  candle's trades whose taker (the side that crossed the spread) was a
  *buyer*; `quote_asset_volume`, `num_trades`, and `close_time_ms`
  (indices 6-8) remain genuinely unused. This is the concrete, real
  order-flow-imbalance proxy Task S5's own planning document
  (`.planning/scalp-s5-binance-1m-orderflow-infra.md`) exists to make
  available -- BingX's own kline wire format has no buyer/seller
  breakdown of any kind, confirmed directly against that endpoint's own
  response shape (see `bingx_klines.py`'s module docstring), so this
  field is Binance-only and `None` for every BingX-sourced row.
- **`endTime` is INCLUSIVE, not half-open** -- a genuine, load-bearing
  divergence from `bingx_klines.py`'s `[start, end)` convention.
  Confirmed by a real `startTime == endTime` request returning exactly
  one row (the candle whose open time equals that value); a half-open
  convention would have returned zero. This module's own public
  contract stays half-open `[start_ms, end_ms)` (matching every other
  function in this pipeline, and what `store.find_missing_ranges`
  produces), so `fetch_klines_page` translates internally by requesting
  Binance's real `endTime = end_ms - 1`. This is safe for every interval
  this pipeline uses (`15m`/`1h`/`1d`, all >= 900,000ms steps): the
  candle sitting exactly at `end_ms` (if any) has its open time excluded
  by the `-1`, while every candle strictly before `end_ms` is still
  `<= end_ms - 1` and included. Deliberately translated here (unlike
  `fred_client.py`'s own inclusive-range divergence, which is
  deliberately NOT translated) because the underlying unit is already
  epoch milliseconds on both sides of the translation -- no calendar
  judgment call is involved the way there would be translating FRED's
  inclusive *dates*.
- **Silent over-limit capping keeps the OLDEST rows (closest to
  `startTime`), not the newest -- the opposite of BingX's verified
  behavior.** Confirmed empirically for both spot and futures: a request
  whose `[startTime, endTime]` span covers more candles than `limit`
  returns the `limit` candles starting from `startTime` forward,
  ascending; the newest candles in the requested range are silently
  dropped, not the oldest. This also means Binance's own rows come back
  **oldest-first (ascending)**, not newest-first like BingX -- no
  reordering needed before storage, same as `fred_client.py`'s FRED
  rows. `iter_klines_range` below still derives its next cursor from
  `max(row.open_time_ms) + step` (the same defensive pattern
  `bingx_klines.py` uses) even though Binance's own capping direction
  makes the naive "assume the page fully covers `limit` candles"
  mistake structurally safer here than it is for BingX (a truncated
  Binance page is always missing its *newest* candles, i.e. exactly the
  ones the next chunk's request would need to re-cover regardless) --
  kept for consistency and because it is still strictly correct.
- **Max `limit` differs by market**: spot's hard max is **1000**
  (`limit=1001` is silently capped to 1000 rows, confirmed by exact row
  count -- not a `400`, unlike FRED's hard-error-over-limit behavior);
  futures' hard max is **1500** (`limit=1500` returns exactly 1500 rows,
  `limit=1501` is a real `HTTP 400`:
  `{"code":-1130,"msg":"Data sent for parameter 'limit' is not
  valid."}` -- futures rejects over-limit outright rather than silently
  capping, a real per-market divergence). `MAX_CANDLES_PER_REQUEST` below
  is deliberately pinned to spot's lower **1000** for both markets --
  simpler than a per-market constant, and this pipeline's real usage
  (spot's full ~9-year history, futures' lighter comparison check) never
  needs futures' extra headroom.
- **A range starting before a symbol's true listing date returns an
  empty array, not an error and not padding** -- confirmed: requesting
  spot BTCUSDT for 2017-01-01 through 2017-08-15 (before the real
  2017-08-17 start) returns `[]`. A range straddling the true start
  (e.g. one day before through several days after) returns rows
  starting from the real earliest candle, not an error either.
- **A malformed request (bad `symbol`, `limit` out of bounds when
  futures actually rejects it, etc.) is a real non-2xx HTTP status
  (confirmed `400`) with a JSON **object** body**
  (`{"code": <int>, "msg": "..."}`), a different shape from the
  success array -- confirmed identically for spot (`limit=0` ->
  `{"code":-1102,...}`) and futures (`symbol=NOTREAL` ->
  `{"code":-1121,"msg":"Invalid symbol."}`). This module validates
  client-side first (misaligned/inverted range, out-of-bounds `limit`)
  for the same fail-fast reason `bingx_klines.py` does, so this path is
  only actually reachable in practice for a genuinely bad `symbol`.
- **Rate limits are real, numeric, and documented** (a genuine
  improvement in confidence over both `bingx_klines.py`'s and
  `fred_client.py`'s own undocumented-limit sections) -- confirmed via
  Binance's official API docs/changelog and cross-checked live against
  each host's own `GET .../exchangeInfo` `rateLimits` field: spot
  `GET /api/v3/klines` has a flat weight of **2** regardless of `limit`,
  against a **6000/minute** per-IP `REQUEST_WEIGHT` budget; futures
  `GET /fapi/v1/klines` has a **tiered** weight (1 for `limit<=100`, 2
  for `<=500`, 5 for `<=1000`, 10 for `>1000`) against a **2400/minute**
  per-IP budget -- both live-confirmed via `exchangeInfo` 2026-08-05.
  Even a full ~9-year spot backfill at `1d` (a handful of 1000-candle
  pages) uses a trivial fraction of either budget, so
  `INTER_REQUEST_DELAY_S` below is kept at the same conservative fixed
  value `bingx_klines.py`/`fred_client.py` already use rather than tuned
  any tighter -- there is no need to.
- **HTTP 418 is a real, distinct status**: Binance's own documented
  behavior escalates repeated rate-limit violations to a temporary IP
  ban signaled by `418` (not `429`) -- not observed live in this
  session (no violation was ever triggered), but deliberately excluded
  from `_RETRYABLE_STATUSES` below on principle: retrying into an active
  ban would only extend it, the same "don't compound a real failure"
  reasoning `bingx_klines.py` already applies to its own non-retryable
  statuses.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from typing import Iterator

from data._grid import interval_ms, require_valid_range
from data.bingx_klines import KlineRow

MAX_CANDLES_PER_REQUEST = 1000  # spot's hard max; also used for futures, see module docstring

# Real, documented, live-confirmed rate limits exist for this API (see
# module docstring) -- 6000/min (spot) and 2400/min (futures) per IP,
# both far above what this pipeline's real usage ever approaches. This
# fixed delay is kept anyway, for the same "don't need to tune it any
# tighter" reasoning as bingx_klines.py's own INTER_REQUEST_DELAY_S.
INTER_REQUEST_DELAY_S = 0.25

# 418 (IP auto-ban after repeated violations) is deliberately NOT
# included -- see module docstring.
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 5
_RETRY_BASE_DELAY_S = 1.0

SPOT_KLINES_PATH = "/api/v3/klines"
FUTURES_KLINES_PATH = "/fapi/v1/klines"


class BinanceKlinesError(RuntimeError):
    """Raised on an error-object response body, a malformed/unparseable
    response, or a non-retryable HTTP error.
    """


def fetch_klines_page(
    base_url: str,
    path: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = MAX_CANDLES_PER_REQUEST,
) -> list[KlineRow]:
    """Fetch one page of klines for the half-open range
    `[start_ms, end_ms)`.

    `path` is `SPOT_KLINES_PATH` or `FUTURES_KLINES_PATH` (or any other
    caller-supplied path, e.g. for test injection) -- both real endpoints
    share an identical wire shape, see module docstring.

    Does **not** guarantee the returned rows cover the full requested
    range -- see the module docstring's silent-capping finding (oldest
    rows kept, not newest). Callers that need a large range fully
    covered must use `iter_klines_range`.

    Raises `ValueError` for misaligned/inverted input or an out-of-range
    `limit` (checked against this pipeline's own half-open input, before
    any wire translation). Raises `BinanceKlinesError` for an
    error-object response, a malformed body, or a non-retryable HTTP
    error.
    """
    step = interval_ms(interval)
    require_valid_range(start_ms, end_ms, step)
    if not (1 <= limit <= MAX_CANDLES_PER_REQUEST):
        raise ValueError(f"limit must be in 1..{MAX_CANDLES_PER_REQUEST}, got {limit}")

    query = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            # Binance's real endTime is inclusive -- translate this
            # module's half-open contract by requesting one ms less than
            # the exclusive end_ms. See module docstring.
            "endTime": end_ms - 1,
            "limit": limit,
        }
    )
    url = f"{base_url}{path}?{query}"

    body = _get_with_retry(url)

    try:
        payload = json.loads(body, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise BinanceKlinesError(f"malformed JSON response from {url}: {exc}") from exc

    if isinstance(payload, dict):
        # Binance's real error shape: {"code": <int>, "msg": "..."}.
        # Every error actually observed live arrives via a non-2xx HTTP
        # status (already raised by _get_with_retry before parsing ever
        # runs), so reaching here means a 2xx response carried an
        # error-shaped body -- not observed live, handled anyway rather
        # than assumed impossible (see module docstring).
        raise BinanceKlinesError(f"Binance returned error code={payload.get('code')!r} msg={payload.get('msg')!r}")

    if not isinstance(payload, list):
        raise BinanceKlinesError(f"Binance response body is not a JSON array: {payload!r}")

    rows = [_parse_row(row) for row in payload]

    if len(rows) > limit:
        # Real Binance never does this -- see module docstring's
        # silent-capping finding. Fail loud rather than silently accept
        # more than was asked for, same as bingx_klines.py.
        raise BinanceKlinesError(f"Binance returned {len(rows)} rows, more than requested limit={limit}")

    return rows


def iter_klines_range(
    base_url: str,
    path: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = MAX_CANDLES_PER_REQUEST,
) -> Iterator[KlineRow]:
    """Walk the half-open range `[start_ms, end_ms)` in `<= limit`-candle
    chunks, yielding every `KlineRow` found in ascending (oldest-first)
    order -- Binance's own real ordering, see module docstring; no
    reordering needed the way BingX's newest-first responses would
    require if this pipeline cared about global order (it doesn't --
    `store.upsert_klines` is order-independent).

    Structurally mirrors `bingx_klines.iter_klines_range` (same cursor-
    from-actual-max-row-time discipline, same empty-page-advances-by-
    chunk-width handling for a genuine gap or a walk past retention) --
    see that function's docstring for the full reasoning, which applies
    unchanged here even though Binance's own capping direction makes the
    naive-arithmetic failure mode this guards against structurally
    unreachable for Binance specifically (see module docstring).
    """
    step = interval_ms(interval)
    require_valid_range(start_ms, end_ms, step)
    if not (1 <= limit <= MAX_CANDLES_PER_REQUEST):
        raise ValueError(f"limit must be in 1..{MAX_CANDLES_PER_REQUEST}, got {limit}")

    cursor = start_ms
    chunk_span = limit * step
    first_request = True

    while cursor < end_ms:
        if not first_request:
            time.sleep(INTER_REQUEST_DELAY_S)
        first_request = False

        chunk_end = min(cursor + chunk_span, end_ms)
        page = fetch_klines_page(base_url, path, symbol, interval, cursor, chunk_end, limit=limit)

        if page:
            max_time = max(row.open_time_ms for row in page)
            cursor = max_time + step
            yield from page
        else:
            cursor = chunk_end


def _parse_row(row: object) -> KlineRow:
    """`len(row) < 6` (the original, unwidened guard) is still the only
    hard requirement -- a short (e.g. 6-element) row still parses
    successfully, with the two taker-buy fields left at `KlineRow`'s own
    `None` default, rather than being treated as malformed. This matters
    for real backward compatibility, not just test convenience: it means
    a caller that ever legitimately receives a shorter row shape (e.g. a
    hypothetical future/alternate endpoint, or an existing hand-built
    fixture) degrades gracefully to "no order-flow data available for
    this row" instead of a hard failure -- the same "missing data is
    `None`, not an error" contract `KlineRow`'s own docstring already
    establishes for BingX rows.
    """
    if not isinstance(row, list) or len(row) < 6:
        raise BinanceKlinesError(f"malformed kline row (not an array of >=6 elements): {row!r}")
    try:
        return KlineRow(
            open_time_ms=int(row[0]),
            open=Decimal(row[1]),
            high=Decimal(row[2]),
            low=Decimal(row[3]),
            close=Decimal(row[4]),
            volume=Decimal(row[5]),
            taker_buy_base_volume=Decimal(row[9]) if len(row) > 9 else None,
            taker_buy_quote_volume=Decimal(row[10]) if len(row) > 10 else None,
        )
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise BinanceKlinesError(f"malformed kline row: {row!r}") from exc


def _get_with_retry(url: str) -> str:
    # Byte-for-byte the same HTTP-transport retry logic as
    # bingx_klines.py's/fred_client.py's _get_with_retry -- duplicated
    # deliberately rather than factored into a shared helper, same
    # precedent those two modules already established between themselves.
    last_exc: BaseException | None = None
    for attempt in range(_MAX_RETRIES):
        if attempt > 0:
            time.sleep(_RETRY_BASE_DELAY_S * (2 ** (attempt - 1)))
        request = urllib.request.Request(url, headers={"User-Agent": "trading-engine-research/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in _RETRYABLE_STATUSES:
                raise BinanceKlinesError(f"Binance returned non-retryable HTTP {exc.code} for {url}") from exc
            continue
        except urllib.error.URLError as exc:
            last_exc = exc
            continue
        except OSError as exc:
            # See bingx_klines.py's own _get_with_retry docstring comment
            # for why this catches a read-time socket error distinct
            # from URLError.
            last_exc = exc
            continue

    raise BinanceKlinesError(f"exceeded {_MAX_RETRIES} retries fetching {url}: {last_exc}") from last_exc
