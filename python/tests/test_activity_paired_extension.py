"""Full paired spin-off connection uses only fabricated in-memory packages."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path
import socket
import sqlite3

import pytest

from research import activity_paired_replay as paired
from research import activity_preflight as saved
from research import activity_price_parity as prices
from research.activity_exit_bounds import mixed_action_scope, provisional_exit_bounds
from research.activity_holding_restore import restore_holding_inputs
from research.activity_quote_extension import extend_holding_quotes
import test_activity_quote_extension as synthetic_quotes
from test_activity_paired_replay import fixture
from test_activity_reviewed_actions import A, AI, B, BI, reference, window


def extension_envelope(monkeypatch, prepared, requested):
    """Encode the existing pure synthetic scan helper as producer-shaped audit."""
    calendar = prepared.calendar
    scope = dict(schema="activity-holding-price-preflight-v1:read-scope-v1",
        calendar_dates=[day.strftime("%Y%m%d") for day in calendar],
        activity_snapshot_sha256=prepared.activity_snapshot_sha256,
        requested_code_dates={code: [day.isoformat() for day in days] for code, days in sorted(requested.items())})
    scope_pin = saved.digest(saved.encoded(scope))
    with monkeypatch.context() as build:
        build.setattr(synthetic_quotes, "DAYS", calendar)
        build.setattr(synthetic_quotes, "ACTIVITY", prepared.activity_snapshot_sha256)
        extension = synthetic_quotes.component(requested, scope_pin)
    positions = {day: index for index, day in enumerate(calendar)}
    rows = []
    for observation in extension.observations:
        series, index = extension.panel[observation.code], positions[observation.observation_date]
        state = ("absent_requested" if observation.typed_values is None else "unresolved_observed"
            if observation.issues else "frozen" if series.frozen[index] else "locked"
            if series.locked[index] else "observed")
        rows.append(dict(code=observation.code, observation_date=observation.observation_date.isoformat(),
            state=state, issues=list(observation.issues),
            typed_values=None if observation.typed_values is None else [list(pair) for pair in observation.typed_values],
            provenance=saved.strict_json(observation.provenance_json)))
    audit = dict(schema="activity-holding-price-preflight-v1:holding-input-audit-v1",
        read_scope_sha256=scope_pin, activity_snapshot_sha256=extension.activity_snapshot_sha256,
        quote_snapshot_sha256=extension.quote_snapshot_sha256, snapshot_received_at=extension.snapshot_received_at,
        fingerprint=saved.strict_json(extension.fingerprint_json),
        scan_metadata=dict(typed_snapshot_sha256=extension.quote_snapshot_sha256,
            snapshot_received_at=extension.snapshot_received_at, fingerprint_scope=synthetic_quotes._FINGERPRINT_SCOPE,
            whole_database_hash=False, BI_snapshot_equality_checked=False, progress_counts_recomputed_from_prices=False),
        requested_code_dates=scope["requested_code_dates"], rows=rows,
        absent_coordinates=[[code, day.isoformat()] for code, day in extension.absent_coordinates],
        unrequested_coordinates=[[code, day.isoformat()] for code, day in extension.unrequested_coordinates],
        summary=dict(requested_codes=len(requested), requested_code_dates=len(extension.requested_coordinates),
            absent_requested_code_dates=len(extension.absent_coordinates),
            unrequested_code_dates=len(extension.unrequested_coordinates),
            state_counts=dict(sorted(Counter(row["state"] for row in rows).items()))))
    envelope = dict(schema="activity-quote-extension-envelope-v1",
        original_quote_snapshot_sha256=prepared.quote_snapshot_sha256, read_scope=scope, holding_input_audit=audit)
    return envelope, extension


def spin_fixture(monkeypatch, *, coordinate_change=None):
    inputs, parameters, pins, calendar = fixture(monkeypatch)
    prepared = paired.prepare_inputs(*inputs[:2], parameters, {role: pins[role] for role in paired.PACKAGE_ROLES})
    coverage, evidence = (saved.strict_json(payload) for payload in inputs[2:])
    end = coverage["windows"][0]["end"]
    ending = calendar.index(date.fromisoformat(end))
    requested = {B: calendar[66:ending + 1]}
    if coordinate_change == "missing": requested[B] = requested[B][1:]
    if coordinate_change == "extra": requested[B] = (calendar[65], *requested[B])
    envelope, extension = extension_envelope(monkeypatch, prepared, requested)
    composite = extend_holding_quotes(prepared.holding_inputs, extension)
    old, effective, available = (calendar[index].isoformat() for index in (64, 65, 66))
    event_id = "synthetic-spin"
    coverage["quote_snapshot_sha256"] = composite.quote_snapshot_sha256
    coverage["windows"][0].update(review_state="reviewed_supported_events", event_ids=[event_id])
    coverage["windows"].append(window(B, BI, effective, end, ref="new-complete"))
    coverage["events"] = [dict(event_id=event_id, kind="compulsory_spin_off", status="verified_effective",
        code=A, isin=AI, successor_code=B, successor_isin=BI, retained_ratio="0.8", new_ratio="0.2",
        old_basis_id="old", retained_basis_id="retained", new_basis_id="new",
        last_eligible_entry_on=old, record_on=old, effective_on=effective,
        retained_available_on=available, new_available_on=available, retained_carry_weight="0.8",
        carry_evidence_level="assumed", carry_evidence_ref="carry", evidence_ref="event")]
    refs = evidence["references"]
    refs[A].update(finding="inferred_supported_events_complete", event_ids=[event_id])
    refs["new-complete"] = reference(B, BI, effective, end, "inferred_no_compulsory_action")
    refs["event"] = reference(A, AI, old, effective, "verified_compulsory_spin_off", (event_id,))
    refs["carry"] = reference(A, AI, effective, effective, "registered_spin_off_carry_assumed", (event_id,))
    for name, code, isin, day, role in (("old", A, AI, old, "last_observable_pre_event"),
            ("retained", A, AI, available, "retained_availability"),
            ("new", B, BI, available, "successor_availability")):
        refs[name] = reference(code, isin, day, day, "verified_" + role + "_basis")
        evidence["bases"][name] = dict(code=code, isin=isin, session=day, role=role,
            raw_close="100", adjusted_close="100", quote_snapshot_sha256=composite.quote_snapshot_sha256,
            evidence_ref=name)
    # Reviews may follow the synthetic evaluation horizon; retain exact bounds.
    for ref in refs.values(): ref["reviewed_on"] = end
    inputs[2:] = [saved.encoded(coverage), saved.encoded(evidence)]
    pins.update(reviewed_action_coverage=saved.digest(inputs[2]), basis_evidence=saved.digest(inputs[3]),
                quote_extension=saved.digest(saved.encoded(envelope)))
    return inputs, parameters, pins, calendar, envelope, composite


def run(inputs, parameters, pins, envelope):
    return paired.paired_replay(*inputs, parameters=parameters, expected_pins=pins,
                               quote_extension=saved.encoded(envelope))


def test_full_paired_spin_off_preserves_original_proofs_and_propagates_successor_lots_without_io(monkeypatch):
    inputs, parameters, pins, calendar, envelope, composite = spin_fixture(monkeypatch)
    before = deepcopy((inputs, parameters, pins, envelope))
    original_fingerprint = composite.original.fingerprint_json
    def no_io(*args, **kwargs): pytest.fail("paired extension attempted IO or a new quote scan")
    for owner, name in ((Path, "open"), (sqlite3, "connect"), (socket, "create_connection"), (prices, "load_scan")):
        monkeypatch.setattr(owner, name, no_io)
    monkeypatch.setattr("builtins.open", no_io)
    calls, replays, replay = [], [], paired.replay_synthetic
    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        result = replay(*args, **kwargs)
        replays.append(result)
        return result
    monkeypatch.setattr(paired, "replay_synthetic", capture)
    value = run(inputs, parameters, pins, envelope)
    saved.encoded(value)
    assert (inputs, parameters, pins, envelope) == before
    assert composite.original.fingerprint_json == original_fingerprint
    assert len(calls) == 2 and calls[0][1]["events"] is calls[1][1]["events"]
    assert calls[0][1]["initial_book"] is calls[1][1]["initial_book"]
    assert all(call[1]["dataset_sha256"] == composite.quote_snapshot_sha256 for call in calls)
    assert all(call[0][1][A] == composite.original.panel[A] for call in calls)
    assert value["original_quote_snapshot_sha256"] == composite.original.quote_snapshot_sha256
    assert value["quote_snapshot_sha256"] == composite.quote_snapshot_sha256
    assert value["quote_lineage"] == saved.strict_json(composite.fingerprint_json)
    assert value["required_code_dates"] == saved.strict_json(inputs[1]["files"]["read-scope.json"])["requested_code_dates"]
    assert len(value["required_code_dates"][A]) == 69
    assert value["selections"]["D1"][0]["source_population_count"] == 6
    assert value["reviewed_action_evidence"]["references"][A]["finding"] == "inferred_supported_events_complete"
    assert value["actual_study_completed"] is value["promotion_allowed"] is value["event_coverage_certified"] is False
    for lag, result in enumerate(replays, 1):
        by_day = {book.as_of: book for book in result.books}
        parent = by_day[calendar[64]].lots[0]
        pending, delivered = by_day[calendar[65]], by_day[calendar[66]]
        assert pending.applied_event_ids == ("synthetic-spin",) and pending.slot_count == 1
        assert tuple(lot.code for lot in pending.lots) == (A, B)
        retained, successor = pending.lots
        assert retained.shares == parent.shares * D("0.8") and successor.shares == parent.shares * D("0.2")
        assert retained.investment_key == successor.investment_key
        assert successor.lot_id != parent.lot_id and successor.dataset_sha256 == composite.quote_snapshot_sha256
        assert all(lot.entered_on == calendar[60 + lag] and lot.due_on == calendar[90 + lag]
                   and lot.available_on == calendar[66] for lot in pending.lots)
        assert all(lot.carried_value is not None for lot in pending.lots)
        assert all(lot.carried_value is None for lot in delivered.lots)
        assert result.closed_trades == 2 and by_day[calendar[90 + lag]].lots == ()


@pytest.mark.parametrize("mutation,message", [
    ("original", "quote extension original snapshot or schema mismatch"),
    ("scope", "holding restoration identifiers mismatch"),
    ("audit", "holding audit row order/state/issues mismatch"),
    ("missing", "extension must exactly supply"), ("extra", "extension must exactly supply"),
    ("basis_anchor", "extension must exactly supply"),
    ("basis_price", "event adjusted basis must match"),
    ("coverage_old_hash", "inconsistent scope or unresolved declaration"),
    ("basis_old_hash", "inconsistent basis identity, session or quote snapshot"),
    ("envelope_pin", "registered quote extension byte pin mismatch"),
])
def test_repinned_invalid_extension_or_evidence_is_refused_before_either_replay(monkeypatch, mutation, message):
    inputs, parameters, pins, calendar, envelope, composite = spin_fixture(monkeypatch,
        coordinate_change=mutation if mutation in ("missing", "extra") else None)
    coverage, evidence = (saved.strict_json(payload) for payload in inputs[2:])
    if mutation == "original": envelope["original_quote_snapshot_sha256"] = "f" * 64
    elif mutation == "scope": envelope["read_scope"]["calendar_dates"].pop()
    elif mutation == "audit": envelope["holding_input_audit"]["rows"][0]["state"] = "absent_requested"
    elif mutation == "basis_anchor":
        missing = calendar[60].isoformat()
        evidence["bases"]["old"]["session"] = missing
        evidence["references"]["old"].update(start=missing, end=missing)
    elif mutation == "basis_price": evidence["bases"]["new"]["adjusted_close"] = "101"
    elif mutation == "coverage_old_hash": coverage["quote_snapshot_sha256"] = composite.original.quote_snapshot_sha256
    elif mutation == "basis_old_hash":
        evidence["bases"]["retained"]["quote_snapshot_sha256"] = composite.original.quote_snapshot_sha256
    inputs[2:] = [saved.encoded(coverage), saved.encoded(evidence)]
    pins.update(reviewed_action_coverage=saved.digest(inputs[2]), basis_evidence=saved.digest(inputs[3]),
                quote_extension="f" * 64 if mutation == "envelope_pin" else saved.digest(saved.encoded(envelope)))
    monkeypatch.setattr(paired, "replay_synthetic", lambda *args, **kwargs: pytest.fail("invalid inputs reached replay"))
    with pytest.raises(ValueError, match=message): run(inputs, parameters, pins, envelope)


@pytest.mark.parametrize("basis_mismatch", [False, True])
def test_validated_original_already_containing_successor_needs_no_extension(monkeypatch, basis_mismatch):
    """Exercise the post-package boundary with both issues in one typed scan."""
    inputs, parameters, pins, calendar, envelope, _ = spin_fixture(monkeypatch)
    prepared = paired.prepare_inputs(*inputs[:2], parameters, {role: pins[role] for role in paired.PACKAGE_ROLES})
    requested = {A: tuple(day for code, day in prepared.holding_inputs.requested_coordinates if code == A),
                 B: tuple(date.fromisoformat(day) for day in envelope["read_scope"]["requested_code_dates"][B])}
    _, full_original = extension_envelope(monkeypatch, prepared, requested)
    prepared = replace(prepared, holding_inputs=full_original, quote_snapshot_sha256=full_original.quote_snapshot_sha256,
        read_scope_sha256=full_original.read_scope_sha256,
        required_code_dates={code: [day.isoformat() for day in days] for code, days in requested.items()},
        expected_windows=(*prepared.expected_windows, dict(code=B, isin=BI,
            start=requested[B][0].isoformat(), end=requested[B][-1].isoformat())))
    monkeypatch.setattr(paired, "prepare_inputs", lambda *args, **kwargs: prepared)
    coverage, evidence = (saved.strict_json(payload) for payload in inputs[2:])
    coverage.update(quote_snapshot_sha256=full_original.quote_snapshot_sha256,
                    required_scope_sha256=full_original.read_scope_sha256)
    for basis in evidence["bases"].values(): basis["quote_snapshot_sha256"] = full_original.quote_snapshot_sha256
    if basis_mismatch: evidence["bases"]["old"]["adjusted_close"] = "101"
    inputs[2:] = [saved.encoded(coverage), saved.encoded(evidence)]
    pins.update(reviewed_action_coverage=saved.digest(inputs[2]), basis_evidence=saved.digest(inputs[3]))
    pins.pop("quote_extension")
    calls, replay = [], paired.replay_synthetic
    def capture(*args, **kwargs):
        assert not basis_mismatch, "original basis mismatch reached replay"
        calls.append(kwargs)
        return replay(*args, **kwargs)
    monkeypatch.setattr(paired, "replay_synthetic", capture)
    if basis_mismatch:
        with pytest.raises(ValueError, match="event adjusted basis must match"):
            paired.paired_replay(*inputs, parameters=parameters, expected_pins=pins)
        assert calls == []
    else:
        value = paired.paired_replay(*inputs, parameters=parameters, expected_pins=pins)
        assert len(calls) == 2 and value["quote_lineage"] is None
        assert value["quote_snapshot_sha256"] == value["original_quote_snapshot_sha256"] == full_original.quote_snapshot_sha256
        assert all(call["dataset_sha256"] == full_original.quote_snapshot_sha256 for call in calls)
        assert all(result["closed_trades"] == 2 for result in value["replays"].values())


def test_post_package_mixed_ordinary_child_and_spin_off_extension_replay_together(monkeypatch):
    """Stub package preparation only; recompute real child proof and both books."""
    inputs, parameters, pins, calendar, envelope, _ = spin_fixture(monkeypatch)
    parameters = dict(parameters, slots=2)  # Both synthetic original signals must actually enter.
    prepared = paired.prepare_inputs(*inputs[:2], dict(parameters, slots=1),
        {role: pins[role] for role in paired.PACKAGE_ROLES})
    c, ci = "000003", "KR7000003000"
    days = tuple(day for code, day in prepared.holding_inputs.requested_coordinates if code == A)
    _, original = extension_envelope(monkeypatch, prepared, {A: days, c: days})
    selections = [tuple(replace(row, eligible_codes=tuple(sorted((*row.eligible_codes, c))))
        if index == 0 else row for index, row in enumerate(arm))
        for arm in (prepared.selections_d1, prepared.selections_d2)]
    required = {code: [day.isoformat() for day in days] for code in (A, c)}
    prepared = replace(prepared, parameters=parameters, holding_inputs=original,
        quote_snapshot_sha256=original.quote_snapshot_sha256, read_scope_sha256=original.read_scope_sha256,
        selections_d1=selections[0], selections_d2=selections[1], required_code_dates=required,
        expected_windows=(*prepared.expected_windows, dict(code=c, isin=ci, start=days[0].isoformat(), end=days[-1].isoformat())))
    monkeypatch.setattr(paired, "prepare_inputs", lambda *args, **kwargs: prepared)
    proof = provisional_exit_bounds(*selections, original, parameters, required, {A: AI, c: ci},
                                   input_sha256=prepared.input_sha256)
    audit, scope = envelope["holding_input_audit"], envelope["read_scope"]
    extension = restore_holding_inputs(audit, scope, quote_snapshot_sha256=audit["quote_snapshot_sha256"],
        activity_snapshot_sha256=original.activity_snapshot_sha256,
        read_scope_sha256=saved.digest(saved.encoded(scope)))
    composite = extend_holding_quotes(original, extension)
    envelope["original_quote_snapshot_sha256"] = original.quote_snapshot_sha256
    coverage, evidence = (saved.strict_json(payload) for payload in inputs[2:])
    mixed = mixed_action_scope(proof, coverage)
    ordinary = next(row for row in mixed["windows"] if row["code"] == c)
    coverage["windows"].append(window(c, ci, ordinary["start"], ordinary["end"], ref="ordinary"))
    evidence["references"]["ordinary"] = reference(c, ci, ordinary["start"], ordinary["end"],
                                                   "inferred_no_compulsory_action")
    evidence["references"]["ordinary"]["reviewed_on"] = days[-1].isoformat()
    coverage.update(quote_snapshot_sha256=composite.quote_snapshot_sha256,
                    required_scope_sha256=mixed["action_scope_sha256"])
    for basis in evidence["bases"].values(): basis["quote_snapshot_sha256"] = composite.quote_snapshot_sha256
    inputs[2:] = [saved.encoded(coverage), saved.encoded(evidence)]
    pins.update(reviewed_action_coverage=saved.digest(inputs[2]), basis_evidence=saved.digest(inputs[3]),
        quote_extension=saved.digest(saved.encoded(envelope)), conditional_exit_scope=saved.digest(saved.encoded(proof)))
    before = deepcopy((inputs, parameters, pins, envelope, original))
    value = paired.paired_replay(*inputs, parameters=parameters, expected_pins=pins,
        conditional_exit_scope=saved.encoded(proof), quote_extension=saved.encoded(envelope))
    assert (inputs, parameters, pins, envelope, original) == before
    assert value["conditional_scope_used"] is True and value["conditional_action_scope"] == mixed
    assert mixed["ordinary_codes"] == [c] and mixed["original_end_codes"] == [A]
    assert {row["action_mode"] for row in mixed["entries"] if row["code"] == c} == {"ordinary_child"}
    assert all(row["action_end_on"] == days[-1].isoformat() for row in mixed["entries"] if row["code"] == A)
    assert value["quote_snapshot_sha256"] == composite.quote_snapshot_sha256
    for lag, arm in enumerate(("D1", "D2"), 1):
        result = value["replays"][arm]
        books = {book["as_of"]: book for book in result["books"]}
        split = books[calendar[65].isoformat()]
        assert {lot["code"] for lot in split["lots"]} == {A, B, c}
        assert len({lot["investment_id"] or lot["lot_id"] for lot in split["lots"]}) == 2
        assert result["closed_trades"] == 3 and books[calendar[90 + lag].isoformat()]["lots"] == []
    assert value["actual_study_completed"] is False
