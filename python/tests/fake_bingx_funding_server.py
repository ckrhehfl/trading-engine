"""Minimal fake BingX funding-rate server for tests -- stdlib
`http.server`, same testing philosophy as `fake_bingx_server.py`
(a real local HTTP server, not deep-mocked client internals). A
separate class rather than extending `FakeBingXKlinesServer`: the two
endpoints' response shapes and empty-result conventions genuinely
differ (see below), and keeping them independent means a change to one
fake can't accidentally regress the other's already-passing tests.

Not named `test_*.py` on purpose so pytest doesn't try to collect it as
a test module itself.
"""

import json
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

FUNDING_PATH = "/openApi/swap/v2/quote/fundingRate"


class FakeBingXFundingServer:
    """Serves canned `/openApi/swap/v2/quote/fundingRate` responses.

    Replicates BingX's real, live-verified (2026-07-27, see
    `bingx_funding.py`'s module docstring and `.planning/sr-m-funding-
    rate-pipeline.md`) behavior:

    - Newest-first ordering, silently capped to the newest
      `effective_limit` rows closest to `endTime` when a range holds
      more rows than the limit -- same silent-capping shape as klines.
    - **Empty result is `"data": null`, not `"data": []`** -- confirmed
      against the live endpoint for both a genuinely out-of-retention
      range and an in-retention range that simply has no funding event
      inside it. This is a real, deliberate difference from
      `FakeBingXKlinesServer` (`"data": []`), not an oversight.
    - `limit > 1000` returns a real BingX error envelope
      (`code: 109400`), not a silent clamp -- also a real difference
      from klines, where an over-limit `limit` is rejected client-side
      before ever being sent (same defense used here).

    Does **not** attempt to replicate BingX's confirmed *flaky*
    null-for-a-range-that-genuinely-has-data behavior automatically --
    that's non-deterministic on the real server and not something a
    fake should bake in as default behavior. Tests that need to
    exercise the retry-on-null path use `force_response()` directly to
    queue a deterministic null (or several) ahead of the real data.
    """

    def __init__(self, hard_cap: int = 1000):
        self._rows: dict[int, dict] = {}
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

    def set_funding_rate(
        self,
        funding_time_ms: int,
        rate: Decimal | str = "0.0001",
        mark_price: Decimal | str = "50000",
    ) -> None:
        with self._lock:
            self._rows[funding_time_ms] = {
                "symbol": "BTC-USDT",
                "fundingRate": str(rate),
                "fundingTime": funding_time_ms,
                "markPrice": str(mark_price),
            }

    def set_funding_rates(
        self,
        funding_times_ms: list[int],
        rate: Decimal | str = "0.0001",
        mark_price: Decimal | str = "50000",
    ) -> None:
        """Convenience: seed a run of funding rows sharing one rate/mark
        price -- most tests only care about which timestamps exist.
        """
        for t in funding_times_ms:
            self.set_funding_rate(t, rate, mark_price)

    def force_response(self, status: int, body: str, times: int = 1) -> None:
        """Queue `times` raw (status, body) responses, consumed FIFO
        ahead of the normal canned-data response -- for retry/error-path
        and flaky-null tests.
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
        if path != FUNDING_PATH:
            return json.dumps({"code": 100404, "msg": "not found", "data": []})

        requested_limit = int(params.get("limit", self._hard_cap))
        if requested_limit > 1000:
            # Real BingX response, verified live 2026-07-27: limit > 1000
            # is a hard error, not a silent clamp (unlike klines).
            return json.dumps(
                {
                    "code": 109400,
                    "msg": "Invalid parameters, err:limit: This field must be less than or equal to 1000. ",
                    "data": {},
                }
            )
        effective_limit = min(requested_limit, self._hard_cap)

        with self._lock:
            if "startTime" in params and "endTime" in params:
                start = int(params["startTime"])
                end = int(params["endTime"])
                matching = sorted(t for t in self._rows if start <= t < end)
            else:
                matching = sorted(self._rows)
            capped = matching[-effective_limit:] if effective_limit > 0 else []
            rows = [self._rows[t] for t in reversed(capped)]  # newest-first

        if not rows:
            # Real BingX: "no rows matched" is `data: null`, not `[]` --
            # confirmed live for both an out-of-retention range and an
            # in-retention range with genuinely zero funding events in it.
            return json.dumps({"code": 0, "msg": "", "data": None})

        return json.dumps({"code": 0, "msg": "", "data": rows})
