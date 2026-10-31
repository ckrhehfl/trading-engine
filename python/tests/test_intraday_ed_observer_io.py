"""Invented stored bytes and native temporary journals; no source activation."""

import ast
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import importlib
import inspect
import json
import os
import stat

import pytest

from research import intraday_ed_observer as core
from research import intraday_ed_observer_io as io


BASE = datetime(2026, 1, 2, 0, 10, tzinfo=timezone.utc)
STREAM = core.StreamIdentity("invented", "offline-fixture", "fixture-stock", "KRX", "raw",
                             "KRW/share", "Asia/Seoul")
POLICY = core.ObserverPolicy(8, timedelta(seconds=5), timedelta(seconds=3), timedelta(milliseconds=50))


def quote(index=1, **changes):
    stamp = BASE + timedelta(seconds=index - 1)
    def exact(text):
        return core.ExactField(core.ObservationStatus.OBSERVED, Decimal(text))
    receipt = core.QuoteReceipt(
        STREAM, f"quote-{index}", index, "invented-v1", "invented", "a" * 64, False,
        core.ReceiptClocks(date(2026, 1, 2), stamp, stamp, stamp, stamp, stamp),
        "continuous", exact("100.000000000000000001"), exact("101.00"),
        exact("0"), exact("7.000000000000000001"),
    )
    return replace(receipt, **changes)


def contract(**changes):
    values = dict(stream=STREAM, policy=POLICY, contract_sha256="b" * 64,
                  source_sha256="c" * 64, runtime_sha256="d" * 64,
                  model_sha256="e" * 64, rule_sha256="f" * 64,
                  max_line_bytes=4096, max_bytes=65536)
    values.update(changes)
    return io.JournalContract(**values)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def assessment(state):
    return core.freeze_decision(state, decision_at=BASE + timedelta(seconds=2),
                                outcome="decline", reason="injected offline rule",
                                model_sha256="e" * 64, contract_sha256="b" * 64)


def completed(path, records=(None,), expected=None):
    expected = expected or contract()
    records = (quote(),) if records == (None,) else records
    with io.JournalWriter(path, expected) as writer:
        for record in records:
            writer.append(io.receipt_bytes(record))
    return expected


def replace_bytes(path, raw):
    # Deliberate damage to this test's newly created temporary file only.
    path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(0o400)


def test_exact_decimal_text_unknown_error_and_transport_round_trip():
    missing = core.ExactField(core.ObservationStatus.UNKNOWN, None, "invented omission")
    error = core.ExactField(core.ObservationStatus.ERROR, None, "invented decode failure")
    receipts = (quote(), quote(bid_quantity=missing, ask_quantity=error),
                core.TransportReceipt(STREAM, BASE, core.TransportKind.GAP, "invented gap"))
    for receipt in receipts:
        raw = io.receipt_bytes(receipt)
        parsed = io.parse_receipt(raw, max_line_bytes=4096)
        assert parsed == receipt
        assert io.receipt_bytes(parsed) == raw
        assert b'"action":"NO_ORDER"' in raw
    assert io.parse_receipt(io.receipt_bytes(quote()), max_line_bytes=4096).bid.value.as_tuple() == (
        Decimal("100.000000000000000001").as_tuple())
    assert io.parse_receipt(io.receipt_bytes(quote()), max_line_bytes=4096).ask.value.as_tuple() == Decimal("101.00").as_tuple()


@pytest.mark.parametrize("change", [
    lambda value: value.update(action="BUY"),
    lambda value: value.update(order={"quantity": 10}),
    lambda value: value.update(schema="unregistered"),
    lambda value: value.update(kind="order"),
    lambda value: value["receipt"].update(sequence=True),
    lambda value: value["receipt"].update(is_final=0),
    lambda value: value["receipt"].update(account="invented"),
    lambda value: value["receipt"]["bid"].update(value=100),
    lambda value: value["receipt"]["bid"].update(value="NaN"),
    lambda value: value["receipt"]["bid"].update(value="Infinity"),
    lambda value: value["receipt"]["bid"].update(value="+100"),
    lambda value: value["receipt"]["clocks"].update(received_at="2026-01-02T00:10:00"),
    lambda value: value["receipt"]["clocks"].update(observation_date="20260102"),
    lambda value: value["receipt"]["stream"].update(channel="OTHER"),
])
def test_untrusted_extra_fields_orders_inexact_values_and_normalized_clocks_refused(change):
    value = json.loads(io.receipt_bytes(quote()))
    change(value)
    with pytest.raises(io.ObserverIORefusal):
        io.parse_receipt(canonical(value), max_line_bytes=4096)


