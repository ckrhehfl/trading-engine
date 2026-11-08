"""Bounded EI feature/label snapshot over the original CB/CC/CX population.

This worker is called only by the registered lifecycle after STARTED. It grants
no input-access authority. The saved-bundle audit recounts observations without
reexecuting science; the worker separately reconstructs every original row.
"""

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_snapshot as snapshot
from research import intraday_context_snapshot_reference as reference
from research import intraday_context_support_unit as support
from research.intraday_scenario_snapshot import SnapshotSeal, _read
from research.intraday_scenario_snapshot_unit import BundleSeal, _sealed_input, _sealed_output, _stream_member


UNIT_ID = "EI-SNAPSHOT-1"
SCHEMA = "EI-SNAPSHOT-1-context-snapshot-v1"
OUTPUT = "ei-context-snapshot.bundle"
MAX_OUTPUT = 256 * wire.MIB  # Includes the header, not 256 MiB plus a header.
MAX_HEADER = 4096
FEATURE_FILENAME = "ei-context-features.jsonl"
LABEL_FILENAME = "ei-context-labels.jsonl"
ORIGINAL_SPEC, ORIGINAL_PIN = support.ORIGINAL_SPEC, support.ORIGINAL_PIN
FULL, WINDOW, COUNTS = support.FULL, support.WINDOW, support.COUNTS
SNAPSHOT_CONTRACT = {
    "feature_schema": snapshot.FEATURE_SCHEMA, "label_schema": snapshot.LABEL_SCHEMA,
    "question_scheme": snapshot.SCHEME_ID, "feature_projection": snapshot.FEATURE_PROJECTION,
    "history_minutes": 30, "horizons_minutes": [5, 15, 30],
    "roster": "original_cb_union_including_unknown_and_no_event",
    "context_identity": "frozen_episode_origin_and_separate_question_time_context",
    "question_geometry": "fixed_anchor_level_band_R_over_4_stop_minus_R_target_plus_2R_expiry_30m",
    "feature_label_order": "seal_and_verify_full_feature_stream_before_first_outcome_callback",
    "unknown_paths": "preserve_missing_order_ambiguity_maturity_and_shorter_supported_horizons",
    "independent_decoder": "intraday_ed_snapshot_verify.reconstruct_reference_session",
}
OUTPUT_CONTRACT = {"filename": OUTPUT, "schema": SCHEMA, "maximum_bytes": MAX_OUTPUT,
                   "maximum_header_bytes": MAX_HEADER}


def contract(specification: bytes, original: bytes) -> tuple[dict, tuple[bridge.InputPin, ...], str]:
    """Bind the fixed source and science; a false readiness flag stays closed."""
    wire.require(wire.digest(original) == ORIGINAL_PIN, "EI snapshot original registration changed")
    spec = json.loads(specification)
    wire.require(type(spec) is dict and spec.get("unit_id") == UNIT_ID
                 and spec.get("input_contract") == wire.EI_SUPPORT_INPUT_CONTRACT
                 and spec.get("source_registration") == {"path": ORIGINAL_SPEC, "sha256": ORIGINAL_PIN}
                 and wire.encode(spec.get("geometry")) == wire.encode(FULL)
                 and wire.encode(spec.get("input_roster")) == wire.encode(wire.EI_SUPPORT_INPUT_BOUNDS)
                 and wire.encode(spec.get("snapshot_contract")) == wire.encode(SNAPSHOT_CONTRACT)
                 and wire.encode(spec.get("output")) == wire.encode(OUTPUT_CONTRACT)
                 and spec.get("resource_profile") == wire.EI_LOCAL_MODEL.id
                 and spec.get("execution_host") == "local_wsl_only"
                 and spec.get("actual_attempt") == "actual1"
                 and type(spec.get("automatic_actual_retries")) is int and spec["automatic_actual_retries"] == 0
                 and spec.get("live_or_paper_order_capable") is False
                 and type(spec.get("actual_runner_ready")) is bool, "EI snapshot registration changed")
    source = json.loads(original)["actual_source_preparation"]
    wire.require(source["geometry"] == FULL and source["window"] == [WINDOW["start"], WINDOW["end"]],
                 "EI snapshot original geometry differs")
    originals = {item["name"]: item for item in source["local_received_sources"]}
    pins = []
    for name, maximum in wire.EI_SUPPORT_INPUT_BOUNDS:
        item = originals[name]
        wire.require(item["maximum"] == maximum and wire.valid_sha(item["sha256"]), "EI snapshot source pin differs")
        pins.append(bridge.InputPin(name, item["path"], item["sha256"], maximum))
    return spec, tuple(pins), support.decoded_pin(source["decoded_archives"])


