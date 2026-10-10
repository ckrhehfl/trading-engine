"""Raw component candidates over fabricated original/full-extension lineage."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

import pytest

from research import activity_component_bounds as bounds
from research import activity_raw_quote_view as raw_view
from research.activity_preflight import digest, encoded, strict_json
from research.activity_raw_episode import RAW_FIELDS, extract_raw_episode_targets
import test_activity_component_bounds as core
import test_activity_exit_bounds as original_fixture

LG, LX, OTHER = "003550", "383800", "000002"
ISINS = {LG: "KR7003550001", LX: "KR7383800000", OTHER: "KR7000002000"}


def fixture(monkeypatch, *, states=None, end=160, lg_start=3, independent=False, **event_args):
    for module in (core, original_fixture):
        monkeypatch.setattr(module, "A", LG)
        monkeypatch.setattr(module, "B", LX if independent else OTHER)
        monkeypatch.setattr(module, "ISINS", ISINS)
    monkeypatch.setattr(core, "C", LX)
    monkeypatch.setattr(core, "CI", ISINS[LX])
    signals = {2: (LG,), 128: (LG, LX), 254: (LG,)} if independent else {2: (LG, OTHER), 128: (LG,), 254: (LG,)}
    values = core.case(monkeypatch, signals=signals, successor=LX, **{"new": 50, **event_args})
    base, calendar = values[1]["quotes"], values[1]["quotes"].calendar
    availability = event_args.get("new", 50)
    plan = dict(schema=raw_view.PLAN_SCHEMA, activity_snapshot_sha256=base.activity_snapshot_sha256,
        base_quote_snapshot_sha256=base.quote_snapshot_sha256, calendar_sha256=raw_view.calendar_sha256(calendar),
        episodes=[dict(code=LG, start=str(calendar[lg_start]), end=str(calendar[end])),
                  dict(code=LX, start=str(calendar[availability]), end=str(calendar[end]))], **raw_view.UNITS)
    observations = []
    for episode in plan["episodes"]:
        code = episode["code"]
        for index, day in enumerate(calendar):
            if not episode["start"] <= str(day) <= episode["end"]:
                continue
            opening = 200 if code == LG else 300
            source = dict(ISU_CD=code, BAS_DD=day.strftime("%Y%m%d"), TDD_OPNPRC=str(opening),
                TDD_HGPRC=str(opening + 2), TDD_LWPRC=str(opening - 1), TDD_CLSPRC=str(opening + 1),
                ACC_TRDVOL="9007199254740993", ACC_TRDVAL="1000")
            state = (states or {}).get((code, index))
            if state in ("frozen", "locked"):
                source.update(dict.fromkeys(RAW_FIELDS[:4], str(opening + 1)))
                if state == "frozen": source.update(ACC_TRDVOL="0", ACC_TRDVAL="0")
            elif state == "zero": source.update(dict.fromkeys(RAW_FIELDS, "0.00"))
            elif state == "invalid": source["TDD_OPNPRC"] = "NULL"
            rows = [] if state == "absent" else [source, source] if state == "duplicate" else [source]
            observations.extend(extract_raw_episode_targets(rows,
                dict(service="stk_bydd_trd", bas_dd=source["BAS_DD"], targets=[code]),
                "2026-10-10T00:00:00+00:00", "9" * 64))
    plan_raw = encoded(plan)
    artifact = raw_view.encode_raw_artifact(base, tuple(observations), digest(plan_raw))
    view = raw_view.build_raw_quote_view(base, tuple(observations), source_artifact=artifact, source_artifact_sha256=digest(artifact),
        unit_plan=plan_raw, unit_plan_sha256=digest(plan_raw))
    coverage, evidence = values[2]
    for row in evidence["bases"].values():
        row.update(raw_close="201" if row["code"] == LG else "301", adjusted_close="201" if row["code"] == LG else "301")
    windows = tuple(dict(code=row["code"], isin=row["isin"], start=row["entry_on"], end=row["original_end_on"])
                    for row in values[0][0]["entries"])
    values[1].update(quotes=view, terms=core.terms_for(coverage, evidence, windows, view))
    return values


def first(proof, arm="D1", code=LG):
    return next(row for row in proof["entries"] if row["origin"]["arm"] == arm and row["origin"]["code"] == code)


def test_raw_candidates_have_separate_identity_full_original_union_and_no_io(monkeypatch):
    values = fixture(monkeypatch)
    view = values[1]["quotes"]
    before = deepcopy(values)
    def forbidden(*args, **kwargs): pytest.fail("bounds attempted IO")
    for owner, name in ((Path, "open"), (socket, "create_connection"), (sqlite3, "connect")):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    proof = core.build(values)
    assert values == before and proof["schema"] == bounds.RAW_SCHEMA
    assert proof["raw_source_artifact_sha256"] == view.source_artifact_sha256
    assert proof["raw_unit_plan_sha256"] == view.unit_plan_sha256 and proof["base_quote_snapshot_sha256"] == view.base.quote_snapshot_sha256
    assert proof["quote_snapshot_sha256"] == view.quote_snapshot_sha256 != view.base.quote_snapshot_sha256
    assert proof["original_quote_snapshot_sha256"] == view.base.original.quote_snapshot_sha256
    assert proof["ordinary_scope_sha256"] == values[0][0]["child_scope_sha256"]
    assert proof["full_extension_code_dates"] == [[code, str(day)] for code, day in view.base.extension.requested_coordinates]
    assert max(day for code, day in view.base.extension.requested_coordinates if code == LX) == view.calendar[420]
    assert len(proof["entries"]) == 8
    for arm, index in (("D1", 129), ("D2", 130)):
        row = first(proof, arm)
        assert row["mode"] == "component_candidate" and row["end_on"] == str(view.calendar[index])
        assert all(part["candidate_on"] == str(view.calendar[index]) for part in row["components"])
    assert view.panel[LG].closes[41] == 201 and view.base.panel[LG].closes[41] == 101
    assert not hasattr(view.raw_observations[0], "typed_values")
    assert bounds.validate_component_exit_bounds(proof, *values[0], **values[1]) == proof


@pytest.mark.parametrize("state", ["absent", "zero", "frozen", "locked", "invalid", "duplicate"])
def test_explicit_raw_state_candidate_skip_or_full_fallback(monkeypatch, state):
    values = fixture(monkeypatch, states={(LX, 129): state})
    row, calendar = first(core.build(values)), values[1]["quotes"].calendar
    part = next(part for part in row["components"] if part["code"] == LX)
    if state in ("invalid", "duplicate"):
        assert row["mode"] == "original_end" and row["end_on"] == str(calendar[420])
        assert part["candidate_on"] is None and part["candidate_state"] == "unresolved_observed"
    else:
        index = 129 if state == "locked" else 130
        assert row["mode"] == "component_candidate" and row["end_on"] == str(calendar[index])
        assert part["candidate_on"] == str(calendar[index])
        assert part["candidate_state"] == ("locked" if state == "locked" else "observed")


@pytest.mark.parametrize("missing", ["outside", "all_zero", "all_frozen"])
def test_no_raw_exit_never_uses_retained_kis_openings(monkeypatch, missing):
    states = {(LX, index): "zero" if missing == "all_zero" else "frozen" for index in range(129, 161)}
    values = fixture(monkeypatch, end=128 if missing == "outside" else 160, states=states)
    row = first(core.build(values)); view = values[1]["quotes"]
    assert row["mode"] == "original_end" and row["end_on"] == str(view.calendar[420])
    assert next(part for part in row["components"] if part["code"] == LX)["candidate_state"] == "unrequested_quote"
    assert view.base.panel[LX].opens[161] == 100 and view.panel[LX].opens[161] is None
    assert next(window for window in core.build(values)["windows"] if window["code"] == LX)["end"] == str(view.calendar[420])


def test_later_parent_and_independent_successor_origins_keep_original_obligation(monkeypatch):
    values = fixture(monkeypatch, independent=True)
    proof = core.build(values); calendar = values[1]["quotes"].calendar
    later = [row for row in proof["entries"] if row["origin"]["code"] in (LG, LX) and row["origin"]["entry_on"] >= str(calendar[129])]
    assert later and any(row["origin"]["code"] == LX for row in later)
    assert all(row["mode"] == "original_end" and row["reason"] == "raw_ordinary_candidate_not_proven"
               and row["end_on"] == str(calendar[420]) and row["components"][0]["candidate_on"] is None for row in later)
    assert any(row["ordinary_candidate_on"] for row in later)


@pytest.mark.parametrize("change", ["half_raw", "base_value", "missing_anchor"])
def test_raw_basis_requires_equal_raw_units_and_requested_resolved_raw_close(monkeypatch, change):
    values = fixture(monkeypatch, lg_start=42 if change == "missing_anchor" else 3)
    if change != "missing_anchor":
        coverage, evidence = values[2]
        evidence["bases"]["old"].update(raw_close="202" if change == "half_raw" else "101",
                                        adjusted_close="201" if change == "half_raw" else "101")
        windows = tuple(dict(code=row["code"], isin=row["isin"], start=row["entry_on"], end=row["original_end_on"])
                        for row in values[0][0]["entries"])
        values[1]["terms"] = core.terms_for(coverage, evidence, windows, values[1]["quotes"])
    with pytest.raises(ValueError, match="raw event basis"):
        core.build(values)


@pytest.mark.parametrize("change", ["series", "source_pin", "rows", "base", "generic_imitation", "proof_pin"])
def test_forged_raw_view_and_rehashed_proof_are_recomputed(monkeypatch, change):
    values = fixture(monkeypatch); view = values[1]["quotes"]
    if change == "proof_pin":
        proof = core.build(values); proof["raw_unit_plan_sha256"] = "0" * 64
        proof["component_scope_sha256"] = digest(encoded({key: value for key, value in proof.items() if key != "component_scope_sha256"}))
        with pytest.raises(ValueError, match="differs"): bounds.validate_component_exit_bounds(proof, *values[0], **values[1])
        return
    if change == "series": altered = replace(view, series=view.base.series)
    elif change == "source_pin": altered = replace(view, source_artifact_sha256="0" * 64)
    elif change == "rows": altered = replace(view, raw_observations=view.raw_observations[:-1])
    elif change == "base": altered = replace(view, base=replace(view.base, original=view.base.extension))
    else: altered = SimpleNamespace(**view.__dict__)
    values[1]["quotes"] = altered
    with pytest.raises(ValueError): core.build(values)


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_raw_view_cannot_shorten_or_expand_full_bp_extension_union(monkeypatch, change):
    values = fixture(monkeypatch, new=150, **{change: True})
    with pytest.raises(ValueError, match="FULL extension"):
        core.build(values)


def test_raw_pre_effective_sale_guard_uses_raw_candidate_not_kis_candidate(monkeypatch):
    # Original KIS first candidate is frozen until day131. Raw day129 lies
    # after cutoff128 and before legal effect130 and must retain the obligation.
    values = fixture(monkeypatch, effective=130, cutoff=128, record=129, old=128, retained=131, new=131,
        original_changes={(LG, 129): "frozen", (LG, 130): "frozen"})
    row = first(core.build(values)); calendar = values[1]["quotes"].calendar
    assert row["ordinary_candidate_on"] == str(calendar[131])
    assert row["mode"] == "original_end" and row["reason"] == "unsupported_pre_effective_sale_if_filled"
    assert row["end_on"] == str(calendar[420])


def test_raw_pending_bars_before_availability_are_not_sale_candidates(monkeypatch):
    values = fixture(monkeypatch, retained=150, new=150, states={(LG, 129): "invalid", (LG, 130): "invalid"})
    row = first(core.build(values)); calendar = values[1]["quotes"].calendar
    assert row["mode"] == "component_candidate" and row["end_on"] == str(calendar[150])


def test_unrequested_raw_entry_cannot_receive_a_shorter_component_bound(monkeypatch):
    values = fixture(monkeypatch, lg_start=10)
    row = first(core.build(values)); calendar = values[1]["quotes"].calendar
    assert row["mode"] == "original_end" and row["reason"] == "raw_origin_entry_unrequested"
    assert row["end_on"] == str(calendar[420])


def test_raw_candidate_day_effect_and_old_basis_entry_guard_are_preserved(monkeypatch):
    values = fixture(monkeypatch, cutoff=128, record=129, effective=129, old=128, retained=129, new=129)
    proof = core.build(values); calendar = values[1]["quotes"].calendar
    assert first(proof)["mode"] == "component_candidate" and first(proof)["end_on"] == str(calendar[129])
    assert any(window["code"] == LX and window["start"] == str(calendar[129]) for window in proof["windows"])
    values = fixture(monkeypatch, old=3)
    proof = core.build(values)
    assert first(proof, "D1")["mode"] == "component_candidate"
    assert first(proof, "D2")["reason"] == "unsupported_entry_after_old_basis_if_filled"
    assert first(proof, "D2")["end_on"] == str(calendar[420])


def test_raw_followup_event_preserves_full_existing_obligation(monkeypatch):
    values = fixture(monkeypatch); view = values[1]["quotes"]; calendar = view.calendar
    coverage, evidence = values[2]
    coverage["events"].append(dict(event_id="payment", kind="final_cash_payment", status="verified_paid",
        code=LX, isin=ISINS[LX], evidence_ref="payment", basis_id="last", last_trading_on=str(calendar[100]),
        record_on=str(calendar[101]), paid_on=str(calendar[102]), net_cash_per_raw_share="1"))
    evidence["bases"]["last"] = dict(code=LX, isin=ISINS[LX], session=str(calendar[100]), role="last_trading",
        raw_close="301", adjusted_close="301", quote_snapshot_sha256=view.quote_snapshot_sha256, evidence_ref="last")
    for name, index, finding, ids in (("payment", 102, "verified_final_cash_payment", ["payment"]),
                                     ("last", 100, "verified_last_trading_basis", [])):
        evidence["references"][name] = dict(code=LX, isin=ISINS[LX], start=str(calendar[index]), end=str(calendar[index]),
            raw_sha256="7" * 64, review_sha256="8" * 64, coordinate="synthetic:payment", reviewed_on="2099-01-01",
            finding=finding, event_ids=ids)
    values[1]["terms"] = core.terms_for(coverage, evidence,
        tuple(strict_json(values[1]["terms"].canonical_json)["original_windows"]), view)
    row = first(core.build(values))
    assert row["reason"] == "unsupported_followup_event_full_path" and row["end_on"] == str(calendar[420])
