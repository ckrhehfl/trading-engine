"""Offline tests for the bounded KIS KRX capture sidecar."""

from collections import deque
from http.client import HTTPSConnection
import io
import json
import os
import socket
import stat

import pytest

from research import intraday_krx_tape_capture as tape


SESSION = "2026-11-20"
START, END = tape._session_bounds(SESSION)
APPROVAL_STUB = "synthetic-approval-xxxxxxxxxxxxxxxx"


def acknowledgement(tr, symbol, status="0", **extra):
    return json.dumps({"header": {"tr_id": tr, "tr_key": symbol, "encrypt": "N"},
        "body": {"rt_cd": status, **extra}})


def packet(tr, width, *symbols, value="100"):
    fields = [field for symbol in symbols for field in [symbol, "090001", value, *(["0"] * (width - 3))]]
    return f"0|{tr}|{len(symbols):04d}|" + "^".join(fields)


class FakeSocket:
    def __init__(self, clock, frames):
        self.clock = clock
        self.frames = deque(frames)
        self.sends = []
        self.pongs = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def send(self, value):
        self.sends.append(json.loads(value))

    def pong(self, value):
        self.pongs.append(value)

    def recv(self, *, timeout):
        assert 0 < timeout <= 1
        if not self.frames:
            self.clock[0] = END
            raise TimeoutError
        tick, raw = self.frames.popleft()
        self.clock[0] = tick
        if isinstance(raw, Exception):
            raise raw
        return raw


@pytest.fixture
def invocation(tmp_path, monkeypatch):
    path = tmp_path / "fixture.env"
    path.write_bytes(b"# only two fields parsed\r\nKIS_APP_KEY=synthetic-key\r\n"
        b"IGNORED_TOKEN=synthetic-unread\r\nexport KIS_APP_SECRET='synthetic-secret'\r\n")
    clock = [START - 60 * tape.NS]
    approvals = []

    def approve(key, secret):
        approvals.append((key, secret))
        return APPROVAL_STUB

    monkeypatch.setattr(tape, "time_ns", lambda: clock[0])
    monkeypatch.setattr(tape, "monotonic_ns", lambda: clock[0] - START + 120 * tape.NS)
    monkeypatch.setattr(tape, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + int(seconds * tape.NS)))
    monkeypatch.setattr(tape, "_issue_approval", approve)
    return path, clock, approvals


def _load_rows(path):
    raw = path.read_text(encoding="utf-8")
    assert all(value not in raw for value in (APPROVAL_STUB, "synthetic-key", "synthetic-secret", "leak-sentinel"))
    return [json.loads(line) for line in raw.splitlines()]


def test_unregistered_day_cannot_open_credentials_or_journal(tmp_path, invocation):
    credentials, _, approvals = invocation
    target = tmp_path / "foreign-session"
    with pytest.raises(ValueError, match="registered session"):
        tape.capture("2026-11-23", target, credentials)
    assert not approvals and not target.exists()


def test_only_allowed_market_requests_and_in_window_records(tmp_path, monkeypatch, invocation):
    credentials, clock, approvals = invocation
    heartbeat = '{"header":{"tr_id":"PINGPONG","datetime":"20261120090000"}}'
    market = packet("H0STCNT0", 47, "005930", "000660")
    out = tmp_path / "once"
    socket = FakeSocket(clock, [(START - 59 * tape.NS, acknowledgement(tr, code, msg1="leak-sentinel"))
        for tr, code in tape.SUBSCRIPTIONS] + [
            (START - 58 * tape.NS, market),
            (START - 57 * tape.NS, heartbeat),
            (START + 1, packet("H0STASP0", 63, "005930", value="leak-sentinel")),
            (START + 2, "0|H0STCNI0|0001|leak-sentinel"),
            (START + 3, '{"header":{"tr_id":"PINGPONG","approval_key":"leak-sentinel"}}'),
            (START + 4, market),
            (END, market),
        ])
    destinations = []
    pauses = []
    advance_clock = tape.sleep

    def connect(url, **kwargs):
        destinations.append((url, kwargs))
        return socket

    def pause(seconds):
        pauses.append(seconds)
        advance_clock(seconds)

    monkeypatch.setattr(tape, "connect", connect)
    monkeypatch.setattr(tape, "sleep", pause)
    assert tape.capture(SESSION, out, credentials) == "complete"
    assert approvals == [("synthetic-key", "synthetic-secret")]
    assert len(destinations) == 1 and destinations[0][0] == "ws://ops.koreainvestment.com:21000"
    assert destinations[0][1]["proxy"] is None
    assert destinations[0][1]["logger"].disabled
    assert pauses == [0.1, 0.1, 0.1]
    assert [(r["body"]["input"]["tr_id"], r["body"]["input"]["tr_key"]) for r in socket.sends] == [
        ("H0STCNT0", "005930"), ("H0STCNT0", "000660"), ("H0STASP0", "005930"), ("H0STASP0", "000660")]
    assert all(r["header"]["tr_type"] == "1" and r["header"]["approval_key"] == APPROVAL_STUB for r in socket.sends)
    assert socket.pongs == [heartbeat]
    journal = out / "capture.jsonl"
    if os.name == "posix":
        assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    rows = _load_rows(journal)
    assert [r["event"] for r in rows] == ["start", "connected", *(["ack"] * 4), *(["invalid"] * 3),
        "market", "disconnected", "end"]
    [saved] = [r for r in rows if r["event"] == "market"]
    assert saved == {"event": "market", "received_ns": START + 4, "connection": 1,
        "tr": "H0STCNT0", "count": 2, "payload": market.split("|", 3)[3]}
    assert rows[-1]["status"] == "complete"
    clock[0] = START - 59 * tape.NS
    with pytest.raises(FileExistsError):
        tape.capture(SESSION, out, credentials)
    assert len(approvals) == 1