def make_invented(directory, sessions, checkpoint):
    return support.make_invented(directory, sessions, checkpoint)


def _members(header: dict) -> tuple[SnapshotSeal, SnapshotSeal]:
    wire.require(type(header) is dict and set(header) == {"schema", "feature_header", "features", "labels"}
                 and header["schema"] == SCHEMA, "EI snapshot bundle header differs")
    seals = []
    for name, schema in (("features", snapshot.FEATURE_SCHEMA), ("labels", snapshot.LABEL_SCHEMA)):
        raw = header[name]
        wire.require(type(raw) is dict and set(raw) == {"schema", "sha256", "size", "records"}
                     and raw["schema"] == schema and wire.valid_sha(raw["sha256"])
                     and type(raw["size"]) is int and 0 < raw["size"] <= snapshot.MAX_BYTES
                     and type(raw["records"]) is int and 1 <= raw["records"] <= snapshot.MAX_COORDINATES,
                     "EI snapshot bundle member seal differs")
        seals.append(SnapshotSeal(**raw))
    feature_header = header["feature_header"]
    wire.require(type(feature_header) is dict and set(feature_header) == {
        "schema", "projection", "questions", "roster_sha256", "coordinates", "memberships"}
        and feature_header["schema"] == snapshot.FEATURE_SCHEMA
        and feature_header["projection"] == snapshot.FEATURE_PROJECTION and feature_header["questions"] == snapshot.SCHEME_ID
        and wire.valid_sha(feature_header["roster_sha256"])
        and type(feature_header["coordinates"]) is int
        and feature_header["coordinates"] == seals[0].records == seals[1].records
        and type(feature_header["memberships"]) is int
        and seals[0].records <= feature_header["memberships"] <= min(2 * seals[0].records, snapshot.MAX_TARGETS),
        "EI snapshot feature header differs")
    return tuple(seals)


def _headers(header: dict, features: SnapshotSeal) -> tuple[bytes, bytes]:
    return wire.encode(header["feature_header"]), wire.encode(snapshot._label_header(features))


def _pack_bundle(path, feature_path, feature_seal, label_path, label_seal, checkpoint, *, expected_targets) -> BundleSeal:
    header = {"schema": SCHEMA, "feature_header": snapshot._header(snapshot._roster(expected_targets)),
              "features": asdict(feature_seal), "labels": asdict(label_seal)}
    features, labels = _members(header)
    raw = wire.encode(header)
    size = len(raw) + features.size + labels.size
    wire.require(len(raw) <= MAX_HEADER and size <= MAX_OUTPUT, "EI snapshot bundle size exceeded")
    digest = sha256(raw)
    with _sealed_output(path) as target:
        wire.write_all(target, raw)
        for member, seal, first in zip((feature_path, label_path), (features, labels), _headers(header, features), strict=True):
            with _sealed_input(member) as (source, actual_size):
                wire.require(actual_size == seal.size, "EI snapshot bundle member size differs")
                _stream_member(source, seal, first, checkpoint, target=target, bundle_digest=digest)
                wire.require(not source.read(1), "EI snapshot bundle trailing member bytes")
    return BundleSeal(digest.hexdigest(), size)


