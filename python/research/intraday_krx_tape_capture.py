"""Bounded KIS KRX market-data capture for two domestic cash equities."""

from argparse import ArgumentParser
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from http.client import HTTPException, HTTPSConnection
import json
import os
from pathlib import Path
import re
import ssl
from time import monotonic_ns, sleep, time_ns

from websockets.exceptions import ConnectionClosed, InvalidHandshake
from websockets.sync.client import connect


KST = timezone(timedelta(hours=9))
SYMBOLS = ("005930", "000660")
# KIS's current and previous official sample field counts, respectively.
WIDTHS = {"H0STCNT0": (47, 46), "H0STASP0": (63, 59)}
SUBSCRIPTIONS = tuple((tr, symbol) for tr in WIDTHS for symbol in SYMBOLS)
APPROVAL_HOST = "openapi.koreainvestment.com"
APPROVAL_PORT = 9443
APPROVAL_PATH = "/oauth2/Approval"
MARKET_SOCKET = "ws://ops.koreainvestment.com:21000"
MAX_FRAME = 1024 * 1024
MAX_JOURNAL = 512 * 1024 * 1024
TERMINAL_RESERVE = 4096
NS = 1_000_000_000
SECRETS = ("KIS_APP_KEY", "KIS_APP_SECRET")
_ASSIGNMENT = re.compile(r"\s*(?:export\s+)?(KIS_APP_KEY|KIS_APP_SECRET)\s*=\s*(.*?)\s*")
_MARKET_FIELD = re.compile(r"(?:[+-]?[0-9]+(?:\.[0-9]+)?|[A-Z]|KRX|[+-])?")


class CaptureFailure(Exception):
    """Use only constant, non-secret failure codes in this exception."""


def _session_bounds(session):
    day = date.fromisoformat(session)
    if day.isoformat() != session or day.weekday() > 4:
        raise ValueError("expected a weekday ISO session date")
    start, end = (int(datetime.combine(day, time(hour), KST).timestamp()) * NS for hour in (9, 10))
    return start, end


def _usable_secret(secret):
    return isinstance(secret, str) and 0 < len(secret) <= 512 and all(33 <= ord(c) <= 126 for c in secret)


def _read_credentials(path):
    values = {}
    try:
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                match = _ASSIGNMENT.fullmatch(line)
                if match is None:
                    continue
                key, value = match.groups()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key] = value
    except (OSError, UnicodeError):
        raise CaptureFailure("credential_file_unavailable") from None
    if not all(_usable_secret(values.get(name)) for name in SECRETS):
        raise CaptureFailure("credential_fields_unavailable")
    return tuple(values[name] for name in SECRETS)


def _issue_approval(key, secret):
    # A direct fixed host connection, with normal certificate verification and
    # no proxy or redirect handling. The application credentials never enter WS.
    tls = ssl.create_default_context()
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        with closing(HTTPSConnection(APPROVAL_HOST, APPROVAL_PORT, timeout=8, context=tls)) as client:
            client.request("POST", APPROVAL_PATH, json.dumps({"grant_type": "client_credentials",
                "appkey": key, "secretkey": secret}), {"Content-Type": "application/json"})
            response = client.getresponse()
            if response.status != 200:
                raise CaptureFailure("approval_http_rejected")
            raw = response.read(4097)
            if len(raw) > 4096:
                raise CaptureFailure("approval_response_too_large")
            payload = json.loads(raw)
    except (HTTPException, OSError, ValueError):
        raise CaptureFailure("approval_transport_or_response_invalid") from None
    approval = payload.get("approval_key") if type(payload) is dict else None
    if not _usable_secret(approval):
        raise CaptureFailure("approval_key_missing")
    return approval


def _subscribe(socket, approval):
    for index, (tr, symbol) in enumerate(SUBSCRIPTIONS):
        if index:
            sleep(0.1)
        socket.send(json.dumps({"header": {"approval_key": approval, "custtype": "P",
            "tr_type": "1", "content-type": "utf-8"}, "body": {"input": {"tr_id": tr, "tr_key": symbol}}}))


def _market_fields(raw):
    parts = raw.split("|", 3)
    if len(parts) != 4:
        return None
    kind, tr, count_text, payload = parts
    if kind != "0" or tr not in WIDTHS or not re.fullmatch(r"[0-9]{1,4}", count_text):
        return None
    count = int(count_text)
    fields = payload.split("^")
    if count == 0 or len(fields) % count:
        return None
    width = len(fields) // count
    if width not in WIDTHS[tr] or any(symbol not in SYMBOLS for symbol in fields[::width]):
        return None
    # Accept only the numeric/flag market wire shape. Untrusted control content
    # and arbitrary strings are never copied into the capture journal.
    if any(len(field) > 24 or _MARKET_FIELD.fullmatch(field.strip(" ")) is None for field in fields):
        return None
    return tr, count, payload


