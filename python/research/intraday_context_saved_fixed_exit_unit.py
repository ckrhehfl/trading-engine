"""Build a saved EI fixed-exit comparison in causal order from pinned files.

The lifecycle caller supplies independently authenticated predecessor seals.
This worker has no discovery, receipt admission, market reader or launch path.
"""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research.intraday_context_economic_binding import POLICY_SHA256
from research.intraday_context_economic_fold import DecisionSeal, _plain, seal_entry_decisions
from research.intraday_context_economic_saved import _decision_coordinate_bytes
from research.intraday_context_economic_unit import ECONOMIC_SCHEMA, MAXIMUM_LEDGER_BYTES
from research.intraday_context_entry_compare import _static_outcome
from research.intraday_context_entry_roster import build_entry_roster
from research.intraday_context_forecast_seal import read_forecasts
from research.intraday_context_forecast_unit import SCHEMA as FORECAST_SCHEMA, _original
from research.intraday_context_forecast_unit_reference import _unpack
from research.intraday_context_saved_decision_codec import decode_saved_entry_pair
from research.intraday_context_saved_fixed_exit import prepare_saved_fixed_exit
from research.intraday_context_saved_fixed_exit_report import join_saved_fixed_exit_row, summarize_saved_fixed_exit
from research.intraday_context_snapshot import _time, read_features
from research.intraday_context_snapshot_unit import open_bundle
from research.intraday_ed_sources import load_universe
from research.intraday_scenario_snapshot_unit import BundleSeal, _sealed_input, _sealed_output


DECISION_SCHEMA = "EI-saved-fixed-exit-decisions-v1"
OUTPUT_SCHEMA = "EI-saved-fixed-exit-comparison-v1"
DECISIONS = "ei-saved-fixed-exit-decisions.jsonl"
OUTPUT = "ei-saved-fixed-exit-comparison.jsonl"
SCENARIO = "S2"
GEOMETRY = {"sessions": 257, "evaluation_sessions": 157, "evaluation_coordinates": 3140, "folds": 5}


@dataclass(frozen=True)
class SavedFixedExitArtifacts:
    decisions: DecisionSeal
    output: BundleSeal
    report: dict


@contextmanager
def _verified_rows(path, seal, maximum_line, checkpoint):
    wire.require(wire.valid_sha(seal.sha256) and type(seal.size) is int and 0 < seal.size <= MAXIMUM_LEDGER_BYTES,
                 "EI saved fixed-exit invalid input seal")
    digest = sha256()
    with _sealed_input(path) as (stream, size):
        wire.require(size == seal.size, "EI saved fixed-exit input byte count differs")

        def rows():
            while raw := stream.readline(maximum_line + 1):
                checkpoint("ei_saved_fixed_exit_read_record")
                wire.require(len(raw) <= maximum_line and raw.endswith(b"\n"), "EI saved fixed-exit record bound differs")
                digest.update(raw)
                yield bridge.canonical(raw)

        iterator = rows()
        yield iterator
        wire.require(next(iterator, None) is None and digest.hexdigest() == seal.sha256,
                     "EI saved fixed-exit input hash or EOF differs")


def _next_row(rows):
    row = next(rows, None)
    wire.require(type(row) is dict, "EI saved fixed-exit missing record")
    return row


def _same(left, right, what):
    wire.require(wire.encode(left) == wire.encode(right), "EI saved fixed-exit " + what + " differs")


def _static(arm):
    return None if arm is None else [arm.status, arm.reason]


def _replacement_fields(original, forecast, *, forecast_sha256):
    decision = prepare_saved_fixed_exit(original, forecast, forecast_sha256=forecast_sha256)
    value = decision.alternate_value
    baseline = _static_outcome(original.opportunity, original.baseline, original.baseline_unavailable, "baseline")
    return {"static": _static(decision.static_outcome), "baseline_static": _static(baseline),
            "expected_net": None if value is None or value.expected_net is None else str(value.expected_net),
            "request_sha256": None if decision.static_outcome is not None else
                wire.digest(wire.encode(_plain(asdict(decision.alternate_step))))}