def _read_bundle(path, checkpoint, destinations=None, *, expected_sha256=None, expected_bytes=None
                 ) -> tuple[SnapshotSeal, SnapshotSeal]:
    pinned = expected_sha256 is not None or expected_bytes is not None
    wire.require(not pinned or wire.valid_sha(expected_sha256)
                 and type(expected_bytes) is int and 0 < expected_bytes <= MAX_OUTPUT,
                 "EI snapshot saved bundle pin differs")
    with _sealed_input(path) as (source, size):
        wire.require(0 < size <= MAX_OUTPUT and (not pinned or size == expected_bytes),
                     "EI snapshot bundle size or external pin differs")
        raw = source.readline(MAX_HEADER + 1)
        wire.require(raw.endswith(b"\n") and len(raw) <= MAX_HEADER, "EI snapshot bundle header exceeded")
        header = json.loads(raw)
        wire.require(wire.encode(header) == raw, "EI snapshot bundle header noncanonical")
        features, labels = _members(header)
        wire.require(len(raw) + features.size + labels.size == size, "EI snapshot bundle member length differs")
        digest = sha256(raw) if pinned else None
        for index, (seal, first) in enumerate(zip((features, labels), _headers(header, features), strict=True)):
            if destinations is None:
                _stream_member(source, seal, first, checkpoint, bundle_digest=digest)
            else:
                with _sealed_output(destinations[index]) as target:
                    _stream_member(source, seal, first, checkpoint, target=target, bundle_digest=digest)
        wire.require(not source.read(1), "EI snapshot bundle trailing bytes")
        wire.require(not pinned or digest.hexdigest() == expected_sha256, "EI snapshot saved bundle pin differs")
    return features, labels


def verify_bundle(path, checkpoint) -> tuple[SnapshotSeal, SnapshotSeal]:
    """Verify framing, hashes and both bound members without loading the bundle."""
    return _read_bundle(path, checkpoint)


def open_bundle(path, directory, checkpoint, *, expected_sha256=None, expected_bytes=None):
    """Unpack exclusively into one caller-owned empty private directory."""
    wire.safe_path(directory)
    mode = directory.stat().st_mode
    wire.require(stat.S_ISDIR(mode) and stat.S_IMODE(mode) == 0o700 and not any(directory.iterdir()),
                 "EI snapshot bundle fresh private directory required")
    features, labels = directory / FEATURE_FILENAME, directory / LABEL_FILENAME
    feature_seal, label_seal = _read_bundle(path, checkpoint, (features, labels),
                                          expected_sha256=expected_sha256, expected_bytes=expected_bytes)
    return features, feature_seal, labels, label_seal


def _population(universe, geometry):
    from research.intraday_trader_inputs import _groups

    groups = _groups(universe)
    counts = {scenario: {"original": len(universe.coordinates(scenario))} for scenario in ("S1", "S2")}
    wire.require(len(universe.sessions) == geometry["sessions"] and len(groups) == geometry["union_coordinates"]
                 and all(row["original"] == geometry["targets_per_scenario"] for row in counts.values()),
                 "EI snapshot original population differs")
    return groups, counts


