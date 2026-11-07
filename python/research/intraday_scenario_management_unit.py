"""Prepare the pinned EH management sources and check paired accounting."""

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from fractions import Fraction
from functools import lru_cache
from itertools import islice
from pathlib import Path
from tempfile import TemporaryDirectory

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_sources as archive
from research import intraday_scenario_snapshot as snapshot
from research import intraday_scenario_snapshot_unit as snapshot_unit
from research import intraday_scenario_support_unit as price
from research.intraday_ed_inputs import MINUTE
from research.intraday_scenario_management_campaign import ManagementCampaign, run_management_campaign
from research.intraday_scenario_management_entry_reference import verify_initial_entry
from research.intraday_scenario_management_exit_reference import verify_management_exits
from research.intraday_scenario_management_execution import (
    MAX_COORDINATES, _management_header, seal_management_execution, verify_management_execution,
)
from research.intraday_scenario_management_prices_reference import ReadSession
from research.intraday_scenario_management_report import _case_metrics
from research.intraday_scenario_management_report_audit import ModeledOpenReadback, verify_management_report
from research.intraday_scenario_management_saved_cash import saved_management_metrics
from research.intraday_scenario_open_reference import _price_row


SESSION_CACHE_SIZE = 192
CHECKPOINT = Callable[[str], object]
BreakoutLimits = dict[tuple[str, str], tuple[datetime, datetime, Fraction, Fraction]]


@dataclass(frozen=True)
class ManagementSources:
    sessions: tuple[str, ...]
    roster: dict[str, tuple[str, ...]]
    features: Path
    feature_seal: snapshot.SnapshotSeal
    read_session: ReadSession
    read_open: ModeledOpenReadback
    raw_audit: dict
    scenario: str

    def projected(self) -> Iterator[snapshot.ProjectedDay]:
        for expected_day, day in zip(self.sessions, snapshot.read_features(self.features, self.feature_seal), strict=True):
            membership = sorted((member.rank, coordinate.symbol) for coordinate in day.coordinates
                                for member in coordinate.targets if member.scenario == self.scenario)
            wire.require(day.day == expected_day and membership == list(enumerate(self.roster[expected_day], 1)),
                         "EH management sealed feature and original CB rosters differ")
            yield day


