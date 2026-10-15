"""Pure BR declaration/binding checks use fabricated inputs only."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta

import pytest

from research import activity_control_connection as module
from research import activity_preflight as saved
from research.activity_control_normal_inputs import assemble_control_normal_inputs
from research.activity_control_selection import _rank
from research.activity_timing import SessionLagPolicy
from test_activity_control_normal_inputs import fixture as normal_fixture
import test_activity_control_inputs_preflight as diagnostic_fixture


def tail_fixture():
    data = normal_fixture()
    formation = data["calendar"][60]
    code = next(f"{value:06d}" for value in range(200000, 200100)
                if _rank(("000001", f"{value:06d}"), formation, 20261005)[0] == "000001")
    for rows in (data["population_audit"]["rows"], data["scope"]["population_partition"],
                 data["scope"]["formations"][0]["ranked_numeric_possible"]):
        row = next(row for row in rows if row["code"] == "000002")
        row.update(code=code, isin=f"KR7{code}000")
        if "krx_metadata_provenance" in row:
            row["krx_metadata_provenance"]["code"] = code
    return data


def fixture(*, unknown_first=False, all_reviewed=False):
    data = normal_fixture() if unknown_first else tail_fixture()
    connected = assemble_control_normal_inputs(**data)
    scope = data["scope"]
    for formation in scope["formations"]:
        formation.update(activity_preopening_targets={"D1": ["000001"], "D2": ["000001"]},
                         activity_preopening_k={"D1": 1, "D2": 1})
    if all_reviewed:
        row = next(row for row in connected["dispositions"] if row["normal_input_disposition"] == "unresolved")
        audit = deepcopy(connected["normal_input_audit"][0])
        audit.update(code=row["code"], isin=row["isin"])
        connected["normal_input_audit"].append(audit)
        row.update(normal_input_disposition="eligible", normal_input_exclusion_reasons=[])
    formation = data["calendar"][60]
    bounds = {arm: (module.ControlBoundDeclaration(formation, frozenset(), frozenset(), "a" * 64),)
              for arm in module.ARMS}
    return dict(calendar=data["calendar"], parameters=data["params"], scope=scope,
        population_audit=connected["population_audit"], dispositions=connected["dispositions"],
        normal_audits=connected["normal_input_audit"], bounds=bounds)


def test_complete_source_prefix_keeps_prior_exclusions_and_unknown_tail():
    data = fixture()
    before = deepcopy(data)
    result = module.counted_selections_from_br(**data)
    assert data == before
    for rows in result.values():
        row = rows[0]
        assert row.source_population_count == 6 and len(row.source_population) == 6
        assert row.eligible_codes == ("000001",)
        assert row.resolved_prefix[-1][0:2] == ("000001", "eligible")
        assert any(item[1] == "excluded" for item in row.resolved_prefix)
        assert all(len(item[2]) == 64 for item in row.resolved_prefix)
        assert len(row.resolved_prefix) < row.source_population_count
        assert row.prior_potential_codes == frozenset()


def test_earlier_unknown_requires_new_scope_even_when_later_nominee_passes():
    with pytest.raises(ValueError, match="new scope required: unresolved control prefix"):
        module.counted_selections_from_br(**fixture(unknown_first=True))


def test_zero_ceiling_needs_no_prefix_and_does_not_exclude_unknown_population():
    data = fixture(unknown_first=True)
    for row in data["scope"]["formations"]:
        row["activity_preopening_targets"]["D2"] = []
        row["activity_preopening_k"]["D2"] = 0
        row["activity_preopening_targets"]["D1"] = []
        row["activity_preopening_k"]["D1"] = 0
    result = module.counted_selections_from_br(**data)
    assert all(rows[0].resolved_prefix == () and rows[0].source_population_count == 6 for rows in result.values())
    assert any(row["normal_input_disposition"] == "unresolved" for row in data["dispositions"])


def two_formations():
    data = fixture(all_reviewed=True)
    first = module.counted_selections_from_br(**data)
    calendar = tuple(date(2020, 1, 1) + timedelta(days=index) for index in range(318))
    data["calendar"], data["parameters"]["end"] = calendar, calendar[314].isoformat()
    formation = calendar[186]
    for name, key in (("population_audit", "rows"), ("scope", "population_partition")):
        added = deepcopy(data[name][key])
        for row in added:
            row["formation"] = formation.strftime("%Y%m%d")
        data[name][key].extend(added)
    added = deepcopy(data["dispositions"])
    for row in added:
        row["formation"] = formation.strftime("%Y%m%d")
    data["dispositions"].extend(added)
    added = deepcopy(data["normal_audits"])
    for row in added:
        row["formation"] = formation.isoformat()
        for arm, lag in (("D1", 1), ("D2", 2)):
            policy = SessionLagPolicy(lag, paired=True)
            row["control_arms"][arm].update(decision_on=policy.execution_on(calendar, 186).isoformat(),
                selection_at=policy.selection_at(calendar, 186).isoformat())
    data["normal_audits"].extend(added)
    row = deepcopy(data["scope"]["formations"][0])
    row["formation"] = formation.strftime("%Y%m%d")
    data["scope"]["formations"].append(row)
    data["scope"]["summary"]["source_rows"] *= 2
    for arm in module.ARMS:
        original = data["bounds"][arm][0]
        successors = frozenset(("999999", "888888"))
        original = replace(original, successor_codes=successors)
        post = first[arm][0].possible_origin_codes | successors
        data["bounds"][arm] = (original, module.ControlBoundDeclaration(formation, post, frozenset(), "b" * 64))
    return data


def test_successor_declaration_changes_only_next_prior_and_is_serialized_deterministically():
    data = two_formations()
    result = module.counted_selections_from_br(**data)
    for arm, rows in result.items():
        assert rows[0].prior_potential_codes == frozenset()
        assert rows[1].prior_potential_codes == rows[0].possible_origin_codes | frozenset(("999999", "888888"))
    payload = module.serialize_counted_selections(result)
    assert payload["D1"][1]["prior_potential_codes"] == sorted(result["D1"][1].prior_potential_codes)
    assert saved.encoded(payload) == saved.encoded(module.serialize_counted_selections(result))


@pytest.mark.parametrize("corruption", ["source_drop", "disposition_drop", "duplicate", "isin", "eligibility",
    "unreviewed_pass", "late_normal", "ceiling", "initial_prior", "closure_pin", "geometry"])
def test_population_review_target_and_bound_contradictions_fail_closed(corruption):
    data = fixture()
    if corruption == "source_drop": data["population_audit"]["rows"].pop()
    elif corruption == "disposition_drop": data["dispositions"].pop()
    elif corruption == "duplicate": data["dispositions"].append(deepcopy(data["dispositions"][0]))
    elif corruption == "isin": data["dispositions"][0]["isin"] = "different"
    elif corruption == "eligibility": data["normal_audits"][0]["control_arms"]["D1"]["eligible"] = False
    elif corruption == "unreviewed_pass":
        row = next(row for row in data["dispositions"] if row["normal_input_disposition"] == "unresolved")
        row.update(normal_input_disposition="eligible", normal_input_exclusion_reasons=[])
    elif corruption == "late_normal": data["normal_audits"][0]["control_arms"]["D1"]["selection_at"] = "2099-01-01T08:30:00+09:00"
    elif corruption == "ceiling": data["scope"]["formations"][0]["activity_preopening_k"]["D1"] = 2
    elif corruption == "initial_prior":
        data["bounds"]["D1"] = (replace(data["bounds"]["D1"][0], prior_potential_codes=frozenset(("000001",))),)
    elif corruption == "closure_pin":
        data["bounds"]["D1"] = (replace(data["bounds"]["D1"][0], evidence_sha256="unreviewed"),)
    else: data["parameters"]["slots"] = 19
    with pytest.raises(ValueError): module.counted_selections_from_br(**data)


def test_next_formation_cannot_forget_prior_origins_or_pending_successors():
    data = two_formations()
    rows = data["bounds"]["D1"]
    data["bounds"]["D1"] = (rows[0], replace(rows[1], prior_potential_codes=frozenset()))
    with pytest.raises(ValueError, match="U_prior"):
        module.counted_selections_from_br(**data)


def binding_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic_fixture, "normal_fixture", tail_fixture)
    root, output, runs, spec, store, _, calendar = diagnostic_fixture.fixture(tmp_path, monkeypatch)
    diagnostic_fixture.module.run_preflight(root, root / "spec.json", output, runs, reader=store.__getitem__)
    load = lambda name: saved.strict_json((output / name).read_bytes())
    result, scope = load("result.json"), saved.strict_json(store[next(path for path in store if path.name == "br_scope_result.json")])
    normal, dispositions = load("normal-input-audit.json"), load("control-dispositions.json")
    formation = calendar[60]
    bounds = {arm: (module.ControlBoundDeclaration(formation, frozenset(), frozenset(), "a" * 64),)
              for arm in module.ARMS}
    selections = module.counted_selections_from_br(calendar, spec["parameters"], scope, load("population-audit.json"),
        dispositions, normal, bounds)
    return dict(selections=selections, calendar=calendar, parameters=spec["parameters"], scope=scope,
        dispositions=dispositions, normal_audits=normal, holding_audit=load("holding-input-audit.json"),
        read_scope=load("read-scope.json"), quote_snapshot_sha256=result["quote_snapshot_sha256"],
        activity_snapshot_sha256=result["activity_snapshot_sha256"], read_scope_sha256=result["read_scope_sha256"],
        provisional_exit_scope=load("provisional-exit-scope.json"), producer_input_sha256=result["input_sha256"])


def test_real_synthetic_diagnostic_restores_unchanged_full_nominee_scope_and_original_proof_pins(tmp_path, monkeypatch):
    data = binding_fixture(tmp_path, monkeypatch)
    before = deepcopy(data)
    bound = module.bind_control_holding_scope(**data)
    assert data == before
    assert bound.holding_inputs.quote_snapshot_sha256 == data["quote_snapshot_sha256"]
    assert bound.provisional_exit_scope == data["provisional_exit_scope"]
    assert bound.selections_d1[0].eligible_codes == ("000001",)
    assert bound.selections_d2[0].eligible_codes == ()
    assert bound.nominees_d1[0].eligible_codes == bound.nominees_d2[0].eligible_codes == ("000001",)
    assert bound.original_windows == tuple(dict(code=row["code"], isin=row["isin"], start=row["entry_on"],
        end=row["original_end_on"]) for row in data["provisional_exit_scope"]["entries"])


@pytest.mark.parametrize("corruption", ["audit_hash", "legacy_label", "quote_hash", "scope", "exit", "proof_pins", "outside_origin"])
def test_binding_refuses_changed_diagnostics_relabeling_or_final_origins_outside_nominees(tmp_path, monkeypatch, corruption):
    data = binding_fixture(tmp_path, monkeypatch)
    if corruption == "audit_hash": data["normal_audits"][0]["code"] = "000002"
    elif corruption == "legacy_label": data["read_scope"]["producer_schema"] = "activity-holding-price-preflight-v1"
    elif corruption == "quote_hash": data["quote_snapshot_sha256"] = "f" * 64
    elif corruption == "scope": data["read_scope"]["requested_code_dates"]["000001"].pop()
    elif corruption == "exit": data["provisional_exit_scope"]["entries"][0]["candidate_on"] = None
    elif corruption == "proof_pins": data["producer_input_sha256"]["extra_connector_pin"] = "f" * 64
    else:
        row = data["selections"]["D1"][0]
        # Construct a structurally valid complete prefix with an unsupported new origin.
        ordered = _rank((code for code, _ in row.source_population), row.formation_on, row.seed)
        code = ordered[0]
        assert code != "000001"
        prefix = []
        for candidate in ordered:
            prefix.append((candidate, "eligible" if candidate == code else "excluded", "a" * 64))
            if candidate == code: break
        data["selections"]["D1"] = (replace(row, resolved_prefix=tuple(prefix)),)
    with pytest.raises(ValueError): module.bind_control_holding_scope(**data)