def audit_saved_bundle(path, directory, *, expected_sha256, expected_bytes, universe, expected_geometry, checkpoint):
    """Recount a pinned saved bundle against its original CB roster, no science.

    ``universe`` must come from the separately pinned original CB bytes. The
    caller binds the returned seals/counts to the worker proof and input manifest.
    This audit does not certify original observations or rerun their algorithms.
    """
    wire.require(wire.valid_sha(expected_sha256) and type(expected_bytes) is int
                 and 0 < expected_bytes <= MAX_OUTPUT, "EI snapshot saved bundle pin differs")
    groups, candidates = _population(universe, expected_geometry)
    feature_path, feature_seal, label_path, label_seal = open_bundle(path, directory, checkpoint,
        expected_sha256=expected_sha256, expected_bytes=expected_bytes)
    features = snapshot.read_features(feature_path, feature_seal, expected_targets=universe.targets)
    labels = _read(label_path, label_seal, snapshot._label_header(feature_seal), maximum=snapshot.MAX_COORDINATES)
    total, scenarios = reference._Counts(), {name: reference._Counts() for name in ("S1", "S2")}
    calendars = {}
    for coordinate, (value, raw) in zip(features, labels, strict=True):
        checkpoint("ei_snapshot_saved_coordinate")
        label = snapshot._read_label_coordinate(value)
        snapshot._validate_labels(label, coordinate)
        wire.require(wire.encode(snapshot._label_coordinate(label)) == raw, "EI snapshot saved label readback differs")
        wire.require(label.status != "session_missing", "EI snapshot label-only missing original session")
        if coordinate.opening is not None:
            clocks = coordinate.opening, coordinate.closing
            wire.require(calendars.setdefault(coordinate.session, clocks) == clocks, "EI snapshot same-date calendar differs")
        total.add(coordinate, label)
        for target in coordinate.targets:
            scenarios[target.scenario].add(coordinate, label)
    counts = {"union": total.payload(), "scenarios": {name: value.payload() for name, value in scenarios.items()}}
    wire.require(total.coordinates == len(groups)
                 and all(scenarios[name].coordinates == candidates[name]["original"] for name in scenarios),
                 "EI snapshot saved population differs")
    return {"full_eof_verified": True, "science_reexecuted": False, "counts": counts,
            "counts_sha256": wire.digest(wire.encode(counts)), "candidate_counts": candidates,
            "original_dates": len(universe.sessions), "original_coordinates": len(groups),
            "original_memberships": len(universe.targets), "universe_sha256": universe.raw_sha256,
            "calendar_sha256": wire.digest(wire.encode(universe.sessions)),
            "members": {"features": asdict(feature_seal), "labels": asdict(label_seal)}}


