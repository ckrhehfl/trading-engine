"""Write one EF snapshot and verify every stored record against raw inputs."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import product
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research import intraday_chart_snapshot as store
from research import intraday_chart_snapshot_reference as ref
from research.intraday_ed_inputs import MINUTE
from research.intraday_ed_snapshot_verify import reconstruct_session
from research.intraday_pattern_calibration import session_open, session_close


FEATURE_FILE = "ef-features.sqlite"
LABEL_FILE = "ef-labels.sqlite"


def _time(value):
    return None if value is None else value.isoformat()


def _counts(reasons):
    return dict(Counter(reason for reason in reasons if reason is not None))


def _rows(day):
    for frame in day.frames:
        chart = frame.chart_issues
        quality = None if chart is None else {
            "latest": _counts(issue.reason for issue in chart.latest),
            "volume": _counts(issue.reason for issue in chart.volume),
            "coarse": _counts(issue.reason for issue in chart.coarse),
            "previous": _counts(issue.reason for issue in chart.previous),
            "previous_opening": _time(chart.previous_expected_opening),
        }
        evidence = wire.encode({"decision_at": _time(frame.decision_at), "execution_at": _time(frame.execution_at),
                                "execution_mature_at": _time(frame.execution_mature_at),
                                "execution_issue": frame.execution_issue.reason if frame.execution_issue else None,
                                "entry_clock_eligible": frame.entry_clock_eligible, "chart": quality})
        peers = tuple(store.PeerRow(peer.scenario, peer.tensor, wire.encode({
            "expected": peer.expected_peer_ids, "common": peer.common_peer_ids, "denominator": peer.denominator,
            "issues": _counts(issue.reason for issue in peer.issues),
        })) for peer in frame.peers)
        label = None
        if frame.offset in store.ENTRY_OFFSETS:
            wire.require(frame.labels is not None, "EF entry grid lost label evidence")
            short, long = frame.labels.horizons
            values = ((long.gross_return, long.mature_at), (short.gross_return, short.mature_at),
                      (short.path.low_return, short.path.mature_at), (short.path.high_return, short.path.mature_at),
                      (long.path.low_return, long.path.mature_at), (long.path.high_return, long.path.mature_at))
            label = store.LabelRow(frame.day, frame.symbol, frame.offset, tuple(
                store.LabelHead(value, maturity) if value is not None else None for value, maturity in values),
                wire.encode(tuple((item.status, item.path.status) for item in (short, long))))
        yield store.SnapshotRow(frame.day, frame.symbol, frame.offset, frame.chart, peers,
                                frame.execution_open, frame.execution_state, evidence), label


def _day_rows(universe, reader, checkpoint):
    from research.intraday_chart_population import iter_chart_days

    for day in iter_chart_days(universe, reader, checkpoint):
        yield store.SnapshotDay(day.day, day.opening, day.closing, day.provenance, _rows(day))


def _expected_note(day, prior, previous_opening, offset, quote_state):
    opening = day.opening
    quote_index = offset + 3
    feature_clock = offset <= 315 and offset <= len(day.prices)
    chart = None
    if feature_clock:
        previous = (prior.price_issues if prior is not None else
                    ("previous_session_not_supplied" if previous_opening is None else "expected_previous_session_missing",))
        chart = {"latest": _counts(day.price_issues[offset - 60:offset]),
                 "volume": _counts(day.volume_issues[offset - 60:offset]),
                 "coarse": _counts(day.price_issues[max(0, offset - 120):offset]),
                 "previous": _counts(previous), "previous_opening": _time(previous_opening)}
    issue = ("execution_outside_continuous_cash_clock" if quote_state == "outside_clock" else day.price_issues[quote_index])
    return wire.encode({"decision_at": _time(opening + (offset + 2) * MINUTE),
                        "execution_at": _time(opening + quote_index * MINUTE),
                        "execution_mature_at": _time(opening + (quote_index + 3) * MINUTE) if quote_state == "available" else None,
                        "execution_issue": issue, "entry_clock_eligible": offset in store.ENTRY_OFFSETS
                        and quote_index + 30 < len(day.prices) - 15, "chart": chart})


def _label_evidence(day, offset, heads):
    pairs = []
    for horizon, point, path in ((15, 1, 2), (30, 0, 4)):
        if offset + 3 + horizon >= len(day.prices) - 15:
            pairs.append(("outside_clock", "outside_clock"))
        else:
            pairs.append(tuple("available" if heads[index] is not None else "unavailable" for index in (point, path)))
    return wire.encode(tuple(pairs))


def verify_records(features, labels, universe, reader, provenance: bytes, checkpoint) -> dict:
    """Compare persisted data to a separate decoder and exact arithmetic."""
    targets = tuple(sorted(universe.targets, key=lambda row: (row.session, row.scenario, row.rank)))
    wire.require(features.metadata == store.SnapshotMetadata(universe.sessions, targets, provenance),
                 "stored EF original scope or provenance differs")
    population = defaultdict(lambda: {"S1": [], "S2": []})
    for target in targets:
        population[target.session][target.scenario].append(target.symbol)
    stored, supervised = iter(features.rows()), iter(labels.audit_rows())
    counts = Counter()
    cohort = {scenario: Counter() for scenario in ("S1", "S2")}
    previous, previous_opening = {}, None
    for day, saved_day in zip(universe.sessions, features.days(), strict=True):
        checkpoint("ef_snapshot_verify_day")
        opening, closing = session_open(day), session_close(day)
        wire.require((saved_day.day, saved_day.opening, saved_day.closing) == (day, opening, closing),
                     "stored EF session clock differs")
        rosters = {scenario: tuple(sorted(symbols)) for scenario, symbols in population[day].items()}
        symbols = tuple(sorted(set(rosters["S1"]) | set(rosters["S2"])))
        decoded = {}
        actual_provenance = []
        for symbol in symbols:
            inputs = reconstruct_session(symbol, day, reader.load_raw_chunk("CC", symbol, day),
                                         reader.load_raw_chunk("CX", symbol, day))
            actual_provenance.append((symbol, inputs.metadata))
            decoded[symbol] = ref.original_day(inputs.session)
        wire.require(saved_day.provenance == tuple(actual_provenance), "stored EF raw provenance differs")
        prior = {symbol: ref.previous_levels(previous.get(symbol)) for symbol in symbols}
        for offset in store.QUOTE_OFFSETS:
            checkpoint("ef_snapshot_verify_clock")
            use_features = offset <= 315 and opening + offset * MINUTE <= closing
            peer_view = {scenario: ref.peer_at(decoded, roster, offset)
                         for scenario, roster in rosters.items()} if use_features else {}
            peer_gaps = {scenario: {symbol: Counter(reason for reason in decoded[symbol].price_issues[offset - 60:offset]
                                                   if reason is not None) for symbol in roster}
                         for scenario, roster in rosters.items()} if use_features else {}
            peer_totals = {scenario: Counter() for scenario in peer_gaps}
            for scenario, gaps in peer_gaps.items():
                for gap in gaps.values():
                    peer_totals[scenario].update(gap)
            for symbol in symbols:
                row = next(stored, None)
                wire.require(row is not None and (row.session, row.offset, row.symbol) == (day, offset, symbol),
                             "stored EF feature coordinate differs")
                current = decoded[symbol]
                chart = ref.chart_at(current, offset, prior[symbol])
                quote, status = ref.quote_at(current, offset)
                wire.require((row.chart, row.execution_open, row.execution_state, row.evidence) ==
                             (chart, quote, status, _expected_note(current, previous.get(symbol), previous_opening, offset, status)),
                             "stored EF chart, quote or availability differs")
                expected_peers = []
                for scenario, roster in rosters.items():
                    if use_features and symbol in roster:
                        tensor, common = peer_view[scenario][symbol]
                        peers = tuple(peer for peer in roster if peer != symbol)
                        gaps = peer_totals[scenario] - peer_gaps[scenario][symbol]
                        if tensor is None:
                            gaps["fewer_than_five_complete_original_peers"] = 1
                        expected_peers.append(store.PeerRow(scenario, tensor, wire.encode({
                            "expected": peers, "common": common, "denominator": len(peers), "issues": dict(gaps)})))
                        if offset in store.ENTRY_OFFSETS:
                            state = ("outside_clock" if offset + 33 >= len(current.prices) - 15 else
                                     "chart_unavailable" if chart is None else "chart_only" if tensor is None else "common")
                            cohort[scenario][state] += 1
                wire.require(row.peers == tuple(expected_peers), "stored EF peer population or values differ")
                counts["feature_rows"] += 1
                if offset in store.ENTRY_OFFSETS:
                    label = next(supervised, None)
                    heads = ref.labels_at(current, offset)
                    expected = tuple(store.LabelHead(*head) if head is not None else None for head in heads)
                    wire.require(label is not None and (label.session, label.offset, label.symbol) == (day, offset, symbol)
                                 and (label.heads, label.evidence) == (expected, _label_evidence(current, offset, heads)),
                                 "stored EF outcome or maturity differs")
                    counts["label_rows"] += 1
                    counts["primary_labels_known"] += heads[0] is not None
        previous, previous_opening = decoded, opening
        reader.assert_unchanged()
    wire.require(next(stored, None) is None and next(supervised, None) is None, "extra stored EF coordinates")
    return {"verified": True, "schema": "EF-independent-snapshot-verification-v1", **counts,
            "candidate_counts": {scenario: {state: values[state] for state in
                                 ("outside_clock", "chart_unavailable", "chart_only", "common")}
                                 for scenario, values in cohort.items()}}


def build_snapshot(directory: Path, universe, reader, provenance: bytes, checkpoint):
    """Publish the seal only after independent complete reread of both artifacts."""
    feature_file, label_file = directory / FEATURE_FILE, directory / LABEL_FILE
    metadata = store.SnapshotMetadata(universe.sessions, universe.targets, provenance)
    seal = store.write_snapshot(feature_file, label_file, metadata, _day_rows(universe, reader, checkpoint), checkpoint)
    with (store.open_feature_snapshot(feature_file, seal, checkpoint) as features,
          store.open_label_snapshot(label_file, seal, checkpoint) as labels):
        verification = verify_records(features, labels, universe, reader, provenance, checkpoint)
    return seal, verification
