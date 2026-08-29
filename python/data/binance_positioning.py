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
) -> list[PositioningRow]:
    """Fetch one endpoint and flatten it into `PositioningRow`s.

    Returns the most recent `limit` observations. These endpoints accept
    `startTime`/`endTime`, but this deliberately does not use them: the
    retention window is short and always moving, so "give me the newest
    N" is both simpler and the only request that reliably returns
    something.
    """
    spec = SPECS.get(spec_name)
    if spec is None:
        raise ValueError(f"unknown metric {spec_name!r}; known: {sorted(SPECS)}")
    if period not in VALID_PERIODS:
        raise ValueError(f"period must be one of {VALID_PERIODS}, got {period!r}")
    if not 0 < limit <= MAX_LIMIT:
        raise ValueError(f"limit must be in 1..{MAX_LIMIT}, got {limit}")

    query = urllib.parse.urlencode({"symbol": symbol, "period": period, "limit": limit})
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
        ts = int(entry["timestamp"])
        for field, metric in spec.fields.items():
            if field not in entry:
                raise BinancePositioningError(
                    f"{spec_name}: row missing documented field {field!r} -- the API "
                    f"shape changed, and silently skipping it would produce an empty "
                    f"series that looks like 'no data'"
                )
            rows.append(PositioningRow(metric=metric, period=period, timestamp_ms=ts, value=str(entry[field])))
    return rows
