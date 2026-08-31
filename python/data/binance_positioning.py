"""Binance USDⓈ-M futures positioning and forced-flow endpoints.

Trade Management Task B. See
`.planning/tm-b-signal-and-data-catalogue.md` for what each series means
and why these three signal families were never fetched before.

## Why this is urgent rather than merely useful

The `/futures/data/` endpoints retain roughly **30 days**. Unlike klines,
this history **cannot be backfilled** -- whatever is not captured is gone
permanently. Every day this does not run is a day that can never be
studied.

That is the entire argument for building it before any strategy needs
it: the alternative is discovering in three months that the most
promising untested direction still cannot be tested.

## Read-only, no credentials

Every endpoint here is public market data. This module never signs a
request, never sends a key, and cannot place an order. It is a data
source in exactly the sense `binance_klines.py` already is -- CLAUDE.md's
"Binance is a read-only historical-data source for research" line covers
it unchanged.

## The metrics, and the caveats that belong with them

| metric | endpoint | means |
|---|---|---|
| `open_interest` / `open_interest_value` | `openInterestHist` | how much leverage exists. Directionless alone |
| `global_long_account` / `global_short_account` / `global_long_short_ratio` | `globalLongShortAccountRatio` | retail crowding, headcount-weighted |
| `top_account_long_short_ratio` | `topLongShortAccountRatio` | how many whales lean each way |
| `top_position_long_short_ratio` | `topLongShortPositionRatio` | how much whale *capital* leans each way -- size-weighted, the more informative of the two |
| `taker_buy_sell_ratio` / `taker_buy_vol` / `taker_sell_vol` | `takerlongshortRatio` | who is currently paying up to move price |

**Liquidations are deliberately not here.** Since 2021-04-27 Binance
publishes only the largest liquidation per 1000ms window, so any
aggregate built from it is a *lower bound*, not a measurement. Collecting
a number that silently understates itself during exactly the bursts that
matter would be worse than not having it, and adding it needs its own
decision about how to represent that censoring.

## Rate limits, respected rather than discovered

1000 requests / 5 minutes per IP on this family, REST-only (no
WebSocket). This module makes one request per (metric, period) per run
and is meant to be driven from cron on a several-minute cadence, which
is nowhere near the limit.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from data.store import PositioningRow

BASE_URL = "https://fapi.binance.com"

# `limit` is capped at 500 by the API; 500 at "5m" is ~42 hours, and at
# "1h" is ~20 days. Asking for the maximum every run makes the collector
# self-healing: a few missed runs are backfilled automatically on the
# next one, within whatever the endpoint still retains.
MAX_LIMIT = 500

DEFAULT_PERIOD = "1h"
VALID_PERIODS = ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d")


@dataclass(frozen=True)
class MetricSpec:
    """One endpoint and how to read the fields out of its rows."""

    path: str
    fields: dict[str, str]
    """`{response field: metric name}`. Explicit rather than derived so a
    field Binance renames fails loudly here instead of silently
    producing an empty series."""


SPECS: dict[str, MetricSpec] = {
    "open_interest": MetricSpec(
        path="/futures/data/openInterestHist",
        fields={
            "sumOpenInterest": "open_interest",
            "sumOpenInterestValue": "open_interest_value",
        },
    ),
    "global_long_short": MetricSpec(
        path="/futures/data/globalLongShortAccountRatio",
        fields={
            "longAccount": "global_long_account",
            "shortAccount": "global_short_account",
            "longShortRatio": "global_long_short_ratio",
        },
    ),
    "top_accounts": MetricSpec(
        path="/futures/data/topLongShortAccountRatio",
        fields={"longShortRatio": "top_account_long_short_ratio"},
    ),
    "top_positions": MetricSpec(
        path="/futures/data/topLongShortPositionRatio",
        fields={"longShortRatio": "top_position_long_short_ratio"},
    ),
    "taker_flow": MetricSpec(
        path="/futures/data/takerlongshortRatio",
        fields={
            "buySellRatio": "taker_buy_sell_ratio",
            "buyVol": "taker_buy_vol",
            "sellVol": "taker_sell_vol",
        },
    ),
}


class BinancePositioningError(RuntimeError):
    """A request failed, or a response was not the documented shape.

    Raised rather than returning empty so a collector run fails visibly.
    A silent empty result on a series that cannot be backfilled is the
    worst possible failure mode here.
    """


def _numeric(spec_name: str, field: str, raw: object) -> str:
    """The field's value as a string, or raise.

    Binance returns these as strings to preserve precision, and the
    original code trusted that by calling `str(...)` on whatever arrived.
    That converts `None`, a nested object, `""` and `NaN` into
    perfectly-storable text: `"None"` and `"nan"` are stored as if they
    were observations, and because `upsert_positioning` uses
    `INSERT OR IGNORE`, the **later good value for that same key is then
    silently discarded** -- a permanent bad row from one transient
    response.

    So the value is parsed as a `Decimal` and required to be finite.
    The original string is what gets stored, not the parsed value, so no
    precision is lost to a round trip.
    """
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise BinancePositioningError(
            f"{spec_name}: field {field!r} is {type(raw).__name__}, not a number "
            f"-- the API shape changed, and storing it would poison the key "
            f"against any later correct value"
        )
    text = str(raw).strip()
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise BinancePositioningError(
            f"{spec_name}: field {field!r} is not a number: {text!r}"
        ) from exc
    if not parsed.is_finite():
        raise BinancePositioningError(
            f"{spec_name}: field {field!r} is {text!r}, which is not finite"
        )
    return text


def _timestamp_ms(spec_name: str, raw: object) -> int:
    """A non-negative integer millisecond timestamp, or raise.

    `int(raw)` was too permissive in both directions that matter here:
    `int(True)` is `1`, and `int("1.9")` raises while `int(1.9)` silently
    truncates. This value is part of `upsert_positioning`'s primary key,
    so a wrong one is not just a bad row -- `INSERT OR IGNORE` means the
    later correct observation for that slot is discarded, permanently, on
    a series that cannot be backfilled.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise BinancePositioningError(
            f"{spec_name}: timestamp is {type(raw).__name__}, expected an "
            f"integer millisecond value"
        )
    try:
        ts = int(str(raw).strip())
    except ValueError as exc:
        raise BinancePositioningError(
            f"{spec_name}: timestamp {raw!r} is not an integer -- a truncated "
            f"fractional value would key a row that later good data cannot replace"
        ) from exc
    if ts < 0:
        raise BinancePositioningError(f"{spec_name}: timestamp {ts} is negative")
    return ts


