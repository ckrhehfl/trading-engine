"""Replay EI economics from admitted immutable forecast and snapshot artifacts.

The lifecycle caller authenticates the releases, raw minute providers and
artifact pins before calling. This worker cannot select or admit a new run.
"""

from dataclasses import asdict, dataclass
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from research import intraday_campaign_wsl_transport as wire
from research import intraday_chart_cash as cash
from research import intraday_context_snapshot as snapshot
from research.intraday_context_economic_fold import (
    EconomicFoldResult, _plain, fit_economic_entry_fold, run_economic_fold, seal_entry_decisions,
)
from research.intraday_context_entry_report import summarize_matched_holding
from research.intraday_context_entry_roster import build_entry_roster
from research.intraday_context_forecast_seal import read_forecasts
from research.intraday_context_forecast_unit import SCHEMA, _LabelIndex, _feature_stream, _original
from research.intraday_context_forecast_unit_reference import _unpack
from research.intraday_context_snapshot_unit import open_bundle
from research.intraday_scenario_snapshot_unit import BundleSeal, _sealed_input, _sealed_output


@dataclass(frozen=True)
class EconomicArtifactsResult:
    report: dict
    training_counts: tuple[dict, ...]
    folds: tuple[EconomicFoldResult, ...]


ECONOMIC_SCHEMA = "EI-context-economic-comparison-v1"
MAXIMUM_LEDGER_BYTES = 128 * wire.MIB


def _execution(row):
    payoff, fill = row.payoff, row.fill
    return {"status": row.status, "reason": row.reason,
            "gross": None if payoff is None else payoff.gross,
            "cost": None if payoff is None else payoff.cost,
            "net": None if payoff is None else payoff.net,
            "entry_notional": None if payoff is None else payoff.entry_notional,
            "fill": None if fill is None else {"outcome": fill.outcome, "quantity": fill.quantity,
                                               "reason": fill.reason, "cost": fill.assumed_cost},
            "holding": None if row.holding is None else _holding(row.holding)}


def _holding(replay):
    return {"status": replay.status, "reason": replay.reason, "cash": replay.episode.book.cash,
            "realized_exits": [asdict(leg) for leg in replay.episode.book.realized_exits],
            "events": [asdict(event) for event in replay.events]}


def _case(opportunity, pair, management, audit):
    arms = None
    if management is not None:
        arms = {name: _holding(getattr(management, name)) for name in ("fixed", "chart", "chart_context")}
        if management.reentry is not None:
            extra = management.reentry.fill
            arms["reentry"] = {**_holding(management.reentry), "extra_modeled_fill": extra is not None and extra.filled}
        arms["independent_net_by_arm"] = audit["net_by_arm"]
    return {"question_sha256": opportunity.question_id, "status": opportunity.status,
            "stage": opportunity.feature.question.stage, "decision_at": opportunity.feature.forecast_at,
            "episode_origin_context": opportunity.feature.question.episode_origin_context,
            "baseline": _execution(pair.baseline), "selected": _execution(pair.selected),
            "selected_minus_baseline_net": pair.net_delta, "same_fill_management": arms}


def write_economic_ledger(path: Path, result: EconomicArtifactsResult, *, scenario: str, cost_bps: int,
                          snapshot_pin: BundleSeal, forecast_pin: BundleSeal, checkpoint) -> BundleSeal:
    """Persist the independent-checked original coordinate and execution ledger."""
    wire.require(type(result) is EconomicArtifactsResult
                 and tuple(row.entry.prepared.fold.index for row in result.folds) == (1, 2, 3, 4, 5)
                 and type(snapshot_pin) is type(forecast_pin) is BundleSeal and callable(checkpoint),
                 "EI economic ledger requires all five original folds")
    header = {"schema": ECONOMIC_SCHEMA, "scenario": scenario, "cost_bps": cost_bps,
              "snapshot": asdict(snapshot_pin), "forecast": asdict(forecast_pin), "report": result.report,
              "training": result.training_counts,
              "entry_decision_seals": [asdict(row.decision_seal) for row in result.folds]}
    digest, size, count = sha256(), 0, 0
    with _sealed_output(path) as target:
        def write(row):
            nonlocal size
            checkpoint("ei_economic_write_coordinate_ledger")
            raw = wire.encode(_plain(row))
            size += len(raw)
            wire.require(size <= MAXIMUM_LEDGER_BYTES and len(raw) <= 4 * wire.MIB,
                         "EI economic ledger row or bundle limit exceeded")
            wire.write_all(target, raw)
            digest.update(raw)

        write(header)
        for row in result.folds:
            prepared = row.entry.prepared
            wire.require((prepared.scenario, prepared.cost_bps) == (scenario, cost_bps),
                         "EI economic ledger profile differs")
            for coordinate, entry_audit, cases, audits in zip(row.entry.coordinates, row.entry_audits,
                    row.management, row.management_audits, strict=True):
                original = coordinate.prepared.roster
                write({"fold": prepared.fold.index, "session": original.session, "symbol": original.symbol,
                       "coordinate_status": original.coordinate_status, "original_frames": original.original_frames,
                       "modeled_opens": coordinate.modeled_opens, "entry_independently_verified": entry_audit["verified"],
                       "questions": [_case(pair.decisions.opportunity, pair, management, audit)
                                     for pair, management, audit in zip(coordinate.outcomes, cases, audits, strict=True)]})
                count += 1
        wire.require(count == result.report["entry"]["original_evaluation_coordinates"],
                     "EI economic ledger original evaluation denominator differs")
        write({"records": count})
    wire.require(wire.file_digest(path, MAXIMUM_LEDGER_BYTES, checkpoint) == (digest.hexdigest(), size),
                 "EI economic ledger readback differs")
    return BundleSeal(digest.hexdigest(), size)


