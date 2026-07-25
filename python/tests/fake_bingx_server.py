"""Minimal fake BingX klines server for tests -- stdlib `http.server`,
not a mocking framework (this codebase has none in Python, matching the
Java side). Ports the *testing philosophy* of
`java/runtime/src/test/java/engine/runtime/FakeBingXTradesServer.java`
(a real local HTTP server, not deep-mocked client internals) -- not its
code, since Java uses `com.sun.net.httpserver.HttpServer` and this uses
`http.server`.

Not named `test_*.py` on purpose so pytest doesn't try to collect it as
a test module itself.
"""

import json
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

KLINES_PATH = "/openApi/swap/v3/quote/klines"


class FakeBingXKlinesServer:
    """Serves canned `/openApi/swap/v3/quote/klines` responses.

    By default, replicates BingX's real (verified 2026-07-26, see
    `bingx_klines.py`'s module docstring) silent-capping behavior: when
    a request's `[startTime, endTime)` range holds more candles than the
    effective limit, only the *newest* `effective_limit` candles (the
    ones closest to `endTime`) are returned -- not the oldest, and not
    an error. `hard_cap` lets a test simulate the server enforcing an
    even smaller cap than the client's own requested `limit`, to prove
    pagination code never trusts `limit` as a count guarantee.
    """

    def __init__(self, hard_cap: int = 1000):
        self._klines: dict[int, dict] = {}
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

    def set_kline(
        self,
        open_time_ms: int,
        open_: Decimal | str,
        high: Decimal | str,
        low: Decimal | str,
        close: Decimal | str,
        volume: Decimal | str,
    ) -> None:
        with self._lock:
            self._klines[open_time_ms] = {
                "open": str(open_),
                "high": str(high),
                "low": str(low),
                "close": str(close),
                "volume": str(volume),
                "time": open_time_ms,
            }

    def set_klines(self, open_times_ms: list[int], price: Decimal | str = "100") -> None:
        """Convenience: seed a run of identically-priced candles, one
        per given open time -- most tests only care about which
        timestamps exist, not real-looking OHLCV values.
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
                    status, body = 200, server._respond(parsed.path, params)

                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        return Handler

    def _respond(self, path: str, params: dict) -> str:
        if path != KLINES_PATH:
            return json.dumps({"code": 100404, "msg": "not found", "data": []})

        start = int(params["startTime"])
        end = int(params["endTime"])
        requested_limit = int(params.get("limit", self._hard_cap))
        effective_limit = min(requested_limit, self._hard_cap)

        with self._lock:
            matching = sorted(t for t in self._klines if start <= t < end)
            capped = matching[-effective_limit:] if effective_limit > 0 else []
            # Newest-first, matching real BingX's verified order.
            rows = [self._klines[t] for t in reversed(capped)]

        return json.dumps({"code": 0, "msg": "", "data": rows})