def _control(raw):
    """Return bounded heartbeat or acknowledgement metadata only."""
    try:
        message = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    if type(message) is not dict or type(header := message.get("header")) is not dict:
        return None
    tr = header.get("tr_id")
    if tr == "PINGPONG":
        stamp = header.get("datetime", "")
        if (len(raw.encode("utf-8")) <= 125 and set(header) <= {"tr_id", "datetime"}
                and set(message) <= {"header", "body"} and message.get("body") is None
                and isinstance(stamp, str) and re.fullmatch(r"[0-9T:+.Z-]*", stamp)):
            return "heartbeat", None
        return None
    symbol = header.get("tr_key")
    body = message.get("body")
    if (tr, symbol) not in SUBSCRIPTIONS or type(body) is not dict:
        return None
    if body.get("rt_cd") == "0" and header.get("encrypt", "N") == "N":
        return "ack", (tr, symbol)
    return "rejected", None


def _record(stream, event, received_ns, *, terminal=False, **fields):
    row = json.dumps({"event": event, "received_ns": received_ns, **fields}, separators=(",", ":")).encode() + b"\n"
    limit = MAX_JOURNAL if terminal else MAX_JOURNAL - TERMINAL_RESERVE
    if stream.tell() + len(row) > limit:
        raise CaptureFailure("journal_limit")
    try:
        stream.write(row)
        stream.flush()
    except OSError:
        raise CaptureFailure("journal_write_failed") from None


def _read_socket(socket, stream, connection, start, end, monotonic_end):
    acknowledged = set()
    ack_deadline = monotonic_ns() + 20 * NS
    while True:
        wall_now, monotonic_now = time_ns(), monotonic_ns()
        remaining = min(end - wall_now, monotonic_end - monotonic_now)
        if remaining <= 0:
            return wall_now >= end and len(acknowledged) == len(SUBSCRIPTIONS)
        if len(acknowledged) != len(SUBSCRIPTIONS) and monotonic_now >= ack_deadline:
            raise CaptureFailure("ack_missing")
        try:
            raw = socket.recv(timeout=min(1, remaining / NS))
        except TimeoutError:
            continue
        received = time_ns()
        if received >= end:
            return len(acknowledged) == len(SUBSCRIPTIONS)
        if not isinstance(raw, str) or len(raw) > MAX_FRAME:
            _record(stream, "invalid", received, connection=connection, reason="unsupported_message")
            continue
        if raw.startswith(("0|", "1|")):
            market = _market_fields(raw)
            if market is None:
                _record(stream, "invalid", received, connection=connection, reason="invalid_market_frame")
            elif received >= start:
                tr, count, payload = market
                _record(stream, "market", received, connection=connection, tr=tr, count=count, payload=payload)
            continue
        control = _control(raw)
        if control is None:
            _record(stream, "invalid", received, connection=connection, reason="invalid_control_frame")
            continue
        kind, identity = control
        if kind == "heartbeat":
            socket.pong(raw)
        elif kind == "rejected":
            raise CaptureFailure("ack_rejected")
        elif identity not in acknowledged:
            acknowledged.add(identity)
            tr, symbol = identity
            _record(stream, "ack", received, connection=connection, tr=tr, symbol=symbol)


def _collect(stream, credentials_path, start, end, monotonic_end):
    approval = _issue_approval(*_read_credentials(credentials_path))
    for connection in (1, 2):
        remaining = min(end - time_ns(), monotonic_end - monotonic_ns())
        if remaining <= 0:
            return "incomplete"
        try:
            # The synchronous client rejects unsuccessful upgrade responses
            # without following a redirect. Disable environmental proxies.
            with connect(MARKET_SOCKET, proxy=None, compression=None, open_timeout=min(8, remaining / NS),
                    close_timeout=1, ping_interval=None, max_size=MAX_FRAME, max_queue=64) as socket:
                _record(stream, "connected", time_ns(), connection=connection)
                _subscribe(socket, approval)
                complete = _read_socket(socket, stream, connection, start, end, monotonic_end)
                _record(stream, "disconnected", time_ns(), connection=connection, reason="window_end")
                return "complete" if complete else "incomplete"
        except InvalidHandshake:
            _record(stream, "invalid", time_ns(), connection=connection, reason="handshake_rejected")
            return "failed"
        except (ConnectionClosed, OSError):
            _record(stream, "disconnected", time_ns(), connection=connection, reason="network_failure")
            if connection == 1 and end - time_ns() > NS:
                sleep(1)
        except CaptureFailure as error:
            _record(stream, "invalid", time_ns(), terminal=True, connection=connection, reason=error.args[0])
            return "failed"
    return "incomplete"


def capture(session, output, credential_file):
    start, end = _session_bounds(session)
    now = time_ns()
    if not start - 120 * NS <= now < start:
        return "outside_launch_window"
    monotonic_end = monotonic_ns() + end - now
    output.mkdir(mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(output / "capture.jsonl", flags, 0o600), "wb") as stream:
        _record(stream, "start", now, session=session, symbols=list(SYMBOLS), start_ns=start, end_ns=end)
        status = "failed"
        try:
            status = _collect(stream, credential_file, start, end, monotonic_end)
        except CaptureFailure as error:
            _record(stream, "invalid", time_ns(), terminal=True, connection=1, reason=error.args[0])
        finally:
            _record(stream, "end", time_ns(), terminal=True, status=status)
            os.fsync(stream.fileno())
    return status


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture",))
    parser.add_argument("session")
    parser.add_argument("output", type=Path)
    parser.add_argument("--credential-file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        status = capture(args.session, args.output, args.credential_file)
    except (CaptureFailure, OSError, ValueError):
        status = "capture_local_error"
    print(status)
    return 0 if status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
