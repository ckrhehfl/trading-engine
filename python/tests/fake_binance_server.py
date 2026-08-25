"""Minimal fake Binance klines server for tests -- stdlib `http.server`,
same testing philosophy as `fake_bingx_server.py`/`fake_fred_server.py`
(a real local HTTP server, not deep-mocked client internals).

Deliberately replicates Binance's real (verified live against
`api.binance.com`/`fapi.binance.com` this session -- see
`binance_klines.py`'s module docstring) wire behavior, which differs from
BingX's in two load-bearing ways:

- **`endTime` is INCLUSIVE**, not half-open like BingX's -- a request
  with `startTime == endTime` returns exactly one row (the candle whose
  open time equals that value), confirmed live. `binance_klines.py`
  translates its own half-open `[start_ms, end_ms)` public contract to
  Binance's real wire convention by requesting `endTime = end_ms - 1`;
  this fake server does NOT perform that translation itself -- it
  faithfully serves whatever inclusive `[startTime, endTime]` it was
  asked for, exactly like the real API, so a test can catch a client-side
  translation bug.
- **Silent over-limit capping keeps the OLDEST rows (closest to
  `startTime`), not the newest** -- confirmed live by requesting a wide
  range with `limit` below the number of candles in range: Binance
  returns rows starting from `startTime` forward, ascending, up to
  `limit` rows. This is the *opposite* of `FakeBingXKlinesServer`'s
  (verified real) newest-closest-to-`endTime` capping.

Not named `test_*.py` on purpose so pytest doesn't try to collect it as a
test module itself.
"""

import json
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

SPOT_KLINES_PATH = "/api/v3/klines"
FUTURES_KLINES_PATH = "/fapi/v1/klines"


class FakeBinanceKlinesServer:
    """Serves canned klines responses at a single fixed `path` (spot or
    futures -- both real endpoints share an identical wire shape, see
    `binance_klines.py`'s module docstring, so one fake server class
    covers both; which path it answers on is just a constructor arg).

    Response body is a bare JSON array of arrays (Binance's real shape --
    no envelope object, unlike BingX's `{"code","msg","data"}`), each row
    a realistic 12-element array (only the first 6 -- open_time, open,
    high, low, close, volume -- are load-bearing for this pipeline; the
    rest are populated with plausible placeholder values so a test
    exercising "does the client ignore trailing fields correctly" has
    real trailing fields to ignore).
    """

    def __init__(self, path: str = SPOT_KLINES_PATH, hard_cap: int = 1000):
        self._klines: dict[int, list] = {}
        self._path = path
        self._hard_cap = hard_cap
        self._forced: list[tuple[int, str]] = []
        self.requests: list[dict] = []
        self._lock = threading.Lock()

        handler = self._make_handler()
        self._httpd = HTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    @property
    def path(self) -> str:
        return self._path

    def set_kline(
        self,
        open_time_ms: int,
        open_: Decimal | str,
        high: Decimal | str,
        low: Decimal | str,
        close: Decimal | str,
        volume: Decimal | str,
        *,
        taker_buy_base_volume: Decimal | str = "0",
        taker_buy_quote_volume: Decimal | str = "0",
    ) -> None:
        with self._lock:
            self._klines[open_time_ms] = [
                open_time_ms,
                str(open_),
                str(high),
                str(low),
                str(close),
                str(volume),
                open_time_ms + 59_999,  # close_time -- placeholder, not load-bearing
                "0",  # quote_asset_volume
                0,  # num_trades
                str(taker_buy_base_volume),
                str(taker_buy_quote_volume),
                "0",  # ignore
            ]

    def set_klines(self, open_times_ms: list[int], price: Decimal | str = "100") -> None:
        """Convenience: seed a run of identically-priced candles, one per
        given open time -- most tests only care about which timestamps
        exist, not real-looking OHLCV values.
        """
        for t in open_times_ms:
            self.set_kline(t, price, price, price, price, "1")

    def force_response(self, status: int, body: str, times: int = 1) -> None:
        """Queue `times` raw (status, body) responses, consumed FIFO
        ahead of the normal canned-data response -- for retry/error-path
        tests.
        """
        with self._lock:
            self._forced.extend([(status, body)] * times)

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # keep test output quiet
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

                with server._lock:
                    server.requests.append({"path": parsed.path, "params": params})
                    forced = server._forced.pop(0) if server._forced else None

                if forced is not None:
                    status, body = forced
                else:
                    status, body = server._respond(parsed.path, params)

                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        return Handler

    def _respond(self, path: str, params: dict) -> tuple[int, str]:
        if path != self._path:
            return 400, json.dumps({"code": -1121, "msg": "Invalid symbol."})

        # Real Binance semantics: endTime is INCLUSIVE (verified live --
        # see module docstring). This fake server does not translate; it
        # trusts the caller sent real wire-convention params, exactly
        # like the production API would.
        start = int(params["startTime"])
        end = int(params["endTime"])
        requested_limit = int(params.get("limit", self._hard_cap))
        effective_limit = min(requested_limit, self._hard_cap)

        with self._lock:
            matching = sorted(t for t in self._klines if start <= t <= end)
            # Oldest-first capping (closest to startTime), NOT newest --
            # the verified-live opposite of BingX's own capping direction.
            capped = matching[:effective_limit] if effective_limit > 0 else []
            rows = [self._klines[t] for t in capped]  # already ascending

        return 200, json.dumps(rows)
