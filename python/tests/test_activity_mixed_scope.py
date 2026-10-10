"""Mixed ordinary/event lifetimes over fabricated, immutable quote inputs."""
from dataclasses import replace
from datetime import timedelta

import pytest

from research import activity_paired_replay as paired
from research import activity_preflight as saved
from research.activity_exit_bounds import mixed_action_scope, provisional_exit_bounds, verify_ordinary_inventory
from research.activity_replay import replay_synthetic
from research.activity_timing import SessionLagPolicy
from test_activity_exit_bounds import A, B, ISINS, PINS, fixture, plan


def test_one_event_keeps_only_its_issue_at_original_end_and_preserves_all_entries():
    case = fixture(signals={2: (A, B), 128: (A,), 254: (A, B)})
    original = plan(case)
    coverage = dict(windows=[dict(code=A, review_state="reviewed_no_event"),
                             dict(code=B, review_state="reviewed_supported_events")],
                    events=[dict(code=B)], unresolved=[])
    scope = mixed_action_scope(original, coverage)
    assert scope["ordinary_codes"] == [A]
    assert scope["original_end_codes"] == [B]
    assert len(scope["entries"]) == len(original["entries"]) == 10
    assert scope["action_scope_sha256"] not in (original["child_scope_sha256"],
                                                original["original_read_scope_sha256"])
    for row in scope["entries"]:
        assert row["action_end_on"] == (row["end_on"] if row["code"] == A else row["original_end_on"])
    assert scope["quote_snapshot_sha256"] == case[2].quote_snapshot_sha256
    assert all(row["end"] == case[3]["end"] for row in scope["windows"] if row["code"] == B)


@pytest.mark.parametrize("fallback", ["event", "supported_window", "unresolved", "successor"])
def test_event_successor_or_unknown_declaration_cannot_shorten_same_code_reentries(fallback):
    original = plan(fixture())
    coverage = dict(windows=[], events=[], unresolved=[])
    if fallback == "event": coverage["events"] = [dict(code=A)]
    elif fallback == "supported_window": coverage["windows"] = [dict(code=A, review_state="unknown")]
    elif fallback == "unresolved": coverage["unresolved"] = [dict(code=A)]
    else: coverage["events"] = [dict(code=B, successor_code=A)]
    scope = mixed_action_scope(original, coverage)
    assert scope["ordinary_codes"] == []
    assert scope["action_scope_sha256"] == original["original_read_scope_sha256"]
    assert all(row["action_end_on"] == row["original_end_on"] for row in scope["entries"])


def connected_case(monkeypatch):
    changes = {(B, index): "absent" for index in range(11, 421)}
    d1, d2, quotes, params, required, identities = fixture(changes=changes, signals={2: (A, B)})
    pins = {role: "c" * 64 for role in paired.PACKAGE_ROLES}
    proof = provisional_exit_bounds(d1, d2, quotes, params, required, identities, input_sha256=pins)
    prepared = paired.PreparedInputs(quotes.calendar, params, quotes.activity_snapshot_sha256,
        quotes.quote_snapshot_sha256, quotes.read_scope_sha256, d1, d2, required, quotes,
        tuple(dict(code=row["code"], isin=row["isin"], start=row["entry_on"], end=row["original_end_on"])
              for row in proof["entries"]), pins)
    monkeypatch.setattr(paired, "prepare_inputs", lambda *a, **kw: prepared)
    last, record = quotes.calendar[10:12]
    paid = quotes.calendar[-1] + timedelta(days=30)
    coverage = dict(schema="activity-reviewed-actions-v1", status="reviewed_complete",
        activity_snapshot_sha256=quotes.activity_snapshot_sha256, quote_snapshot_sha256=quotes.quote_snapshot_sha256,
        required_scope_sha256="a" * 64, calendar_sha256=proof["calendar_sha256"],
        windows=[], events=[dict(code=B, isin=ISINS[B], event_id="pending", kind="final_cash_payment",
            status="verified_paid", last_trading_on=last.isoformat(), record_on=record.isoformat(),
            paid_on=paid.isoformat(), net_cash_per_raw_share="25", basis_id="last", evidence_ref="payment")],
        noops=[], unresolved=[])
    scope = mixed_action_scope(proof, coverage)
    coverage["required_scope_sha256"] = scope["action_scope_sha256"]
    refs = {}
    for index, row in enumerate(scope["windows"]):
        event_ids = ["pending"] if row["code"] == B else []
        ref = str(index)
        coverage["windows"].append(dict(row, review_state="reviewed_supported_events" if event_ids
                                        else "reviewed_no_event", event_ids=event_ids, evidence_refs=[ref]))
        refs[ref] = dict(row, raw_sha256="d" * 64, review_sha256="e" * 64,
            coordinate="synthetic-interval", reviewed_on=params["end"], event_ids=event_ids,
            finding="inferred_supported_events_complete" if event_ids else "inferred_no_compulsory_action")
    template = next(row for row in refs.values() if row["code"] == B)
    refs["payment"] = dict(template, start=paid.isoformat(), end=paid.isoformat(), reviewed_on=paid.isoformat(),
                            finding="verified_final_cash_payment")
    refs["last"] = dict(template, start=last.isoformat(), end=last.isoformat(), event_ids=[],
                         finding="verified_last_trading_basis")
    evidence = dict(references=refs, bases={"last": dict(code=B, isin=ISINS[B], session=last.isoformat(),
        role="last_trading", raw_close="101", adjusted_close="101", quote_snapshot_sha256=quotes.quote_snapshot_sha256,
        evidence_ref="last")})
    raw = saved.encoded(coverage), saved.encoded(evidence), saved.encoded(proof)
    pins = dict(pins, reviewed_action_coverage=saved.digest(raw[0]), basis_evidence=saved.digest(raw[1]),
                conditional_exit_scope=saved.digest(raw[2]))
    return raw, params, pins, scope