def _fold_decisions(path, seal, stored, fold, expected, original_union_coordinates, checkpoint,
                    *, fields=_replacement_fields):
    predictions = {(row.session, row.symbol, row.question_id): row for row in stored.forecast.predictions}
    wire.require(len(predictions) == len(stored.forecast.predictions), "EI saved fixed-exit duplicated forecasts")
    roster_digest = sha256()
    with _verified_rows(path, seal, wire.MIB, checkpoint) as rows:
        document = _next_row(rows)
        wire.require(set(document) == {"schema", "header"} and document["schema"] == "ei-context-entry-decisions-draft-v1",
                     "EI saved fixed-exit original decision schema differs")
        header = document["header"]
        expected_header = {"scenario": SCENARIO, "fold": _plain(asdict(fold)), "feature_sha256": stored.feature_seal.sha256,
                           "forecast_sha256": stored.seal.sha256, "execution_policy_sha256": POLICY_SHA256, "cost_bps": 33,
                           "original_union_coordinates": original_union_coordinates, "coordinates": len(expected)}
        wire.require(set(header) == set(expected_header) | {"value_model_sha256", "question_roster_sha256"}
                     and wire.valid_sha(header["value_model_sha256"]) and wire.valid_sha(header["question_roster_sha256"])
                     and len(expected) == seal.coordinates, "EI saved fixed-exit decision header or count differs")
        _same({key: header[key] for key in expected_header}, expected_header, "original decision binding")
        for coordinate, projected in zip(expected, stored.forecast.original_test_coordinates, strict=True):
            saved = _next_row(rows)
            wire.require(set(saved) == {"coordinate"}, "EI saved fixed-exit original decision record differs")
            row = saved["coordinate"]
            wire.require(set(row) == {"roster", "opening", "closing", "decisions"}, "EI saved fixed-exit coordinate shape differs")
            _same([row["roster"], row["opening"], row["closing"]],
                  [_plain(asdict(coordinate)), _time(projected.opening), _time(projected.closing)], "coordinate roster")
            session, symbol = coordinate.session, coordinate.symbol
            output = {"fold": fold.index, "session": session, "symbol": symbol,
                      "coordinate_status": coordinate.coordinate_status, "original_frames": coordinate.original_frames,
                      "questions": []}
            for raw, opportunity in zip(row["decisions"], coordinate.questions, strict=True):
                key = opportunity.question_id
                forecast = predictions.pop((session, symbol, key), None)
                wire.require(forecast is not None, "EI saved fixed-exit forecast roster differs")
                original = decode_saved_entry_pair(raw, opportunity, scenario=SCENARIO,
                    forecast_sha256=stored.seal.sha256, execution_policy_sha256=POLICY_SHA256)
                value = None if original.selected is None else original.selected.entry_value
                wire.require(value is None or value.model_sha256 == header["value_model_sha256"],
                             "EI saved fixed-exit payoff model pin differs")
                feature = opportunity.feature
                output["questions"].append({"question_sha256": key, "status": opportunity.status,
                    "stage": feature.question.stage, "decision_at": _time(feature.forecast_at),
                    "episode_origin_context": feature.question.episode_origin_context,
                    **fields(original, forecast, forecast_sha256=stored.seal.sha256)})
            roster_digest.update(_decision_coordinate_bytes(output))
            yield output
        wire.require(not predictions and roster_digest.hexdigest() == header["question_roster_sha256"],
                     "EI saved fixed-exit sealed original question roster differs")


def _outcome_rows(path, outcome_pin, sealed_path, sealed, binding, outcome_header, expected_coordinates, checkpoint):
    # This is the only evaluation-ledger access and is called after the durable
    # seal and its readback. Both streams are exhausted before a receipt exists.
    with (_verified_rows(sealed_path, sealed, wire.MIB, checkpoint) as decisions,
          _verified_rows(path, outcome_pin, 4 * wire.MIB, checkpoint) as originals):
        _same(_next_row(decisions), binding, "new sealed decision binding")
        header = _next_row(originals)
        _same({key: header[key] for key in outcome_header}, outcome_header, "original ledger header")
        for fold, day, symbol in expected_coordinates:
            row = _next_row(decisions)
            _same([row["fold"], row["session"], row["symbol"]], [fold, day, symbol], "sealed coordinate roster")
            yield join_saved_fixed_exit_row(row, _next_row(originals))
        _same(_next_row(originals), {"records": len(expected_coordinates)}, "original ledger trailer")