@pytest.mark.parametrize("raw", [b"{}\n", b'{"x":1,"x":2}', b'{"x":1.5}',
                                 b'{"x":NaN}', b'{ "x":1}', b"\xff", b"[]", b"{} {}"])
def test_duplicate_noncanonical_nonfinite_and_malformed_json_refused(raw):
    with pytest.raises(io.ObserverIORefusal):
        io.parse_receipt(raw, max_line_bytes=4096)


@pytest.mark.parametrize("maximum", [0, -1, True, 2.0])
def test_explicit_positive_byte_limits_required(maximum):
    with pytest.raises(io.ObserverIORefusal):
        contract(max_bytes=maximum)
    with pytest.raises(io.ObserverIORefusal):
        io.parse_receipt(b"{}", max_line_bytes=maximum)


def test_line_bound_is_checked_before_receipt_decoding():
    raw = io.receipt_bytes(quote())
    with pytest.raises(io.ObserverIORefusal, match="bounded"):
        io.parse_receipt(raw, max_line_bytes=len(raw) - 1)


def test_journal_rebuilds_state_from_exact_raw_bytes_and_all_hashes_are_immutable(tmp_path):
    path = tmp_path / "receipts.jsonl"
    records = (quote(), quote(2))
    expected = completed(path, records)
    result = io.replay_journal(path, expected)
    assert result.raw_receipts == tuple(io.receipt_bytes(record) for record in records)
    assert result.state == core.replay(STREAM, records, POLICY)
    assert result.journal_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.action == result.contract.action == "NO_ORDER"
    assert assessment(result.state).frozen.action == "NO_ORDER"
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    with pytest.raises(FrozenInstanceError):
        result.contract.runtime_sha256 = "0" * 64
    for name in ("source_sha256", "runtime_sha256", "model_sha256", "rule_sha256", "contract_sha256"):
        with pytest.raises(io.ObserverIORefusal, match="metadata"):
            io.replay_journal(path, replace(expected, **{name: "0" * 64}))


def test_duplicate_conflict_disconnect_reconnect_and_gap_evidence_remain_sticky(tmp_path):
    first = quote()
    records = (first, first, replace(first, ask=core.ExactField(core.ObservationStatus.OBSERVED, Decimal("103"))),
               core.TransportReceipt(STREAM, BASE, core.TransportKind.DISCONNECTED, "invented disconnect"),
               core.TransportReceipt(STREAM, BASE, core.TransportKind.RECONNECTED, "invented reconnect"),
               core.TransportReceipt(STREAM, BASE, core.TransportKind.GAP, "invented loss"), quote(2))
    path = tmp_path / "faults.jsonl"
    expected = completed(path, records)
    result = io.replay_journal(path, expected)
    assert result.state.records == records
    assert len(result.raw_receipts) == 7
    assert result.state.quality[1].reasons == ("duplicate_message",)
    decision = assessment(result.state)
    assert decision.frozen is None
    assert {"conflicting_message_id", "transport_disconnected", "transport_reconnected", "transport_gap"} <= set(decision.reasons)


def test_replay_uses_changed_raw_receipt_even_with_a_valid_recomputed_frame_digest(tmp_path):
    path = tmp_path / "raw-oracle.jsonl"
    expected = completed(path)
    lines = path.read_bytes().splitlines(keepends=True)
    changed = quote(bid_quantity=core.ExactField(core.ObservationStatus.UNKNOWN, None, "invented lost value"))
    lines[1] = io.receipt_bytes(changed) + b"\n"
    footer = json.loads(lines[-1])
    footer["raw_prefix_sha256"] = hashlib.sha256(b"".join(lines[:-1])).hexdigest()
    lines[-1] = canonical(footer) + b"\n"
    replace_bytes(path, b"".join(lines))
    result = io.replay_journal(path, expected)
    assert result.state == core.replay(STREAM, (changed,), POLICY)
    assert assessment(result.state).frozen is None
    assert result.state.latest.bid_quantity.value is None


