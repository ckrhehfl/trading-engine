"""Replay one costed EI policy from received, authenticated research artifacts."""

from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_sources as sources
from research.intraday_context_economic_unit import (
    ECONOMIC_SCHEMA, MAXIMUM_LEDGER_BYTES, compare_economic_artifacts, write_economic_ledger,
)
from research.intraday_context_support_unit import FULL
from research.intraday_ed_inputs import MINUTE
from research.intraday_ed_snapshot_verify import reconstruct_reference_session
from research.intraday_pattern_calibration import session_close, session_open
from research.intraday_scenario_snapshot_unit import BundleSeal


INPUT_CONTRACT = wire.EI_ECONOMIC_INPUT_CONTRACT
INPUT_BOUNDS = wire.EI_ECONOMIC_INPUT_BOUNDS
OUTPUT = "ei-context-economic-ledger.jsonl"
EVALUATION_COORDINATES = 20 * bridge.EH_FORECAST_EVALUATION_SESSIONS


def input_digest(inputs: tuple[bridge.InputFile, ...]) -> str:
    wire.require(type(inputs) is tuple and len(inputs) == len(INPUT_BOUNDS), "fixed EI economic input roster required")
    rows = []
    for item, (name, maximum) in zip(inputs, INPUT_BOUNDS, strict=True):
        wire.require(type(item) is bridge.InputFile and item.name == name and item.maximum == maximum
                     and type(item.size) is int and 0 < item.size <= maximum and wire.valid_sha(item.sha256),
                     "EI economic input size or pin differs")
        rows.append({"name": name, "sha256": item.sha256, "size": item.size})
    return wire.digest(wire.encode(rows))


def compute_received_economics(
    directory: Path, envelope: bridge.InputEnvelope, *, expected_inputs: tuple[bridge.InputFile, ...],
    original_decoded_sha256: dict, snapshot_summary: dict, scenario: str, cost_bps: int,
    execution_policy_sha256: str, invented_only: bool, include_reentry: bool, checkpoint,
) -> bridge.OutputReceipt:
    """Use no file outside the caller's admitted received inputs and scratch."""
    wire.require(type(invented_only) is bool and type(include_reentry) is bool and scenario in ("S1", "S2")
                 and type(original_decoded_sha256) is dict and set(original_decoded_sha256) == {"CC", "CX"}
                 and all(wire.valid_sha(value) for value in original_decoded_sha256.values())
                 and type(envelope) is bridge.InputEnvelope and envelope.manifest == expected_inputs
                 and envelope.input_contract == INPUT_CONTRACT and envelope.geometry == wire.encode(FULL)
                 and envelope.stream_sha256 == input_digest(expected_inputs), "EI economic admitted inputs differ")
    files = {item.name: item for item in expected_inputs}
    paths = {name: directory / "received" / name for name in files}
    for name, item in files.items():
        wire.require(wire.file_digest(paths[name], item.maximum, checkpoint) == (item.sha256, item.size),
                     "EI economic received bytes differ")
    cb = files["CB"]
    universe = sources.load_universe(bridge.bounded_read(paths["CB"], cb.maximum), cb.sha256,
                                     expected_sessions=FULL["sessions"])
    archive_pins = {kind: (paths[kind], sources.RawArchivePin(kind, files[kind].sha256, original_decoded_sha256[kind],
        FULL[kind.lower() + "_chunks"], FULL[kind.lower() + "_rows"],
        0 if kind == "CC" else FULL["cx_daily_rows"])) for kind in ("CC", "CX")}
    index = directory / "raw-byte-chunks.sqlite"
    index_seal = sources.ByteChunkIndex.build(index, archive_pins, checkpoint)
    snapshot, forecast = files["EI-SNAPSHOT"], files["EI-FORECAST"]
    snapshot_pin, forecast_pin = BundleSeal(snapshot.sha256, snapshot.size), BundleSeal(forecast.sha256, forecast.size)
    source_sha = wire.digest(wire.encode(index_seal["archives"]))
    with sources.audit_index(index, archive_pins, checkpoint, expected_cache_sha256=index_seal["sha256"]) as reader:
        def chunks(day, symbol):
            return tuple(reader.load_raw_chunk(kind, symbol, day) for kind in ("CC", "CX"))

        @lru_cache(maxsize=128)
        def session(day, symbol):
            return sources.full_reference_session_inputs(symbol, day, *chunks(day, symbol))

        def reference(day, symbol):
            opening, closing = session_open(day), session_close(day)
            starts = tuple(opening + i * MINUTE for i in range((closing - opening) // MINUTE))
            return reconstruct_reference_session(symbol, day, *chunks(day, symbol), requested_starts=starts)

        def visible(day, symbol, at):
            return sources.reference_session_inputs(symbol, day, *chunks(day, symbol), requested_starts=(at,)).minutes.get(at)

        result = compare_economic_artifacts(directory, paths["EI-SNAPSHOT"], paths["EI-FORECAST"],
            snapshot_pin=snapshot_pin, forecast_pin=forecast_pin, universe=universe, snapshot_summary=snapshot_summary,
            scenario=scenario, cost_bps=cost_bps, execution_policy_sha256=execution_policy_sha256,
            training_source_sha256=source_sha, include_reentry=include_reentry, read_visible=visible,
            read_training_session=session, read_reference_training_session=reference, read_evaluation_session=session,
            checkpoint=checkpoint)
        reader.assert_unchanged()
    seal = write_economic_ledger(directory / OUTPUT, result, scenario=scenario, cost_bps=cost_bps,
                                snapshot_pin=snapshot_pin, forecast_pin=forecast_pin, checkpoint=checkpoint)
    wire.require(seal.size <= MAXIMUM_LEDGER_BYTES and result.report["entry"]["original_evaluation_coordinates"]
                 == EVALUATION_COORDINATES, "EI economic original coordinate output differs")
    for name, item in files.items():
        wire.require(wire.file_digest(paths[name], item.maximum, checkpoint) == (item.sha256, item.size),
                     "EI economic input changed during replay")
    manifest = [item.metadata() for item in expected_inputs]
    proof = {"schema": ECONOMIC_SCHEMA, "verified": True, "full_eof_verified": True,
             "invented_only": invented_only, "input_manifest": manifest,
             "input_manifest_sha256": wire.digest(wire.encode(manifest)), "scenario": scenario,
             "cost_bps": cost_bps, "include_reentry": include_reentry, "folds": len(result.folds),
             "original_evaluation_coordinates": EVALUATION_COORDINATES,
             "initial_model": "context_sequence", "event_level": True, "account_return": False,
             "entry_decision_seals": [asdict(row.decision_seal) for row in result.folds],
             "training_source_sha256": source_sha, "execution_policy_sha256": execution_policy_sha256,
             "saved_ledger_audit_complete": False}
    return bridge.OutputReceipt(seal.sha256, seal.size, envelope.geometry, wire.encode(proof),
                                wire.encode({scenario: {"original": EVALUATION_COORDINATES}}))