def _write_results(destination, rows, binding, checkpoint):
    digest = sha256()
    size = 0
    verified = []
    with _sealed_output(destination) as stream:
        def write(value):
            nonlocal size
            raw = wire.encode(value)
            size += len(raw)
            wire.require(len(raw) <= 4 * wire.MIB and size <= MAXIMUM_LEDGER_BYTES, "EI saved fixed-exit result bound exceeded")
            wire.write_all(stream, raw)
            digest.update(raw)

        write(binding)
        for row in rows:
            verified.append(row)
            write(row)
        report = summarize_saved_fixed_exit(verified)
        write({"records": len(verified), "report": report})
    seal = BundleSeal(digest.hexdigest(), size)
    wire.require(wire.file_digest(destination, MAXIMUM_LEDGER_BYTES, checkpoint) == (seal.sha256, seal.size),
                 "EI saved fixed-exit output readback differs")
    return seal, report


def _seal_and_join(directory, causal_rows, expected, binding, outcome_path, outcome_pin, outcome_header, checkpoint):
    def records():
        yield wire.encode(binding)
        for (fold, day, symbol), row in zip(expected, causal_rows, strict=True):
            _same([row["fold"], row["session"], row["symbol"]], [fold, day, symbol], "causal coordinate order")
            yield wire.encode(row)

    sealed_path = directory / DECISIONS
    decisions = seal_entry_decisions(sealed_path, records(), checkpoint)
    wire.require(decisions.coordinates == len(expected), "EI saved fixed-exit all original coordinates required")
    results = _outcome_rows(outcome_path, outcome_pin, sealed_path, decisions, binding, outcome_header, expected, checkpoint)
    output, report = _write_results(directory / OUTPUT, results,
        {"schema": OUTPUT_SCHEMA, "decision_seal": asdict(decisions), "predecessor": binding}, checkpoint)
    return SavedFixedExitArtifacts(decisions, output, report)


@contextmanager
def _causal_inputs(directory, snapshot_path, snapshot_pin, forecast_path, forecast_pin, original_decisions,
                   universe, snapshot_summary, checkpoint, fields):
    members = snapshot_summary["members"]
    calendar, _ = _original(universe, members, snapshot_summary)
    wire.require(len(original_decisions) == len(calendar.folds) == 5, "EI saved fixed-exit requires five sealed original folds")
    with TemporaryDirectory(prefix="ei-saved-fixed-exit-members-", dir=directory) as temporary:
        scratch = Path(temporary)
        feature_path, feature_seal, _, label_seal = open_bundle(snapshot_path, scratch, checkpoint,
            expected_sha256=snapshot_pin.sha256, expected_bytes=snapshot_pin.size)
        _same({"features": asdict(feature_seal), "labels": asdict(label_seal)}, members, "snapshot member seals")
        forecast, folds = _unpack(forecast_path, scratch, forecast_pin.sha256, forecast_pin.size, members, checkpoint)
        _same([forecast["schema"], forecast["scenario"], forecast["adapter_mode"], forecast["contains_economic_returns"],
               forecast["original_universe_sha256"], forecast["audited_snapshot_summary_sha256"]],
              [FORECAST_SCHEMA, SCENARIO, "fixed_native", False, universe.raw_sha256,
               wire.digest(wire.encode(snapshot_summary))], "forecast binding")
        projected = tuple(read_features(feature_path, feature_seal, expected_targets=universe.targets))
        roster = build_entry_roster(projected, expected_targets=universe.targets, scenario=SCENARIO,
                                    source_sha256=feature_seal.sha256)
        originals = {(row.session, row.symbol): row for row in projected}
        expected = tuple(sorted((fold.index, row.session, row.symbol) for fold in calendar.folds
                                for row in roster.coordinates if row.session in fold.test_sessions))

        def causal_rows():
            for fold, (path, pin), (source, seal) in zip(calendar.folds, folds, original_decisions, strict=True):
                days = frozenset(fold.test_sessions)
                expected = tuple(row for row in roster.coordinates if row.session in days)
                test = tuple(originals[row.session, row.symbol] for row in expected)
                stored = read_forecasts(path, pin, feature_seal=feature_seal, label_seal=label_seal,
                    expected_coordinates=test, expected_fold=fold, expected_scenario=SCENARIO)
                yield from _fold_decisions(source, seal, stored, fold, expected, roster.original_union_coordinates, checkpoint,
                                           fields=fields)

        yield causal_rows(), expected