@pytest.mark.parametrize("offset", [-121, 0, 1, 3601])
def test_out_of_launch_window_has_no_io(tmp_path, invocation, offset):
    credentials, clock, approvals = invocation
    clock[0] = START + offset * tape.NS
    path = tmp_path / "never-created"
    assert tape.capture(SESSION, path, credentials) == "outside_launch_window"
    assert not path.exists() and approvals == []


def test_retry_only_network_failure_and_never_renews_approval(tmp_path, monkeypatch, invocation):
    credentials, clock, approvals = invocation
    calls = []

    def connect(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return FakeSocket(clock, [(START - 59 * tape.NS, OSError("leak-sentinel"))])
        return FakeSocket(clock, [(START - 57 * tape.NS, acknowledgement(*tape.SUBSCRIPTIONS[0], status="1"))])

    monkeypatch.setattr(tape, "connect", connect)
    out = tmp_path / "retry"
    assert tape.capture(SESSION, out, credentials) == "failed"
    assert len(calls) == 2 and len(approvals) == 1
    rows = _load_rows(out / "capture.jsonl")
    assert [r.get("reason") for r in rows if r["event"] in {"invalid", "disconnected"}] == ["network_failure", "ack_rejected"]
    assert rows[-1]["status"] == "failed"


def test_missing_ack_has_failure_and_no_retry(tmp_path, monkeypatch, invocation):
    credentials, clock, _ = invocation
    calls = []

    def connect(url, **kwargs):
        calls.append(url)
        return FakeSocket(clock, [(START - 39 * tape.NS, TimeoutError())])

    monkeypatch.setattr(tape, "connect", connect)
    out = tmp_path / "missing-ack"
    assert tape.capture(SESSION, out, credentials) == "failed"
    assert len(calls) == 1
    rows = _load_rows(out / "capture.jsonl")
    assert rows[-2]["reason"] == "ack_missing"


def test_approval_transport_uses_fixed_verified_https_and_never_redirects(tmp_path, monkeypatch):
    calls = []
    keylog = tmp_path / "untrusted-tls-keylog"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))

    class FakeTLS(HTTPSConnection):
        def request(self, method, url, body, headers):
            calls.append((self.host, self.port, self._context, method, url, json.loads(body), headers))

        def getresponse(self):
            class Redirect(io.BytesIO):
                status = 302

            return Redirect(b'{"approval_key":"leak-sentinel"}')

    monkeypatch.setattr(tape, "HTTPSConnection", FakeTLS)
    with pytest.raises(tape.CaptureFailure, match="approval_http_rejected"):
        tape._issue_approval("synthetic-key", "synthetic-secret")
    [(host, port, context, method, url, body, headers)] = calls
    assert (host, port, method, url) == ("openapi.koreainvestment.com", 9443, "POST", "/oauth2/Approval")
    assert context.check_hostname and context.verify_mode == tape.ssl.CERT_REQUIRED
    assert context.keylog_filename is None and not keylog.exists()
    assert body == {"appkey": "synthetic-key", "grant_type": "client_credentials", "secretkey": "synthetic-secret"}
    assert headers == {"Content-Type": "application/json"}


def test_installed_sync_ws_transport_rejects_redirect_without_second_destination(monkeypatch):
    destinations = []

    class SocketPairSide:
        def __getattr__(self, name):
            return getattr(client, name)

        def setsockopt(self, *_args):
            # TCP_NODELAY is inapplicable to the local in-memory socket pair.
            pass

        def sendall(self, data):
            client.sendall(data)
            if data.startswith(b"GET "):
                server.sendall(b"HTTP/1.1 302 Found\r\nLocation: ws://example.invalid/\r\nContent-Length: 0\r\n\r\n")

    def connect_pair(destination, **_kwargs):
        destinations.append(destination)
        assert destinations == [("ops.koreainvestment.com", 21000)]
        return SocketPairSide()

    client, server = socket.socketpair()
    with client, server:
        monkeypatch.setattr(socket, "create_connection", connect_pair)
        with pytest.raises(tape.InvalidHandshake):
            tape.connect(tape.MARKET_SOCKET, proxy=None, open_timeout=1, close_timeout=1)
    assert destinations == [("ops.koreainvestment.com", 21000)]


def test_terminal_record_is_retained_when_market_hits_storage_bound(tmp_path, monkeypatch, invocation):
    credentials, clock, _ = invocation
    monkeypatch.setattr(tape, "MAX_JOURNAL", 504)
    monkeypatch.setattr(tape, "TERMINAL_RESERVE", 224)
    out = tmp_path / "bounded"
    monkeypatch.setattr(tape, "connect", lambda *_args, **_kwargs:
        FakeSocket(clock, [(START, packet("H0STASP0", 63, "005930"))]))
    assert tape.capture(SESSION, out, credentials) == "failed"
    path = out / "capture.jsonl"
    assert path.stat().st_size <= tape.MAX_JOURNAL
    rows = _load_rows(path)
    assert rows[-2]["reason"] == "journal_limit"
    assert rows[-1]["status"] == "failed"
