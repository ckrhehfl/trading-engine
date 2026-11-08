"""Recount saved EI forecasts and bind numerical receipts to the publication.

The lifecycle auditor supplies verified original receipt bytes, fixed paths,
and snapshot predecessor pins after checking the journal. This read-only audit
of inputs and output (with private scratch) does not admit a run, fit a model,
or replace verification of the publication's process and resource evidence.
"""

from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_forecast_unit_reference as reference
from research.intraday_context_numerical_receipt import verify_numerical_receipt
from research.intraday_context_support_unit import FULL
from research.intraday_ed_sources import load_universe


def _need(condition, message):
    wire.require(condition, "EI forecast saved publication " + message)


def audit_received_forecast(cache: Path, output: Path, scratch: Path, publication_raw: bytes, *,
                            expected_publication_sha256: str, expected_binding: dict,
                            expected_inputs: tuple[bridge.InputFile, ...], snapshot_summary: dict,
                            scenario: str, environment: dict, manifest_raw: bytes,
                            import_proof_raw: bytes, checkpoint) -> dict:
    """Verify stored probabilities, support and scores without loading ML."""
    _need(type(publication_raw) is bytes and 0 < len(publication_raw) <= 65536
          and wire.valid_sha(expected_publication_sha256)
          and wire.digest(publication_raw) == expected_publication_sha256,
          "does not match the original bounded publication")
    publication = bridge.canonical(publication_raw)
    _need(scenario in ("S1", "S2") and callable(checkpoint)
          and type(expected_binding) is dict and type(expected_binding.get("invented_only")) is bool
          and type(expected_inputs) is tuple and len(expected_inputs) == 2
          and all(type(item) is bridge.InputFile for item in expected_inputs),
          "requires a fixed scenario, mode and two pinned predecessor inputs")
    cb, snapshot = expected_inputs
    for item, name, maximum in ((cb, "CB", 16 * wire.MIB), (snapshot, "EI-SNAPSHOT", 256 * wire.MIB)):
        _need(item.name == name and type(item.maximum) is int and item.maximum == maximum
              and type(item.size) is int and 0 < item.size <= maximum and wire.valid_sha(item.sha256),
              "has a different predecessor input bound or pin")
    _need(type(snapshot_summary) is dict and type(snapshot_summary.get("members")) is dict
          and snapshot_summary.get("universe_sha256") == cb.sha256,
          "does not bind the original CB roster to the audited snapshot")
    manifest = [item.metadata() for item in expected_inputs]
    fixed = {"schema": reference.SCHEMA, "verified": True, "invented_only": expected_binding["invented_only"],
        "full_geometry": True, "full_eof_verified": True, "all_original_coordinates_verified": True,
        "input_manifest": manifest, "input_manifest_sha256": wire.digest(wire.encode(manifest)),
        "snapshot_summary_sha256": wire.digest(wire.encode(snapshot_summary)),
        "historical_availability_certified": False, "stored_volume_units_certified": False,
        "contains_economic_returns": False}
    proof = publication.get("independent_verification")
    _need(type(proof) is dict and set(proof) == set(fixed) | {"forecast_summary", "numerical_environment"}
          and wire.encode({key: proof[key] for key in fixed}) == wire.encode(fixed)
          and wire.encode(publication.get("geometry")) == wire.encode(FULL)
          and publication.get("input_contract") == "ei-forecast-audited-snapshot-and-cb-v1",
          "proof, input roster or geometry differs")
    numerical = proof["numerical_environment"]
    _need(type(numerical) is dict and set(numerical) == {"before", "after"}, "lacks numerical before/after receipts")
    numerical_audit = verify_numerical_receipt(manifest_raw, import_proof_raw, publication_raw,
        expected_environment=environment, expected_publication_sha256=expected_publication_sha256,
        expected_binding=expected_binding, before=numerical["before"], after=numerical["after"])

    received = cache / "received"
    cb_raw = bridge.bounded_read(received / cb.name, cb.maximum)
    _need((wire.digest(cb_raw), len(cb_raw)) == (cb.sha256, cb.size), "received CB bytes differ")
    universe = load_universe(cb_raw, cb.sha256, expected_sessions=FULL["sessions"])
    del cb_raw
    snapshot_path = received / snapshot.name
    snapshot_pin = (snapshot.sha256, snapshot.size)
    _need(wire.file_digest(snapshot_path, snapshot.maximum, checkpoint) == snapshot_pin,
          "received snapshot bytes differ")
    audit = reference.audit_forecast_bundle(output / "ei-context-forecast.bundle", snapshot_path, scratch,
        expected_sha256=publication["output_sha256"], expected_bytes=publication["output_bytes"],
        expected_members=snapshot_summary["members"], expected_summary=snapshot_summary, universe=universe,
        scenario=scenario, checkpoint=checkpoint)
    _need(wire.encode(audit["producer_summary"]) == wire.encode(proof["forecast_summary"])
          and wire.encode(publication.get("candidate_counts")) == wire.encode({
              scenario: {"original": audit["original_evaluation_coordinates"]}}),
          "published support or summary differs from the independent recount")
    _need(wire.file_digest(snapshot_path, snapshot.maximum, checkpoint) == snapshot_pin,
          "received snapshot changed during audit")
    return {"saved_forecast": audit, "numerical_receipt": numerical_audit}