def _decision_binding(snapshot_pin, forecast_pin, original_decisions, outcome_pin):
    return {"schema": DECISION_SCHEMA, "scenario": SCENARIO, "snapshot": asdict(snapshot_pin),
            "forecast": asdict(forecast_pin), "original_decisions": [asdict(seal) for _, seal in original_decisions],
            "original_ledger": asdict(outcome_pin), "cost_bps": 33, "replacement": "context_lightgbm"}


def compare_saved_fixed_exit(directory: Path, *, snapshot_path: Path, snapshot_pin: BundleSeal,
                            forecast_path: Path, forecast_pin: BundleSeal, original_decisions: tuple[tuple[Path, DecisionSeal], ...],
                            outcome_path: Path, outcome_pin: BundleSeal, universe, snapshot_summary: dict,
                            checkpoint) -> SavedFixedExitArtifacts:
    """Consume a single fixed S2 predecessor roster and output three fixed-exit arms."""
    binding = _decision_binding(snapshot_pin, forecast_pin, original_decisions, outcome_pin)
    outcome_header = {"schema": ECONOMIC_SCHEMA, "scenario": SCENARIO, "cost_bps": 33,
                      "snapshot": asdict(snapshot_pin), "forecast": asdict(forecast_pin),
                      "entry_decision_seals": binding["original_decisions"]}
    with _causal_inputs(directory, snapshot_path, snapshot_pin, forecast_path, forecast_pin, original_decisions,
                        universe, snapshot_summary, checkpoint, _replacement_fields) as (causal_rows, expected):
        return _seal_and_join(directory, causal_rows, expected, binding, outcome_path, outcome_pin, outcome_header, checkpoint)


def _verify_sealed_decisions(path, seal, binding, causal_rows, expected, checkpoint):
    wire.require(seal.coordinates == len(expected), "EI saved fixed-exit independent decision count differs")
    questions = 0
    with _verified_rows(path, seal, wire.MIB, checkpoint) as saved:
        _same(_next_row(saved), binding, "independent decision header")
        for (fold, day, symbol), row in zip(expected, causal_rows, strict=True):
            _same([row["fold"], row["session"], row["symbol"]], [fold, day, symbol], "independent causal coordinate")
            _same(_next_row(saved), row, "independent decision replay")
            questions += len(row["questions"])
    return {"verified": True, "coordinates": len(expected), "questions": questions, "decision_sha256": seal.sha256}


def audit_saved_fixed_exit_decisions(directory: Path, *, snapshot_path: Path, snapshot_pin: BundleSeal,
                                    forecast_path: Path, forecast_pin: BundleSeal,
                                    original_decisions: tuple[tuple[Path, DecisionSeal], ...], outcome_pin: BundleSeal,
                                    universe, snapshot_summary: dict, decision_seal: DecisionSeal, checkpoint) -> dict:
    """Rebuild the saved decisions from authenticated inputs without opening the payoff ledger."""
    from research.intraday_context_saved_fixed_exit_causal_reference import reference_fixed_exit_fields

    binding = _decision_binding(snapshot_pin, forecast_pin, original_decisions, outcome_pin)
    with _causal_inputs(directory, snapshot_path, snapshot_pin, forecast_path, forecast_pin, original_decisions,
                        universe, snapshot_summary, checkpoint, reference_fixed_exit_fields) as (causal_rows, expected):
        return _verify_sealed_decisions(directory / DECISIONS, decision_seal, binding, causal_rows, expected, checkpoint)