def test_connected_mixed_path_keeps_pending_slot_without_expanding_unrelated_ordinary_evidence(monkeypatch):
    raw, params, pins, scope = connected_case(monkeypatch)
    calls, replay = [], paired.replay_synthetic

    def execute(*args, **kwargs):
        calls.append(kwargs["dataset_sha256"])
        return replay(*args, **kwargs)

    monkeypatch.setattr(paired, "replay_synthetic", execute)
    result = paired.paired_replay({}, {}, *raw[:2], parameters=params, expected_pins=pins,
                                  conditional_exit_scope=raw[2])
    assert result["conditional_scope_used"] is True
    assert result["conditional_action_scope"] == scope
    assert len(calls) == 2 and calls[0] == calls[1] == result["quote_snapshot_sha256"]
    assert all(result["report"]["arms"][arm]["terminal_open_lots"] == 1 for arm in ("D1", "D2"))
    assert result["actual_study_completed"] is False


def test_result_invariant_rejects_surviving_ordinary_lot_or_unregistered_delayed_entry():
    d1, d2, quotes, params, required, identities = fixture(signals={2: (A,)})
    proof = provisional_exit_bounds(d1, d2, quotes, params, required, identities, input_sha256=PINS)
    scope = mixed_action_scope(proof, dict(windows=[], events=[], unresolved=[]))
    replay = replay_synthetic(quotes.calendar, quotes.panel, params, d1,
        dataset_sha256=quotes.quote_snapshot_sha256, timing_policy=SessionLagPolicy(1, paired=True))
    verify_ordinary_inventory({"D1": replay}, scope)
    early = next(book for book in replay.books if book.lots)
    lot = early.lots[0]
    candidate = quotes.calendar[3 + params["holding_sessions"]]
    surviving = replace(early, as_of=candidate)
    with pytest.raises(ValueError, match="survives"):
        verify_ordinary_inventory({"D1": replace(replay, books=(surviving,))}, scope)
    delayed = replace(lot, entered_on=lot.entered_on + timedelta(days=1),
                      mark_date=lot.mark_date + timedelta(days=1), available_on=lot.available_on + timedelta(days=1))
    with pytest.raises(ValueError, match="exact entry/due"):
        verify_ordinary_inventory({"D1": replace(replay, books=(replace(surviving, lots=(delayed,)),))}, scope)


def test_inventory_violation_stops_paired_report(monkeypatch):
    raw, params, pins, _ = connected_case(monkeypatch)
    monkeypatch.setattr(paired, "verify_ordinary_inventory", lambda *a: (_ for _ in ()).throw(ValueError("inventory")))
    monkeypatch.setattr(paired, "paired_timing_report", lambda *a, **kw: pytest.fail("invalid inventory reached report"))
    with pytest.raises(ValueError, match="inventory"):
        paired.paired_replay({}, {}, *raw[:2], parameters=params, expected_pins=pins, conditional_exit_scope=raw[2])
