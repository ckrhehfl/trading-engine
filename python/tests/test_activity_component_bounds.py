"""Fabricated quotes and linked declarations only; no actual evidence or IO."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from research import activity_component_bounds as bounds
from research.activity_accounting import Lot
from research.activity_book import ActivityBook
from research.activity_preflight import digest, encoded
from research.activity_quote_extension import extend_holding_quotes
from research.activity_replay import replay_synthetic
from research.activity_reviewed_actions import reviewed_action_terms, reviewed_actions
from research.activity_timing import SessionLagPolicy
from test_activity_exit_bounds import fixture, plan as ordinary_plan, PINS, A, B, ISINS, ACTIVITY
import test_activity_quote_extension as extension_fixture

C, CI = "000003", "KR7000003000"


def declarations(original_case, *, retained=50, new=150, effective=42, cutoff=40, record=41,
                 old=41, successor=C):
    original, calendar = original_case[2], original_case[2].calendar
    plan = ordinary_plan(original_case)
    raw = dict(event_id="split", kind="compulsory_spin_off", status="verified_effective", code=A, isin=ISINS[A],
        successor_code=successor, successor_isin=ISINS.get(successor, CI), evidence_ref="event",
        old_basis_id="old", retained_basis_id="retained", new_basis_id="new", retained_ratio="0.6", new_ratio="0.4",
        retained_carry_weight="0.7", carry_evidence_ref="carry", carry_evidence_level="assumed",
        last_eligible_entry_on=str(calendar[cutoff]), record_on=str(calendar[record]), effective_on=str(calendar[effective]),
        retained_available_on=str(calendar[retained]), new_available_on=str(calendar[new]))
    coverage = dict(schema="activity-reviewed-actions-v1", status="reviewed_complete",
        activity_snapshot_sha256=ACTIVITY, quote_snapshot_sha256="e" * 64,
        required_scope_sha256=original.read_scope_sha256, calendar_sha256=plan["calendar_sha256"],
        windows=[], events=[raw], noops=[], unresolved=[])
    evidence = dict(references={}, bases={})

    def reference(name, code, isin, start, end, finding, ids=()):
        evidence["references"][name] = dict(raw_sha256="c" * 64, review_sha256="d" * 64,
            coordinate="synthetic:section-1", code=code, isin=isin, start=str(start), end=str(end),
            reviewed_on="2099-01-01", finding=finding, event_ids=list(ids))

    reference("event", A, ISINS[A], calendar[cutoff], calendar[effective], "verified_compulsory_spin_off", ("split",))
    reference("carry", A, ISINS[A], calendar[effective], calendar[effective], "registered_spin_off_carry_assumed", ("split",))
    for name, code, index, role in (("old", A, old, "last_observable_pre_event"),
            ("retained", A, retained, "retained_availability"), ("new", successor, new, "successor_availability")):
        isin = ISINS.get(code, CI)
        evidence["bases"][name] = dict(code=code, isin=isin, session=str(calendar[index]), role=role,
            raw_close="101", adjusted_close="101", quote_snapshot_sha256="e" * 64, evidence_ref=name)
        reference(name, code, isin, calendar[index], calendar[index], "verified_" + role + "_basis")
    windows = tuple(dict(code=row["code"], isin=row["isin"], start=row["entry_on"], end=row["original_end_on"])
                    for row in plan["entries"])
    return coverage, evidence, windows


def terms_for(coverage, evidence, windows, quotes):
    coverage["quote_snapshot_sha256"] = quotes.quote_snapshot_sha256
    for row in evidence["bases"].values():
        row["quote_snapshot_sha256"] = quotes.quote_snapshot_sha256
    return reviewed_action_terms(coverage, evidence, expected_windows=windows, calendar=quotes.calendar,
        activity_snapshot_sha256=quotes.activity_snapshot_sha256, quote_snapshot_sha256=quotes.quote_snapshot_sha256)


def case(monkeypatch, *, changes=None, original_changes=None, extra=False, missing=False, successor=C,
         signals=None, **event_args):
    original_case = fixture(changes=original_changes, signals=signals or {2: (A, B), 128: (A,), 254: (A,)})
    original, calendar = original_case[2], original_case[2].calendar
    plan = ordinary_plan(original_case)
    coverage, evidence, windows = declarations(original_case, successor=successor, **event_args)
    # The coordinate projection does not depend on the eventual composite SHA.
    provisional = reviewed_action_terms(coverage, evidence, expected_windows=windows, calendar=calendar,
        activity_snapshot_sha256=ACTIVITY, quote_snapshot_sha256="e" * 64)
    coords = {(code, date.fromisoformat(day)) for code, days in provisional.successor_required for day in days}
    coords.update((row["code"], date.fromisoformat(row["session"])) for row in evidence["bases"].values())
    coords.difference_update(original.requested_coordinates)
    if extra:
        coords.add((C, calendar[149]))
    if missing:
        coords.remove((C, calendar[160]))
    quotes = original
    if coords:
        scope = {}
        for code, day in sorted(coords):
            scope.setdefault(code, []).append(day)
        monkeypatch.setattr(extension_fixture, "DAYS", calendar)
        monkeypatch.setattr(extension_fixture, "ACTIVITY", ACTIVITY)
        raw = {(code, day): (day.strftime("%Y%m%d"), "100", "102", "99", "101", "100", "10") for code, day in coords}
        for index, state in (changes or {}).items():
            day = calendar[index]
            raw[C, day] = (None if state == "absent" else
                (day.strftime("%Y%m%d"), "100", "100", "100", "100", "0", "0" if state == "frozen" else "10")
                if state in ("frozen", "locked") else
                (day.strftime("%Y%m%d"), None, "102", "99", "101", "100", "10"))
        extension = extension_fixture.component(scope, "f" * 64, changes=raw)
        quotes = extend_holding_quotes(original, extension)
    terms = terms_for(coverage, evidence, windows, quotes)
    return (plan, *original_case), dict(input_sha256=PINS, quotes=quotes, terms=terms), (coverage, evidence)


def build(values):
    return bounds.component_exit_bounds(*values[0], **values[1])


def first(proof, *, arm="D1", code=A):
    return next(row for row in proof["entries"] if row["origin"]["arm"] == arm and row["origin"]["code"] == code)


def final_coverage(values, proof):
    coverage, evidence = deepcopy(values[2])
    coverage.update(windows=[], required_scope_sha256=proof["component_scope_sha256"])
    for index, window in enumerate(proof["windows"]):
        ids = [row["event_id"] for row in coverage["events"] if row["code"] == window["code"]
               and row.get("last_eligible_entry_on", row.get("last_trading_on", row.get("effective_on"))) <= window["end"]
               and window["start"] <= row.get("effective_on", row.get("paid_on"))]
        ref = "complete-" + str(index)
        finding = "inferred_supported_events_complete" if ids else "inferred_no_compulsory_action"
        evidence["references"][ref] = dict(window, raw_sha256="5" * 64, review_sha256="6" * 64,
            coordinate="synthetic:complete-interval", reviewed_on="2099-01-01", finding=finding, event_ids=ids)
        coverage["windows"].append(dict(window, review_state="reviewed_supported_events" if ids else "reviewed_no_event",
                                        event_ids=ids, evidence_refs=[ref]))
    return coverage, evidence


def test_every_origin_group_union_and_full_quote_union_are_independent(monkeypatch):
    values = case(monkeypatch)
    proof, calendar = build(values), values[1]["quotes"].calendar
    row = first(proof)
    assert len(proof["entries"]) == len({row["origin_sha256"] for row in proof["entries"]}) == 8
    assert row["mode"] == "component_candidate" and row["end_on"] == str(calendar[150])
    assert [part["candidate_on"] for part in row["components"]] == [str(calendar[129]), str(calendar[150])]
    assert next(row for row in proof["windows"] if row["code"] == C) == dict(code=C, isin=CI,
        start=str(calendar[42]), end=str(calendar[150]))
    later = [row for row in proof["entries"] if row["origin"]["code"] == A and row["origin"]["entry_on"] >= str(calendar[129])]
    assert all(row["mode"] == "ordinary_child" and row["event_id"] is None for row in later)
    assert max(day for code, day in values[1]["quotes"].extension.requested_coordinates if code == C) == calendar[420]
    coverage, evidence = final_coverage(values, proof)
    events, short_quotes = reviewed_actions(coverage, evidence, expected_windows=tuple(proof["windows"]),
        calendar=calendar, activity_snapshot_sha256=ACTIVITY, quote_snapshot_sha256=proof["quote_snapshot_sha256"],
        read_scope_sha256=proof["component_scope_sha256"], spin_off_windows=tuple(proof["spin_off_windows"]))
    assert events == values[1]["terms"].events
    assert max(short_quotes[C]) == str(calendar[150])
    assert max(dict(values[1]["terms"].successor_required)[C]) == str(calendar[420])
    assert all(proof[key] is False for key in ("books_computed", "returns_computed", "action_coverage_certified", "actual_exits_certified"))


@pytest.mark.parametrize("state", ["absent", "frozen", "locked", "unresolved"])
def test_requested_component_states_and_unresolved_fallback(monkeypatch, state):
    values = case(monkeypatch, new=50, changes={129: state})
    row, calendar = first(build(values)), values[1]["quotes"].calendar
    if state == "unresolved":
        assert row["mode"] == "original_end" and row["end_on"] == str(calendar[420])
    else:
        assert row["end_on"] == str(calendar[129 if state == "locked" else 130])


def test_later_parent_availability_and_pending_partial_inventory(monkeypatch):
    values = case(monkeypatch, retained=170, new=50)
    row, calendar = first(build(values)), values[1]["quotes"].calendar
    assert [part["candidate_on"] for part in row["components"]] == [str(calendar[170]), str(calendar[129])]
    pending = case(monkeypatch, new=421)
    proof = build(pending)
    assert first(proof)["mode"] == "original_end" and first(proof)["components"][1]["candidate_on"] is None
    assert (C, calendar[421]) in pending[1]["quotes"].requested_coordinates
    replays = replay(pending)
    bounds.verify_component_inventory(replays, proof, pending[1]["terms"].events)
    assert [lot.code for lot in replays["D1"].books[-1].lots] == [C]


@pytest.mark.parametrize("change", ["endpoint", "origin", "component", "union", "identity", "terms"])
def test_rehashed_changed_or_missing_proof_fields_fail(monkeypatch, change):
    values = case(monkeypatch)
    proof = build(values)
    if change == "endpoint":
        proof["entries"][0]["end_on"] = proof["entries"][0]["origin"]["entry_on"]
    elif change == "origin":
        proof["entries"].pop()
    elif change == "component":
        proof["entries"][0]["components"].pop()
    elif change == "union":
        proof["windows"].pop()
    elif change == "identity":
        proof["entries"][0]["origin"]["isin"] = "KR7999999000"
    else:
        proof["terms_sha256"] = "0" * 64
    proof["component_scope_sha256"] = digest(encoded({key: value for key, value in proof.items() if key != "component_scope_sha256"}))
    with pytest.raises(ValueError, match="differs"):
        bounds.validate_component_exit_bounds(proof, *values[0], **values[1])


@pytest.mark.parametrize("change", ["extra", "missing", "snapshot", "decoded", "required"])
def test_full_quote_exact_union_and_forged_terms_refused(monkeypatch, change):
    values = case(monkeypatch, extra=change == "extra", missing=change == "missing")
    if change == "snapshot":
        values[1]["quotes"] = replace(values[1]["quotes"], quote_snapshot_sha256="0" * 64)
    elif change == "decoded":
        terms = values[1]["terms"]
        values[1]["terms"] = replace(terms, events=(replace(terms.events[0], new_ratio=Decimal("0.5")),))
    elif change == "required":
        values[1]["terms"] = replace(values[1]["terms"], successor_required=())
    with pytest.raises(ValueError):
        build(values)


def test_unresolved_priority_interval_keeps_full_end_without_source_claim(monkeypatch):
    values = case(monkeypatch)
    calendar = values[1]["quotes"].calendar
    values[1]["unresolved_priority_intervals"] = (dict(code=C, isin=CI, start=str(calendar[145]), end=str(calendar[150])),)
    row = first(build(values))
    assert row["mode"] == "original_end" and row["reason"] == "unresolved_origin_interval"
    values[1]["unresolved_priority_intervals"][0]["isin"] = "KR7999999000"
    with pytest.raises(ValueError, match="identity"):
        build(values)


def test_legal_effectiveness_on_non_session_keeps_inclusive_action_cells(monkeypatch):
    values = case(monkeypatch, effective=43, old=42)
    coverage, evidence = values[2]
    calendar = values[1]["quotes"].calendar
    on = str(calendar[42] + timedelta(days=1))
    assert date.fromisoformat(on) not in calendar
    coverage["events"][0]["effective_on"] = on
    evidence["references"]["event"]["end"] = on
    evidence["references"]["carry"].update(start=on, end=on)
    values[1]["terms"] = terms_for(coverage, evidence,
        tuple(json.loads(values[1]["terms"].canonical_json)["original_windows"]), values[1]["quotes"])
    proof = build(values)
    assert next(row for row in proof["windows"] if row["code"] == C)["start"] == on
    coverage, evidence = final_coverage(values, proof)
    reviewed_actions(coverage, evidence, expected_windows=tuple(proof["windows"]), calendar=calendar,
        activity_snapshot_sha256=ACTIVITY, quote_snapshot_sha256=proof["quote_snapshot_sha256"],
        read_scope_sha256=proof["component_scope_sha256"], spin_off_windows=tuple(proof["spin_off_windows"]))
    bounds.verify_component_inventory(replay(values), proof, values[1]["terms"].events)


def replay(values):
    args, kwargs = values[:2]
    return {arm: replay_synthetic(kwargs["quotes"].calendar, kwargs["quotes"].panel, args[4], selections,
                dataset_sha256=kwargs["quotes"].quote_snapshot_sha256, events=kwargs["terms"].events,
                timing_policy=SessionLagPolicy(lag, paired=True))
            for arm, lag, selections in (("D1", 1, args[1]), ("D2", 2, args[2]))}


def test_candidate_day_event_precedes_sales_and_independent_successor_origin(monkeypatch):
    values = case(monkeypatch, cutoff=128, record=129, effective=129, old=128, retained=129)
    proof, replays = build(values), replay(values)
    bounds.verify_component_inventory(replays, proof, values[1]["terms"].events)
    assert first(proof)["mode"] == "component_candidate"
    assert any(lot.code == C for lot in replays["D1"].books[126].lots)
    independent = case(monkeypatch, successor=B, new=50, signals={2: (A, B), 128: (A, B), 254: (A,)})
    proof, replays = build(independent), replay(independent)
    bounds.verify_component_inventory(replays, proof, independent[1]["terms"].events)
    assert first(proof, code=B)["event_id"] is None and proof["full_extension_code_dates"] == []
    overlapping = next(book for book in replays["D1"].books if book.as_of == independent[1]["quotes"].calendar[50])
    assert len([lot for lot in overlapping.lots if lot.code == B]) == 2


def test_pre_effective_sale_and_ineligible_origin_remain_unsupported(monkeypatch):
    values = case(monkeypatch, cutoff=128, record=135, effective=140, retained=140)
    assert first(build(values))["reason"] == "unsupported_pre_effective_sale_if_filled"
    with pytest.raises(ValueError, match="pre-effective"):
        replay(values)
    values = case(monkeypatch, cutoff=128, record=129, effective=140, retained=140,
                  signals={2: (), 128: (A,), 254: (A,)})
    assert first(build(values))["reason"] == "unsupported_post_cutoff_holding_if_filled"


@pytest.mark.parametrize("old,unsupported_arms", [(2, ("D1", "D2")), (3, ("D2",)), (4, ())])
def test_entry_after_old_basis_retains_full_scope_even_for_unfilled_origins(monkeypatch, old, unsupported_arms):
    # The last observable mark is the declared anchor. An entry after it cannot
    # reconcile that mark under the existing accounting constructor, even when
    # the potential entry is unfilled. Entry exactly on the anchor is supported.
    changes = {(A, index): "frozen" for index in range(old + 1, 50)}
    values = case(monkeypatch, old=old, original_changes=changes)
    proof, calendar = build(values), values[1]["quotes"].calendar
    assert len(proof["entries"]) == len(values[0][0]["entries"])
    for arm, entry_index in (("D1", 3), ("D2", 4)):
        row = first(proof, arm=arm)
        assert row["origin"]["entry_on"] == str(calendar[entry_index])
        assert all(part["candidate_on"] is not None for part in row["components"])
        if arm in unsupported_arms:
            assert row["mode"] == "original_end"
            assert row["reason"] == "unsupported_entry_after_old_basis_if_filled"
            assert row["end_on"] == row["original_end_on"] == str(calendar[420])
        else:
            assert row["mode"] == "component_candidate" and row["end_on"] == str(calendar[150])
    successor = next(row for row in proof["windows"] if row["code"] == C)
    assert successor["end"] == str(calendar[420 if unsupported_arms else 150])
    assert proof["full_extension_code_dates"] == [[code, str(day)] for code, day in sorted(
        values[1]["quotes"].extension.requested_coordinates)]
    replays = replay(values)
    bounds.verify_component_inventory(replays, proof, values[1]["terms"].events)
    for arm in unsupported_arms:
        entry_on = date.fromisoformat(first(proof, arm=arm)["origin"]["entry_on"])
        entry_book = next(book for book in replays[arm].books if book.as_of == entry_on)
        assert not any(lot.code == A for lot in entry_book.lots)


def test_followup_final_payment_uses_full_existing_path_without_ordinary_certificate(monkeypatch):
    values = case(monkeypatch, new=50)
    coverage, evidence = values[2]
    quotes, calendar = values[1]["quotes"], values[1]["quotes"].calendar
    coverage["events"].append(dict(event_id="payment", kind="final_cash_payment", status="verified_paid",
        code=C, isin=CI, evidence_ref="payment", basis_id="last", last_trading_on=str(calendar[100]),
        record_on=str(calendar[101]), paid_on=str(calendar[102]), net_cash_per_raw_share="1"))
    evidence["bases"]["last"] = dict(code=C, isin=CI, session=str(calendar[100]), role="last_trading",
        raw_close="101", adjusted_close="101", quote_snapshot_sha256=quotes.quote_snapshot_sha256, evidence_ref="last")
    for name, index, finding, ids in (("payment", 102, "verified_final_cash_payment", ["payment"]),
                                     ("last", 100, "verified_last_trading_basis", [])):
        evidence["references"][name] = dict(code=C, isin=CI, start=str(calendar[index]), end=str(calendar[index]),
            raw_sha256="7" * 64, review_sha256="8" * 64, coordinate="synthetic:payment", reviewed_on="2099-01-01",
            finding=finding, event_ids=ids)
    values[1]["terms"] = terms_for(coverage, evidence,
        tuple(json.loads(values[1]["terms"].canonical_json)["original_windows"]), quotes)
    proof = build(values)
    assert first(proof)["mode"] == "original_end"
    assert first(proof)["reason"] == "unsupported_followup_event_full_path"
    assert next(row for row in proof["windows"] if row["code"] == C)["end"] == str(calendar[420])
    coverage, evidence = final_coverage(values, proof)
    events, _ = reviewed_actions(coverage, evidence, expected_windows=tuple(proof["windows"]), calendar=calendar,
        activity_snapshot_sha256=ACTIVITY, quote_snapshot_sha256=proof["quote_snapshot_sha256"],
        read_scope_sha256=proof["component_scope_sha256"], spin_off_windows=tuple(proof["spin_off_windows"]))
    assert [event.event_id for event in events] == ["split", "payment"]


@pytest.mark.parametrize("change", ["survival", "group", "due", "availability", "event"])
def test_inventory_lineage_and_candidate_survival_rejected(monkeypatch, change):
    values = case(monkeypatch)
    proof, quotes = build(values), values[1]["quotes"]
    row = first(proof)
    origin = row["origin"]
    entered, due = date.fromisoformat(origin["entry_on"]), date.fromisoformat(origin["due_on"])
    parent_id = f"{A}:{entered}"
    identity = json.dumps(("split", parent_id), separators=(",", ":"))
    lot = Lot("spin-off-component:" + identity, C, Decimal(1), Decimal(1), entered, entered, due,
              quotes.calendar[150], quotes.quote_snapshot_sha256, "spin-off-investment:" + identity)
    book = ActivityBook(Decimal(1), (lot,), quotes.calendar[149], ("split",))
    bounds.verify_component_inventory({"D1": SimpleNamespace(books=(book,))}, proof, values[1]["terms"].events)
    if change == "survival":
        book = replace(book, as_of=quotes.calendar[150])
    elif change == "event":
        book = replace(book, applied_event_ids=())
    else:
        lot = replace(lot, **({"investment_id": "unmapped"} if change == "group" else
                             {"due_on": quotes.calendar[130]} if change == "due" else
                             {"available_on": quotes.calendar[151]}))
        book = replace(book, lots=(lot,))
    with pytest.raises(ValueError):
        bounds.verify_component_inventory({"D1": SimpleNamespace(books=(book,))}, proof, values[1]["terms"].events)
