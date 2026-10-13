"""Raw paired boundary over fabricated adapters; package preparation is stubbed.

Upstream package restoration has its own real synthetic-package suites. Here
only that boundary is stubbed; extension, raw restoration, both price proofs,
linked actions, both real replays and the final inventory check run unchanged.
"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import socket
import sqlite3

import pytest

from research import activity_component_bounds as bounds
from research import activity_paired_replay as paired
from research import activity_raw_quote_view as raw
from research.activity_preflight import digest, encoded, strict_json
from research.activity_quote_extension import extend_holding_quotes
import test_activity_raw_component_bounds as synthetic
import test_activity_component_bounds as core
import test_activity_exit_bounds as original
from test_activity_paired_extension import extension_envelope

LG, LX = synthetic.LG, synthetic.LX


def fixture(monkeypatch, *, end=160, states=None):
    package_pins = {name: digest(name.encode()) for name in paired.PACKAGE_ROLES}
    monkeypatch.setattr(core, "PINS", package_pins)
    monkeypatch.setattr(original, "PINS", package_pins)
    build_original = original.fixture
    # Exactly two affected potential origins, with later empty formations.
    monkeypatch.setattr(core, "fixture", lambda **kw: build_original(
        changes=kw.get("changes"), signals={2: (LG,)}))
    values = synthetic.fixture(monkeypatch, end=end, states=states)
    plan, d1, d2, holding, parameters, required, identities = values[0]
    old_view = values[1]["quotes"]
    requested = {}
    for code, day in old_view.base.extension.requested_coordinates:
        requested.setdefault(code, []).append(day)
    envelope, extension = extension_envelope(monkeypatch, holding, requested)
    composite = extend_holding_quotes(holding, extension)
    unit = strict_json(old_view.unit_plan_json)
    unit["base_quote_snapshot_sha256"] = composite.quote_snapshot_sha256
    unit_bytes = encoded(unit)
    artifact = raw.encode_raw_artifact(composite, old_view.raw_observations, digest(unit_bytes))
    view = raw.restore_raw_quote_view(composite, artifact, digest(artifact), unit_bytes, digest(unit_bytes))
    coverage, evidence = values[2]
    windows = tuple(dict(code=row["code"], isin=row["isin"], start=row["entry_on"], end=row["original_end_on"])
                    for row in plan["entries"])
    terms = core.terms_for(coverage, evidence, windows, view)
    proof = bounds.component_exit_bounds(plan, d1, d2, holding, parameters, required, identities,
        input_sha256=package_pins, quotes=view, terms=terms)
    values = ((plan, d1, d2, holding, parameters, required, identities),
              dict(input_sha256=package_pins, quotes=view, terms=terms), (coverage, evidence))
    coverage, evidence = core.final_coverage(values, proof)
    wrapper = encoded(dict(schema=bounds.WRAPPER_SCHEMA, ordinary_exit_scope=plan, component_scope=proof))
    inputs = [{}, {}, encoded(coverage), encoded(evidence)]
    pins = dict(package_pins, reviewed_action_coverage=digest(inputs[2]), basis_evidence=digest(inputs[3]),
        conditional_exit_scope=digest(wrapper), quote_extension=digest(encoded(envelope)),
        raw_episode_quotes=digest(artifact), raw_episode_unit_plan=digest(unit_bytes))
    prepared = paired.PreparedInputs(holding.calendar, parameters, holding.activity_snapshot_sha256,
        holding.quote_snapshot_sha256, holding.read_scope_sha256, d1, d2, required, holding, windows, package_pins)
    monkeypatch.setattr(paired, "prepare_inputs", lambda *args, **kwargs: prepared)
    options = dict(parameters=parameters, expected_pins=pins, conditional_exit_scope=wrapper,
        quote_extension=encoded(envelope), raw_episode_quotes=artifact, raw_episode_unit_plan=unit_bytes)
    return inputs, options, view, proof


def test_raw_projection_reaches_both_real_books_and_preserves_full_original_lineage(monkeypatch):
    inputs, options, view, proof = fixture(monkeypatch)
    before, calls, engine = deepcopy((inputs, options)), [], paired.replay_synthetic
    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return engine(*args, **kwargs)
    monkeypatch.setattr(paired, "replay_synthetic", capture)
    def forbidden(*args, **kwargs): pytest.fail("pure raw pair attempted IO")
    for owner, name in ((Path, "open"), (socket, "create_connection"), (sqlite3, "connect")):
        monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    result = paired.paired_replay(*inputs, **options)
    assert before == (inputs, options)
    assert [call[1]["timing_policy"].session_lag for call in calls] == [1, 2]
    assert all(call[1]["raw_quote_view"] == view for call in calls)
    assert all(call[1]["dataset_sha256"] == view.quote_snapshot_sha256 for call in calls)
    assert calls[0][1]["events"] is calls[1][1]["events"]
    assert calls[0][1]["initial_book"] is calls[1][1]["initial_book"]
    assert result["quote_lineage"] == strict_json(view.fingerprint_json)
    assert result["original_quote_snapshot_sha256"] == view.original.quote_snapshot_sha256
    assert result["conditional_component_scope"] == proof
    assert proof["schema"] == bounds.RAW_SCHEMA
    assert result["required_code_dates"][LG][-1] == options["parameters"]["end"]
    assert result["quote_required_code_dates"][LG][-1] == str(view.calendar[160])
    for arm in ("D1", "D2"):
        books = result["replays"][arm]["books"]
        pending = next(book for book in books if book["as_of"] == str(view.calendar[42]))
        assert {lot["code"] for lot in pending["lots"]} == {LG, LX}
        assert all(lot["investment_id"] for lot in pending["lots"])
        assert len({lot["investment_id"] for lot in pending["lots"]}) == 1
        assert all(lot["carried_value"] is not None for lot in pending["lots"])
        assert books[-1]["lots"] == []
        assert result["replays"][arm]["closed_trades"] == 2
    assert result["actual_study_completed"] is result["promotion_allowed"] is False
    encoded(result)


@pytest.mark.parametrize("change", ["missing_quotes", "missing_units", "missing_extension", "missing_proof",
    "ordinary_proof", "raw_pin", "unit_pin", "half_raw_basis", "base_price", "shortened_component",
    "coverage_gap", "coverage_scope"])
def test_raw_pair_refuses_invalid_boundary_before_either_engine(monkeypatch, change):
    inputs, options, view, proof = fixture(monkeypatch)
    if change.startswith("missing_"):
        key = {"missing_quotes": "raw_episode_quotes", "missing_units": "raw_episode_unit_plan",
               "missing_extension": "quote_extension", "missing_proof": "conditional_exit_scope"}[change]
        options[key] = None
    elif change == "ordinary_proof":
        options["conditional_exit_scope"] = encoded(strict_json(options["conditional_exit_scope"])["ordinary_exit_scope"])
        options["expected_pins"]["conditional_exit_scope"] = digest(options["conditional_exit_scope"])
    elif change in ("raw_pin", "unit_pin"):
        options["expected_pins"]["raw_episode_quotes" if change == "raw_pin" else "raw_episode_unit_plan"] = "0" * 64
    elif change in ("half_raw_basis", "base_price"):
        evidence = strict_json(inputs[3])
        evidence["bases"]["old"].update(raw_close="202" if change == "half_raw_basis" else "101",
            adjusted_close="201" if change == "half_raw_basis" else "101")
        inputs[3] = encoded(evidence)
        options["expected_pins"]["basis_evidence"] = digest(inputs[3])
    elif change in ("coverage_gap", "coverage_scope"):
        coverage = strict_json(inputs[2])
        if change == "coverage_gap": coverage["windows"].pop()
        else: coverage["required_scope_sha256"] = view.original.read_scope_sha256
        inputs[2] = encoded(coverage)
        options["expected_pins"]["reviewed_action_coverage"] = digest(inputs[2])
    else:
        wrapper = strict_json(options["conditional_exit_scope"])
        wrapper["component_scope"]["entries"][0]["end_on"] = str(view.calendar[128])
        wrapper["component_scope"]["component_scope_sha256"] = digest(encoded({key: value
            for key, value in wrapper["component_scope"].items() if key != "component_scope_sha256"}))
        options["conditional_exit_scope"] = encoded(wrapper)
        options["expected_pins"]["conditional_exit_scope"] = digest(options["conditional_exit_scope"])
    monkeypatch.setattr(paired, "replay_synthetic", lambda *a, **kw: pytest.fail("invalid raw boundary reached engine"))
    with pytest.raises(ValueError): paired.paired_replay(*inputs, **options)


def test_raw_episode_end_is_not_permission_to_drop_a_still_held_component(monkeypatch):
    states = {(LX, index): "zero" for index in range(129, 161)}
    inputs, options, view, proof = fixture(monkeypatch, states=states)
    assert all(row["end_on"] == str(view.calendar[420]) for row in proof["entries"])
    monkeypatch.setattr(paired, "replay_synthetic", lambda *a, **kw: pytest.fail("short raw scope reached engine"))
    with pytest.raises(ValueError, match="potential raw scope"):
        paired.paired_replay(*inputs, **options)


def test_raw_pair_rejects_a_replay_that_retains_components_past_the_proven_exit(monkeypatch):
    inputs, options, view, proof = fixture(monkeypatch)
    engine = paired.replay_synthetic
    def stale_inventory(*args, **kwargs):
        result = engine(*args, **kwargs)
        candidate = view.calendar[128 + kwargs["timing_policy"].session_lag]
        prior = next(book for book in result.books if book.as_of == view.calendar[128])
        assert prior.lots
        return replace(result, books=tuple(replace(book, lots=prior.lots) if book.as_of == candidate else book
                                           for book in result.books))
    monkeypatch.setattr(paired, "replay_synthetic", stale_inventory)
    with pytest.raises(ValueError, match="component survives its candidate session close"):
        paired.paired_replay(*inputs, **options)


def test_unfilled_origins_cannot_excuse_a_short_raw_original_end_fallback(monkeypatch):
    states = {(LX, index): "zero" for index in range(129, 161)}
    states.update({(LG, 3): "absent", (LG, 4): "absent"})
    inputs, options, view, proof = fixture(monkeypatch, states=states)
    assert all(row["end_on"] == str(view.calendar[420]) for row in proof["entries"])
    monkeypatch.setattr(paired, "replay_synthetic", lambda *a, **kw: pytest.fail("short unfilled raw scope reached engine"))
    with pytest.raises(ValueError, match="potential raw scope"):
        paired.paired_replay(*inputs, **options)


def test_unfilled_exchange_origins_cannot_hide_the_full_successor_raw_obligation(monkeypatch):
    # First obtain the real full composite from the existing spin fixture. Its
    # retained anchor is removed when declaring the unsupported stock exchange.
    states = {(LG, index): "absent" for index in (3, 4, *(i for i in range(42, 421) if i != 50))}
    inputs, options, old_view, _ = fixture(monkeypatch, end=420, states=states)
    ordinary = strict_json(options["conditional_exit_scope"])["ordinary_exit_scope"]
    unit = strict_json(old_view.unit_plan_json)
    unit["episodes"][1]["end"] = str(old_view.calendar[160])
    unit_bytes = encoded(unit)
    absent = synthetic.extract_raw_episode_targets([], dict(service="stk_bydd_trd",
        bas_dd=old_view.calendar[50].strftime("%Y%m%d"), targets=[LG]),
        "2026-10-10T00:00:00+00:00", "9" * 64)[0]
    observations = tuple(absent if (row.code, row.requested_bas_dd) == (LG, absent.requested_bas_dd) else row
        for row in old_view.raw_observations
        if row.code != LX or row.requested_bas_dd <= old_view.calendar[160].strftime("%Y%m%d"))
    artifact = raw.encode_raw_artifact(old_view.base, observations, digest(unit_bytes))
    view = raw.restore_raw_quote_view(old_view.base, artifact, digest(artifact), unit_bytes, digest(unit_bytes))
    coverage, evidence = strict_json(inputs[2]), strict_json(inputs[3])
    spin = coverage["events"][0]
    exchange = {key: spin[key] for key in ("event_id", "status", "code", "isin", "successor_code",
        "successor_isin", "old_basis_id", "new_basis_id", "effective_on", "evidence_ref")}
    exchange.update(kind="compulsory_stock_exchange", ratio="0.4", available_on=spin["new_available_on"])
    coverage.update(events=[exchange], windows=[], noops=[], unresolved=[],
        required_scope_sha256=view.original.read_scope_sha256)
    evidence["bases"].pop("retained")
    evidence["references"] = {key: value for key, value in evidence["references"].items()
                              if key in ("event", "old", "new")}
    evidence["references"]["event"].update(start=spin["effective_on"], finding="verified_compulsory_stock_exchange")
    prepared = paired.prepare_inputs({}, {}, options["parameters"], {})
    terms = core.terms_for(coverage, evidence, prepared.expected_windows, view)
    args = (ordinary, prepared.selections_d1, prepared.selections_d2, prepared.holding_inputs,
        prepared.parameters, prepared.required_code_dates,
        {window["code"]: window["isin"] for window in prepared.expected_windows})
    kwargs = dict(input_sha256=prepared.input_sha256, quotes=view, terms=terms)
    proof = bounds.component_exit_bounds(*args, **kwargs)
    assert all(row["mode"] == "original_end" and row["event_id"] is None
               and row["end_on"] == str(view.calendar[420]) for row in proof["entries"])
    assert next(window for window in proof["windows"] if window["code"] == LX)["end"] == str(view.calendar[420])
    coverage, evidence = core.final_coverage((args, kwargs, (coverage, evidence)), proof)
    inputs[2:] = [encoded(coverage), encoded(evidence)]
    options.update(raw_episode_quotes=artifact, raw_episode_unit_plan=unit_bytes,
        conditional_exit_scope=encoded(dict(schema=bounds.WRAPPER_SCHEMA,
            ordinary_exit_scope=ordinary, component_scope=proof)))
    for role, payload in (("reviewed_action_coverage", inputs[2]), ("basis_evidence", inputs[3]),
            ("raw_episode_quotes", artifact), ("raw_episode_unit_plan", unit_bytes),
            ("conditional_exit_scope", options["conditional_exit_scope"])):
        options["expected_pins"][role] = digest(payload)
    monkeypatch.setattr(paired, "replay_synthetic", lambda *a, **kw: pytest.fail("short successor scope reached engine"))
    with pytest.raises(ValueError, match="potential raw scope"):
        paired.paired_replay(*inputs, **options)
