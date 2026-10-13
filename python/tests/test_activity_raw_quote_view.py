"""Fabricated raw rows and full typed base; no saved evidence, DB or network."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
from pathlib import Path
import socket
import sqlite3

import pytest

from research import activity_raw_quote_view as module
from research.activity_preflight import digest, encoded, strict_json
from research.activity_quote_extension import extend_holding_quotes
from research.activity_raw_episode import RAW_FIELDS, extract_raw_episode_targets
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
    for state in (None, "absent", "zero", "invalid", "duplicate", "frozen", "locked"):
        view = build(*fixture(state=state))
        restored = module.restore_raw_quote_view(view.base, view.source_artifact_json,
            view.source_artifact_sha256, view.unit_plan_json, view.unit_plan_sha256)
        assert restored == view
        assert restored.raw_observations == view.raw_observations


@pytest.mark.parametrize("mutation", ["decimal_number", "price", "sequence", "extra", "unit", "pin"])
def test_raw_restore_rejects_rehashed_or_unpinned_foreign_storage(mutation):
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
        module.restore_raw_quote_view(view.base, raw, "e" * 64 if mutation == "pin" else digest(raw),
            view.unit_plan_json, view.unit_plan_sha256)