def _get_with_retry(url: str, *, attempts: int = 3) -> str:
    """Same shape as `binance_klines._get_with_retry`.

    HTTP 418 is Binance's documented temporary-IP-ban signal and is
    **not** retried -- retrying into an active ban extends it. 429 is
    likewise not retried here; the caller's cron cadence is the right
    place to back off, not a tight loop.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "trading-engine/1.0"})
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code in (418, 429):
                raise BinancePositioningError(
                    f"rate limited or banned (HTTP {exc.code}) -- not retrying, "
                    f"back off at the scheduler instead"
                ) from exc
            last = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(2**attempt)
    raise BinancePositioningError(f"request failed after {attempts} attempts: {url}") from last


def fetch_metric(
    spec_name: str,
    symbol: str,
    *,
    period: str = DEFAULT_PERIOD,
    limit: int = MAX_LIMIT,
    base_url: str = BASE_URL,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> list[PositioningRow]:
    """Fetch one endpoint and flatten it into `PositioningRow`s.

    With no bounds, returns the most recent `limit` observations.

    **`start_ms`/`end_ms` exist because "newest 500" is not enough at
    `5m`.** 500 rows at `5m` spans about 41 hours, so an outage longer
    than that leaves a hole that the next run can never see -- and since
    `upsert_positioning` is `INSERT OR IGNORE` over a 30-day retention
    window, a hole is permanent even though the data is still on the
    server. That is not hypothetical for this project: the host running
    the collector has been observed dark for 13 hours at a stretch, and
    down for days at a time.

    Bounds are half-open (`start_ms <= t < end_ms`), matching this
    project's convention everywhere else, and are applied by the caller
    -- `collect_gap` below is what turns "what is missing" into a series
    of bounded requests.
    """
    spec = SPECS.get(spec_name)
    if spec is None:
        raise ValueError(f"unknown metric {spec_name!r}; known: {sorted(SPECS)}")
    if period not in VALID_PERIODS:
        raise ValueError(f"period must be one of {VALID_PERIODS}, got {period!r}")
    if not 0 < limit <= MAX_LIMIT:
        raise ValueError(f"limit must be in 1..{MAX_LIMIT}, got {limit}")

    if start_ms is not None and end_ms is not None and start_ms >= end_ms:
        raise ValueError(f"start_ms {start_ms} must be < end_ms {end_ms}")

    params: dict[str, object] = {"symbol": symbol, "period": period, "limit": limit}
    if start_ms is not None:
        params["startTime"] = start_ms
    if end_ms is not None:
        # Binance treats endTime as INCLUSIVE on this family, as it does
        # on klines. This project's convention is half-open, so the last
        # millisecond is trimmed rather than letting one extra
        # observation leak in at every page boundary and be re-fetched.
        params["endTime"] = end_ms - 1
    query = urllib.parse.urlencode(params)
    payload = _get_with_retry(f"{base_url}{spec.path}?{query}")
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BinancePositioningError(f"{spec_name}: response was not JSON") from exc
    if not isinstance(raw, list):
        # These endpoints return a bare array. An object here is Binance's
        # error envelope, which must not be mistaken for "no data".
        raise BinancePositioningError(f"{spec_name}: expected a JSON array, got {type(raw).__name__}: {raw}")

    rows: list[PositioningRow] = []
    for entry in raw:
        if not isinstance(entry, dict) or "timestamp" not in entry:
            raise BinancePositioningError(f"{spec_name}: row missing 'timestamp': {entry}")
        ts = _timestamp_ms(spec_name, entry["timestamp"])
        for field, metric in spec.fields.items():
            if field not in entry:
                raise BinancePositioningError(
                    f"{spec_name}: row missing documented field {field!r} -- the API "
                    f"shape changed, and silently skipping it would produce an empty "
                    f"series that looks like 'no data'"
                )
            rows.append(
                PositioningRow(
                    metric=metric, period=period, timestamp_ms=ts,
                    value=_numeric(spec_name, field, entry[field]),
                )
            )
    return rows


PERIOD_MS: dict[str, int] = {
    "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
    "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "12h": 43_200_000, "1d": 86_400_000,
}
"""Each period's span. Explicit rather than parsed from the string, so an
unsupported period is a `KeyError` here instead of a silently wrong page
size."""

RETENTION_MS = 30 * 86_400_000
"""Binance's documented retention on the `/futures/data/` family. A
request for anything older comes back empty, so paging stops there rather
than walking backwards forever."""


def collect_gap(
    spec_name: str,
    symbol: str,
    *,
    period: str,
    since_ms: int | None,
    now_ms: int,
    base_url: str = BASE_URL,
    max_pages: int = 20,
) -> list[PositioningRow]:
    """Every observation from `since_ms` to `now_ms`, paged.

    This is what makes an outage recoverable. A plain "newest 500" request
    covers about 41 hours at `5m`; anything longer leaves a hole that no
    later run will ever ask for again, and `INSERT OR IGNORE` means the
    hole is permanent even though Binance still has the data for 30 days.

    `since_ms` is the newest timestamp already stored (exclusive), or
    `None` for a first run. It is clamped to the retention edge -- asking
    for older than that returns nothing and would just burn requests.

    **Fails closed on an unfinished walk.** If `max_pages` is exhausted
    with ground still to cover, this raises rather than returning a
    partial series, because a partial return is indistinguishable from
    "that is all there was" and would be stored as if the gap were
    filled.
    """
    if period not in PERIOD_MS:
        raise ValueError(f"period must be one of {sorted(PERIOD_MS)}, got {period!r}")
    step = PERIOD_MS[period]
    earliest = now_ms - RETENTION_MS
    start = earliest if since_ms is None else max(since_ms + 1, earliest)
    if start >= now_ms:
        return []

    page_span = step * MAX_LIMIT
    rows: list[PositioningRow] = []
    seen: set[tuple[str, int]] = set()
    cursor = start
    for _ in range(max_pages):
        if cursor >= now_ms:
            break
        page_end = min(cursor + page_span, now_ms)
        page = fetch_metric(
            spec_name, symbol, period=period, limit=MAX_LIMIT,
            base_url=base_url, start_ms=cursor, end_ms=page_end,
        )
        # STOP at an empty page that lies entirely in the past, rather
        # than stepping over it.
        #
        # Advancing would be the one unrecoverable mistake this function
        # can make: the later pages' rows get stored, `_latest_stored`
        # moves past the hole, and every future run begins after it --
        # so a transient failure (a flake, a rate limit, a partial
        # outage) becomes a permanent gap in a series that cannot be
        # backfilled.
        #
        # Stopping is safe in both directions, and deliberately not a
        # raise. If the gap is transient the next run resumes at exactly
        # this cursor and heals it. If it is a real, permanent hole in
        # Binance's own data, retrying forever would strand the series --
        # but it cannot, because `start` is clamped to
        # `now_ms - RETENTION_MS`, so within 30 days the window slides
        # past the hole on its own. A raise would have made a permanent
        # upstream gap stop collection indefinitely.
        if not page and page_end < now_ms:
            print(
                f"  {spec_name} {symbol} {period}: empty page over "
                f"[{cursor}, {page_end}) -- stopping the walk here rather than "
                f"stepping over it. Next run resumes from this point."
            )
            break
        for row in page:
            key = (row.metric, row.timestamp_ms)
            if key not in seen:
                seen.add(key)
                rows.append(row)
        cursor = page_end
    else:
        if cursor < now_ms:
            raise BinancePositioningError(
                f"{spec_name} {symbol} {period}: {max_pages} pages did not reach "
                f"{now_ms} (stopped at {cursor}). Returning a partial series would "
                f"look identical to a complete one and mark the gap as filled."
            )
    return rows