def compare_economic_artifacts(
    directory: Path, snapshot_path: Path, forecast_path: Path, *, snapshot_pin: BundleSeal,
    forecast_pin: BundleSeal, universe, snapshot_summary: dict, scenario: str, cost_bps: int,
    training_source_sha256: str, execution_policy_sha256: str, include_reentry: bool,
    read_visible, read_training_session, read_reference_training_session, read_evaluation_session, checkpoint,
) -> EconomicArtifactsResult:
    """Use sealed forecasts without refitting or choosing a model from its scores."""
    wire.require(scenario in ("S1", "S2") and type(cost_bps) is int and cost_bps in (33, 43)
                 and type(include_reentry) is bool and type(snapshot_pin) is type(forecast_pin) is BundleSeal
                 and snapshot_path != forecast_path and wire.valid_sha(training_source_sha256)
                 and wire.valid_sha(execution_policy_sha256)
                 and all(callable(fn) for fn in (read_visible, read_training_session, read_reference_training_session,
                                                read_evaluation_session, checkpoint)),
                 "fixed EI economic artifacts or policy differ")
    members = snapshot_summary["members"]
    calendar, groups = _original(universe, members, snapshot_summary)
    book, risk = cash.new_book("EI-" + scenario), Fraction(100_000)
    results, training_counts = [], []
    with TemporaryDirectory(prefix="ei-economic-members-", dir=directory) as temporary:
        scratch = Path(temporary)
        feature_path, feature_seal, label_path, label_seal = open_bundle(snapshot_path, scratch, checkpoint,
            expected_sha256=snapshot_pin.sha256, expected_bytes=snapshot_pin.size)
        wire.require({"features": asdict(feature_seal), "labels": asdict(label_seal)} == members,
                     "EI economic original snapshot seals differ")
        forecast, folds = _unpack(forecast_path, scratch, forecast_pin.sha256, forecast_pin.size, members, checkpoint)
        wire.require(forecast["schema"] == SCHEMA and forecast["scenario"] == scenario
                     and forecast["adapter_mode"] == "fixed_native" and forecast["contains_economic_returns"] is False
                     and forecast["original_universe_sha256"] == universe.raw_sha256
                     and forecast["audited_snapshot_summary_sha256"] == wire.digest(wire.encode(snapshot_summary)),
                     "EI economic predecessor forecast identity differs")
        with _sealed_input(label_path) as (stream, size):
            wire.require(size == label_seal.size, "EI economic sealed label member size differs")
            labels = _LabelIndex(stream, label_path, label_seal, feature_seal, groups, checkpoint)
            coordinates = tuple(_feature_stream(feature_path, feature_seal, universe, labels,
                                                snapshot_summary, checkpoint))
            roster = build_entry_roster(coordinates, expected_targets=universe.targets, scenario=scenario,
                                        source_sha256=feature_seal.sha256)
            own_keys = frozenset((row.session, row.symbol) for row in roster.coordinates)
            original = {(row.session, row.symbol): row for row in coordinates
                        if (row.session, row.symbol) in own_keys}
            for fold, (prediction_path, seal) in zip(calendar.folds, folds, strict=True):
                checkpoint("ei_economic_start_fold")
                training_days, test_days = frozenset(fold.training_sessions), frozenset(fold.test_sessions)
                train = (row for (day, _), row in original.items() if day in training_days)
                test = tuple(row for (day, _), row in original.items() if day in test_days)
                stored = read_forecasts(prediction_path, seal, feature_seal=feature_seal, label_seal=label_seal,
                    expected_coordinates=test, expected_fold=fold, expected_scenario=scenario,
                    expected_adapter_mode="fixed_native")
                read_question = labels.reader(fold=fold.index, phase="training", allowed_dates=fold.training_sessions)

                def read_labels(day, symbol):
                    selected = tuple(read_question(day, symbol, snapshot.question_digest(row.question))
                                     for row in original[day, symbol].questions)
                    wire.require(selected and all(row.status == selected[0].status for row in selected),
                                 "EI economic training label status differs")
                    return snapshot.LabeledCoordinate(day, symbol, selected[0].status, selected)

                parameters = {"book": book, "risk_budget": risk, "cost_bps": cost_bps,
                              "training_source_sha256": training_source_sha256,
                              "execution_policy_sha256": execution_policy_sha256, "checkpoint": checkpoint}
                model, rows, support = fit_economic_entry_fold(train, roster, fold, expected_targets=universe.targets,
                    read_labels=read_labels, read_session=read_training_session,
                    read_reference_session=read_reference_training_session, **parameters)
                destination = directory / f"ei-entry-decisions-fold-{fold.index}.jsonl"
                result = run_economic_fold(roster, stored, fold, model, training_rows=rows, read_visible=read_visible,
                    read_training_session=read_training_session, read_evaluation_session=read_evaluation_session,
                    seal_decisions=lambda records: seal_entry_decisions(destination, records, checkpoint),
                    include_reentry=include_reentry, **parameters)
                results.append(result)
                training_counts.append(support)
            labels.assert_unchanged()
    complete = tuple(results)
    report = summarize_matched_holding(complete, expected_folds=calendar.folds, include_reentry=include_reentry)
    return EconomicArtifactsResult(report, tuple(training_counts), complete)
