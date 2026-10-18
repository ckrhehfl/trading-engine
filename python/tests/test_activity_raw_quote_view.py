"""Fabricated raw rows and full typed base; no saved evidence, DB or network."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path
import socket
import sqlite3

import pytest

from research import activity_raw_quote_view as module
from research.activity_preflight import digest, encoded, strict_json
from research.activity_quote_extension import extend_holding_quotes
from research.activity_raw_episode import RAW_FIELDS, decode_raw_episode_row, extract_raw_episode_targets
from research.activity_replay import _quote
import test_activity_quote_extension as synthetic

LG, LX, OTHER = "003550", "383800", "000001"
DAYS = synthetic.DAYS


def fixture(*, state=None):
    original = synthetic.component({LG: DAYS[:6], OTHER: DAYS[:6]}, "b" * 64,
        changes={(LG, DAYS[2]): None, (OTHER, DAYS[2]): None})
    extension = synthetic.component({LX: DAYS[2:6]}, "c" * 64)
    base = extend_holding_quotes(original, extension)
    plan = dict(schema=module.PLAN_SCHEMA, activity_snapshot_sha256=base.activity_snapshot_sha256,
        base_quote_snapshot_sha256=base.quote_snapshot_sha256, calendar_sha256=module.calendar_sha256(DAYS),
        episodes=[dict(code=LG, start=str(DAYS[1]), end=str(DAYS[4])),
                  dict(code=LX, start=str(DAYS[2]), end=str(DAYS[4]))], **module.UNITS)
    observations = []
    for episode in plan["episodes"]:
        code = episode["code"]
        for day in DAYS:
            if not episode["start"] <= str(day) <= episode["end"]:
                continue
            source = dict(ISU_CD=code, BAS_DD=day.strftime("%Y%m%d"),
                TDD_OPNPRC="+007.00", TDD_HGPRC="9", TDD_LWPRC="6", TDD_CLSPRC="8",
                ACC_TRDVOL="9007199254740993", ACC_TRDVAL="123456789012345678901234567890.125")
            chosen = state if (code, day) == (LG, DAYS[2]) else None
            if chosen in ("locked", "frozen"):
                source.update(dict.fromkeys(RAW_FIELDS[:4], "7.00"))
                if chosen == "frozen": source.update(ACC_TRDVOL="0", ACC_TRDVAL="0")
            elif chosen == "zero": source.update(dict.fromkeys(RAW_FIELDS, "0.00"))
            elif chosen == "reference_close":
                source.update(dict.fromkeys(RAW_FIELDS, "0.00"), TDD_CLSPRC="+126,500.00")
            elif chosen == "invalid": source["TDD_OPNPRC"] = "NULL"
            rows = [] if chosen == "absent" else [source, source] if chosen == "duplicate" else [source]
            observations.extend(extract_raw_episode_targets(rows, dict(service="stk_bydd_trd", bas_dd=day.strftime("%Y%m%d"), targets=[code]),
                "2026-10-10T00:00:00+00:00", "d" * 64))
    return base, tuple(observations), plan


def build(base, observations, plan):
    unit_plan = encoded(plan)
    source = module.encode_raw_artifact(base, observations, digest(unit_plan))
    return module.build_raw_quote_view(base, observations, source_artifact=source, source_artifact_sha256=digest(source),
        unit_plan=unit_plan, unit_plan_sha256=digest(unit_plan))


def test_many_raw_basis_declarations_refreeze_once_per_binding(monkeypatch):
    from research.activity_raw_component_preflight import _bind_raw_basis
    view = build(*fixture())
    evidence = dict(references={}, bases={str(index): dict(code=row.code,
        session=str(date.fromisoformat(row.requested_bas_dd)),
        raw_close=str(row.close), adjusted_close=str(row.close), quote_snapshot_sha256=view.quote_snapshot_sha256)
        for index, row in enumerate(view.raw_observations)})
    calls, original = [], module.validate_raw_quote_view
    def counted(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(module, "validate_raw_quote_view", counted)
    _bind_raw_basis(evidence, view)
    assert len(evidence["bases"]) == 7 and calls == [True]


@pytest.mark.parametrize("change", ["copy", "cross", "row", "base", "series", "metadata"])
@pytest.mark.parametrize("restored", [False, True])
def test_invocation_raw_witness_rejects_replacement_and_nested_mutation(change, restored):
    view = build(*fixture())
    context, use = (module._restore_raw_quote_context(view.base, view.source_artifact_json,
        view.source_artifact_sha256, view.unit_plan_json, view.unit_plan_sha256)
        if restored else module._prepare_raw_quote_context(view))
    if restored: view, _ = use(context)
    candidate = context
    if change == "copy": candidate = replace(context)
    elif change == "cross": candidate, _ = module._prepare_raw_quote_context(view)
    elif change == "row": object.__setattr__(view.raw_observations[0], "close", Decimal("999"))
    elif change == "base": object.__setattr__(view.base.original.observations[0], "code", "changed")
    elif change == "series": object.__setattr__(view.series[0][1], "opens", ())
    else: object.__setattr__(view, "source_artifact_sha256", "e" * 64)
    with pytest.raises(ValueError):
        use(candidate)


@pytest.mark.parametrize("state", [None, "locked", "frozen", "absent", "zero", "reference_close", "invalid", "duplicate"])
@pytest.mark.parametrize("restored", [False, True])
def test_private_basis_uses_readonly_scalars_and_preserves_public_state_guards(state, restored):
    view = build(*fixture(state=state))
    context, use = (module._restore_raw_quote_context(view.base, view.source_artifact_json,
        view.source_artifact_sha256, view.unit_plan_json, view.unit_plan_sha256)
        if restored else module._prepare_raw_quote_context(view))
    _, rows = use(context)
    key = LG, DAYS[2]
    assert type(rows[key]) is tuple and all(not module.is_dataclass(value) for value in rows[key])
    with pytest.raises(TypeError):
        rows[key] = ()
    basis_at = module._raw_basis_accessor(context, use)
    if state in (None, "locked"):
        assert basis_at(*key) == module.raw_price_basis(view, *key)
    else:
        with pytest.raises(ValueError): module.raw_price_basis(view, *key)
        with pytest.raises(ValueError): basis_at(*key)


def test_restore_context_fully_builds_once_and_keeps_exact_artifact_bytes(monkeypatch):
    view = build(*fixture())
    builds, build_view = [], module.build_raw_quote_view
    def counted(*args, **kwargs):
        fresh = build_view(*args, **kwargs); builds.append(fresh); return fresh
    monkeypatch.setattr(module, "build_raw_quote_view", counted)
    context, use = module._restore_raw_quote_context(view.base, view.source_artifact_json,
        view.source_artifact_sha256, view.unit_plan_json, view.unit_plan_sha256)
    fresh, _ = use(context)
    assert len(builds) == 1 and fresh is builds[0] and fresh == view
    assert fresh.source_artifact_json is view.source_artifact_json and fresh.unit_plan_json is view.unit_plan_json
    assert fresh.base is view.base and fresh.raw_observations == view.raw_observations


@pytest.mark.parametrize("forged", [False, True])
def test_restore_context_branch_never_accepts_a_supplied_view(forged):
    view = build(*fixture())
    inputs = (view.base, view.source_artifact_json, view.source_artifact_sha256, view.unit_plan_json, view.unit_plan_sha256)
    if forged: view = replace(view, quote_snapshot_sha256="e" * 64)
    with pytest.raises(ValueError, match="exact artifact inputs"):
        module._prepare_raw_quote_context(view, _restore_inputs=inputs)


@pytest.mark.parametrize("change", ["copy", "cross", "quotes", "row", "base", "series"])
def test_private_basis_binding_rejects_foreign_context_or_changed_quotes(change):
    from research.activity_raw_component_preflight import _bind_raw_basis
    view = build(*fixture())
    context, use = module._restore_raw_quote_context(view.base, view.source_artifact_json,
        view.source_artifact_sha256, view.unit_plan_json, view.unit_plan_sha256)
    quotes, _ = use(context)
    evidence = dict(references={}, bases={"old": dict(code=LG, session=str(DAYS[1]),
        raw_close="8", adjusted_close="8", quote_snapshot_sha256=quotes.quote_snapshot_sha256)})
    if change == "copy": context = replace(context)
    elif change == "cross": context, _ = module._prepare_raw_quote_context(quotes)
    elif change == "quotes": quotes = replace(quotes)
    elif change == "row": object.__setattr__(quotes.raw_observations[0], "close", Decimal("999"))
    elif change == "base": object.__setattr__(quotes.base.original.observations[0], "code", "changed")
    else: object.__setattr__(quotes.series[0][1], "opens", ())
    with pytest.raises(ValueError): _bind_raw_basis(evidence, quotes, _raw_context=(context, use))


def test_full_base_absences_new_identity_exact_raw_texts_and_unaffected_panel_without_io(monkeypatch):
    base, observations, plan = fixture()
    before = deepcopy((base, observations, plan))
    def forbidden(*args, **kwargs): pytest.fail("raw view attempted IO")
    for owner, name in ((Path, "open"), (socket, "create_connection"), (sqlite3, "connect")):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    with localcontext() as context:
        context.prec = 3
        view = build(base, observations, plan)
        assert module.validate_raw_quote_view(view) == view
    assert (base, observations, plan) == before
    assert view.base is base and view.original is base.original and view.extension is base.extension
    assert view.base_requested_coordinates is base.requested_coordinates
    assert view.base_absent_coordinates is base.absent_coordinates
    assert (LG, DAYS[2]) in view.base_absent_coordinates
    assert (LG, DAYS[2]) not in view.absent_coordinates  # Explicit separate raw projection, no base repair.
    assert view.panel[LG].opens[2] == Decimal("7.00") and view.panel[LG].closes[2] == 8
    assert view.panel[LG].opens[2] != base.panel[LG].opens[1]
    assert view.panel[OTHER] is base.panel[OTHER]
    assert view.panel[LG].turnover[2] == Decimal("123456789012345678901234567890.125")
    assert dict(view.raw_observations[0].original_texts)["TDD_OPNPRC"] == "+007.00"
    assert not hasattr(view.raw_observations[0], "typed_values")
    assert view.quote_snapshot_sha256 == digest(view.fingerprint_json)
    assert view.quote_snapshot_sha256 not in (base.quote_snapshot_sha256, base.original.quote_snapshot_sha256,
        base.extension.quote_snapshot_sha256, view.source_artifact_sha256, view.unit_plan_sha256)
    lineage = strict_json(view.fingerprint_json)
    assert lineage["base_lineage"] == strict_json(base.fingerprint_json)
    assert lineage["source_unit_plan"] == plan and lineage["atomic_source_vintage_certified"] is False
    assert view.summary["original_end_obligation_discharged"] is False
    assert view.summary["episode_end_is_actual_exit"] is False
    assert view.summary["legacy_replay_boundary_guard_integrated"] is False
    with pytest.raises(TypeError): view.panel[LG] = base.panel[LG]
    with pytest.raises(FrozenInstanceError): view.quote_snapshot_sha256 = "e" * 64
    summary = view.summary; summary["books_computed"] = True
    assert view.summary["books_computed"] is False


def test_outside_episode_has_no_kis_fallback_and_mandatory_boundary_guard_rejects():
    view = build(*fixture())
    for code, indices in ((LG, (0, 5, 6, 7)), (LX, (0, 1, 5, 6, 7))):
        for index in indices:
            assert (code, DAYS[index]) in view.unrequested_coordinates
            assert view.panel[code].observed[index] is False
            assert all(getattr(view.panel[code], name)[index] is None for name in module.NAMES[:-1])
            assert _quote(view.panel[code], index) == (None, None, False, False)
            with pytest.raises(ValueError, match="crossed"):
                module.require_raw_coordinate(view, code, DAYS[index])
    assert module.require_raw_coordinate(view, OTHER, DAYS[7]) is None


@pytest.mark.parametrize("state", ["absent", "zero", "invalid", "duplicate", "frozen", "locked"])
def test_source_states_preserved_without_zero_prices_or_invented_halts(state):
    view = build(*fixture(state=state))
    key = LG, DAYS[2]
    raw = next(row for row in view.raw_observations if row.code == LG and row.requested_bas_dd == DAYS[2].strftime("%Y%m%d"))
    assert raw.market_state_cause == "unknown"
    if state in ("invalid", "duplicate"):
        assert key in view.unresolved_coordinates and view.panel[LG].observed[2] is True
        with pytest.raises(ValueError, match="unresolved"): _quote(view.panel[LG], 2)
        with pytest.raises(ValueError, match="unresolved"): module.require_raw_coordinate(view, *key)
    elif state in ("absent", "zero"):
        assert view.panel[LG].observed[2] is False
        assert _quote(view.panel[LG], 2) == (None, None, False, False)
        assert (key in view.absent_coordinates) == (state == "absent")
        assert (key in view.no_trade_coordinates) == (state == "zero")
        module.require_raw_coordinate(view, *key)
    else:
        assert _quote(view.panel[LG], 2) == (Decimal("7.00"), Decimal("7.00"), state == "frozen", state == "locked")
    if state != "locked":
        with pytest.raises(ValueError): module.raw_price_basis(view, *key)


def test_raw_raw_basis_is_explicit_unit_definition_not_a_kis_factor():
    view = build(*fixture())
    basis = module.raw_price_basis(view, LG, DAYS[1])
    assert basis.raw_close == basis.adjusted_close == Decimal("8")
    assert basis.raw_shares_per_adjusted_share == 1 and basis.dataset_sha256 == view.quote_snapshot_sha256
    assert "declared_raw_share_unit" in basis.source and view.source_artifact_sha256 in basis.source
    assert view.summary["unit_bridge_certified"] is False
    assert view.original.panel[LG].closes[1] == Decimal("100.000")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "order", "extra", "tuple", "forged_price", "forged_unit",
    "plan_activity", "plan_base", "plan_calendar", "plan_unit", "plan_dates", "plan_extra", "plan_duplicate"])
def test_rehashed_forged_inputs_do_not_bypass_scope_rows_or_lineage(mutation):
    base, observations, plan = fixture()
    if mutation == "missing": observations = observations[:-1]
    elif mutation == "duplicate": observations = (*observations, observations[-1])
    elif mutation == "order": observations = tuple(reversed(observations))
    elif mutation == "extra": observations = (*observations, replace(observations[-1], requested_bas_dd=DAYS[5].strftime("%Y%m%d")))
    elif mutation == "tuple": observations = list(observations)
    elif mutation == "forged_price": observations = (replace(observations[0], close=Decimal("999")), *observations[1:])
    elif mutation == "forged_unit": observations = (replace(observations[0], price_unit="adjusted"), *observations[1:])
    elif mutation.startswith("plan_"):
        if mutation == "plan_activity": plan["activity_snapshot_sha256"] = "e" * 64
        elif mutation == "plan_base": plan["base_quote_snapshot_sha256"] = "e" * 64
        elif mutation == "plan_calendar": plan["calendar_sha256"] = "e" * 64
        elif mutation == "plan_unit": plan["price_unit"] = "adjusted"
        elif mutation == "plan_dates": plan["episodes"][0]["start"] = "2021-05-23"
        elif mutation == "plan_extra": plan["authorization"] = True
        else: plan["episodes"][1] = plan["episodes"][0]
    with pytest.raises(ValueError): build(base, observations, plan)


@pytest.mark.parametrize("mutation", ["raw_pin", "plan_pin", "artifact_rows", "base", "series", "identity", "summary", "absence"])
def test_source_pins_and_refreeze_reject_tampered_projection(mutation):
    base, observations, plan = fixture()
    view = build(base, observations, plan)
    if mutation in ("raw_pin", "plan_pin", "artifact_rows"):
        source = view.source_artifact_json
        if mutation == "artifact_rows": source = source.replace(b'"raw_shares"', b'"adjusted"')
        with pytest.raises(ValueError):
            module.build_raw_quote_view(base, observations, source_artifact=source,
                source_artifact_sha256="e" * 64 if mutation == "raw_pin" else digest(source),
                unit_plan=view.unit_plan_json, unit_plan_sha256="e" * 64 if mutation == "plan_pin" else view.unit_plan_sha256)
        return
    if mutation == "base": view = replace(view, base=replace(base, requested_coordinates=()))
    elif mutation == "series": view = replace(view, series=base.series)
    elif mutation == "identity": view = replace(view, quote_snapshot_sha256="e" * 64)
    elif mutation == "summary": view = replace(view, summary_json=b"{}\n")
    else: view = replace(view, absent_coordinates=())
    with pytest.raises(ValueError): module.validate_raw_quote_view(view)


@pytest.mark.parametrize("consumer", [module.require_raw_coordinate, module.raw_price_basis])
@pytest.mark.parametrize("mutation", ["outside_row", "changed_price"])
def test_public_raw_consumers_reject_forged_rows_without_trusting_frozen_type(consumer, mutation):
    view = build(*fixture())
    session = DAYS[1]
    if mutation == "outside_row":
        session = DAYS[0]
        source = dict(ISU_CD=LG, BAS_DD=session.strftime("%Y%m%d"),
            TDD_OPNPRC="7", TDD_HGPRC="9", TDD_LWPRC="6", TDD_CLSPRC="8",
            ACC_TRDVOL="100", ACC_TRDVAL="800")
        extra = extract_raw_episode_targets([source], dict(service="stk_bydd_trd",
            bas_dd=source["BAS_DD"], targets=[LG]), "2026-10-10T00:00:00+00:00", "d" * 64)
        rows = (*extra, *view.raw_observations)
    else:
        rows = (replace(view.raw_observations[0], close=Decimal("999")), *view.raw_observations[1:])
    forged = replace(view, raw_observations=rows)
    with pytest.raises(ValueError):
        consumer(forged, LG, session)


def test_raw_artifact_restore_roundtrip_keeps_exact_text_units_and_missing_states():
    for state in (None, "absent", "zero", "reference_close", "invalid", "duplicate", "frozen", "locked"):
        view = build(*fixture(state=state))
        restored = module.restore_raw_quote_view(view.base, view.source_artifact_json,
            view.source_artifact_sha256, view.unit_plan_json, view.unit_plan_sha256)
        assert restored == view
        assert restored.raw_observations == view.raw_observations


def reference_row(**changes):
    source = dict(ISU_CD=LG, BAS_DD=DAYS[2].strftime("%Y%m%d"),
                  **dict.fromkeys(RAW_FIELDS, "0"))
    source["TDD_CLSPRC"] = "126500"
    source.update(changes)
    return decode_raw_episode_row(source, code=LG, bas_dd=DAYS[2].strftime("%Y%m%d"),
        retrieved_at="2026-10-10T00:00:00+00:00", response_sha256="d" * 64, row_position=7)


def test_reference_close_is_view_only_non_executable_and_retains_unresolved_source_bytes():
    base, observations, plan = fixture(state="reference_close")
    view = build(base, observations, plan)
    row = observations[1]
    assert module.is_raw_no_trade_reference_close(row) is True
    assert row.status == row.state == "unresolved" and row.issues == ("mixed_or_active_zero_prices",)
    assert all(getattr(row, name) is None for name in ("open", "high", "low", "close", "volume", "turnover"))
    assert row.market_state_cause == "unknown" and row.source_truth_certified is False
    assert view.raw_observations is observations
    assert view.source_artifact_json == module.encode_raw_artifact(base, observations, view.unit_plan_sha256)
    assert dict(row.original_texts)["TDD_CLSPRC"] == "+126,500.00"
    key = LG, DAYS[2]
    assert key in view.no_trade_coordinates and key not in view.unresolved_coordinates and key not in view.absent_coordinates
    assert all(getattr(view.panel[LG], name)[2] is None for name in module.NAMES[:-1])
    assert view.panel[LG].observed[2] is False and _quote(view.panel[LG], 2) == (None, None, False, False)
    assert module.require_raw_coordinate(view, *key) is row
    with pytest.raises(ValueError, match="raw basis"):
        module.raw_price_basis(view, *key)
    identity = strict_json(view.fingerprint_json)
    assert identity["schema"] == "activity-raw-episode-quote-view-v2"
    assert identity["reference_close_policy"] == "non_executable_ohl_zero_positive_reference_close_zero_activity_v1"
    assert identity["reference_close_no_trade_coordinates"] == [dict(code=LG, session=str(DAYS[2]),
        response_sha256=row.response_sha256, row_position=row.row_position)]
    assert view.summary["schema"] == "activity-raw-episode-quote-view-v2:summary-v1"
    assert view.summary["original_raw_unresolved_code_dates"] == 1
    assert view.summary["raw_unresolved_code_dates"] == 0
    assert view.summary["raw_reference_close_no_trade_code_dates"] == view.summary["raw_no_trade_code_dates"] == 1
    assert module.validate_raw_quote_view(view) == view


def test_v1_identity_and_summary_stay_exact_when_reference_close_projection_is_unused():
    view = build(*fixture(state="invalid"))
    identity = strict_json(view.fingerprint_json)
    assert identity["schema"] == module.SCHEMA == "activity-raw-episode-quote-view-v1"
    assert "reference_close_policy" not in identity and "reference_close_no_trade_coordinates" not in identity
    assert "original_raw_unresolved_code_dates" not in view.summary
    assert "raw_reference_close_no_trade_code_dates" not in view.summary


def test_reference_close_summary_distinguishes_original_unresolved_absent_and_projection():
    base, observations, plan = fixture(state="absent")
    source = reference_row()
    reference = replace(source, requested_bas_dd=DAYS[1].strftime("%Y%m%d"), returned_bas_dd=DAYS[1].strftime("%Y%m%d"))
    view = build(base, (reference, *observations[1:]), plan)
    assert view.summary["original_raw_unresolved_code_dates"] == 2
    assert view.summary["raw_absent_code_dates"] == 1 and view.summary["raw_unresolved_code_dates"] == 0
    assert view.summary["raw_reference_close_no_trade_code_dates"] == 1


@pytest.mark.parametrize("changes", [dict(ACC_TRDVOL="1"), dict(ACC_TRDVAL="1"),
    dict(TDD_OPNPRC="1"), dict(TDD_HGPRC="126500"), dict(TDD_LWPRC="1"), dict(TDD_CLSPRC="0"),
    dict(TDD_CLSPRC="-1"), dict(TDD_CLSPRC="NULL"), dict(TDD_CLSPRC="NaN"),
    dict(TDD_CLSPRC=None), dict(TDD_CLSPRC=126500), dict(BAS_DD="20210101"), dict(ISU_CD=LX)])
def test_reference_close_predicate_does_not_accept_nearby_invalid_or_other_shapes(changes):
    assert module.is_raw_no_trade_reference_close(reference_row(**changes)) is False


@pytest.mark.parametrize("change", [dict(close=Decimal("126500")), dict(issues=("invalid_ohlc_bounds",)),
    dict(state="observed"), dict(status="observed"), dict(returned_bas_dd="20210101"), dict(row_positions=(8,)),
    dict(canonical_decimal_strings=tuple((field, "1") for field in RAW_FIELDS))])
def test_reference_close_predicate_refuses_forged_decoder_rows(change):
    with pytest.raises(ValueError):
        module.is_raw_no_trade_reference_close(replace(reference_row(), **change))


@pytest.mark.parametrize("mutation", ["policy", "coordinate", "order", "summary", "panel", "unresolved", "no_trade"])
def test_refreeze_recomputes_reference_close_policy_coordinates_counts_and_panel(mutation):
    base, observations, plan = fixture(state="reference_close")
    observations = (reference_row(), *observations[1:])
    # A second reference row remains at its original requested coordinate.
    observations = (replace(observations[0], requested_bas_dd=DAYS[1].strftime("%Y%m%d"),
        returned_bas_dd=DAYS[1].strftime("%Y%m%d")), *observations[1:])
    view = build(base, observations, plan)
    if mutation in ("policy", "coordinate", "order"):
        identity = strict_json(view.fingerprint_json)
        if mutation == "policy": identity["reference_close_policy"] = "allow_reference_close_price"
        elif mutation == "coordinate": identity["reference_close_no_trade_coordinates"][0]["row_position"] += 1
        else: identity["reference_close_no_trade_coordinates"].reverse()
        raw = encoded(identity)
        view = replace(view, fingerprint_json=raw, quote_snapshot_sha256=digest(raw))
    elif mutation == "summary":
        summary = view.summary; summary["original_raw_unresolved_code_dates"] = 0
        view = replace(view, summary_json=encoded(summary))
    elif mutation == "panel": view = replace(view, series=base.series)
    elif mutation == "unresolved": view = replace(view, unresolved_coordinates=((LG, DAYS[2]),))
    else: view = replace(view, no_trade_coordinates=())
    with pytest.raises(ValueError, match="disagrees"):
        module.validate_raw_quote_view(view)


@pytest.mark.parametrize("mutation", ["decimal_number", "price", "sequence", "extra", "unit", "pin"])
@pytest.mark.parametrize("consumer", [module.restore_raw_quote_view, module._restore_raw_quote_context])
def test_raw_restore_rejects_rehashed_or_unpinned_foreign_storage(mutation, consumer):
    view = build(*fixture())
    artifact = strict_json(view.source_artifact_json)
    row = artifact["rows"][0]
    if mutation == "decimal_number": row["close"] = 8
    elif mutation == "price": row["close"] = "999"
    elif mutation == "sequence": row["issues"] = None
    elif mutation == "extra": row["typed_values"] = []
    elif mutation == "unit": row["price_unit"] = "adjusted"
    raw = encoded(artifact)
    with pytest.raises(ValueError):
        consumer(view.base, raw, "e" * 64 if mutation == "pin" else digest(raw),
            view.unit_plan_json, view.unit_plan_sha256)