@contextmanager
def open_management_sources(
    directory: Path, manifest: tuple[bridge.InputFile, ...], *, cc_decoded_sha256: str,
    expected_members: dict, scenario: str, invented_only: bool, checkpoint: CHECKPOINT,
) -> Iterator[ManagementSources]:
    """Open only received files; callers bind their provenance before the worker runs."""
    expected = (*wire.EH_SUPPORT_INPUT_BOUNDS, ("EH-SNAPSHOT", snapshot_unit.MAX_OUTPUT))
    wire.require(tuple((item.name, item.maximum) for item in manifest) == expected
                 and wire.valid_sha(cc_decoded_sha256)
                 and scenario in ("S1", "S2") and type(invented_only) is bool,
                 "EH management input roster or mode differs")
    received = directory / "received"
    for item in manifest:
        wire.require(wire.file_digest(received / item.name, item.maximum, checkpoint) == (item.sha256, item.size),
                     "EH management received file differs")
    cb, cc, saved = manifest
    full = price.FULL
    universe = archive.load_universe(bridge.bounded_read(received / cb.name, cb.maximum), cb.sha256,
                                     expected_sessions=full["sessions"])
    wire.require(invented_only or (universe.sessions[0], universe.sessions[-1])
                 == (price.WINDOW["start"], price.WINDOW["end"]), "EH management original calendar differs")
    roster = {day: [] for day in universe.sessions}
    for coordinate in universe.coordinates(scenario):
        roster[coordinate.session].append((coordinate.rank, coordinate.symbol))
    expected_ranks = tuple(range(1, full["targets_per_scenario"] // full["sessions"] + 1))
    ranked = {day: tuple(sorted(pairs)) for day, pairs in roster.items()}
    wire.require(all(tuple(rank for rank, _ in pairs) == expected_ranks for pairs in ranked.values()),
                 "EH management original CB ranks differ")
    names = {day: tuple(symbol for _, symbol in pairs) for day, pairs in ranked.items()}
    archives = {"CC": (received / cc.name, archive.RawArchivePin(
        "CC", cc.sha256, cc_decoded_sha256, full["cc_chunks"], full["cc_rows"], 0))}

    with TemporaryDirectory(prefix="eh-management-members-", dir=directory) as scratch:
        unpacked = Path(scratch)
        features, feature_seal, _, label_seal = snapshot_unit.open_bundle(received / saved.name, unpacked, checkpoint)
        wire.require(expected_members == {"features": asdict(feature_seal), "labels": asdict(label_seal)},
                     "EH management audited snapshot member seals differ")
        index = unpacked / "eh-price-byte-chunks.sqlite"
        index_seal = archive.ByteChunkIndex.build(index, archives, checkpoint, price_only=True)
        with archive.audit_index(index, archives, checkpoint, expected_cache_sha256=index_seal["sha256"],
                                 price_only=True) as reader:
            @lru_cache(maxsize=SESSION_CACHE_SIZE)
            def read_session(day, symbol):
                checkpoint("eh_management_received_session")
                wire.require(day in names and symbol in names[day], "EH management price coordinate outside scope")
                raw = reader.load_raw_chunk("CC", symbol, day)
                return None if raw is None else archive.full_reference_session_inputs(symbol, day, raw, None)

            def read_open(day, symbol, at):
                session = read_session(day, symbol)
                wire.require(session is not None and session.opening <= at < session.closing,
                             "EH management modeled open outside source session")
                row, issue = _price_row(session, at, at + 3 * MINUTE)
                return Fraction(row.ohlc[0]) if issue is None else None

            yield ManagementSources(universe.sessions, names, features, feature_seal,
                                    read_session, read_open, reader.receipt, scenario)
            reader.assert_unchanged()


def compare_management_sources(
    sources: ManagementSources, *, cost_bps: int, risk_budget: Fraction, downside_penalty: Fraction,
    checkpoint: CHECKPOINT,
) -> tuple[ManagementCampaign, BreakoutLimits]:
    """Use the fixed held policy and independently reconcile the modeled ledger."""
    limits: BreakoutLimits = {}

    def projected():
        for day in sources.projected():
            for row in day.rows:
                frame = row.frame
                question = frame.initial.question
                if sources.scenario in row.scenarios and frame.initial.stage == "breakout" and question is not None:
                    limits.setdefault((day.day, frame.symbol), (frame.observed_at, frame.event_expires_at,
                        question.stop, frame.initial.start_close + question.band))
            yield day

    campaign = run_management_campaign(projected(), sources.sessions, scenario=sources.scenario,
        read_session=sources.read_session, risk_budget=risk_budget, cost_bps=cost_bps,
        downside_penalty=downside_penalty, fresh_reentry=True, held_comparison=True, checkpoint=checkpoint)
    evaluation = {day.day: sources.roster[day.day] for day in campaign.days}
    observations = {key: value for day in campaign.days for key, value in day.observations.items()}
    verify_management_report(campaign.report, evaluation, observations, cost_bps=cost_bps,
                             include_reentry=True, modeled_open_at=sources.read_open)
    return campaign, limits


def verify_management_record(
    rows: Iterable[dict], campaign: ManagementCampaign, sources: ManagementSources, *,
    cost_bps: int, risk_budget: Fraction, checkpoint: CHECKPOINT,
) -> None:
    """Check each saved coordinate and its arithmetic against the separately audited report inputs."""
    from research.intraday_campaign_ml_labels import fixed_calendar

    split = fixed_calendar(sources.sessions)
    start = len(sources.sessions) - len(split.evaluation_reference_sessions)
    records = iter(rows)
    for day, observed in zip(campaign.days, islice(sources.projected(), start, None), strict=True):
        wire.require(day.day == observed.day, "EH management saved day differs")
        for case in day.cases:
            checkpoint("eh_management_saved_case")
            row = next(records, None)
            wire.require(row is not None and (row["status"], row["reason"], row["symbol"])
                         == (case.status, case.reason, case.symbol), "EH management saved coordinate differs")
            verify_initial_entry(row, observed, scenario=sources.scenario, read_session=sources.read_session,
                                 risk_budget=risk_budget, cost_bps=cost_bps)
            if row["status"] != "confirmed":
                wire.require(case.managed is None and saved_management_metrics(row, event_start=None, cost_bps=cost_bps)
                             == (None, ()), "EH management saved unconfirmed economics differ")
                continue
            breakout = next(item.frame for item in observed.rows if item.frame.symbol == case.symbol
                            and sources.scenario in item.scenarios and item.frame.initial.stage == "breakout")
            question = breakout.initial.question
            session = sources.read_session(day.day, case.symbol)
            wire.require(question is not None and session is not None and case.managed is not None,
                         "EH management confirmed case source differs")
            verify_management_exits(row, session, level=question.level, band=question.band, risk_budget=risk_budget,
                                    cost_bps=cost_bps, fresh_reentry=True)
            basis, metrics = saved_management_metrics(row, event_start=breakout.event_at, cost_bps=cost_bps)
            wire.require((basis, {item.name: item for item in metrics})
                         == _case_metrics(case.managed.comparison, day.day, case.symbol, cost_bps),
                         "EH management saved fill and report economics differ")
    wire.require(next(records, None) is None, "EH management unexpected saved coordinate")


def compute(directory: Path, envelope: bridge.InputEnvelope, plan: bridge.UnitPlan, *,
            invented_only: bool, expected_members: dict, checkpoint: CHECKPOINT) -> bridge.OutputReceipt:
    """Write and independently re-open the fixed management ledger under the original research budget."""
    binding = bridge._EH_BINDINGS[plan.unit_id]
    scenario, cost = binding.scenario, binding.management_cost
    wire.require(scenario in ("S1", "S2") and cost in (33, 43)
                 and envelope.input_contract == wire.EH_MANAGEMENT_INPUT_CONTRACT
                 and envelope == bridge._eh_management_envelope(plan, invented_only),
                 "EH management fixed worker mode, population or price inputs differ")
    policy = bridge.canonical(bridge.EH_MANAGEMENT_POLICY)
    risk, penalty = Fraction(policy["risk_budget_won"]), Fraction(policy["downside_penalty"])
    with open_management_sources(directory, envelope.manifest, cc_decoded_sha256=envelope.stream_sha256,
                                 expected_members=expected_members, scenario=scenario,
                                 invented_only=invented_only, checkpoint=checkpoint) as sources:
        campaign, limits = compare_management_sources(sources, cost_bps=cost, risk_budget=risk,
                                                      downside_penalty=penalty, checkpoint=checkpoint)
        output = directory / binding.output
        seal = seal_management_execution(output, campaign, sources.feature_seal,
                                         invented_only=invented_only, risk_budget=risk)
        execution = verify_management_execution(output, seal, features=sources.feature_seal, calendar=sources.roster,
            scenario=scenario, mode=policy["mode"], cost_bps=cost, fresh_reentry=policy["fresh_reentry"],
            modeled_open_at=sources.read_open, downside_penalty=penalty, read_session=sources.read_session,
            breakout_limits=limits, invented_only=invented_only, risk_budget=risk, report=campaign.report)
        header = _management_header(sources.feature_seal, scenario, policy["mode"], cost, policy["fresh_reentry"],
            penalty, invented_only=invented_only, risk_budget=risk, report=campaign.report)
        rows = (row for row, _ in snapshot._read(output, seal, header, maximum=MAX_COORDINATES))
        verify_management_record(rows, campaign, sources, cost_bps=cost, risk_budget=risk, checkpoint=checkpoint)
        raw_audit = sources.raw_audit
    wire.require(wire.file_digest(output, binding.output_max, checkpoint) == (seal.sha256, seal.size),
                 "EH management verified output changed")
    manifest = envelope.payload()["inputs"]
    summary = {"scenario": scenario, "cost_bps": cost, "evaluation_sessions": campaign.report.original_sessions,
               "original_evaluation_coordinates": campaign.report.original_symbol_days,
               "verified_execution": execution, "report_sha256": wire.digest(wire.encode(header["report"])),
               "input_members": expected_members}
    proof = {"verified": True, "schema": binding.schema, "full_eof_verified": True,
             "all_original_coordinates_verified": True, "invented_only": invented_only, "full_geometry": True,
             "input_manifest": manifest, "input_manifest_sha256": wire.digest(wire.encode(manifest)),
             "raw_byte_audit": raw_audit, "summary_sha256": wire.digest(wire.encode(summary)),
             "management_summary": summary, "historical_availability_certified": False}
    return bridge.OutputReceipt(seal.sha256, seal.size, envelope.geometry, wire.encode(proof), plan.candidate_bounds)