@pytest.mark.parametrize("damage", [
    lambda raw: raw.rsplit(b"\n", 2)[0] + b"\n",  # Crash before complete marker.
    lambda raw: raw[:-1],
    lambda raw: raw + b"{",
    lambda raw: raw.replace(b'"record_count":1', b'"record_count":2'),
    lambda raw: raw.replace(b'"101.00"', b'"102.00"'),
    lambda raw: raw.replace(b'"kind":"quote"', b'"kind":"state"'),
])
def test_partial_malformed_conflicting_or_appended_tail_never_silently_recovers(tmp_path, damage):
    path = tmp_path / "damaged.jsonl"
    expected = completed(path)
    damaged = damage(path.read_bytes())
    replace_bytes(path, damaged)
    with pytest.raises(io.ObserverIORefusal):
        io.replay_journal(path, expected)
    assert path.read_bytes() == damaged


def test_abort_and_exception_leave_incomplete_receipts_without_truncation(tmp_path):
    path = tmp_path / "aborted.jsonl"
    with pytest.raises(RuntimeError, match="invented crash"):
        with io.JournalWriter(path, contract()) as writer:
            writer.append(io.receipt_bytes(quote()))
            raise RuntimeError("invented crash")
    raw = path.read_bytes()
    assert io.receipt_bytes(quote()) + b"\n" in raw
    with pytest.raises(io.ObserverIORefusal, match="incomplete"):
        io.replay_journal(path, contract())
    assert path.read_bytes() == raw
    with pytest.raises(FileExistsError):
        io.JournalWriter(path, contract())


def test_fsync_failure_keeps_evidence_and_cannot_finish_as_complete(tmp_path, monkeypatch):
    path = tmp_path / "failed-sync.jsonl"
    writer = io.JournalWriter(path, contract())
    real_fsync = os.fsync
    def fail_sync(descriptor):
        raise OSError("invented sync failure")
    monkeypatch.setattr(os, "fsync", fail_sync)
    with pytest.raises(OSError, match="sync failure"):
        writer.append(io.receipt_bytes(quote()))
    monkeypatch.setattr(os, "fsync", real_fsync)
    with pytest.raises(io.ObserverIORefusal, match="failed"):
        writer.finish()
    raw = path.read_bytes()
    assert io.receipt_bytes(quote()) in raw
    with pytest.raises(io.ObserverIORefusal, match="incomplete"):
        io.replay_journal(path, contract())


def test_each_receipt_is_fsynced_before_append_returns_and_partial_write_stays_unknown(tmp_path, monkeypatch):
    path = tmp_path / "durable.jsonl"
    real_sync = os.fsync
    synced_sizes = []
    def record_sync(descriptor):
        info = os.fstat(descriptor)
        if stat.S_ISREG(info.st_mode):
            synced_sizes.append(info.st_size)
        real_sync(descriptor)
    monkeypatch.setattr(os, "fsync", record_sync)
    writer = io.JournalWriter(path, contract())
    writer.append(io.receipt_bytes(quote()))
    assert synced_sizes[-1] == path.stat().st_size
    assert len(synced_sizes) == 2
    real_write = io.storage.write_all
    def partial_write(handle, raw):
        real_write(handle, raw[:17])
        raise OSError("invented partial write")
    monkeypatch.setattr(io.storage, "write_all", partial_write)
    with pytest.raises(OSError, match="partial write"):
        writer.append(io.receipt_bytes(quote(2)))
    writer.abort()
    raw = path.read_bytes()
    assert raw.endswith(io.receipt_bytes(quote(2))[:17])
    with pytest.raises(io.ObserverIORefusal, match="partial"):
        io.replay_journal(path, contract())
    assert path.read_bytes() == raw


def test_unsealed_completion_is_not_claimed_as_a_successful_finish(tmp_path):
    path = tmp_path / "unsealed.jsonl"
    expected = completed(path)
    path.chmod(0o600)
    with pytest.raises(io.ObserverIORefusal, match="not sealed"):
        io.replay_journal(path, expected)


