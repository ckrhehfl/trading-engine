"""BingX historical klines client -- public, unauthenticated
`GET /openApi/swap/v3/quote/klines`. Stdlib-only (`urllib`), no
`requests`/`httpx` -- see CLAUDE.md's "Strategy Research Operational
Design" data-pipeline section.

`base_url` is always a caller-supplied argument, never a literal in this
module -- same "caller decides the host" pattern as
`java/exchange/.../BingXAdapter.java` and
`java/runtime/.../BingXPriceFeed.java`. The actual BingX hostname must
never appear as a literal string anywhere under `java/` or `python/` --
enforced by `.github/workflows/bingx-hostname-guard.yml`.

Response shape verified directly against the live production endpoint
2026-07-26 (see `.planning/sr-a-data-pipeline.md` for the full
investigation):

- Envelope: `{"code": 0, "msg": "", "data": [...]}` -- same pattern as
  every other BingX endpoint already documented in CLAUDE.md.
- Each row: `{"open": "...", "high": "...", "low": "...", "close":
  "...", "volume": "...", "time": <int ms, open time>}`. OHLCV fields
  come back as JSON strings already; `time` is a bare (unquoted)
  integer. `json.loads(..., parse_float=Decimal)` is used regardless,
  per CLAUDE.md's explicit instruction, as a defensive measure against
  BingX ever sending a bare (non-string) numeric field for a price --
  `Decimal(str(x))` after a plain `json.loads` would already have lost
  precision in the float round-trip *before* the string conversion ever
  ran.
- Rows come back **newest-first (descending `time`)** within the
  requested range -- confirmed for both an unbounded request and a
  request with an explicit `startTime`/`endTime`. This is the opposite
  of `GET /openApi/swap/v2/quote/trades`, which
  `java/runtime/.../BingXPriceFeedTest.java` already verified is
  oldest-first. Pagination code here must not assume either order; it
  always derives the next cursor from `max()` over each page's parsed
  timestamps, never from array position.
- **A request whose `[startTime, endTime)` span covers more candles than
  `limit` is silently capped to the `limit` candles closest to
  `endTime` (the newest ones in range) -- not the oldest `limit`
  candles from `startTime`.** Confirmed empirically: a 1500-candle-wide
  request with `limit=1000` returned exactly 1000 rows, spanning from
  `endTime - 1000*step` up to `endTime - step`; the oldest ~500 candles
  in the requested range were silently dropped, not returned and not
  errored on. This is the concrete mechanism behind CLAUDE.md's
  "`limit` is not a reliable count guarantee" note, and it is *why*
  `fetch_klines_page` below does not try to guess or guard against a
  too-wide span itself -- it just returns whatever BingX actually sent,
  however many rows that is, and it is `iter_klines_range`'s job (by
  always chunking to `<= limit` candles *before* requesting) to make
  sure a too-wide span is never actually sent, and to derive its next
  cursor only from real returned timestamps rather than assume any
  particular count.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator

from data._grid import interval_ms, require_valid_range

MAX_CANDLES_PER_REQUEST = 1000

# This endpoint's rate limit isn't documented anywhere BingX publishes
# (CLAUDE.md's "Exchange API Facts" section only lists documented limits
# for authenticated endpoints) -- a conservative fixed delay between page
# requests plus retry-with-backoff on 429/5xx is the mitigation, not a
# measured/tuned number.
INTER_REQUEST_DELAY_S = 0.25

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 5
_RETRY_BASE_DELAY_S = 1.0

_KLINES_PATH = "/openApi/swap/v3/quote/klines"


class BingXKlinesError(RuntimeError):
    """Raised on a non-zero `code` envelope, a malformed/unparseable
    response body, or a non-retryable HTTP error.
    """


@dataclass(frozen=True)
class KlineRow:
    """One OHLCV bar as returned by BingX -- not `backtest.kline.Kline`
    (that type is datetime-keyed and Python-internal to the backtest
    engine; this one is ms-timestamp-keyed, matching the wire format,
    and is what `store.py` persists).
    """

    open_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def fetch_klines_page(
    base_url: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = MAX_CANDLES_PER_REQUEST,
) -> list[KlineRow]:
    """Fetch one page of klines for the half-open range
    `[start_ms, end_ms)`.

    Does **not** guarantee the returned rows cover the full requested
    range -- see the module docstring's silent-capping finding. Callers
    that need a large range fully covered must use `iter_klines_range`,
    which chunks requests to `<= limit` candles and re-derives its
    cursor from actual returned data rather than assuming completeness.

    Raises `ValueError` for misaligned/inverted input or an out-of-range
    `limit` (fails loud rather than silently clamping -- see
    `data._grid`). Raises `BingXKlinesError` for a non-zero `code`, a
    malformed body, or a non-retryable HTTP error.
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
            "endTime": end_ms,
            "limit": limit,
        }
    )
    url = f"{base_url}{_KLINES_PATH}?{query}"

    body = _get_with_retry(url)

    try:
        payload = json.loads(body, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise BingXKlinesError(f"malformed JSON response from {url}: {exc}") from exc

    if not isinstance(payload, dict):
        raise BingXKlinesError(f"response body is not a JSON object: {payload!r}")

    code = payload.get("code")
    if code != 0:
        raise BingXKlinesError(f"BingX returned non-zero code={code!r} msg={payload.get('msg')!r}")

    raw_rows = payload.get("data")
    if not isinstance(raw_rows, list):
        raise BingXKlinesError(f"BingX response 'data' is not a list: {raw_rows!r}")

    rows = [_parse_row(row) for row in raw_rows]

    if len(rows) > limit:
        # Real BingX never does this (limit is an upper bound, not a
        # target) -- this would mean either BingX's behavior changed or
        # this fake-server-in-tests has a bug. Fail loud either way
        # rather than silently accepting more than was asked for.
        raise BingXKlinesError(f"BingX returned {len(rows)} rows, more than requested limit={limit}")

    return rows


def iter_klines_range(
    base_url: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    limit: int = MAX_CANDLES_PER_REQUEST,
) -> Iterator[KlineRow]:
    """Walk the half-open range `[start_ms, end_ms)` in `<= limit`-candle
    chunks, yielding every `KlineRow` found (in per-page order as
    returned by BingX -- currently newest-first within a page; callers
    needing a strict global order should sort the result themselves).

    The pagination cursor always advances from `max(row.open_time_ms for
    row in page) + step` -- the actual last (i.e. newest) bar's
    timestamp plus one grid step -- never from
    `start_ms + limit * interval_ms` arithmetic. That arithmetic would
    silently assume a page fully covers `limit` candles' worth of time
    even when BingX returned fewer (a real gap in exchange data, or the
    range walking past the exchange's retention start) -- exactly the
    failure mode CLAUDE.md's "don't trust `limit`" rule exists to
    prevent.

    An empty page is treated as a normal stopping condition for that
    chunk, not an error: it means BingX has no data in that sub-range
    (either genuinely no trading happened, or the walk has gone past
    the exchange's actual retention start when walking forward from an
    early sentinel date). The cursor still advances -- by the full
    chunk width, since there's no row to derive a tighter cursor from --
    so an empty stretch doesn't stall the walk.
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
        page = fetch_klines_page(base_url, symbol, interval, cursor, chunk_end, limit=limit)

        if page:
            max_time = max(row.open_time_ms for row in page)
            cursor = max_time + step
            yield from page
        else:
            cursor = chunk_end


def _parse_row(row: object) -> KlineRow:
    if not isinstance(row, dict):
        raise BingXKlinesError(f"malformed kline row (not an object): {row!r}")
    try:
        return KlineRow(
            open_time_ms=int(row["time"]),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=Decimal(row["volume"]),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise BingXKlinesError(f"malformed kline row: {row!r}") from exc


def _get_with_retry(url: str) -> str:
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
                raise BingXKlinesError(f"BingX returned non-retryable HTTP {exc.code} for {url}") from exc
            continue
        except urllib.error.URLError as exc:
            last_exc = exc
            continue
        except OSError as exc:
            # response.read() can raise a raw socket-level error (e.g. a
            # connection reset mid-body) that urlopen() never wraps into
            # URLError -- URLError only covers failures up through
            # establishing the response, not failures while reading its
            # body. urllib.error.{URL,HTTP}Error are themselves OSError
            # subclasses (confirmed via their __mro__), but Python tries
            # except clauses top-to-bottom, so those two already-handled
            # cases never fall through to this one -- only a genuine
            # read-time OSError does. Retried the same as any other
            # transient network failure.
            last_exc = exc
            continue

    raise BingXKlinesError(f"exceeded {_MAX_RETRIES} retries fetching {url}: {last_exc}") from last_exc
