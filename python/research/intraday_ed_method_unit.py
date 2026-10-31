"""Fixed downstream ED units over one completed, independently verified snapshot.

Construction consumes bounded publication bytes, never an evidence location.
Only the existing bridge's post-STARTED hooks open transferred bodies. The two
passes authenticate EOF before planning, then retain one source day at a time.
The snapshot's producer binding is distinct from this unit's input binding.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, fields, replace
from fractions import Fraction
from itertools import zip_longest
import gc
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_account_verify as account_verify
from research import intraday_ed_comparison as comparison
from research import intraday_ed_comparison_unit as producer
from research import intraday_ed_models as models
from research import intraday_ed_replay as replay
from research import intraday_ed_snapshot as snapshot
from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_pattern_calibration import dispersion


SCHEMA = "ED-H1-method-results-v1"
OUTPUT_FILENAMES = {"ED-H1-RULES": "h1-rules.jsonl.gz",
    "ED-H1-LIGHTGBM": "h1-lightgbm.jsonl.gz",
    "ED-H1-LIGHTGBM-ABLATION": "h1-lightgbm-ablation.jsonl.gz"}
MAXIMUM = 512 * wire.MIB
DOMAINS = ("full_original_population", "observed_predecision_support_conditional")
MINIMUM_PUBLIC_SOURCES = tuple(sorted(set(producer.MINIMUM_PUBLIC_SOURCES) | {
    "python/research/intraday_ed_method_unit.py", "python/tests/test_intraday_ed_method_unit.py",
    "python/research/intraday_ed_comparison.py", "python/research/intraday_ed_models.py",
    "python/research/intraday_ed_account_verify.py", "python/research/intraday_ed_numerical.py",
    "python/research/intraday_ed_result_verify.py", "python/tests/test_intraday_ed_result_verify.py",
    "python/research/intraday_campaign_ml_models.py", "python/research/intraday_campaign_ml_labels.py"}))


@dataclass(frozen=True)
class SnapshotReference:
    publication: bytes
    publication_sha256: str
    output_path: str
    invented_only: bool
    output_sha256: str
    output_bytes: int
    decoded_sha256: str
    producer_binding: bytes


@dataclass(frozen=True)
class ConditionalGateEvidence:
    """Reserved immutable evidence, never an executable or unbound approval.

    A saved-output resolver must independently reconstruct every condition from
    the two completed artifacts before this evidence can be adopted. Merely
    constructing this object does not open the conditional arm.
    """
    rules_publication: bytes
    rules_publication_sha256: str
    rules_output_sha256: str
    learner_publication: bytes
    learner_publication_sha256: str
    learner_output_sha256: str
    snapshot_sha256: str
    source_sha256: str
    reconstructed_conditions: bytes


def resolve_snapshot_reference(publication: bytes, *, publication_sha256: str,
                               expected_output: str, invented_only: bool) -> SnapshotReference:
    """Resolve fixed prior-output metadata without stat/open/read of its body.

    The private caller declares the exact prior output path before its own
    qualification. Publication provenance must be externally pinned; an output
    cannot certify itself or choose a different source after seeing outcomes.
    """
    wire.require(type(publication) is bytes and 0 < len(publication) <= 65536
        and wire.valid_sha(publication_sha256) and wire.digest(publication) == publication_sha256
        and type(invented_only) is bool, "bounded externally pinned snapshot publication required")
    value = json.loads(publication, object_pairs_hook=producer._pairs,
        parse_constant=lambda _x: (_ for _ in ()).throw(ValueError("nonfinite publication")))
    wire.require(type(expected_output) is str, "exact fixed prior snapshot output path required")
    path = PurePosixPath(expected_output)
    wire.require(path.is_absolute() and ".." not in path.parts
        and str(path) == expected_output and path.name == producer.OUTPUT_FILENAME,
        "exact fixed prior snapshot output path required")
    wire.require(type(value) is dict and value.get("unit_id") == "ED-H1-SNAPSHOT"
        and value.get("status") == "completed" and value.get("verification_complete") is True
        and value.get("invented_only") is invented_only and value.get("full_bridge") is True
        and value.get("local_pipe_test") is False and value.get("resource_profile") == wire.ED_H1_600.id
        and value.get("input_contract") == wire.ED_INPUTS_CONTRACT
        and value.get("geometry") == producer.FULL_GEOMETRY
        and value.get("candidate_counts") == producer.CANDIDATE_BOUNDS,
        "completed same-geometry full-bridge snapshot required")
    verified = value.get("independent_verification")
    wire.require(type(verified) is dict and verified.get("verified") is True
        and verified.get("full_geometry") is True and verified.get("invented_only") is invented_only
        and verified.get("all_original_coordinates_verified") is True
        and verified.get("sampled_scientific_rows") is False
        and verified.get("record_counts") == {"session": 257, "coordinate": 10280}
        and wire.valid_sha(verified.get("decoded_sha256"))
        and wire.valid_sha(verified.get("input_manifest_sha256")),
        "independent complete snapshot verification required")
    hashes = value.get("source_hashes")
    wire.require(type(hashes) is dict and set(producer.MINIMUM_PUBLIC_SOURCES) <= set(hashes)
        and all(type(name) is str and wire.valid_sha(pin) for name, pin in hashes.items())
        and type(value.get("local_identity")) is dict
        and wire.valid_sha(value["local_identity"].get("runtime_sha256"))
        and wire.valid_sha(value.get("output_sha256"))
        and type(value.get("output_bytes")) is int and 0 < value["output_bytes"] <= MAXIMUM
        and verified.get("compressed_bytes") == value["output_bytes"],
        "snapshot source/runtime/output identity required")
    binding = {"registration_sha256": hashes[producer.SPECIFICATION_PATH],
        "source_sha256": wire.digest(wire.encode(hashes)),
        "runtime_sha256": value["local_identity"]["runtime_sha256"],
        "input_manifest_sha256": verified["input_manifest_sha256"], "caller": "ED-H1-SNAPSHOT"}
    return SnapshotReference(publication, publication_sha256, expected_output, invented_only,
        value["output_sha256"], value["output_bytes"], verified["decoded_sha256"], wire.encode(binding))


def _check_reference(reference: SnapshotReference) -> None:
    wire.require(type(reference) is SnapshotReference and reference == resolve_snapshot_reference(
        reference.publication, publication_sha256=reference.publication_sha256,
        expected_output=reference.output_path, invented_only=reference.invented_only),
        "resolved immutable snapshot reference differs")


def method_hooks(plan: bridge.UnitPlan, specification: bytes, reference: SnapshotReference, *,
                 invented_only: bool, source_manifest_sha256: str,
                 runtime_sha256: str, invented_reference: SnapshotReference | None = None,
                 invented_source: Path | None = None,
                 conditional_gate: ConditionalGateEvidence | None = None) -> bridge.UnitHooks:
    """Close over data only; configuration cannot supply code or a selector."""
    _check_reference(reference)
    active = reference
    if invented_only:
        wire.require(type(invented_reference) is SnapshotReference and isinstance(invented_source, Path)
            and invented_source.is_absolute() and ".." not in invented_source.parts,
            "fixed prior invented snapshot reference and local source required")
        _check_reference(invented_reference)
        wire.require(invented_reference.invented_only is True,
            "invented profile cannot copy actual market snapshot")
        active = invented_reference
    spec = producer.contract(specification, actual=not invented_only)
    bridge.validate_plan(plan)
    pin = bridge.InputPin("ED-SNAPSHOT", reference.output_path, reference.output_sha256, MAXIMUM)
    wire.require(type(invented_only) is bool and reference.invented_only is False
        and wire.valid_sha(source_manifest_sha256) and wire.valid_sha(runtime_sha256)
        and plan.unit_id in OUTPUT_FILENAMES and plan.input_contract == wire.ED_SNAPSHOT_CONTRACT
        and plan.resource_profile == wire.ED_H1_600.id
        and plan.specification_path == producer.SPECIFICATION_PATH and plan.input_pins == (pin,)
        and plan.full_geometry == wire.encode(producer.FULL_GEOMETRY)
        and plan.candidate_bounds == wire.encode(producer.CANDIDATE_BOUNDS)
        and plan.decoded_stream_sha256 == reference.decoded_sha256
        and plan.output_filename == OUTPUT_FILENAMES[plan.unit_id] and plan.output_max_bytes == MAXIMUM
        and plan.actual_runner_ready == spec["actual_runner_ready"]
        and set(MINIMUM_PUBLIC_SOURCES) <= set(plan.source_names), "fixed ED method plan differs")
    for prior in (reference, active):
        producer_binding = bridge.canonical(prior.producer_binding)
        wire.require(producer_binding["registration_sha256"] == wire.digest(specification)
            and producer_binding["source_sha256"] == source_manifest_sha256
            and producer_binding["runtime_sha256"] == runtime_sha256,
            "snapshot producer must use the same frozen complete source/runtime/registration")
    # The ninth arm needs independently reconstructed paired prior accounts.
    # No truthy config flag, producer mean, or absent evidence can open it.
    wire.require(plan.unit_id != "ED-H1-LIGHTGBM-ABLATION",
        "conditional ablation gate evidence is not yet registered; paired identified positive33/43 S1/S2 required")
    registration = wire.digest(specification)

    def load_plan(config: dict) -> bridge.UnitPlan:
        wire.require(config.get("invented_only") is invented_only
            and config["source_hashes"].get(producer.SPECIFICATION_PATH) == registration
            and wire.digest(wire.encode(config["source_hashes"])) == source_manifest_sha256
            and config["local_identity"]["runtime_sha256"] == runtime_sha256,
            "fixed method hook/source/runtime configuration differs")
        producer.contract(specification, actual=not invented_only)
        return plan

    def make_invented(directory: Path, sessions: int, checkpoint) -> bridge.InputEnvelope:
        wire.require(invented_only and sessions == 257,
            "derived qualification requires prior full invented snapshot, never a tiny substitute")
        source = invented_source
        wire.require(wire.file_digest(source, MAXIMUM, checkpoint) ==
            (active.output_sha256, active.output_bytes), "prior invented snapshot body differs")
        target = directory / "ED-SNAPSHOT"
        wire.safe_path(target)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(source_fd, "rb") as incoming:
                while block := incoming.read(wire.BLOCK):
                    checkpoint("copy_prior_invented_snapshot")
                    wire.write_all(output, block)
            output.flush()
            os.fsync(output.fileno())
        wire.require(wire.file_digest(target, MAXIMUM, checkpoint) ==
            (active.output_sha256, active.output_bytes), "invented snapshot copy differs")
        wire.require(wire.file_digest(source, MAXIMUM, checkpoint) ==
            (active.output_sha256, active.output_bytes), "prior invented snapshot changed during copy")
        return bridge.InputEnvelope((bridge.InputFile("ED-SNAPSHOT", str(target), active.output_sha256,
            MAXIMUM, active.output_bytes),), active.decoded_sha256,
            wire.encode(producer.FULL_GEOMETRY), wire.ED_SNAPSHOT_CONTRACT)

    def compute_verify(directory: Path, envelope: bridge.InputEnvelope, checkpoint) -> bridge.OutputReceipt:
        binding = {"registration_sha256": registration, "source_sha256": source_manifest_sha256,
            "runtime_sha256": runtime_sha256, "input_manifest_sha256": wire.digest(wire.encode(
                [item.metadata() for item in envelope.manifest])), "caller": plan.unit_id}
        return _compute(directory, envelope, plan, active, binding, spec, checkpoint)

    return bridge.UnitHooks(load_plan, make_invented, compute_verify)


def _records(path: Path, reference: SnapshotReference, checkpoint) -> Iterator[dict]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        yield from snapshot.read_snapshot(stream, expected_sha256=reference.output_sha256,
            expected_decoded_sha256=reference.decoded_sha256, checkpoint=checkpoint,
            validate_types=False)


def _first_pass(path: Path, reference: SnapshotReference, checkpoint):
    header, rows, counts = None, [], Counter()
    for record in _records(path, reference, checkpoint):
        counts[record["kind"]] += 1
        if record["kind"] == "header":
            header = record
            wire.require(header["binding"] == bridge.canonical(reference.producer_binding),
                "snapshot body producer source/runtime/registration/input binding differs")
        elif record["kind"] == "coordinate":
            row = comparison.snapshot_opportunity(snapshot.decode_coordinate(record))
            # Holding-price paths already reside in the independently verified
            # immutable input. Never retain a whole-period Minute panel here.
            if row.label is not None:
                row = replace(row, label=replace(row.label, path=()))
            rows.append(row)
    wire.require(header is not None and counts == {"header": 1, "session": 257,
        "coordinate": 10280, "footer": 1}, "full original snapshot framing required")
    calendar = fixed_calendar(tuple(header["calendar"]["sessions"]))
    wire.require(Counter(row.scenario for row in rows) == producer.CANDIDATE_BOUNDS,
        "original5140 coordinates per timing required")
    return header, calendar, tuple(rows)


def _premean_plans(calendar, rows) -> dict:
    plans = {"support": comparison.support_plan(calendar, rows), "own_labels": {}}
    for scenario in models.SCENARIOS:
        scoped = [row for row in rows if row.scenario == scenario
            and row.session in calendar.evaluation_reference_sessions and row.candidate is not None]
        plans["own_labels"][scenario] = dispersion([{"session": row.session,
            "label": None if row.label is None or row.label.status == "unknown" else row.label.net33}
            for row in scoped], list(calendar.evaluation_reference_sessions))
    return plans


def _verify_admission(plan, day_rows: tuple, *, predictions: dict | None = None) -> dict:
    """Check raw snapshot versus plan using predecision fields and scalar signs.

    Native/scalar model agreement and independent fold membership have already
    been checked separately. Outcome fields are deliberately never referenced.
    """
    if predictions is None:
        predictions = {prediction.parent_id: prediction for _view, _fit, batch, _receipt in plan.fits
                       for prediction in batch.rows}
    decisions = {(d.session, d.symbol): d for d in plan.decisions}
    for raw in day_rows:
        if raw.coordinate.scenario != plan.scenario:
            continue
        decision = decisions[raw.coordinate.session, raw.coordinate.symbol]
        if raw.parent_scan.status == "known_no_parent":
            status = "no_parent"
        elif raw.parent_scan.status == "parent_identity_unknown" or not raw.common_support:
            status = "unknown"
        elif plan.arm in (comparison.RULE, comparison.CONTROL):
            exact = dict(raw.window_inputs.static_exact)
            accept = (plan.arm == comparison.CONTROL or raw.opening_context.context.state == "ELIGIBLE_ORIGINAL")
            accept = accept and exact["target_minus_peer_30m_return"] > 0 and exact["last5_share_volume_ratio"] > 1
            status = "accept" if accept else "decline"
        else:
            prediction = predictions[raw.candidate.parent_id]
            status = ("unknown" if prediction.prediction_hex is None else
                      "accept" if float.fromhex(prediction.prediction_hex) > 0 else "decline")
        wire.require(decision.status == status and decision.parent_id ==
            (None if raw.candidate is None else raw.candidate.parent_id), "independent policy admission differs")
        if decision.action is not None:
            wire.require(decision.action.candidate == raw.candidate
                and decision.action.symbol == raw.coordinate.symbol,
                "policy changed the fixed parent or exit contract")
    return {"verified": True, "test_labels_read": False, "admission_from_raw_predecision_fields": True}


def _day_accounts(plan, session, minutes, carries: dict, oracles: dict):
    for cost in (33, 43):
        for domain in DOMAINS:
            observed = domain == DOMAINS[1]
            for fresh in (False, True):
                key = plan.arm, plan.scenario, cost, domain, fresh
                prior = None if fresh or key not in carries else carries[key].state
                result = comparison.replay_policy_day(plan, session, minutes, cost_bps=cost,
                    state=None if fresh else carries.get(key), observed_support_only=observed,
                    fresh_day=fresh, compress_idle_minutes=True)
                effective = (replace(session, policy_state="unavailable", reason="fold_training_unavailable")
                    if session.opening.date().isoformat() in plan.unavailable_sessions else session)
                actions = tuple(d.action for d in plan.decisions
                    if d.session == session.opening.date().isoformat() and d.action is not None
                    and (not observed or d.status != "unknown"))
                if key not in oracles:
                    oracles[key] = account_verify.EDAccountVerifier(plan.arm, cost,
                        "day_local_counterfactual" if fresh else "continuous", domain=domain)
                receipt = oracles[key].verify_day(actions, minutes, effective, result,
                    compress_idle_minutes=True)
                wire.require(receipt.get("verified") is True
                    and receipt.get("producer_state_used_as_input") is False,
                    "independent account verification required")
                if not fresh:
                    carries[key] = comparison.policy_carry(plan, result)
                yield key, result, receipt, prior


class _Report:
    def __init__(self, stream, maximum: int):
        self.sink = producer._BoundedSink(stream, maximum)
        self.hashed = snapshot._HashedIO(self.sink)
        self.compressed = gzip.GzipFile(filename="", fileobj=self.hashed, mode="wb", compresslevel=1, mtime=0)
        self.prefix, self.decoded, self.counts, self.bytes = hashlib.sha256(), hashlib.sha256(), Counter(), 0

    def add(self, kind: str, **payload) -> None:
        raw = snapshot.canonical({"kind": kind, **snapshot.encode(payload)}) + b"\n"
        wire.require(len(raw) <= snapshot.MAX_LINE and self.bytes + len(raw) <= snapshot.MAX_STREAM,
            "method report record/decoded limit exceeded")
        self.compressed.write(raw)
        self.prefix.update(raw)
        self.decoded.update(raw)
        self.bytes += len(raw)
        self.counts[kind] += 1

    def close(self):
        counts, prefix = dict(self.counts), self.prefix.hexdigest()
        self.add("footer", record_counts=counts, decoded_prefix_sha256=prefix)
        self.compressed.close()
        return self.hashed.digest.hexdigest(), self.hashed.size, self.decoded.hexdigest(), dict(self.counts)


def _write_policy_records(report: _Report, policy: comparison.PolicyPlan):
    """Persist every original fold/row before releasing its training payload.

    Native prediction and independent membership/numerical verification already
    completed. Replay needs only the exact prediction objects and decisions;
    model artifacts and all training/inference rows remain in the saved report.
    A separate frame also releases its last loop's view/artifact/row references.
    """
    predictions = {prediction.parent_id: prediction for _view, _fit, batch, _receipt in policy.fits
                   for prediction in batch.rows}
    counts = {"model_fit_slots": len(policy.fits),
        "model_fits_performed": sum(fit.status == "ready" for _view, fit, _batch, _proof in policy.fits),
        "unavailable_fit_slots": sum(fit.status == "unavailable" for _view, fit, _batch, _proof in policy.fits)}
    for view, fit, batch, verification in policy.fits:
        report.add("model_fold", arm=policy.arm, scenario=policy.scenario,
            fold_view={field.name: getattr(view, field.name) for field in fields(view)
                if field.name not in ("training", "testing")},
            fit=fit, predictions=batch, independent_verification=verification)
        for training in view.training:
            report.add("model_training_row", arm=policy.arm, scenario=policy.scenario,
                fold=view.fold.index, row=training)
        for testing in view.testing:
            report.add("model_inference_row", arm=policy.arm, scenario=policy.scenario,
                fold=view.fold.index, row=testing)
    for decision in policy.decisions:
        report.add("decision", arm=policy.arm, scenario=policy.scenario, decision=decision)
    return replace(policy, fits=()), predictions, counts


def _verify_report(path: Path, pin: str, decoded_pin: str, counts: dict, checkpoint) -> dict:
    """Reread the durable bytes through gzip EOF; reject noncanonical framing."""
    prefix, decoded, seen, footer, size = hashlib.sha256(), hashlib.sha256(), Counter(), False, 0
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as raw:
        hashed = snapshot._HashedIO(raw)
        with gzip.GzipFile(fileobj=hashed, mode="rb") as stream:
            while line := stream.readline(snapshot.MAX_LINE + 1):
                checkpoint("method_report_verify")
                size += len(line)
                wire.require(not footer and line.endswith(b"\n") and len(line) <= snapshot.MAX_LINE
                    and size <= snapshot.MAX_STREAM,
                    "method report framing differs")
                record = json.loads(line, object_pairs_hook=producer._pairs)
                wire.require(snapshot.canonical(record) + b"\n" == line, "method report canonical bytes differ")
                kind = record.get("kind")
                wire.require((bool(seen) and kind != "header") or
                    (not seen and kind == "header" and record.get("schema") == SCHEMA),
                    "method report header required")
                if kind == "footer":
                    wire.require(record == {"kind": "footer", "record_counts": dict(seen),
                        "decoded_prefix_sha256": prefix.hexdigest()}, "method report footer differs")
                    footer = True
                else:
                    prefix.update(line)
                seen[kind] += 1
                decoded.update(line)
    wire.require(footer and seen == counts and hashed.digest.hexdigest() == pin
        and decoded.hexdigest() == decoded_pin, "method report stored identity differs")
    return {"verified": True, "stored_compressed_sha256": pin,
        "stored_decoded_sha256": decoded_pin, "record_counts": dict(seen), "full_eof_verified": True}


def _account_day_summary(key, result, session, prior=None) -> dict:
    """Exact realized quantities/costs; no hypothetical zero-cost rerun."""
    opening, closing = result.snapshots[0], result.snapshots[-1]
    starts = {} if prior is None else {lot.candidate.parent_id:
        (session.opening, lot.quantity, lot.quantity * lot.entry_price) for lot in prior.book.lots}
    notional_minutes, overlap = Fraction(), 0
    for event in result.events:
        if event.kind == "entry" and event.quantity:
            starts[event.parent_id] = (event.at, event.quantity, -event.cash_delta - event.assumed_cost)
        elif event.kind == "exit" and event.quantity and event.parent_id in starts:
            start, _quantity, notional = starts.pop(event.parent_id)
            notional_minutes += notional * int((event.at - start).total_seconds() // 60)
    # Include observed exposure of positions whose exit remains unknown. Its
    # price-valued duration is conditional, never an identified realized fill.
    for start, _quantity, notional in starts.values():
        notional_minutes += notional * int((closing.at - start).total_seconds() // 60)
    positions = 0 if prior is None else len(prior.book.lots)
    previous = session.opening
    for event in sorted(result.events, key=lambda item: item.at):
        if previous is not None and positions > 1:
            overlap += int((event.at - previous).total_seconds() // 60)
        if event.kind == "entry" and event.quantity:
            positions += 1
        elif event.kind == "exit" and event.quantity:
            positions -= 1
        previous = event.at
    if positions > 1:
        overlap += int((session.closing - previous).total_seconds() // 60)
    identified = closing.full_nav is not None and not result.state.book.indeterminate
    return_known = identified and opening.full_nav is not None and opening.full_nav > 0
    return {"arm": key[0], "scenario": key[1], "cost_bps": key[2], "domain": key[3],
        "scope": "fresh_day_counterfactual_diagnostic" if key[4] else "continuous",
        "session": closing.at.date().isoformat(), "day_open_nav": opening.full_nav,
        "day_close_nav": closing.full_nav,
        "identified_day_return": ((closing.full_nav - opening.full_nav) / opening.full_nav
            if return_known else None),
        "same_quantity_gross_day_return": ((closing.same_quantity_gross_nav - opening.same_quantity_gross_nav)
            / opening.full_nav if return_known else None),
        "observed_nav": closing.observed_nav, "same_quantity_gross_nav": closing.same_quantity_gross_nav,
        "cash": closing.cash, "entry_cost_to_date": result.state.total_entry_cost,
        "realized_same_quantity_gross_pnl_to_date": result.state.realized_gross_pnl,
        "notional_minutes_observed": notional_minutes, "overlap_minutes_observed": overlap,
        "entry_reason_counts": dict(Counter(e.reason for e in result.events if e.kind == "entry")),
        "entries": sum(e.kind == "entry" and e.quantity > 0 for e in result.events),
        "unknown_event_count": sum(e.kind == "unknown" for e in result.events),
        "first_unknown_at": result.state.first_unknown_at,
        "identified": identified,
        "fresh_days_stitched": False}


def _concentration(calendar, rows, plan) -> dict:
    lookup = {(r.scenario, r.session, r.symbol): r for r in rows}
    buckets = defaultdict(lambda: {"accepted": 0, "known": 0, "unknown": 0,
                                   "gross": Fraction(), "net33": Fraction(), "net43": Fraction()})
    halfway = len(calendar.evaluation_reference_sessions) // 2
    for decision in plan.decisions:
        if decision.status != "accept":
            continue
        row = lookup[decision.scenario, decision.session, decision.symbol]
        fold = next(f.index for f in calendar.folds if row.session in f.test_sessions)
        half = "first" if row.session in calendar.evaluation_reference_sessions[:halfway] else "second"
        group = "known_no_event" if row.event_types == ("NO_EVENT",) else "event"
        for dimension, value in (("date", row.session), ("name", row.symbol),
                ("fold", str(fold)), ("chronological_half", half), ("event_group", group)):
            bucket = buckets[dimension + ":" + value]
            bucket["accepted"] += 1
            known = row.label is not None and row.label.status != "unknown"
            bucket["known" if known else "unknown"] += 1
            if known:
                for field in ("gross", "net33", "net43"):
                    bucket[field] += getattr(row.label, field)
    return {"arm": plan.arm, "scenario": plan.scenario, "buckets": dict(buckets),
        "unit_exposure_contributions_not_account_PnL": True, "selected_winning_subgroups": False}


def _paired_accounts(calendar, accounts):
    """Same-budget rule/control contrasts, without another price replay.

    Unknown dates remain in the original calendar. The identified intersection
    is an explicit conditional diagnostic and cannot certify the whole account.
    Whole-history NAV increments exist only when BOTH histories are identified.
    """
    for key in sorted(accounts):
        if key[0] != comparison.RULE:
            continue
        control_key = (comparison.CONTROL, *key[1:])
        wire.require(control_key in accounts, "same-calendar rule control account required")
        left, right = accounts[key], accounts[control_key]
        wire.require(tuple(day["session"] for day in left) == tuple(day["session"] for day in right)
            == calendar.evaluation_reference_sessions, "paired original157 calendar changed")
        paired = []
        for treated, control in zip(left, right, strict=True):
            known = (treated["identified_day_return"] is not None
                     and control["identified_day_return"] is not None)
            net_change = (None if not known else ((treated["day_close_nav"] - treated["day_open_nav"])
                - (control["day_close_nav"] - control["day_open_nav"])) / replay.cash.INITIAL_CAPITAL)
            paired.append({"session": treated["session"], "identified_intersection": known,
                "same_initial_budget_day_net_change_difference": net_change,
                "daily_return_difference": None if not known else
                    treated["identified_day_return"] - control["identified_day_return"],
                "notional_minutes_difference": treated["notional_minutes_observed"] - control["notional_minutes_observed"],
                "overlap_minutes_difference": treated["overlap_minutes_observed"] - control["overlap_minutes_observed"],
                "entries_difference": treated["entries"] - control["entries"],
                "treated_entry_reasons": treated["entry_reason_counts"],
                "control_entry_reasons": control["entry_reason_counts"],
                "treated_first_unknown_at": treated["first_unknown_at"],
                "control_first_unknown_at": control["first_unknown_at"]})
        planning = dispersion([{"session": day["session"],
            "label": day["same_initial_budget_day_net_change_difference"]} for day in paired],
            list(calendar.evaluation_reference_sessions))
        yield "paired_account_plan", {"treated_key": key, "control_key": control_key,
            "before_mean_sample_plan": planning, "original_calendar": calendar.evaluation_reference_sessions,
            "same_initial_budget": replay.cash.INITIAL_CAPITAL,
            "fewer_trades_implies_superior_selection": False,
            "observed_support_conditional_certifies_original_population": False}
        for day in paired:
            yield "paired_account_day", {"treated_key": key, "control_key": control_key, **day}
        known_values = [day["same_initial_budget_day_net_change_difference"] for day in paired
                        if day["identified_intersection"]]
        complete = len(known_values) == 157
        contributions = {}
        for fold in calendar.folds:
            values = [day["same_initial_budget_day_net_change_difference"] for day in paired
                if day["identified_intersection"] and day["session"] in fold.test_sessions]
            contributions["fold:" + str(fold.index)] = {"identified_days": len(values),
                "same_budget_net_change_sum": sum(values, Fraction())}
        halves = (calendar.evaluation_reference_sessions[:78], calendar.evaluation_reference_sessions[78:])
        for half, dates in zip(("first", "second"), halves, strict=True):
            values = [day["same_initial_budget_day_net_change_difference"] for day in paired
                if day["identified_intersection"] and day["session"] in dates]
            contributions["chronological_half:" + half] = {"identified_days": len(values),
                "same_budget_net_change_sum": sum(values, Fraction())}
        yield "paired_account_summary", {"treated_key": key, "control_key": control_key,
            "original_sessions": 157, "identified_intersection_sessions": len(known_values),
            "unknown_pair_sessions": 157 - len(known_values),
            "identified_intersection_fraction": Fraction(len(known_values), 157),
            "identified_intersection_mean_same_budget_day_net_change_difference":
                sum(known_values, Fraction()) / len(known_values) if known_values else None,
            "continuous_terminal_net_increment": (left[-1]["day_close_nav"] - right[-1]["day_close_nav"])
                / replay.cash.INITIAL_CAPITAL if complete and not key[4] else None,
            "continuous_terminal_increment_status": ("fresh_day_not_continuous" if key[4] else
                "identified" if complete else "one_or_both_account_histories_unknown"),
            "concentration": contributions, "conditional_subset_mean_is_full_population_claim": False,
            "same_quantity_within_each_account_not_equal_quantities_between_arms": True,
            "fresh_days_stitched": False, "edge_claim": False}


def _compute(directory, envelope, plan, reference, binding, spec, checkpoint):
    from research import intraday_ed_result_verify as result_verify
    envelope.payload()
    wire.require(envelope.input_contract == wire.ED_SNAPSHOT_CONTRACT
        and envelope.geometry == plan.full_geometry and envelope.stream_sha256 == reference.decoded_sha256
        and len(envelope.manifest) == 1 and envelope.manifest[0].name == "ED-SNAPSHOT"
        and (envelope.manifest[0].sha256, envelope.manifest[0].size, envelope.manifest[0].maximum) ==
            (reference.output_sha256, reference.output_bytes, MAXIMUM), "single original snapshot input differs")
    if not reference.invented_only:
        bridge._actual_envelope(envelope, plan)
    path = directory / "received" / "ED-SNAPSHOT"
    wire.require(wire.file_digest(path, MAXIMUM, checkpoint) ==
        (reference.output_sha256, reference.output_bytes), "received snapshot identity differs")
    header, calendar, rows = _first_pass(path, reference, checkpoint)
    del header  # Its binding/calendar were checked; retain no duplicate raw target matrix.
    before_means = _premean_plans(calendar, rows)
    numerical = None
    if plan.unit_id != "ED-H1-RULES":
        from research import intraday_ed_numerical
        numerical = intraday_ed_numerical.activate(spec["numerical_environment"], checkpoint)
    plans = []
    def fit_checkpoint(stage):
        # LightGBM Dataset/Booster cycles can retain native matrices after a
        # serialized fit/predict/verification has returned. Native allocations
        # do not themselves advance Python's automatic GC thresholds.
        if stage.startswith("ED-after-fold-"):
            gc.collect()
        checkpoint(stage)
    for scenario in models.SCENARIOS:
        if plan.unit_id == "ED-H1-RULES":
            plans.extend(comparison.plan_rule(calendar, rows, scenario=scenario, without_filing=removed)
                         for removed in (False, True))
        else:
            model_binding = models.EDModelBinding(binding["source_sha256"], binding["runtime_sha256"],
                reference.output_sha256, binding["registration_sha256"], 1, scenario)
            plans.append(comparison.plan_lightgbm(calendar, rows, scenario=scenario,
                binding=model_binding, without_filing=plan.unit_id.endswith("-ABLATION"), checkpoint=fit_checkpoint))
    output = directory / plan.output_filename
    wire.safe_path(output)
    carries, oracles, accounts, admission_days = {}, {}, defaultdict(list), 0
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        report = _Report(stream, plan.output_max_bytes)
        report.add("header", schema=SCHEMA, binding=binding,
            snapshot_reference={"publication_sha256": reference.publication_sha256,
                "path": reference.output_path, "compressed_sha256": reference.output_sha256,
                "decoded_sha256": reference.decoded_sha256, "producer_binding": reference.producer_binding},
            calendar=calendar, premean_plans=before_means, invented_only=reference.invented_only,
            full_geometry=True, qualifies_actual_by_itself=False,
            conditional_ablation={"status": "closed_pending_independently_verified_prior_paired_accounts",
                "required": "same learner identified positive net33/net43 and rule-control increment BOTH S1/S2",
                "unbound_boolean_can_open": False},
            assumptions=snapshot.ASSUMPTIONS, edge_claim=False, promotion_allowed=False)
        prediction_indexes, fit_counts = {}, Counter()
        for index in range(len(plans)):
            lean, predictions, counts = _write_policy_records(report, plans[index])
            plans[index] = lean
            prediction_indexes[lean.arm, lean.scenario] = predictions
            fit_counts.update(counts)
        gc.collect()
        checkpoint("ED-after-model-storage-release")

        def finish_day(record, raw_rows):
            nonlocal admission_days
            if record["session"] not in calendar.evaluation_reference_sessions:
                return
            views = snapshot.decode_session(record)
            minutes = {(view.session.symbol, at): minute for view in views
                       for at, minute in view.session.minutes.items()}
            session = replay.EDSession(views[0].session.opening, views[0].session.closing)
            for policy in plans:
                admission = _verify_admission(policy, tuple(raw_rows),
                    predictions=prediction_indexes[policy.arm, policy.scenario])
                for key, result, receipt, prior in _day_accounts(policy, session, minutes, carries, oracles):
                    summary = _account_day_summary(key, result, session, prior)
                    summary_check = result_verify.verify_account_day_summary(key, result, session, summary, prior)
                    accounts[key].append(summary)
                    report.add("account_day", key=key, result=result, summary=summary,
                        independent_verification=receipt, admission_verification=admission,
                        summary_verification=summary_check)
            admission_days += 1

        day_record, raw_rows = None, []
        for record in _records(path, reference, checkpoint):
            if record["kind"] in ("session", "footer"):
                if day_record is not None:
                    finish_day(day_record, raw_rows)
                day_record, raw_rows = (record, []) if record["kind"] == "session" else (None, [])
            elif record["kind"] == "coordinate":
                raw_rows.append(snapshot.decode_coordinate(record))
        wire.require(admission_days == 157 and all(oracle.verified_days == 157 for oracle in oracles.values()),
            "all original evaluation days require independent account verification")
        for policy in plans:
            summary = comparison.unit_summary(calendar, rows, policy)
            concentration = _concentration(calendar, rows, policy)
            checked = result_verify.verify_policy_summary(calendar, rows, policy, summary,
                                                          concentration=concentration)
            report.add("unit_summary", summary=summary, concentration=concentration,
                       independent_verification=checked)
        for scenario in models.SCENARIOS:
            pairs = comparison.match_non_event(calendar, rows, scenario=scenario)
            paired_plan = dispersion([{"session": pair.session, "label": pair.net33_difference}
                for pair in pairs], list(calendar.evaluation_reference_sessions))
            pair_plan = {"scenario": scenario, "before_mean_sample_plan": paired_plan,
                         "matching_is_not_account_or_causal_identification": True}
            report.add("matched_pair_plan", **pair_plan)
            for pair in pairs:
                report.add("matched_pair", pair=pair)
            pair_summary = {"scenario": scenario, "original_pairs": len(pairs),
                "matched": sum(pair.control_parent_id is not None for pair in pairs),
                "known": sum(pair.net33_difference is not None for pair in pairs),
                "means": {field: (sum(values, Fraction()) / len(values) if values else None)
                    for field in ("gross_difference", "net33_difference", "net43_difference")
                    for values in [[getattr(pair, field) for pair in pairs if getattr(pair, field) is not None]]},
                "unknown_outcomes_rematched": False, "edge_claim": False}
            result_verify.verify_matched_pairs(calendar, rows, scenario, pairs, pair_summary, plan=pair_plan)
            report.add("matched_pair_summary", **pair_summary)
        for key, days in accounts.items():
            planning = dispersion([{"session": day["session"], "label": day["identified_day_return"]}
                for day in days], list(calendar.evaluation_reference_sessions))
            returns = [day["identified_day_return"] for day in days if day["identified_day_return"] is not None]
            complete = all(day["identified"] for day in days)
            account_summary = {"key": key, "evaluation_sessions": 157,
                "original_calendar": calendar.evaluation_reference_sessions,
                "identified_days": sum(day["identified"] for day in days),
                "before_mean_sample_plan": planning,
                "identified_day_return_mean": sum(returns, Fraction()) / len(returns) if returns else None,
                "mean_denominator": "identified-day subset; original157 always retained",
                "continuous_terminal_net_return": (days[-1]["day_close_nav"] / replay.cash.INITIAL_CAPITAL - 1
                    if complete and not key[4] else None),
                "continuous_terminal_same_quantity_gross_return": (days[-1]["same_quantity_gross_nav"] /
                    replay.cash.INITIAL_CAPITAL - 1 if complete and not key[4] else None),
                "continuous_net_return_status": ("fresh_day_not_a_continuous_account" if key[4] else
                    "identified" if complete else "full_history_unknown"),
                "fresh_days_stitched": False, "edge_claim": False}
            result_verify.verify_account_summary(calendar, key, days, account_summary)
            report.add("account_summary", **account_summary)
        for produced, expected in zip_longest(_paired_accounts(calendar, accounts),
                result_verify.paired_account_records(calendar, accounts)):
            wire.require(produced is not None and expected is not None
                and snapshot.canonical(snapshot.encode(produced)) == snapshot.canonical(snapshot.encode(expected)),
                "independent paired account aggregate differs")
            kind, payload = produced
            report.add(kind, **payload)
        if numerical is not None:
            numerical_after = intraday_ed_numerical.verify_after(numerical, checkpoint)
            report.add("numerical_environment", before=numerical, after=numerical_after)
        pin, size, decoded_pin, counts = report.close()
        stream.flush()
        os.fsync(stream.fileno())
    stored = _verify_report(output, pin, decoded_pin, counts, checkpoint)
    wire.require(wire.file_digest(output, MAXIMUM, checkpoint) == (pin, size)
        and wire.file_digest(path, MAXIMUM, checkpoint) == (reference.output_sha256, reference.output_bytes),
        "stored output or immutable source changed")
    fd = os.open(output, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        os.fchmod(stream.fileno(), 0o400)
        os.fsync(stream.fileno())
    wire.fsync_directory(directory)
    verification = {**stored, "all_account_days_verified": admission_days,
        "producer_state_used_as_oracle_input": False,
        "model_fit_slots": fit_counts["model_fit_slots"],
        "model_fits_performed": fit_counts["model_fits_performed"],
        "unavailable_fit_slots": fit_counts["unavailable_fit_slots"],
        "input_manifest_sha256": binding["input_manifest_sha256"], "snapshot_eof_passes": 2,
        "invented_only": reference.invented_only, "full_geometry": True,
        "qualifies_actual_by_itself": False, "scientific_report_complete": True,
        "policy_summaries_independently_verified": True,
        "account_summaries_independently_verified": True,
        "paired_accounts_independently_verified": True,
        "cross_unit_comparison": "separate saved-output learner/rule-control comparison and conditional gate",
        "conditional_gate_opened": False}
    return bridge.OutputReceipt(pin, size, envelope.geometry, wire.encode(verification),
        wire.encode(dict(Counter(row.scenario for row in rows))))
