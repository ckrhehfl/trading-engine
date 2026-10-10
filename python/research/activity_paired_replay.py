"""Pure package preparation and descriptive paired accounting over pinned bytes.

There is no IO, trial logging or actual study execution here. Callers establish
registered byte pins, independent package verification and reviewed evidence.
Original BL source partitions and the full BM holding quote snapshot remain
separate. Optional successor scans form a new, explicitly composite artifact.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from decimal import Decimal

from research import activity_holding_preflight as holding
from research import activity_preflight as saved
from research.activity_book import ActivityBook
from research.activity_exit_bounds import mixed_action_scope, validate_exit_bounds, verify_ordinary_inventory
from research.activity_holding_inputs import HoldingInputs, holding_requirements
from research.activity_holding_restore import restore_holding_inputs
from research.activity_partition_selection import PartitionSelection, selections_from_bl
from research.activity_quote_extension import extend_holding_quotes
from research.activity_replay import replay_synthetic
from research.activity_reviewed_actions import reviewed_actions
from research.activity_timing import SessionLagPolicy
from research.activity_timing_report import paired_timing_report


BM_FILES = ("result.json", "specification.json", "source-manifest.json", "input-manifest.json",
            "read-scope.json", "holding-input-audit.json")
BM_ROLES = {"bm_" + name.removesuffix(".json").replace("-", "_"): name for name in BM_FILES}
BM_RECEIPT_FALSE = (
    "original_source_truth_verified", "database_transaction_verified", "original_BL_verification_receipt_reread",
    "whole_database_hash_verified", "BI_snapshot_equality_checked", "historical_vintage_certified",
    "price_basis_certified", "event_coverage_certified", "returns_computed", "original_source_inputs_opened",
    "database_access", "api_access",
)
BM_RESULT_FALSE = ("returns_computed", "actions_applied", "holding_selection_executed", "event_coverage_certified",
                   "no_event_coverage_certified", "price_basis_certified", "historical_vintage_certified",
                   "historical_eligibility_certified", "promotion_allowed")


def _json_value(value):
    """Preserve Decimal accounting and bare date coordinates in replay output."""
    if isinstance(value, Decimal):
        return str(value)
    if type(value) in (date, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


PACKAGE_ROLES = (*holding.BL_ROLES, "bl_verification", "bj_scope", *BM_ROLES, "bm_verification")
ACTION_ROLES = ("reviewed_action_coverage", "basis_evidence")
CONDITIONAL_ROLE = "conditional_exit_scope"
EXTENSION_ROLE = "quote_extension"
SCHEMA = "activity-paired-replay-v1"


@dataclass(frozen=True)
class PreparedInputs:
    """Validated original full-scope inputs shared by pure consumers.

    The frozen shell contains the caller's unchanged parameters and immutable
    selections/holding adapter. Scope dictionaries are projections, never
    replacement quote fingerprints or proof partitions. No books are computed.
    """

    calendar: tuple[date, ...]
    parameters: dict
    activity_snapshot_sha256: str
    quote_snapshot_sha256: str | None
    read_scope_sha256: str
    selections_d1: tuple[PartitionSelection, ...]
    selections_d2: tuple[PartitionSelection, ...]
    required_code_dates: dict[str, list[str]]
    holding_inputs: HoldingInputs
    expected_windows: tuple[dict, ...]
    input_sha256: dict[str, str]


def prepare_inputs(
    bl_package: dict,
    bm_package: dict,
    parameters: dict,
    expected_pins: dict,
) -> PreparedInputs:
    """Validate 18 pinned package inputs and restore the exact full holding union.

    BL has files (nine filename-to-bytes entries), verification and bj_scope;
    BM has files (six filename-to-bytes entries) and verification. expected_pins
    has exactly PACKAGE_ROLES, excluding action coverage and basis evidence.
    parameters is the caller's frozen reference dictionary. Independent receipts
    and joins establish their declared boundaries, never original source truth.
    This preparation reads no paths, performs no scan and computes no returns.
    """
    require = saved.require
    require(type(bl_package) is dict and set(bl_package) == {"files", "verification", "bj_scope"}
            and type(bm_package) is dict and set(bm_package) == {"files", "verification"}, "exact package bundles required")
    require(type(bl_package["files"]) is dict and set(bl_package["files"]) == set(holding.BL_FILES)
            and type(bm_package["files"]) is dict and set(bm_package["files"]) == set(BM_FILES), "exact output file sets required")
    raw = {**{role: bl_package["files"][name] for role, name in holding.BL_ROLES.items()},
           **{role: bm_package["files"][name] for role, name in BM_ROLES.items()},
           "bl_verification": bl_package["verification"], "bj_scope": bl_package["bj_scope"],
           "bm_verification": bm_package["verification"]}
    require(type(expected_pins) is dict and set(expected_pins) == set(raw), "exact registered byte pins required")
    for role, payload in raw.items():
        saved._pin(expected_pins[role])
        require(type(payload) is bytes and saved.digest(payload) == expected_pins[role], "registered input byte pin mismatch")
    bl_roles = (*holding.BL_ROLES, "bl_verification", "bj_scope")
    decoded = {role: saved.strict_json(raw[role]) for role in bl_roles}
    require(type(parameters) is dict
            and all(type(value) is dict for role, value in decoded.items()
                    if role not in ("bl_input_manifest", "bl_normal_input_audit"))
            and all(type(decoded[role]) is list for role in ("bl_input_manifest", "bl_normal_input_audit")),
            "typed parameters and declarations required")
    bl = {name: decoded[role] for role, name in holding.BL_ROLES.items()}
    bl_hashes = {name: expected_pins[role] for role, name in holding.BL_ROLES.items()}
    receipt, bj = decoded["bl_verification"], decoded["bj_scope"]
    calendar, activity_sha = holding.validate_package(bl, bl_hashes, receipt, bj, parameters)
    selections = {arm: selections_from_bl(bl["result.json"], bl["population-audit.json"],
        bl["normal-input-audit.json"], calendar, parameters, arm) for arm in ("D1", "D2")}
    required = {code: list(days) for code, days in holding_requirements(selections["D1"], selections["D2"],
        calendar, parameters, bj["requested_code_dates"]).items()}
    windows = []
    for arm, rows in selections.items():
        identities = {(date.fromisoformat(signal["formation"]), signal["code"]): signal["isin"]
                      for signal in bl["result.json"]["arms"][arm]["signals"]}
        windows.extend({"code": code, "isin": identities[row.formation_on, code],
                        "start": row.decision_on.isoformat(), "end": parameters["end"]}
                       for row in rows for code in row.eligible_codes)
    bl_package_sha = receipt["package_sha256"]
    # Large BL proof/audit trees are no longer needed. Do not keep them alive
    # alongside the decoded BM typed audit and its restored Decimal panel.
    del bl, decoded, receipt, bj
    decoded = {role: saved.strict_json(raw[role]) for role in (*BM_ROLES, "bm_verification")}
    require(all(type(value) is dict for role, value in decoded.items() if role != "bm_input_manifest")
            and type(decoded["bm_input_manifest"]) is list, "typed BM package declarations required")
    bm = {name: decoded[role] for role, name in BM_ROLES.items()}
    bm_hashes = {name: expected_pins[role] for role, name in BM_ROLES.items()}
    verification = decoded["bm_verification"]
    result, spec, sources, manifest, scope, audit = (bm[name] for name in BM_FILES)
    holding.validate_spec(spec, {"parameters": parameters})
    require(verification.get("verified") is True
            and verification.get("scope") == "BM_new_output_typed_rows_scope_and_internal_lineage_only"
            and verification.get("file_sha256") == bm_hashes
            and verification.get("package_sha256") == saved.digest(holding._canonical([[name, bm_hashes[name]] for name in BM_FILES]))
            and all(verification.get(name) is False for name in BM_RECEIPT_FALSE)
            and all(type(verification.get(name)) is int and verification[name] == 0
                    for name in ("D1_completed_studies", "D2_completed_studies")), "independent BM receipt mismatch")
    require(verification.get("evidence_sha256") == {"bl-result.json": bl_hashes["result.json"],
            "bl-read-scope.json": bl_hashes["read-scope.json"], "bj-result.json": expected_pins["bj_scope"]},
            "BM independent upstream evidence links mismatch")
    require(result.get("schema") == holding.SCHEMA and result.get("status") == "holding_price_inputs_audited"
            and result.get("specification_sha256") == bm_hashes["specification.json"]
            and result.get("source_manifest_sha256") == bm_hashes["source-manifest.json"]
            and result.get("read_scope_sha256") == bm_hashes["read-scope.json"]
            and result.get("holding_input_audit_sha256") == bm_hashes["holding-input-audit.json"]
            and result.get("input_manifest") == manifest
            and verification.get("summary") == result.get("summary") == audit.get("summary"), "BM package lineage mismatch")
    require(type(sources.get("files")) is list
            and sources.get("runtime_dependencies") == "stdlib_and_manifested_local_Python_only"
            and all(type(row) is dict for row in sources["files"])
            and any(row.get("sha256") == bm_hashes["specification.json"]
                    and row.get("bytes") == len(raw["bm_specification"]) for row in sources["files"]),
            "BM source specification link mismatch")
    for role in (*holding.BL_ROLES, "bl_verification", "bj_scope"):
        require(spec["inputs"][role]["sha256"] == expected_pins[role], "BM declared upstream pin mismatch")
    roles = (*holding.BL_ROLES, "bl_verification", "bj_scope")
    before = scope.get("input_manifest_before_database")
    require(type(before) is list and len(before) == len(roles) and before == manifest[:len(roles)]
            and all(type(row) is dict and row.get("role") == role
                    and row.get("expected_sha256") == row.get("sha256") == expected_pins[role]
                    and row.get("path") == spec["inputs"][role]["path"] and row.get("read_status") == "hash_verified"
                    and type(row.get("bytes")) is int and row["bytes"] == len(raw[role])
                    for row, role in zip(before, roles)), "BM consumed upstream manifest mismatch")
    require(scope.get("BL_file_sha256") == bl_hashes
            and scope.get("BL_package_sha256") == bl_package_sha
            and scope.get("BL_verification_sha256") == expected_pins["bl_verification"]
            and scope.get("BJ_scope_sha256") == expected_pins["bj_scope"]
            and scope.get("calendar_dates") == [day.strftime("%Y%m%d") for day in calendar]
            and scope.get("scan_database") == spec["inputs"]["scan_database"], "BM read-scope upstream links mismatch")
    quote_sha = result.get("quote_snapshot_sha256")
    require(all(obj.get("activity_snapshot_sha256") == activity_sha for obj in (result, scope, audit, verification))
            and all(obj.get("quote_snapshot_sha256") == quote_sha for obj in (audit, verification)), "activity/quote identifiers mismatch")
    require(all(obj.get("returns_computed") is False for obj in (result, scope))
            and all(result.get(name) is False for name in BM_RESULT_FALSE)
            and all(type(result.get(name)) is int and result[name] == 0
                    for name in ("D1_completed_studies", "D2_completed_studies"))
            and all(result.get(name) is None for name in ("holding_lot_denominator", "action_lot_denominator"))
            and all(type(obj.get(name)) is int and obj[name] == 0
                    for obj in (result, scope) for name in ("new_source_requests", "automatic_retries")), "BM diagnostic boundary mismatch")
    require(saved.encoded(scope.get("requested_code_dates")) == saved.encoded(required), "exact holding requirement union mismatch")
    quotes = restore_holding_inputs(audit, scope, quote_snapshot_sha256=quote_sha,
        activity_snapshot_sha256=activity_sha, read_scope_sha256=bm_hashes["read-scope.json"])
    return PreparedInputs(calendar, parameters, activity_sha, quote_sha, bm_hashes["read-scope.json"],
        selections["D1"], selections["D2"], required, quotes, tuple(windows), dict(expected_pins))


def paired_replay(
    bl_package: dict,
    bm_package: dict,
    reviewed_coverage: bytes,
    basis_evidence: bytes,
    *,
    parameters: dict,
    expected_pins: dict,
    conditional_exit_scope: bytes | None = None,
    quote_extension: bytes | None = None,
) -> dict:
    """Prepare original full inputs and connect reviewed actions to both books.

    All PACKAGE_ROLES plus ACTION_ROLES are exact byte pins. Both arms reuse the
    same parameters, events, original quote snapshot and normalized NAV1 initial
    book at calendar[lookback]. Dates/Decimal accounting are serialized explicitly.
    This pure result does not authorize actual reads, certify evidence or count
    as a logged completed study. Optional quote_extension is a separately pinned
    envelope of a retained audit/read scope. It adds exactly the missing event
    series/anchors under a new composite identity, preserving both scan vintages.
    Independent extension-output/source/unit review remains a caller obligation.
    Optional conditional_exit_scope is a separately pinned price-only child
    proof, recomputed from the full original inputs. Its shorter windows apply
    only to reviewed no-event issues; event/unresolved issues retain the original
    full windows independently. Quote rows and fingerprint are never shortened.
    """
    require = saved.require
    roles = (PACKAGE_ROLES + ACTION_ROLES + (() if conditional_exit_scope is None else (CONDITIONAL_ROLE,))
             + (() if quote_extension is None else (EXTENSION_ROLE,)))
    require(type(expected_pins) is dict and set(expected_pins) == set(roles),
            "exact registered package and action byte pins required")
    prepared = prepare_inputs(bl_package, bm_package, parameters,
                              {role: expected_pins[role] for role in PACKAGE_ROLES})
    actions = {}
    for role, payload in (("reviewed_action_coverage", reviewed_coverage), ("basis_evidence", basis_evidence)):
        saved._pin(expected_pins[role])
        require(type(payload) is bytes and saved.digest(payload) == expected_pins[role],
                "registered action byte pin mismatch")
        actions[role] = saved.strict_json(payload)
        require(type(actions[role]) is dict, "typed action declarations required")
    calendar, activity_sha, quote_sha = (prepared.calendar, prepared.activity_snapshot_sha256,
                                         prepared.quote_snapshot_sha256)
    selections = {"D1": prepared.selections_d1, "D2": prepared.selections_d2}
    quotes = prepared.holding_inputs
    plan, mixed, use_child = None, None, False
    action_windows, action_scope_sha = prepared.expected_windows, prepared.read_scope_sha256
    if conditional_exit_scope is not None:
        saved._pin(expected_pins[CONDITIONAL_ROLE])
        require(type(conditional_exit_scope) is bytes
                and saved.digest(conditional_exit_scope) == expected_pins[CONDITIONAL_ROLE],
                "registered conditional exit byte pin mismatch")
        identities = {}
        for window in prepared.expected_windows:
            code, isin = window["code"], window["isin"]
            require(code not in identities or identities[code] == isin, "conflicting original issue identity")
            identities[code] = isin
        plan = validate_exit_bounds(saved.strict_json(conditional_exit_scope), prepared.selections_d1,
            prepared.selections_d2, quotes, parameters, prepared.required_code_dates, identities,
            input_sha256=prepared.input_sha256)
        mixed = mixed_action_scope(plan, actions["reviewed_action_coverage"])
        use_child = bool(mixed["ordinary_codes"]) or not plan["entries"]
        action_windows = tuple(mixed["windows"])
        action_scope_sha = mixed["action_scope_sha256"]
    quote_lineage = None
    if quote_extension is not None:
        saved._pin(expected_pins[EXTENSION_ROLE])
        require(type(quote_extension) is bytes and saved.digest(quote_extension) == expected_pins[EXTENSION_ROLE],
                "registered quote extension byte pin mismatch")
        extension = saved.strict_json(quote_extension)
        require(type(extension) is dict and set(extension) == {
            "schema", "original_quote_snapshot_sha256", "read_scope", "holding_input_audit"}
            and extension["schema"] == "activity-quote-extension-envelope-v1"
            and extension["original_quote_snapshot_sha256"] == prepared.quote_snapshot_sha256,
            "quote extension original snapshot or schema mismatch")
        audit, scope = extension["holding_input_audit"], extension["read_scope"]
        require(type(audit) is dict and type(scope) is dict, "typed extension audit/scope required")
        component = restore_holding_inputs(audit, scope, quote_snapshot_sha256=audit.get("quote_snapshot_sha256"),
            activity_snapshot_sha256=activity_sha, read_scope_sha256=saved.digest(saved.encoded(scope)))
        quotes = extend_holding_quotes(prepared.holding_inputs, component)
        quote_sha = quotes.quote_snapshot_sha256
        quote_lineage = saved.strict_json(quotes.fingerprint_json)
    events, successor_required = reviewed_actions(actions["reviewed_action_coverage"], actions["basis_evidence"],
        expected_windows=action_windows, calendar=calendar, activity_snapshot_sha256=activity_sha,
        quote_snapshot_sha256=quote_sha, read_scope_sha256=action_scope_sha)
    required_extra = {(code, date.fromisoformat(day)) for code, days in successor_required.items() for day in days}
    bases = actions["basis_evidence"]["bases"].values()
    required_extra.update((row["code"], date.fromisoformat(row["session"])) for row in bases)
    required_extra.difference_update(prepared.holding_inputs.requested_coordinates)
    if quote_extension is None:
        require(not required_extra, "successor requires a separately registered coherent quote snapshot")
    else:
        require(set(component.requested_coordinates) == required_extra,
                "extension must exactly supply separately required successor and basis coordinates")
    positions, panel = {day: index for index, day in enumerate(calendar)}, quotes.panel
    observations = {(row.code, row.observation_date): row for row in quotes.observations}
    for row in actions["basis_evidence"]["bases"].values():
        key = row["code"], date.fromisoformat(row["session"])
        observation = observations.get(key)
        require(key[1] in positions and observation is not None and observation.typed_values is not None
                and not observation.issues and panel[key[0]].closes[positions[key[1]]] == Decimal(row["adjusted_close"]),
                "event adjusted basis must match a requested resolved quote")
    initial = ActivityBook(Decimal(1), (), calendar[parameters["lookback"]])
    replays = {arm: replay_synthetic(calendar, quotes.panel, parameters, selections[arm], dataset_sha256=quote_sha,
        events=events, initial_book=initial, timing_policy=SessionLagPolicy(lag, paired=True))
        for arm, lag in (("D1", 1), ("D2", 2))}
    if mixed is not None:
        verify_ordinary_inventory(replays, mixed)
    report = paired_timing_report(replays["D1"], replays["D2"], selections["D1"], selections["D2"], initial_nav=initial.nav)
    quote_required = {}
    for code, day in quotes.requested_coordinates:
        quote_required.setdefault(code, []).append(day.isoformat())
    return {"schema": SCHEMA, "status": "pure_paired_replay",
            "initial_nav": "1", "activity_snapshot_sha256": activity_sha, "quote_snapshot_sha256": quote_sha,
            "original_quote_snapshot_sha256": prepared.quote_snapshot_sha256, "quote_lineage": quote_lineage,
            "read_scope_sha256": prepared.read_scope_sha256, "input_sha256": dict(expected_pins),
            "action_scope_sha256": action_scope_sha, "conditional_exit_scope": plan,
            "conditional_action_scope": mixed,
            "conditional_scope_used": use_child,
            "reviewed_action_coverage": actions["reviewed_action_coverage"],
            "reviewed_action_evidence": actions["basis_evidence"],
            "required_code_dates": prepared.required_code_dates, "quote_required_code_dates": quote_required,
            "selections": _json_value(selections),
            "replays": _json_value(replays), "report": report,
            "actual_study_completed": False, "promotion_allowed": False,
            "source_truth_certified": False, "event_coverage_certified": False,
            "price_basis_certified": False, "historical_vintage_certified": False}