def test_record_and_total_byte_limits_refuse_without_modifying_accepted_prefix(tmp_path):
    path = tmp_path / "capacity.jsonl"
    expected = contract(policy=replace(POLICY, max_records=1))
    with io.JournalWriter(path, expected) as writer:
        writer.append(io.receipt_bytes(quote()))
        prefix = path.read_bytes()
        with pytest.raises(core.ReplayCapacityExceeded):
            writer.append(io.receipt_bytes(quote(2)))
        assert path.read_bytes() == prefix
    assert len(io.replay_journal(path, expected).raw_receipts) == 1

    bounded = contract(max_bytes=1700)
    other = tmp_path / "bytes.jsonl"
    with io.JournalWriter(other, bounded) as writer:
        prefix = other.read_bytes()
        with pytest.raises(io.ObserverIORefusal, match="max_bytes"):
            writer.append(io.receipt_bytes(quote()))
        assert other.read_bytes() == prefix
    assert io.replay_journal(other, bounded).raw_receipts == ()


def test_exclusive_creation_private_permissions_and_no_symlink_or_worktree_destination(tmp_path):
    path = tmp_path / "existing.jsonl"
    path.write_bytes(b"invented original")
    with pytest.raises(FileExistsError):
        io.JournalWriter(path, contract())
    assert path.read_bytes() == b"invented original"
    link = tmp_path / "link.jsonl"
    link.symlink_to(path)
    with pytest.raises((ValueError, OSError)):
        io.JournalWriter(link, contract())
    folder = tmp_path / "linked-parent"
    folder.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        io.JournalWriter(folder / "new.jsonl", contract())
    (tmp_path / ".git").mkdir()
    with pytest.raises(ValueError, match="worktree"):
        io.JournalWriter(tmp_path / "worktree.jsonl", contract())


@pytest.mark.parametrize("marker_kind", ["file", "directory", "dangling_symlink"])
@pytest.mark.parametrize("operation", ["create", "replay"])
def test_anchored_ancestor_git_entry_blocks_native_worktree_even_after_path_precheck(
        tmp_path, monkeypatch, marker_kind, operation):
    ancestor = tmp_path / "invented-native-worktree"
    ancestor.mkdir(mode=0o700)
    private = ancestor / "private"
    private.mkdir(mode=0o700)
    path = private / "receipts.jsonl"
    expected = completed(path) if operation == "replay" else contract()
    precheck = io.storage.native_cache
    marker = ancestor / ".git"
    def add_git_after_precheck(candidate, worktree):
        precheck(candidate, worktree)
        if marker_kind == "file":
            marker.write_bytes(b"invented Git pointer; contents must not be read")
        elif marker_kind == "directory":
            marker.mkdir()
        else:
            marker.symlink_to(ancestor / "absent-invented-git")
    monkeypatch.setattr(io.storage, "native_cache", add_git_after_precheck)
    with pytest.raises(io.ObserverIORefusal, match="inside a worktree"):
        if operation == "create":
            io.JournalWriter(path, expected)
        else:
            io.replay_journal(path, expected)
    assert marker.lstat()
    if operation == "create":
        assert not path.exists()


def test_group_readable_directory_and_hardlinked_journal_are_refused(tmp_path):
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(io.ObserverIORefusal, match="private"):
        io.JournalWriter(public / "new.jsonl", contract())
    path = tmp_path / "private.jsonl"
    expected = completed(path)
    os.link(path, tmp_path / "hardlink.jsonl")
    with pytest.raises(io.ObserverIORefusal, match="exclusive"):
        io.replay_journal(path, expected)


def test_external_change_is_detected_without_overwriting_it(tmp_path):
    path = tmp_path / "external.jsonl"
    writer = io.JournalWriter(path, contract())
    with path.open("ab") as handle:
        handle.write(b"invented external change\n")
    changed = path.read_bytes()
    with pytest.raises(io.ObserverIORefusal, match="outside sole writer"):
        writer.append(io.receipt_bytes(quote()))
    writer.abort()
    assert path.read_bytes() == changed


def test_import_has_no_io_and_production_imports_expose_no_network_or_order_route(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("import performed IO")
    with monkeypatch.context() as patched:
        patched.setattr(os, "open", forbidden)
        patched.setattr(io.storage, "native_cache", forbidden)
        importlib.reload(io)
    tree = ast.parse(inspect.getsource(io))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imports <= {"__future__", "dataclasses", "datetime", "decimal", "hashlib", "json", "os",
                       "pathlib", "stat", "typing", "research"}
    assert not any(isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                   and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"
                   for node in ast.walk(tree))