def compute(directory, envelope, plan, *, invented_only, checkpoint) -> bridge.OutputReceipt:
    """Seal every feature before labels, then independently check original bytes."""
    from research import intraday_ed_sources as sources
    from research.intraday_ed_comparison_unit import _decoded_digest
    from research.intraday_ed_inputs import MINUTE
    from research.intraday_ed_snapshot_verify import reconstruct_reference_session
    from research.intraday_pattern_calibration import session_open, session_close

    wire.require(type(invented_only) is bool and envelope.input_contract == wire.EI_SUPPORT_INPUT_CONTRACT,
                 "EI snapshot input contract differs")
    geometry = bridge.canonical(envelope.geometry)
    if invented_only:
        wire.require(wire.encode(geometry) == wire.encode(support.invented_geometry(geometry["sessions"])),
                     "EI snapshot invented geometry differs")
    else:
        wire.require(plan is not None and plan.unit_id == UNIT_ID and plan.actual_runner_ready is True
                     and plan.resource_profile == wire.EI_LOCAL_MODEL.id, "EI snapshot actual readiness closed")
        bridge._actual_envelope(envelope, plan)
    manifest = envelope.payload()["inputs"]
    wire.validate_registered_input_manifest(manifest, input_contract=wire.EI_SUPPORT_INPUT_CONTRACT)
    files = {item.name: item for item in envelope.manifest}
    paths = {name: directory / "received" / name for name in files}
    for name, item in files.items():
        wire.require(wire.file_digest(paths[name], item.maximum, checkpoint) == (item.sha256, item.size),
                     "EI snapshot received source differs")
    original_raw = bridge.bounded_read(Path(plan.local_worktree) / ORIGINAL_SPEC, wire.MIB)
    wire.require(wire.digest(original_raw) == ORIGINAL_PIN, "EI snapshot original registration changed during worker")
    decoded = json.loads(original_raw)["actual_source_preparation"]["decoded_archives"]
    if invented_only:
        decoded = {kind: _decoded_digest(paths[kind], sources.MAX_STREAM, checkpoint) for kind in ("CC", "CX")}
    wire.require(envelope.stream_sha256 == support.decoded_pin(decoded), "EI snapshot decoded stream differs")
    universe = sources.load_universe(bridge.bounded_read(paths["CB"], files["CB"].maximum),
                                    files["CB"].sha256, expected_sessions=geometry["sessions"])
    groups, candidates = _population(universe, geometry)
    if not invented_only:
        wire.require((universe.sessions[0], universe.sessions[-1]) == (WINDOW["start"], WINDOW["end"]),
                     "EI snapshot actual calendar differs")
    archives = {kind: (paths[kind], sources.RawArchivePin(kind, files[kind].sha256, decoded[kind],
        geometry[kind.lower() + "_chunks"], geometry[kind.lower() + "_rows"],
        0 if kind == "CC" else geometry["cx_daily_rows"])) for kind in ("CC", "CX")}
    cache = directory / "raw-byte-chunks.sqlite"
    cache_seal = sources.ByteChunkIndex.build(cache, archives, checkpoint)
    output = directory / OUTPUT
    with sources.audit_index(cache, archives, checkpoint, expected_cache_sha256=cache_seal["sha256"]) as reader:
        def session(day, symbol):
            return sources.session_inputs(symbol, day, reader.load_raw_chunk("CC", symbol, day),
                                          reader.load_raw_chunk("CX", symbol, day)).session

        def coordinates():
            for (day, symbol), targets in sorted(groups.items()):
                checkpoint("ei_snapshot_feature_coordinate")
                yield snapshot.project_coordinate(tuple(targets), session(day, symbol))

        def outcome_session(day, symbol):
            checkpoint("ei_snapshot_label_coordinate")
            return session(day, symbol)

        def independent_session(day, symbol):
            opening, closing = session_open(day), session_close(day)
            starts = tuple(opening + index * MINUTE for index in range((closing - opening) // MINUTE))
            return reconstruct_reference_session(symbol, day, reader.load_raw_chunk("CC", symbol, day),
                reader.load_raw_chunk("CX", symbol, day), requested_starts=starts)

        with TemporaryDirectory(prefix="ei-snapshot-members-", dir=directory) as temporary:
            feature_path, label_path = (Path(temporary) / name for name in (FEATURE_FILENAME, LABEL_FILENAME))
            features = snapshot.write_features(feature_path, coordinates(), expected_targets=universe.targets)
            checkpoint("ei_snapshot_features_sealed")
            labels = snapshot.write_labels(label_path, feature_path, features, outcome_session, expected_targets=universe.targets)
            summary = reference.verify_snapshot(feature_path, features, label_path, labels,
                expected_targets=universe.targets, reference_session=independent_session, checkpoint=checkpoint)
            summary.update(members={"features": asdict(features), "labels": asdict(labels)},
                           universe_sha256=universe.raw_sha256, calendar_sha256=wire.digest(wire.encode(universe.sessions)))
            seal = _pack_bundle(output, feature_path, features, label_path, labels, checkpoint,
                                expected_targets=universe.targets)
        reader.assert_unchanged()
        raw_audit = dict(reader.receipt)
    wire.require(wire.file_digest(output, MAX_OUTPUT, checkpoint) == (seal.sha256, seal.size), "EI snapshot output changed")
    proof = {"verified": True, "schema": SCHEMA, "full_eof_verified": True,
             "all_original_coordinates_verified": True, "invented_only": invented_only, "full_geometry": geometry == FULL,
             "input_manifest": manifest, "input_manifest_sha256": wire.digest(wire.encode(manifest)),
             "raw_byte_audit": raw_audit, "snapshot_summary": summary,
             "summary_sha256": wire.digest(wire.encode(summary)), "support_totals": summary["counts"],
             "historical_availability_certified": False, "stored_volume_units_certified": False,
             "contains_model_fits_or_returns": False}
    return bridge.OutputReceipt(seal.sha256, seal.size, envelope.geometry, wire.encode(proof), wire.encode(candidates))
