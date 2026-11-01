"""Invented KIS-shaped bytes only; no export acquisition or source activation."""

import ast
import base64
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
from research import intraday_ed_observer_kis_export as connection


BASE = datetime(2026, 1, 2, 0, 10, tzinfo=timezone.utc)
STREAM = core.StreamIdentity("KIS", "invented-offline-export", "KRX:005930", "KRX", "raw",
                             "KRW/share", "Asia/Seoul")
POLICY = core.ObserverPolicy(8, timedelta(seconds=5), timedelta(seconds=3), timedelta(milliseconds=50))


def wire(**changes):
    payload = {"rt_cd": "0", "msg_cd": "invented", "msg1": "발명 호가",
               "output1": {"bidp1": "100", "askp1": "101", "bidp_rsqn1": "0",
                           "askp_rsqn1": "7", "aspr_acpt_hour": "091000"}, "output2": {}}
    payload.update(changes)
    return json.dumps(payload, ensure_ascii=False).encode()


def export(raw=None, **changes):
    raw = wire() if raw is None else raw
    values = dict(raw=raw, raw_sha256=hashlib.sha256(raw).hexdigest(), source="KIS",
                  endpoint=connection.ENDPOINT, tr_id=connection.TR_ID, market_division="J",
                  code="005930", channel="KRX", message_id="invented-quote-1", received_at=BASE,
                  received_at_epoch_ms=(BASE - connection.EPOCH) // timedelta(milliseconds=1))
    values.update(changes)
    return connection.ReceivedKisQuoteExport(**values)


def binding(exports=None, journal_changes=None, supplied=None, **changes):
    exports = (export(),) if exports is None else exports
    journal_values = dict(stream=STREAM, policy=POLICY, contract_sha256="a" * 64,
                          source_sha256="b" * 64, runtime_sha256="c" * 64, model_sha256="d" * 64,
                          rule_sha256="e" * 64, max_line_bytes=4096, max_bytes=65536)
    journal_values.update(journal_changes or {})
    values = dict(journal=io.JournalContract(**journal_values), observation_date=date(2026, 1, 2),
                  data_vintage="invented-v1", evidence_level="invented-wire-fixture",
                  exports_sha256=connection.export_batch_sha256(exports),
                  decision_sha256=connection.supplied_decision_sha256(supplied or decision()), max_export_bytes=2048,
                  max_total_export_bytes=8192, max_sidecar_bytes=32768)
    values.update(changes)
    return connection.KisExportBinding(**values)


def decision(**changes):
    values = dict(decision_at=BASE + timedelta(seconds=1), outcome="decline", reason="supplied invented rule")
    values.update(changes)
    return connection.SuppliedDecision(**values)


def sidecar(path):
    return path.with_name(path.name + connection.SIDECAR_SUFFIX)


def connect(path, exports=None, expected=None, supplied=None):
    exports = (export(),) if exports is None else exports
    supplied = supplied or decision()
    expected = binding(exports, supplied=supplied) if expected is None else expected
    return connection.connect_offline_exports(path, expected, exports, supplied)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def damage(path, raw):
    # Damage only a newly created invented fixture, leaving its changed evidence.
    path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(0o400)


@pytest.mark.parametrize("outcome", ["accept", "decline", "unknown"])
def test_original_kis_wire_to_durable_replay_and_no_order_day_report(tmp_path, outcome):
    path = tmp_path / "invented.jsonl"
    report = connect(path, supplied=decision(outcome=outcome))
    replayed = connection.replay_export_journal(path, report.binding)
    assert replayed == report
    assert report.exports == (export(),)
    retained = json.loads(sidecar(path).read_bytes())
    assert base64.b64decode(retained["exports"][0]["raw_base64"]) == export().raw
    assert retained["decision"]["outcome"] == outcome
    assert retained["action"] == report.action == "NO_ORDER"
    assert retained["binding"]["journal"]["model_sha256"] == "d" * 64
    assert report.journal.state.latest.raw_sha256 == hashlib.sha256(export().raw).hexdigest()
    assert report.journal.raw_receipts == (io.receipt_bytes(connection.decode_received_export(export(), report.binding)),)
    assert report.journal.state.latest.bid_quantity.value == Decimal(0)
    assert (report.observed_records, report.unknown_records, report.error_records) == (0, 1, 0)
    assert report.supplied_decision.outcome == outcome
    assert report.assessment.status is core.ObservationStatus.UNKNOWN
    assert report.assessment.frozen is None
    assert {"sequence_unknown", "clock_unknown:source_event_at", "session_unknown"} <= set(report.assessment.reasons)
    assert stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(sidecar(path).stat().st_mode) == 0o400
    with pytest.raises(FrozenInstanceError):
        report.action = "BUY"


def test_no_source_date_sequence_finality_session_or_publication_clock_is_invented():
    payload = json.loads(wire())
    payload["output1"]["aspr_acpt_hour"] = "235959"
    item = export(json.dumps(payload).encode())
    receipt = connection.decode_received_export(item, binding((item,)))
    assert receipt.clocks.observation_date == date(2026, 1, 2)
    assert receipt.clocks.received_at == BASE
    assert receipt.clocks.source_event_at is None
    assert receipt.clocks.available_at is receipt.clocks.first_seen_at is receipt.clocks.retrieved_at is None
    assert receipt.sequence is receipt.is_final is None
    assert receipt.session_state == "unknown"


def test_large_integer_prices_are_exact_and_true_zero_is_not_missing():
    raw = wire(output1={"bidp1": "9007199254740993", "askp1": "9007199254740994",
                        "bidp_rsqn1": "0", "askp_rsqn1": "0007"})
    item = export(raw)
    receipt = connection.decode_received_export(item, binding((item,)))
    assert receipt.bid.value == Decimal("9007199254740993")
    assert receipt.ask.value == Decimal("9007199254740994")
    assert receipt.bid_quantity.status is core.ObservationStatus.OBSERVED
    assert receipt.bid_quantity.value == Decimal(0)


@pytest.mark.parametrize("value,status", [(None, "unknown"), ("", "unknown"), (" ", "unknown"),
                                          (0, "error"), (True, "error"), (1.5, "error"),
                                          ("1.5", "error"), ("NaN", "error"), ("-1", "error")])
def test_missing_and_bad_wire_fields_remain_distinct_from_actual_zero(value, status):
    item = export(wire(output1={"bidp1": value}))
    receipt = connection.decode_received_export(item, binding((item,)))
    assert receipt.bid.status.value == status
    assert receipt.bid.value is None
    assert receipt.ask.status is core.ObservationStatus.UNKNOWN


def test_empty_quote_and_response_failure_are_preserved_with_quality_states(tmp_path):
    empty = export(wire(output1={}))
    failed = export(wire(rt_cd="1", msg_cd="invented-error"), message_id="invented-error")
    report = connect(tmp_path / "error.jsonl", (empty, failed))
    assert report.journal.state.records[0].bid.status is core.ObservationStatus.UNKNOWN
    assert report.journal.state.records[1].bid.reason == "kis_response_error:1"
    assert (report.unknown_records, report.error_records) == (1, 1)
    assert report.assessment.frozen is None


def test_error_response_without_output1_keeps_the_original_error():
    item = export(b'{"rt_cd":"1","msg_cd":"invented"}')
    receipt = connection.decode_received_export(item, binding((item,)))
    assert receipt.bid.status is core.ObservationStatus.ERROR
    assert receipt.ask_quantity.reason == "kis_response_error:1"


def test_duplicates_crossed_quotes_and_receive_staleness_are_visible(tmp_path):
    first = export()
    crossed = export(wire(output1={"bidp1": "102", "askp1": "101", "bidp_rsqn1": "0", "askp_rsqn1": "7"}),
                     message_id="invented-crossed")
    report = connect(tmp_path / "quality.jsonl", (first, first, crossed),
                     supplied=decision(decision_at=BASE + timedelta(seconds=10)))
    assert len(report.exports) == len(report.journal.raw_receipts) == 3
    assert "duplicate_message" in report.journal.state.quality[1].reasons
    assert "crossed_quote" in report.journal.state.quality[2].reasons
    assert "stale_receipt" in report.assessment.reasons
    assert (report.unknown_records, report.error_records) == (2, 1)


@pytest.mark.parametrize("changes", [
    {"source": "other"}, {"endpoint": "/uapi/unsupported"}, {"tr_id": "FHMIF10010000"},
    {"market_division": "UN"}, {"code": "000660"}, {"code": "5930"}, {"channel": "NXT"},
    {"raw_sha256": "0" * 64}, {"message_id": ""}, {"received_at": BASE.replace(tzinfo=None)},
    {"received_at_epoch_ms": True}, {"received_at_epoch_ms": 1},
    {"received_at_epoch_ms": 10 ** 30}, {"source_date": date(2026, 1, 2)},
    {"received_at": BASE + timedelta(days=1),
     "received_at_epoch_ms": (BASE + timedelta(days=1) - connection.EPOCH) // timedelta(milliseconds=1)},
])
def test_bad_late_batch_metadata_is_rejected_before_either_file_is_created(tmp_path, changes):
    later = {"message_id": "invented-later", **changes}
    exports = (export(), export(**later))
    path = tmp_path / "refused.jsonl"
    with pytest.raises(ValueError):
        connect(path, exports)
    assert not path.exists()
    assert not sidecar(path).exists()


@pytest.mark.parametrize("raw", [b'{}', b'[]', b'\xff', b'{"rt_cd":"0","rt_cd":"1","output1":{}}',
                                b'{"rt_cd":"0","output2":{"bidp1":"100"}}',
                                b'{"rt_cd":0,"output1":{}}', b'{"rt_cd":"0","output1":[]}',
                                b'{"rt_cd":"0","output1":{"bidp1":NaN}}',
                                b'{"rt_cd":"0","output1":{},"order":{"side":"BUY"}}',
                                b'{"rt_cd":"0","output1":{"aspr_acpt_hour":"250000"}}',
                                b'{"rt_cd":"0","output1":{"aspr_acpt_hour":91000}}'])
def test_malformed_or_other_export_types_never_create_a_journal(tmp_path, raw):
    path = tmp_path / "malformed.jsonl"
    with pytest.raises(ValueError):
        connect(path, (export(raw),))
    assert not path.exists() and not sidecar(path).exists()


@pytest.mark.parametrize("change", [
    {"exports_sha256": "0" * 64}, {"decision_sha256": "0" * 64},
    {"max_export_bytes": 1}, {"max_total_export_bytes": 1},
    {"max_sidecar_bytes": 1}, {"observation_date": date(2026, 1, 3)},
])
def test_complete_source_binding_and_all_byte_limits_precede_writes(tmp_path, change):
    path = tmp_path / "bounded.jsonl"
    with pytest.raises(ValueError):
        connect(path, expected=binding(**change))
    assert not path.exists() and not sidecar(path).exists()


@pytest.mark.parametrize("journal_changes", [{"max_line_bytes": 10}, {"max_bytes": 100},
                                              {"policy": replace(POLICY, max_records=1)}])
def test_complete_canonical_journal_capacity_precedes_any_write(tmp_path, journal_changes):
    path = tmp_path / "capacity.jsonl"
    exports = (export(), export(message_id="second"))
    with pytest.raises(ValueError):
        connect(path, exports, binding(exports, journal_changes=journal_changes))
    assert not path.exists() and not sidecar(path).exists()


@pytest.mark.parametrize("changes", [{"outcome": "BUY"}, {"reason": ""},
                                     {"decision_at": BASE.replace(tzinfo=None)},
                                     {"decision_at": BASE + timedelta(days=1)}])
def test_external_decision_is_validated_before_any_write(tmp_path, changes):
    path = tmp_path / "decision.jsonl"
    with pytest.raises(ValueError):
        connect(path, supplied=decision(**changes))
    assert not path.exists() and not sidecar(path).exists()


@pytest.mark.parametrize("stream_changes", [{"source": "other"}, {"symbol": "KRX:5930"},
                                            {"channel": "SOR"}, {"price_basis": "adjusted"},
                                            {"timezone": "UTC"}, {"physical_unit": "unknown"}])
def test_source_contract_refuses_aliases_or_different_market_metadata(stream_changes):
    with pytest.raises(ValueError):
        binding(journal_changes={"stream": replace(STREAM, **stream_changes)})


def test_original_sidecar_is_sealed_and_synced_before_canonical_receipt_append(tmp_path, monkeypatch):
    path = tmp_path / "ordering.jsonl"
    append = io.JournalWriter.append
    calls = []
    def observe(writer, raw):
        assert stat.S_IMODE(sidecar(path).stat().st_mode) == 0o400
        assert base64.b64decode(json.loads(sidecar(path).read_bytes())["exports"][0]["raw_base64"]) == export().raw
        calls.append(raw)
        append(writer, raw)
    monkeypatch.setattr(io.JournalWriter, "append", observe)
    report = connect(path)
    assert tuple(calls) == report.journal.raw_receipts


def test_partial_sidecar_write_preserves_both_incomplete_files_and_cannot_replay(tmp_path, monkeypatch):
    path = tmp_path / "partial.jsonl"
    write = io.storage.write_all
    def fail(handle, raw):
        if b'"raw_base64"' in raw:
            write(handle, raw[:23])
            raise OSError("invented partial source write")
        write(handle, raw)
    monkeypatch.setattr(io.storage, "write_all", fail)
    with pytest.raises(OSError, match="partial source"):
        connect(path)
    before = (path.read_bytes(), sidecar(path).read_bytes())
    assert len(before[1]) == 23
    with pytest.raises(ValueError):
        connection.replay_export_journal(path, binding())
    with pytest.raises(FileExistsError):
        connect(path)
    assert (path.read_bytes(), sidecar(path).read_bytes()) == before


def test_receipt_write_failure_preserves_sealed_original_and_unsealed_journal(tmp_path, monkeypatch):
    path = tmp_path / "failed-journal.jsonl"
    def fail(writer, raw):
        raise OSError("invented journal failure")
    monkeypatch.setattr(io.JournalWriter, "append", fail)
    with pytest.raises(OSError, match="journal failure"):
        connect(path)
    original = sidecar(path).read_bytes()
    assert stat.S_IMODE(sidecar(path).stat().st_mode) == 0o400
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="incomplete"):
        connection.replay_export_journal(path, binding())
    assert sidecar(path).read_bytes() == original


@pytest.mark.parametrize("kind", ["missing", "unsealed", "changed", "symlink", "hardlink"])
def test_both_files_require_exclusive_sealed_unchanged_original_evidence(tmp_path, kind):
    path = tmp_path / "raw-link.jsonl"
    report = connect(path)
    original = sidecar(path)
    if kind == "missing":
        original.unlink()
    elif kind == "unsealed":
        original.chmod(0o600)
    elif kind == "changed":
        value = json.loads(original.read_bytes())
        value["decision"]["outcome"] = "BUY"
        damage(original, canonical(value))
    elif kind == "symlink":
        saved = tmp_path / "invented-original.json"
        original.rename(saved)
        original.symlink_to(saved)
    else:
        os.link(original, tmp_path / "invented-second-link.json")
    journal_before = path.read_bytes()
    with pytest.raises((ValueError, OSError)):
        connection.replay_export_journal(path, report.binding)
    assert path.read_bytes() == journal_before


def test_changed_canonical_quote_with_recomputed_journal_digest_does_not_replace_raw_oracle(tmp_path):
    path = tmp_path / "mismatch.jsonl"
    report = connect(path)
    lines = path.read_bytes().splitlines(keepends=True)
    receipt = json.loads(lines[1])
    receipt["receipt"]["ask"]["value"] = "102"
    lines[1] = canonical(receipt) + b"\n"
    footer = json.loads(lines[-1])
    footer["raw_prefix_sha256"] = hashlib.sha256(b"".join(lines[:-1])).hexdigest()
    lines[-1] = canonical(footer) + b"\n"
    damage(path, b"".join(lines))
    assert io.replay_journal(path, report.binding.journal).state.latest.ask.value == Decimal(102)
    with pytest.raises(ValueError, match="canonical journal differ"):
        connection.replay_export_journal(path, report.binding)


def test_changed_original_bytes_cannot_retain_the_frozen_source_hash(tmp_path):
    path = tmp_path / "changed-raw.jsonl"
    report = connect(path)
    value = json.loads(sidecar(path).read_bytes())
    value["exports"][0]["raw_base64"] = base64.b64encode(wire(output1={})).decode()
    damage(sidecar(path), canonical(value))
    with pytest.raises(ValueError, match="source|sidecar"):
        connection.replay_export_journal(path, report.binding)


def test_modified_but_valid_external_decision_cannot_replace_the_frozen_input(tmp_path):
    path = tmp_path / "changed-decision.jsonl"
    report = connect(path)
    value = json.loads(sidecar(path).read_bytes())
    value["decision"]["outcome"] = "accept"
    damage(sidecar(path), canonical(value))
    with pytest.raises(ValueError, match="sidecar"):
        connection.replay_export_journal(path, report.binding)


def test_existing_sidecar_is_never_overwritten_even_when_journal_does_not_exist(tmp_path):
    path = tmp_path / "existing-sidecar.jsonl"
    sidecar(path).write_bytes(b"invented original")
    with pytest.raises(FileExistsError):
        connect(path)
    assert sidecar(path).read_bytes() == b"invented original"
    with pytest.raises(ValueError, match="incomplete"):
        io.replay_journal(path, binding().journal)


def test_import_and_public_connection_surface_have_no_source_or_order_route(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("import performed IO")
    with monkeypatch.context() as patched:
        patched.setattr(os, "open", forbidden)
        patched.setattr(io.storage, "native_cache", forbidden)
        importlib.reload(connection)
    tree = ast.parse(inspect.getsource(connection))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imports <= {"__future__", "base64", "dataclasses", "datetime", "decimal", "hashlib", "json",
                       "os", "pathlib", "re", "stat", "typing", "research"}
    research_names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                      and node.module == "research" for alias in node.names}
    assert research_names == {"intraday_ed_observer", "intraday_ed_observer_io"}
    assert not any(isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                   and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"
                   for node in ast.walk(tree))
    assert "callback" not in inspect.signature(connection.connect_offline_exports).parameters
