"""Counterexamples to treating a bounded quotation diagnostic as certified data."""

from dataclasses import replace
from decimal import Decimal
import io
import json
from pathlib import Path
import sqlite3
import stat
from types import SimpleNamespace
import urllib.error
import urllib.parse

import pytest

from data import kis_basis_probe as probe
from data import kis_klines as client
from data.bingx_klines import KlineRow


EXPECTED_ANCHORS = (
    ("033660", "20210805"), ("316140", "20210827"),
    ("367480", "20230817"), ("146060", "20230908"),
    ("476470", "20250619"), ("462310", "20250709"),
)
BOGUS_CODES = ("999999", "ZZZZZZ", "000000")
VERSION = "a" * 40


def bar(day="20210805", *, raw=False):
    factor = Decimal(2 if raw else 1)
    return KlineRow(
        open_time_ms=client.trading_date_to_ms(day),
        open=Decimal(100) * factor, high=Decimal(110) * factor,
        low=Decimal(90) * factor, close=Decimal(105) * factor,
        volume=Decimal(3), quote_volume=Decimal(315),
    )


def make_scan(path, *, panel_start="20190102", panel_end="20260918"):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE scan_panel(id INTEGER, start TEXT, end TEXT);
            CREATE TABLE scan_progress(code TEXT, status TEXT);
            CREATE TABLE scan_bars(code TEXT, bsop_date TEXT, open TEXT,
                high TEXT, low TEXT, close TEXT, volume TEXT, turnover TEXT);
        """)
        conn.execute("INSERT INTO scan_panel VALUES(1,?,?)", (panel_start, panel_end))
        for code, day in EXPECTED_ANCHORS:
            conn.execute("INSERT INTO scan_progress VALUES(?,'done')", (code,))
            conn.execute("INSERT INTO scan_bars VALUES(?,?, '100','110','90','105','3','315')", (code, day))
    return path


def read_json(path):
    return json.loads(path.read_text())


def events(output):
    return [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]


def assert_rejected(output, reason):
    assert read_json(output / "failure.json")["reason"] == reason
    assert not (output / "result.json").exists()


@pytest.fixture(autouse=True)
def safe_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "ROOT", tmp_path / "checkout")
    monkeypatch.setattr(probe, "source_version", lambda: VERSION)
    monkeypatch.setattr(probe, "in_continuous_session", lambda: False)
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)


@pytest.fixture
def run_inputs(tmp_path):
    return make_scan(tmp_path / "scan.sqlite3"), tmp_path / "evidence"


def install_quotes(monkeypatch, transform=None):
    calls = []

    def fetch(session, code, start, end, *, adjusted, is_index=False, before_attempt=None, before_headers=None):
        if before_headers is not None:
            before_headers()
        before_attempt()
        calls.append((code, start, end, adjusted, is_index))
        rows = [] if code in BOGUS_CODES else [bar(start, raw=adjusted == "1")]
        return transform(calls, rows) if transform else rows

    monkeypatch.setattr(probe, "fetch_daily_page", fetch)
    return calls


def session():
    return SimpleNamespace(host=client.PAPER_HOST)


def test_fixed_matrix_is_72_calls_with_every_control_before_its_targets(run_inputs, monkeypatch):
    scan, output = run_inputs
    calls = install_quotes(monkeypatch)
    result = probe.acquire(scan, output, session)

    expected = []
    for code, day in EXPECTED_ANCHORS:
        for basis in ("0", "1"):
            expected.extend((control, day, day, basis, False) for control in (*BOGUS_CODES, "005930"))
        expected.extend((code, day, day, basis, False) for basis in ("0", "1"))
    expected.extend((code, day, day, basis, False) for code, day in EXPECTED_ANCHORS for basis in ("0", "1"))
    assert calls == expected
    assert len(calls) == result["logical_calls"] == result["quotation_http_attempts"] == 72
    assert result["basis_certified"] is False
    assert result["code_version"] == VERSION
    assert [(a["code"], a["day"]) for a in result["anchors"]] == list(EXPECTED_ANCHORS)
    assert all(a["adjusted"]["close"] == "105" and a["raw"]["close"] == "210" for a in result["anchors"])
    assert len([e for e in events(output) if e["event"] == "response_validated"]) == 72
    assert read_json(output / "result.json") == result
    assert not (output / "failure.json").exists()


@pytest.mark.parametrize("fault", ["pending_fsync", "rename"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_final_publication_failure_never_leaves_a_success_artifact(run_inputs, monkeypatch, fault, cleanup_fails):
    scan, output = run_inputs
    pending = output / ".result.pending"
    failure = output / "failure.json"
    original_fsync = probe.os.fsync
    original_rename = Path.rename
    original_unlink = Path.unlink
    publication_error = OSError("synthetic final publication failure")
    pending_synced = False
    failure_synced = False
    injected = []

    def fsync(fd):
        nonlocal pending_synced, failure_synced
        inode = probe.os.fstat(fd).st_ino
        is_pending = pending.exists() and pending.stat().st_ino == inode
        if fault == "pending_fsync" and is_pending:
            injected.append("pending_fsync")
            raise publication_error
        original_fsync(fd)
        if is_pending:
            pending_synced = True
        if failure.exists() and failure.stat().st_ino == inode:
            failure_synced = True

    def rename(path, target):
        if path == pending and fault == "rename":
            assert pending_synced, "publication attempted before durable output"
            injected.append("rename")
            raise publication_error
        return original_rename(path, target)

    def unlink(path, *args, **kwargs):
        if path == pending and cleanup_fails:
            injected.append("cleanup")
            raise OSError("synthetic cleanup failure must not replace original")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(probe.os, "fsync", fsync)
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(Path, "unlink", unlink)
    install_quotes(monkeypatch)
    with pytest.raises(OSError, match="synthetic final") as exc:
        probe.acquire(scan, output, session)
    assert exc.value is publication_error
    assert injected == ([fault, "cleanup"] if cleanup_fails else [fault])
    assert pending.exists() is cleanup_fails
    assert failure_synced
    assert read_json(failure)["logical_calls"] == 72
    assert read_json(failure)["quotation_http_attempts"] == 72
    assert_rejected(output, "upstream_or_interrupted")


@pytest.mark.parametrize("control_index", range(8))
def test_any_failed_control_prevents_first_target(run_inputs, monkeypatch, control_index):
    scan, output = run_inputs

    def corrupt(calls, rows):
        if len(calls) == control_index + 1:
            return [] if calls[-1][0] == "005930" else [bar()]
        return rows

    calls = install_quotes(monkeypatch, corrupt)
    with pytest.raises(probe.ProbeRefusal):
        probe.acquire(scan, output, session)
    assert len(calls) == control_index + 1
    assert not any(code == "033660" for code, *_ in calls)
    reason = "missing_or_duplicate_response" if control_index in (3, 7) else "negative_control_has_rows"
    assert_rejected(output, reason)


@pytest.mark.parametrize("fault,reason", [
    ("empty", "missing_or_duplicate_response"),
    ("duplicate", "missing_or_duplicate_response"),
    ("wrong_date", "wrong_date"),
    ("malformed", "upstream_or_interrupted"),
    ("nondecimal", "invalid_number"),
    ("nan", "invalid_number"),
    ("infinite", "invalid_number"),
    ("zero_price", "nonpositive_or_unobservable_anchor"),
    ("negative_price", "nonpositive_or_unobservable_anchor"),
    ("zero_volume", "nonpositive_or_unobservable_anchor"),
    ("zero_turnover", "nonpositive_or_unobservable_anchor"),
    ("inconsistent", "inconsistent_ohlc"),
])
def test_bad_target_cannot_produce_success(run_inputs, monkeypatch, fault, reason):
    scan, output = run_inputs

    def corrupt(calls, rows):
        if calls[-1][0] != "033660":
            return rows
        bad = {
            "empty": [], "duplicate": rows * 2,
            "wrong_date": [bar("20210806")], "malformed": [object()],
            "nondecimal": [replace(bar(), close="105")],
            "nan": [replace(bar(), close=Decimal("NaN"))],
            "infinite": [replace(bar(), high=Decimal("Infinity"))],
            "zero_price": [replace(bar(), low=Decimal(0))],
            "negative_price": [replace(bar(), low=Decimal(-1))],
            "zero_volume": [replace(bar(), volume=Decimal(0))],
            "zero_turnover": [replace(bar(), quote_volume=Decimal(0))],
            "inconsistent": [replace(bar(), high=Decimal(104))],
        }
        return bad[fault]

    calls = install_quotes(monkeypatch, corrupt)
    with pytest.raises((probe.ProbeRefusal, AttributeError)):
        probe.acquire(scan, output, session)
    assert len(calls) == 9
    assert_rejected(output, reason)


@pytest.mark.parametrize("field", ("open", "high", "low", "close", "volume", "quote_volume"))
def test_archived_adjusted_mismatch_compares_every_field(run_inputs, monkeypatch, field):
    scan, output = run_inputs

    def changed(calls, rows):
        if calls[-1][0] == "033660":
            return [replace(rows[0], **{field: getattr(rows[0], field) + Decimal(1)})]
        return rows

    calls = install_quotes(monkeypatch, changed)
    with pytest.raises(probe.ProbeRefusal, match="archived_adjusted_mismatch"):
        probe.acquire(scan, output, session)
    assert len(calls) == 9
    assert_rejected(output, "archived_adjusted_mismatch")


@pytest.mark.parametrize("basis,failed_call", [("0", 61), ("1", 62)])
def test_both_target_bases_must_repeat_identically(run_inputs, monkeypatch, basis, failed_call):
    scan, output = run_inputs

    def changed(calls, rows):
        if len(calls) > 60 and calls[-1][3] == basis:
            return [replace(rows[0], close=rows[0].close + Decimal(1))]
        return rows

    calls = install_quotes(monkeypatch, changed)
    with pytest.raises(probe.ProbeRefusal, match="repeat_mismatch"):
        probe.acquire(scan, output, session)
    assert len(calls) == failed_call
    assert_rejected(output, "repeat_mismatch")


def test_decimal_padding_does_not_create_false_mismatch(run_inputs, monkeypatch):
    scan, output = run_inputs

    def padded(calls, rows):
        return [replace(row, **{field: getattr(row, field).quantize(Decimal(".000")) for field in probe.FIELDS}) for row in rows]

    install_quotes(monkeypatch, padded)
    assert probe.acquire(scan, output, session)["logical_calls"] == 72


@pytest.mark.parametrize("start,end", [("19910828", "20181231"), ("20190102", "20260919")])
def test_wrong_panel_refused_before_any_price_or_progress_read(tmp_path, monkeypatch, start, end):
    scan = make_scan(tmp_path / "reserved.sqlite3", panel_start=start, panel_end=end)
    output = tmp_path / "evidence"
    original = sqlite3.connect
    statements = []

    def traced(*args, **kwargs):
        conn = original(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(probe.sqlite3, "connect", traced)
    with pytest.raises(probe.ProbeRefusal, match="wrong_or_reserved_panel"):
        probe.acquire(scan, output, lambda: pytest.fail("auth before panel validation"))
    assert [s for s in statements if s.startswith("SELECT")] == ["SELECT start,end FROM scan_panel WHERE id=1"]
    assert not (output / "archive.json").exists()
    assert_rejected(output, "wrong_or_reserved_panel")


@pytest.mark.parametrize("mutation,reason", [
    ("DELETE FROM scan_bars WHERE code='033660'", "missing_or_duplicate_archived_anchor"),
    ("INSERT INTO scan_bars SELECT * FROM scan_bars WHERE code='033660'", "missing_or_duplicate_archived_anchor"),
    ("UPDATE scan_progress SET status='failed' WHERE code='033660'", "incomplete_scan"),
    ("UPDATE scan_bars SET close='not-a-number' WHERE code='033660'", "upstream_or_interrupted"),
    ("UPDATE scan_bars SET turnover='0' WHERE code='033660'", "nonpositive_or_unobservable_anchor"),
])
def test_invalid_archive_stops_before_authentication(run_inputs, mutation, reason):
    scan, output = run_inputs
    with sqlite3.connect(scan) as conn:
        conn.execute(mutation)
    with pytest.raises(Exception):
        probe.acquire(scan, output, lambda: pytest.fail("auth before archive validation"))
    assert_rejected(output, reason)


def test_database_is_opened_read_only_and_unchanged(run_inputs, monkeypatch):
    scan, output = run_inputs
    original = sqlite3.connect
    before = scan.read_bytes()
    files_before = set(scan.parent.iterdir())
    statements = []

    def readonly(*args, **kwargs):
        assert args[0].endswith("?mode=ro") and kwargs["uri"] is True
        conn = original(*args, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE scan_panel SET start='19910828'")
        conn.rollback()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(probe.sqlite3, "connect", readonly)
    install_quotes(monkeypatch)
    probe.acquire(scan, output, session)
    assert scan.read_bytes() == before
    assert set(scan.parent.iterdir()) - files_before == {output}
    assert "PRAGMA query_only=ON" in statements and "BEGIN" in statements
    price_reads = [s for s in statements if "FROM scan_bars" in s]
    assert len(price_reads) == 6
    for statement, (code, day) in zip(price_reads, EXPECTED_ANCHORS, strict=True):
        assert f"code='{code}' AND bsop_date='{day}'" in statement


def test_started_is_fsynced_before_price_access_and_auth(run_inputs, monkeypatch):
    scan, output = run_inputs
    original_connect = sqlite3.connect
    original_fsync = probe.os.fsync
    sync_count = 0
    price_reads = 0

    def fsync(fd):
        nonlocal sync_count
        original_fsync(fd)
        sync_count += 1

    def guarded_connect(*args, **kwargs):
        assert sync_count >= 1
        assert read_json(output / "started.json")["planned_logical_calls"] == 72
        conn = original_connect(*args, **kwargs)

        def authorize(action, table, column, db, trigger):
            nonlocal price_reads
            if action == sqlite3.SQLITE_READ and table == "scan_bars":
                price_reads += 1
                assert (output / "started.json").exists()
                assert sync_count >= 1
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
        return conn

    def authenticate():
        assert price_reads > 0 and sync_count >= 2
        assert read_json(output / "archive.json")["anchors"]["033660"]["close"] == "105"
        return session()

    monkeypatch.setattr(probe.os, "fsync", fsync)
    monkeypatch.setattr(probe.sqlite3, "connect", guarded_connect)
    install_quotes(monkeypatch)
    probe.acquire(scan, output, authenticate)
    assert price_reads > 0


def test_directory_entries_are_durable_before_prices_and_success_return(run_inputs, monkeypatch):
    scan, output = run_inputs
    original_fsync = probe.os.fsync
    original_archive = probe.archived_anchors
    original_rename = Path.rename
    boundaries = []

    def fsync(fd):
        info = probe.os.fstat(fd)
        original_fsync(fd)
        if stat.S_ISDIR(info.st_mode):
            if info.st_ino == output.parent.stat().st_ino:
                boundaries.append("output_parent_synced")
            elif output.exists() and info.st_ino == output.stat().st_ino:
                boundaries.append("published_directory_synced" if (output / "result.json").exists() else "output_directory_synced")
        elif (output / "started.json").exists() and info.st_ino == (output / "started.json").stat().st_ino:
            boundaries.append("started_file_synced")

    def archived(path):
        assert boundaries[:3] == ["output_parent_synced", "started_file_synced", "output_directory_synced"]
        boundaries.append("prices_read")
        return original_archive(path)

    def authenticate():
        assert "prices_read" in boundaries
        boundaries.append("authenticated")
        return session()

    def rename(path, target):
        renamed = original_rename(path, target)
        if path == output / ".result.pending":
            boundaries.append("renamed")
        return renamed

    monkeypatch.setattr(probe.os, "fsync", fsync)
    monkeypatch.setattr(probe, "archived_anchors", archived)
    monkeypatch.setattr(Path, "rename", rename)
    install_quotes(monkeypatch)
    result = probe.acquire(scan, output, authenticate)
    boundaries.append("returned")
    assert result["logical_calls"] == 72
    assert boundaries[-3:] == ["renamed", "published_directory_synced", "returned"]


@pytest.mark.parametrize("which_directory", ["output_parent", "started_parent"])
def test_preprice_directory_sync_failure_refuses_before_database_or_auth(run_inputs, monkeypatch, which_directory):
    scan, output = run_inputs
    original_fsync = probe.os.fsync
    injected = []
    error = OSError("synthetic directory sync failure")

    def fsync(fd):
        info = probe.os.fstat(fd)
        target = output.parent if which_directory == "output_parent" else output
        if stat.S_ISDIR(info.st_mode) and target.exists() and info.st_ino == target.stat().st_ino:
            injected.append(which_directory)
            raise error
        original_fsync(fd)

    monkeypatch.setattr(probe.os, "fsync", fsync)
    monkeypatch.setattr(probe, "archived_anchors", lambda _: pytest.fail("price read before durable directory entry"))
    with pytest.raises(OSError) as exc:
        probe.acquire(scan, output, lambda: pytest.fail("authentication before durable directory entry"))
    assert exc.value is error
    assert injected == [which_directory]
    assert not (output / "archive.json").exists()
    assert not (output / "result.json").exists()


def test_postrename_directory_sync_failure_removes_success_and_records_failure(run_inputs, monkeypatch):
    scan, output = run_inputs
    original_fsync = probe.os.fsync
    injected = []
    error = OSError("synthetic publication directory failure")
    failure_directory_synced = []

    def fsync(fd):
        info = probe.os.fstat(fd)
        is_output = stat.S_ISDIR(info.st_mode) and output.exists() and info.st_ino == output.stat().st_ino
        if is_output and (output / "result.json").exists() and not injected:
            injected.append("postrename")
            raise error
        original_fsync(fd)
        if is_output and (output / "failure.json").exists():
            failure_directory_synced.append(True)

    monkeypatch.setattr(probe.os, "fsync", fsync)
    install_quotes(monkeypatch)
    with pytest.raises(OSError) as exc:
        probe.acquire(scan, output, session)
    assert exc.value is error
    assert injected == ["postrename"]
    assert failure_directory_synced == [True]
    assert not (output / ".result.pending").exists()
    assert_rejected(output, "upstream_or_interrupted")


def test_missing_evidence_parent_is_not_created_recursively(run_inputs, monkeypatch):
    scan, output = run_inputs
    nested = output / "unprovisioned" / "evidence"
    monkeypatch.setattr(probe, "archived_anchors", lambda _: pytest.fail("price read with missing evidence parent"))
    with pytest.raises(FileNotFoundError):
        probe.acquire(scan, nested, lambda: pytest.fail("authenticated with missing evidence parent"))
    assert not output.exists()


@pytest.mark.parametrize("condition", ["session", "dirty", "inside_checkout", "existing"])
def test_preflight_refusals_do_not_read_prices_or_authenticate(run_inputs, monkeypatch, condition):
    scan, output = run_inputs
    monkeypatch.setattr(probe, "archived_anchors", lambda _: pytest.fail("price read during preflight"))
    if condition == "session":
        monkeypatch.setattr(probe, "in_continuous_session", lambda: True)
    elif condition == "dirty":
        def dirty():
            raise probe.ProbeRefusal("uncommitted_sources")
        monkeypatch.setattr(probe, "source_version", dirty)
    elif condition == "inside_checkout":
        output = probe.ROOT / "evidence"
    else:
        output.mkdir()
        (output / "operator-file").write_text("preserve me")
    with pytest.raises((probe.ProbeRefusal, FileExistsError)):
        probe.acquire(scan, output, lambda: pytest.fail("preflight authenticated"))
    assert not (output / "started.json").exists()
    if condition == "existing":
        assert (output / "operator-file").read_text() == "preserve me"
    else:
        assert not output.exists()


def test_symlink_into_checkout_is_not_an_external_output(run_inputs, monkeypatch, tmp_path):
    scan, _ = run_inputs
    probe.ROOT.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(probe.ROOT, target_is_directory=True)
    with pytest.raises(probe.ProbeRefusal, match="outside_checkout"):
        probe.acquire(scan, alias / "evidence", lambda: pytest.fail("authenticated through symlink"))
    assert not (probe.ROOT / "evidence").exists()


# Preserve the unpatched function: most diagnostic tests intentionally avoid Git.
SOURCE_VERSION = probe.source_version


@pytest.mark.parametrize("status", [b" M python/data/kis_klines.py\n", b"?? python/data/new.py\n"])
def test_source_version_rejects_tracked_and_untracked_changes(monkeypatch, status):
    calls = []

    def git(args, **kwargs):
        calls.append(args)
        return status

    monkeypatch.setattr(probe.subprocess, "check_output", git)
    with pytest.raises(probe.ProbeRefusal, match="uncommitted_sources"):
        SOURCE_VERSION()
    assert len(calls) == 1 and "--untracked-files=all" in calls[0]


def vendor_row(day="20210805"):
    return {"stck_bsop_date": day, "stck_oprc": "100", "stck_hgpr": "110",
            "stck_lwpr": "90", "stck_clpr": "105", "acml_vol": "3", "acml_tr_pbmn": "315"}


class HttpSession:
    host = client.PAPER_HOST

    def headers(self, tr_id):
        assert tr_id == client.TR_DAILY_ITEM
        return {"authorization": "synthetic-token", "appkey": "synthetic-key"}


def http_response(payload):
    return io.BytesIO(json.dumps(payload).encode())


@pytest.mark.parametrize("stage", ["initial", "expired_refresh"])
def test_session_opening_during_token_cache_miss_blocks_post_and_get(run_inputs, monkeypatch, stage):
    scan, output = run_inputs
    protected = False
    cache_reads = []
    requests = []
    cache_writes = []
    clock_reads = []

    def cached_token(host, app_key):
        nonlocal protected
        cache_reads.append(host)
        if stage == "expired_refresh" and len(cache_reads) == 1:
            return "synthetic-bootstrap-token"
        protected = True
        return None

    def monotonic():
        clock_reads.append(None)
        return 0 if len(clock_reads) == 1 else client.TOKEN_REUSE_S + 1

    def urlopen(request, **kwargs):
        requests.append(request.method)
        if request.method == "POST":
            return http_response({"access_token": "synthetic-issued-token"})
        return http_response({"rt_cd": "0", "output2": []})

    monkeypatch.setenv("KIS_APP_KEY", "synthetic-key")
    monkeypatch.setenv("KIS_APP_SECRET", "synthetic-secret")
    monkeypatch.setattr(probe, "in_continuous_session", lambda: protected)
    monkeypatch.setattr(client, "_read_cached_token", cached_token)
    monkeypatch.setattr(client, "_write_cached_token", lambda *args: cache_writes.append(args))
    monkeypatch.setattr(client.time, "monotonic", monotonic)
    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen)

    assert probe.main(["--scan-db", str(scan), "--output-dir", str(output)]) == 1
    assert requests == [], "token POST or quotation GET escaped the final session guard"
    assert cache_writes == []
    assert len(cache_reads) == (1 if stage == "initial" else 3)
    assert read_json(output / "failure.json")["quotation_http_attempts"] == 0
    assert_rejected(output, "protected_session")


@pytest.mark.parametrize("cache_hit", [False, True])
def test_token_guard_allows_cache_reuse_and_outside_session_post(monkeypatch, cache_hit):
    requests = []
    cache_writes = []
    guard_calls = []

    def guard():
        guard_calls.append(None)
        probe.outside_session()

    def urlopen(request, **kwargs):
        requests.append(request.method)
        if request.method == "POST":
            assert request.full_url == client.PAPER_HOST + client.TOKEN_PATH
            return http_response({"access_token": "synthetic-token"})
        return http_response({"rt_cd": "0", "output2": [vendor_row()]})

    monkeypatch.setattr(client, "_read_cached_token", lambda *args: "synthetic-token" if cache_hit else None)
    monkeypatch.setattr(client, "_write_cached_token", lambda *args: cache_writes.append(args))
    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen)
    authenticated = client.KisSession("synthetic-key", "synthetic-secret", before_token_request=guard)
    assert client.fetch_daily_page(
        authenticated, "033660", "20210805", "20210805", adjusted="0",
        before_headers=probe.outside_session, before_attempt=probe.outside_session,
    ) == [bar()]
    assert requests == (["GET"] if cache_hit else ["POST", "GET"])
    assert len(guard_calls) == len(cache_writes) == (0 if cache_hit else 1)


def test_real_fetch_counts_transport_and_application_retries_separately(run_inputs, monkeypatch):
    scan, output = run_inputs
    requests = []

    def urlopen(request, **kwargs):
        requests.append(request)
        if len(requests) == 1:
            raise urllib.error.HTTPError(request.full_url, 500, "temporary", {}, None)
        if len(requests) == 2:
            return http_response({"rt_cd": "1", "msg_cd": "OPSQ0003"})
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        code = params["FID_INPUT_ISCD"][0]
        day = params["FID_INPUT_DATE_1"][0]
        assert params["FID_INPUT_DATE_2"] == [day]
        assert params["FID_COND_MRKT_DIV_CODE"] == ["J"]
        assert params["FID_PERIOD_DIV_CODE"] == ["D"]
        assert params["FID_ORG_ADJ_PRC"][0] in ("0", "1")
        assert request.method == "GET" and request.full_url.startswith(client.PAPER_HOST + client.DAILY_ITEM_PATH)
        return http_response({"rt_cd": "0", "output2": [] if code in BOGUS_CODES else [vendor_row(day)]})

    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen)
    result = probe.acquire(scan, output, HttpSession)
    assert result["logical_calls"] == 72
    assert result["quotation_http_attempts"] == len(requests) == 74
    attempts = [event for event in events(output) if event["event"] == "quotation_http_attempt"]
    assert [event["quotation_http_attempts"] for event in attempts] == list(range(1, 75))
    assert [event["logical_calls"] for event in attempts[:4]] == [1, 1, 1, 2]


@pytest.mark.parametrize("retry_kind", ["transport", "application"])
def test_session_opening_during_retry_prevents_next_http_attempt(run_inputs, monkeypatch, retry_kind):
    scan, output = run_inputs
    requests = []
    headers_called = []
    protected = False

    class GuardedSession(HttpSession):
        def headers(self, tr_id):
            assert not protected, "token refresh started inside the protected session"
            headers_called.append(tr_id)
            return super().headers(tr_id)

    def urlopen(request, **kwargs):
        nonlocal protected
        requests.append(request)
        protected = True
        if retry_kind == "transport":
            raise urllib.error.HTTPError(request.full_url, 500, "temporary", {}, None)
        return http_response({"rt_cd": "1", "msg_cd": "OPSQ0003"})

    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(probe, "in_continuous_session", lambda: protected)
    with pytest.raises(probe.ProbeRefusal, match="protected_session"):
        probe.acquire(scan, output, GuardedSession)
    assert len(requests) == 1
    assert headers_called == [client.TR_DAILY_ITEM]
    failure = read_json(output / "failure.json")
    assert failure["logical_calls"] == failure["quotation_http_attempts"] == 1
    assert_rejected(output, "protected_session")


def test_session_opening_during_inter_request_delay_prevents_first_http_attempt(run_inputs, monkeypatch):
    scan, output = run_inputs
    protected = False

    def sleep(_):
        nonlocal protected
        protected = True

    monkeypatch.setattr(probe.time, "sleep", sleep)
    monkeypatch.setattr(probe, "in_continuous_session", lambda: protected)
    monkeypatch.setattr(client.urllib.request, "urlopen", lambda *a, **k: pytest.fail("HTTP after session opened"))
    with pytest.raises(probe.ProbeRefusal, match="protected_session"):
        probe.acquire(scan, output, HttpSession)
    assert read_json(output / "failure.json")["quotation_http_attempts"] == 0
    assert_rejected(output, "protected_session")


@pytest.mark.parametrize("sync_boundary", ["event_file", "event_directory"])
def test_session_opening_during_attempt_evidence_sync_cancels_unsent_get(run_inputs, monkeypatch, sync_boundary):
    scan, output = run_inputs
    original_fsync = probe.os.fsync
    protected = False
    injected = []
    requests = []
    event_path = output / "events.jsonl"

    def fsync(fd):
        nonlocal protected
        info = probe.os.fstat(fd)
        original_fsync(fd)
        if injected or not event_path.exists():
            return
        last_event = events(output)[-1]
        if last_event["event"] != "quotation_http_attempt":
            return
        at_file = stat.S_ISREG(info.st_mode) and info.st_ino == event_path.stat().st_ino
        at_directory = stat.S_ISDIR(info.st_mode) and info.st_ino == output.stat().st_ino
        if (sync_boundary == "event_file" and at_file) or (sync_boundary == "event_directory" and at_directory):
            injected.append(sync_boundary)
            protected = True

    def urlopen(request, **kwargs):
        requests.append(request.method)
        return http_response({"rt_cd": "0", "output2": []})

    monkeypatch.setattr(probe.os, "fsync", fsync)
    monkeypatch.setattr(probe, "in_continuous_session", lambda: protected)
    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen)
    with pytest.raises(probe.ProbeRefusal, match="protected_session"):
        probe.acquire(scan, output, HttpSession)
    assert injected == [sync_boundary]
    assert requests == []
    assert read_json(output / "failure.json")["quotation_http_attempts"] == 0
    cancelled = [event for event in events(output) if event["event"] == "quotation_http_attempt_cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0]["quotation_http_attempts"] == 0
    assert_rejected(output, "protected_session")


@pytest.mark.parametrize("payload", [
    {"rt_cd": "0"}, {"rt_cd": "0", "output2": {}},
    {"rt_cd": "0", "output2": [None]}, {"rt_cd": "0", "output2": [{}]},
    {"rt_cd": "0", "output2": [dict(vendor_row(), stck_clpr="malformed")]},
])
def test_real_parser_refuses_malformed_negative_control_response(run_inputs, monkeypatch, payload):
    scan, output = run_inputs
    monkeypatch.setattr(client.urllib.request, "urlopen", lambda *a, **k: http_response(payload))
    with pytest.raises(client.KisKlinesError):
        probe.acquire(scan, output, HttpSession)
    assert_rejected(output, "upstream_or_interrupted")


@pytest.mark.parametrize("present", [None, "KIS_APP_KEY", "KIS_APP_SECRET"])
def test_missing_credentials_refuse_cli_without_files_or_auth(run_inputs, monkeypatch, capsys, present):
    scan, output = run_inputs
    if present:
        monkeypatch.setenv(present, "synthetic-private-value")
    monkeypatch.setattr(probe, "acquire", lambda *args: pytest.fail("started without complete credentials"))
    with pytest.raises(SystemExit) as exc:
        probe.main(["--scan-db", str(scan), "--output-dir", str(output)])
    assert exc.value.code == 2
    assert "synthetic-private-value" not in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("where,exception_type", [
    ("auth", RuntimeError), ("quote", RuntimeError),
    ("quote", probe.ProbeRefusal), ("quote", KeyboardInterrupt),
])
def test_cli_and_failure_evidence_never_echo_secret_bearing_exceptions(run_inputs, monkeypatch, capsys, where, exception_type):
    scan, output = run_inputs
    private = "synthetic-private-value-never-persist"
    monkeypatch.setenv("KIS_APP_KEY", private)
    monkeypatch.setenv("KIS_APP_SECRET", private)

    def broken(*args, **kwargs):
        raise exception_type(private)

    monkeypatch.setattr(probe, "KisSession", broken if where == "auth" else lambda *a, **k: session())
    monkeypatch.setattr(probe, "fetch_daily_page", broken)
    assert probe.main(["--scan-db", str(scan), "--output-dir", str(output)]) == 1
    captured = capsys.readouterr()
    assert private not in captured.out + captured.err
    assert "Traceback" not in captured.err
    assert_rejected(output, "upstream_or_interrupted")
    assert all(private not in path.read_text() for path in output.iterdir())