def saved_fixed_exit_input_digest(files: tuple[bridge.InputFile, ...]) -> str:
    wire.require(type(files) is tuple and all(type(item) is bridge.InputFile for item in files),
                 "EI saved fixed-exit immutable input manifest required")
    rows = wire.validate_registered_input_manifest([item.metadata() for item in files],
        input_contract=wire.EI_SAVED_FIXED_EXIT_INPUT_CONTRACT)
    return wire.digest(wire.encode(rows))


def compute_received_saved_fixed_exit(directory: Path, envelope: bridge.InputEnvelope, *,
                                      expected_inputs: tuple[bridge.InputFile, ...],
                                      original_decision_seals: tuple[DecisionSeal, ...], snapshot_summary: dict,
                                      invented_only: bool, checkpoint) -> bridge.OutputReceipt:
    """Produce and independently verify one already admitted saved comparison."""
    wire.require(type(invented_only) is bool and type(envelope) is bridge.InputEnvelope
                 and envelope.manifest == expected_inputs and envelope.geometry == wire.encode(GEOMETRY)
                 and envelope.input_contract == wire.EI_SAVED_FIXED_EXIT_INPUT_CONTRACT
                 and envelope.stream_sha256 == saved_fixed_exit_input_digest(expected_inputs),
                 "EI saved fixed-exit received envelope differs")
    cb, snapshot, forecast, *decisions, outcome = expected_inputs
    wire.require(type(original_decision_seals) is tuple and len(original_decision_seals) == GEOMETRY["folds"]
                 and all(type(seal) is DecisionSeal and (seal.sha256, seal.size) == (item.sha256, item.size)
                         and type(seal.coordinates) is int and seal.coordinates > 0
                         for item, seal in zip(decisions, original_decision_seals, strict=True))
                 and sum(seal.coordinates for seal in original_decision_seals) == GEOMETRY["evaluation_coordinates"],
                 "EI saved fixed-exit received predecessor decision seals differ")
    received = directory / "received"
    cb_raw = bridge.bounded_read(received / cb.name, cb.maximum)
    wire.require(len(cb_raw) == cb.size, "EI saved fixed-exit received CB size differs")
    universe = load_universe(cb_raw, cb.sha256, expected_sessions=GEOMETRY["sessions"])
    causal = {"snapshot_path": received / snapshot.name, "snapshot_pin": BundleSeal(snapshot.sha256, snapshot.size),
              "forecast_path": received / forecast.name, "forecast_pin": BundleSeal(forecast.sha256, forecast.size),
              "original_decisions": tuple((received / item.name, seal) for item, seal in
                                          zip(decisions, original_decision_seals, strict=True)),
              "outcome_pin": BundleSeal(outcome.sha256, outcome.size), "universe": universe,
              "snapshot_summary": snapshot_summary, "checkpoint": checkpoint}
    result = compare_saved_fixed_exit(directory, outcome_path=received / outcome.name, **causal)
    checked = audit_saved_fixed_exit_decisions(directory, decision_seal=result.decisions, **causal)
    wire.require(checked["verified"] is True and checked["decision_sha256"] == result.decisions.sha256
                 and checked["coordinates"] == result.decisions.coordinates == GEOMETRY["evaluation_coordinates"]
                 and result.report["original_evaluation_coordinates"] == GEOMETRY["evaluation_coordinates"]
                 and result.report["original_evaluation_sessions"] == GEOMETRY["evaluation_sessions"],
                 "EI saved fixed-exit independent full geometry differs")
    proof = {"schema": OUTPUT_SCHEMA, "verified": True, "full_eof_verified": True, "full_geometry": True,
             "all_original_coordinates_verified": True, "invented_only": invented_only, "scenario": SCENARIO,
             "cost_bps": 33, "replacement": "context_lightgbm", "input_manifest_sha256": envelope.stream_sha256,
             "snapshot_summary_sha256": wire.digest(wire.encode(snapshot_summary)), "decision_seal": asdict(result.decisions),
             "causal_replay": checked, "account_return": False}
    return bridge.OutputReceipt(result.output.sha256, result.output.size, envelope.geometry, wire.encode(proof),
        wire.encode({SCENARIO: {"original": GEOMETRY["evaluation_coordinates"]}}))
