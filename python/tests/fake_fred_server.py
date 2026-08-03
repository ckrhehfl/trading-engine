"""Minimal fake FRED server for tests -- stdlib `http.server`, same
testing philosophy as `fake_bingx_funding_server.py` (a real local HTTP
server, not deep-mocked client internals).

Replicates FRED's real, live-verified (2026-08-03, see
`data.fred_client`'s module docstring and
`.planning/sr-w-macro-data-pipeline.md`) behavior:

- The `series/observations` endpoint implements *real* `offset`/`limit`
  pagination against a real `count` -- unlike the BingX fakes, this one
  does not need a `hard_cap`-style silent-capping simulation, because
  real FRED doesn't silently cap either; it honestly returns
  `rows[offset:offset+limit]` plus an honest total `count`. Tests that
  want to exercise multi-page traversal do so by passing a small
  `limit` to `iter_observations`/`fetch_observations_page`, not by
  configuring this fake specially.
- A date **present** in this fake's internal store is returned as a
  real observation row -- its value may be the literal string `"."` to
  simulate FRED's own missing-observation marker (seed via
  `set_observation(..., value=None)`). A date **not** present is simply
  absent from the response, exactly like a real weekend or a
  not-yet-published recent date -- there is deliberately no separate
  "gap" concept in this fake beyond "was `set_observation` ever called
  for this date".
- `limit > 100000` and a missing/empty `api_key` both return FRED's real
  HTTP 400 JSON error envelope (`{"error_code", "error_message"}`) --
  verified live this session.

Not named `test_*.py` on purpose so pytest doesn't try to collect it as
a test module itself.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

OBSERVATIONS_PATH = "/fred/series/observations"

_MAX_LIMIT = 100_000


class FakeFredServer:
    def __init__(self):
        self._series: dict[str, dict[str, str]] = {}
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

    def set_observation(self, series_id: str, observation_date: str, value: str | None = "1.0") -> None:
        """Seed one observation row. `value=None` seeds FRED's own
        missing-observation marker (served as the literal string `"."`),
        which is a real row, not the absence of one -- to simulate a
        date FRED never returns a row for at all (a weekend, or a
        not-yet-published date), simply never call this for that date.
        """
        with self._lock:
            self._series.setdefault(series_id, {})[observation_date] = "." if value is None else str(value)

    def set_observations(self, series_id: str, dates_and_values: list[tuple[str, str | None]]) -> None:
        """Convenience: seed several rows for one series in one call."""
        for observation_date, value in dates_and_values:
            self.set_observation(series_id, observation_date, value)

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
        if path != OBSERVATIONS_PATH:
            return 404, json.dumps({"error_code": 404, "error_message": "Not Found"})

        if not params.get("api_key"):
            # Real FRED response shape for a missing/empty api_key --
            # this task's own pre-verified fact (a keyless request
            # returns HTTP 400 with a clear error).
            return 400, json.dumps(
                {"error_code": 400, "error_message": "Bad Request. api_key variable is not set."}
            )

        limit = int(params.get("limit", _MAX_LIMIT))
        if not (1 <= limit <= _MAX_LIMIT):
            # Real, verified 2026-08-03: limit=100001 returns exactly
            # this error_message text.
            return 400, json.dumps(
                {
                    "error_code": 400,
                    "error_message": "Bad Request.  Variable limit is not between 1 and 100000.",
                }
            )
        offset = int(params.get("offset", 0))

        series_id = params.get("series_id", "")
        start = params.get("observation_start", "0000-01-01")
        end = params.get("observation_end", "9999-12-31")

        with self._lock:
            series_rows = self._series.get(series_id, {})
            # Lexicographic order matches chronological order for
            # YYYY-MM-DD strings.
            matching_dates = sorted(d for d in series_rows if start <= d <= end)
            page_dates = matching_dates[offset : offset + limit]
            observations = [
                {
                    "realtime_start": "2026-08-03",
                    "realtime_end": "2026-08-03",
                    "date": d,
                    "value": series_rows[d],
                }
                for d in page_dates
            ]
            count = len(matching_dates)

        body = json.dumps(
            {
                "realtime_start": "2026-08-03",
                "realtime_end": "2026-08-03",
                "observation_start": start,
                "observation_end": end,
                "units": "lin",
                "output_type": 1,
                "file_type": "json",
                "order_by": "observation_date",
                "sort_order": "asc",
                "count": count,
                "offset": offset,
                "limit": limit,
                "observations": observations,
            }
        )
        return 200, body
