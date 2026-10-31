"""Full ED snapshot science reconstruction from independently supplied raw bytes.

This verifier never calls the parent, feature, rule, label, fold or snapshot
producer. Shared trusted boundaries are authenticated CC/CX decoding, the
fixed calendar, declared datatypes/constants and canonical JSON serialization.
Filing HTML is parsed by the shared HTML tree; admission semantics are checked
here separately. A current listing is not historical issuer/completeness proof.
No reader opens actual inputs until the registered caller supplies them.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
from math import isfinite, sqrt
import re

from research import intraday_ed_disclosures as dart
from research import intraday_ed_inputs as inputs
from research import intraday_ed_replay as replay
from research import intraday_ed_sources as sources
from research import intraday_pattern_calibration as calendar
from research.intraday_pattern_events import Bar


MINUTE = inputs.MINUTE
SCHEMA = "ED-H1-common-snapshot-v1"


class SnapshotVerificationRefusal(ValueError):
    """A disagreement is an integrity failure, never a no-event/zero outcome."""


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise SnapshotVerificationRefusal(reason)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _difference_path(actual: object, expected: object, path: str = "record") -> str:
    """Report only the first field name/index, never private observation values."""
    if type(actual) is not type(expected):
        return path + "<type>"
    if type(expected) is dict:
        if set(actual) != set(expected):
            return path + "<keys>"
        for name in expected:
            if _canonical(actual[name]) != _canonical(expected[name]):
                return _difference_path(actual[name], expected[name], path + "." + name)
    elif type(expected) is list:
        if len(actual) != len(expected):
            return path + "<length>"
        for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
            if _canonical(left) != _canonical(right):
                return _difference_path(left, right, path + f"[{index}]")
    return path


def _project(value: object) -> object:
    """Wire projection only; no scientific producer is consulted."""
    if isinstance(value, Fraction):
        return {"fraction": [value.numerator, value.denominator]}
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if type(value) is float:
        _need(isfinite(value), "nonfinite_verifier_float")
        return {"float64": value.hex()}
    if isinstance(value, datetime):
        return value.isoformat()
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, bytes):
        return json.loads(value)
    if isinstance(value, inputs.SessionInputs):
        return {"symbol": value.symbol, "opening": _project(value.opening),
                "closing": _project(value.closing),
                "minutes": [_project(value.minutes[at]) for at in sorted(value.minutes)]}
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _project(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _project(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_project(item) for item in value]
    return value


def _number(value: object, *, positive: bool) -> Decimal | None:
    if type(value) is not str:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() and (number > 0 if positive else number >= 0) else None


def reconstruct_session(symbol: str, day: str, price: dict | None,
                        volume: dict | None, *, requested_starts: tuple[datetime, ...] | None = None
                        ) -> sources.SessionSourceView:
    """Rebuild minute collisions, numeric states and source metadata from CC/CX."""
    opening, closing = calendar.session_open(day), calendar.session_close(day)
    requested = None if requested_starts is None else frozenset(requested_starts)
    buckets: dict[str, dict[datetime, list[dict]]] = {"CC": {}, "CX": {}}
    for kind, chunk in (("CC", price), ("CX", volume)):
        if chunk is None:
            continue
        identity = sources._raw_identity(chunk, kind)  # trusted raw framing boundary
        _need(identity[:2] == (symbol, day), "raw_session_identity_differs")
        for row in chunk["rows"]:
            at = datetime.fromtimestamp((row["open_time_ms"] // 60_000) * 60, opening.tzinfo)
            if requested is not None and at not in requested:
                continue
            buckets[kind].setdefault(at, []).append(row)
    minutes, quality = {}, []
    for at in sorted(buckets["CC"].keys() | buckets["CX"].keys()):
        prices, volumes = buckets["CC"].get(at, []), buckets["CX"].get(at, [])
        ps = "conflict" if len(prices) > 1 else "observed" if prices else "missing"
        vs = "conflict" if len(volumes) > 1 else "observed" if volumes else "missing"
        pq, vq = ps, vs
        ohlc = tuple(_number(prices[0][name], positive=True) for name in
                     ("open", "high", "low", "close")) if ps == "observed" else None
        if ohlc is not None and (None in ohlc or not
                ohlc[2] <= min(ohlc[0], ohlc[3]) <= max(ohlc[0], ohlc[3]) <= ohlc[1]):
            ps, ohlc, pq = "conflict", None, "raw_invalid_numeric_or_ohlc"
        shares = _number(volumes[0]["volume"], positive=False) if vs == "observed" else None
        if vs == "observed" and shares is None:
            vs, vq = "conflict", "raw_invalid_numeric"
        if pq != "observed" or vq != "observed":
            quality.append({"minute": at.isoformat(), "price": pq, "volume": vq})
        minutes[at] = inputs.Minute(symbol, at, at + 3 * MINUTE, ohlc, shares, ps, vs)
    metadata = {"symbol": symbol, "session": day, "source_event_at": None,
        "available_at_assumption": "minute-end-plus2-not-retrieved-at", "price_basis": None,
        "physical_unit_status": None, "venue_channel": None, "session_state": None,
        "share_quantity_interpretation": "stored volume field; physical share units unverified",
        "stored_numeric_observations_are_unit_certification": False, "minute_quality": quality,
        "sources": {kind: None if chunk is None else
            {key: value for key, value in chunk.items() if key not in {"rows", "daily_rows"}} | {
                "raw_chunk_sha256": hashlib.sha256(_canonical(chunk) + b"\n").hexdigest(),
                "stored_fetched_at": [[row["open_time_ms"], row["fetched_at"]] for row in chunk["rows"]]}
            for kind, chunk in (("CC", price), ("CX", volume))}}
    if requested_starts is not None:
        metadata["requested_minute_starts"] = [at.isoformat() for at in requested_starts]
    return sources.SessionSourceView(inputs.SessionInputs(symbol, opening, closing, minutes),
                                     _canonical(metadata))


def independent_report_semantics(raw: bytes) -> dict[str, str]:
    """Admit only a plain report anchor and unadorned span wrappers.

    The shared tree provides HTML structure, not the producer's semantic test.
    Any extra report-cell text, annotation, image or attribute yields unknown.
    """
    tree = dart._Document(raw.decode("utf-8-sig", errors="strict")).root
    bodies = tree.find("tbody", id="tbody")
    _need(len(bodies) == 1, "independent_filing_container_differs")
    result = {}
    for row in bodies[0].find("tr"):
        cells = [node for node in row.children if isinstance(node, dart._Element) and node.tag == "td"]
        _need(len(cells) == 6, "independent_filing_columns_differ")
        anchors = cells[2].find("a")
        _need(len(anchors) == 1, "independent_filing_anchor_differs")
        anchor = anchors[0]
        receipt = (anchor.attrs.get("id") or "").removeprefix("r_")
        _need(re.fullmatch(r"[0-9]{14}", receipt) is not None and receipt not in result,
              "independent_filing_receipt_differs")
        stack = [(cells[2], False)]
        plain = True
        while stack:
            node, inside = stack.pop()
            if isinstance(node, str):
                plain = plain and (inside or not node.strip())
                continue
            if node is anchor:
                plain = plain and set(node.attrs) <= {"href", "id", "onclick", "target"}
                plain = plain and node.attrs.get("target", "_blank") == "_blank"
                inside = True
            elif node is not cells[2]:
                plain = plain and node.tag == "span" and not node.attrs
            else:
                plain = plain and not node.attrs
            stack.extend((child, inside) for child in reversed(node.children))
        result[receipt] = "plain_original_title_grammar" if plain else "semantic_unknown_annotation"
    return result


def verify_filing_semantics(source: sources.FilingSource, raw_files: Mapping[str, bytes]) -> None:
    """Check every admitted/excluded row against pinned saved listing HTML."""
    _need(len(set(source.query_codes)) == len(source.query_codes)
          and not set(source.complete_codes) & set(source.unknown_codes)
          and set(source.complete_codes) | set(source.unknown_codes) == set(source.query_codes),
          "independent_filing_query_population_differs")
    rows_by_symbol: dict[str, list[sources.FilingRow]] = {}
    for row in source.rows:
        rows_by_symbol.setdefault(row.symbol, []).append(row)
    queries = json.loads(source.query_metadata)
    for index, code in enumerate(source.query_codes, 1):
        if code not in source.complete_codes:
            _need(not rows_by_symbol.get("KRX:" + code), "unknown_query_has_filing_rows")
            continue
        raw = raw_files.get(f"DART-R{index:03d}")
        _need(type(raw) is bytes and hashlib.sha256(raw).hexdigest() == queries[code]["raw_sha256"],
              "independent_filing_raw_pin_differs")
        semantics = independent_report_semantics(raw)
        bodies = dart._Document(raw.decode("utf-8-sig", errors="strict")).root.find("tbody", id="tbody")
        raw_identity = {}
        for tr in bodies[0].find("tr"):
            cells = [node for node in tr.children if isinstance(node, dart._Element) and node.tag == "td"]
            anchor = cells[2].find("a")[0]
            receipt = anchor.attrs["id"].removeprefix("r_")
            title = " ".join(anchor.text().split())
            match = re.fullmatch(r"((?:\[[^\[\]]+\]\s*)*)(사업|반기|분기)보고서\s*\(([0-9]{4})\.([0-9]{2})\)", title)
            _need(match is not None, "independent_filing_title_grammar_differs")
            prefixes = re.findall(r"\[([^\[\]]+)\]", match[1])
            _need(len(set(prefixes)) == len(prefixes) and set(prefixes) <= dart.VERSION_PREFIXES,
                  "independent_filing_version_prefix_differs")
            observed = " ".join(cells[4].text().split()).replace(".", "-")
            _need(date.fromisoformat(observed).strftime("%Y%m%d") == receipt[:8],
                  "independent_filing_observation_date_differs")
            raw_identity[receipt] = title, match[2] + "보고서", prefixes, observed
        expected_rows = rows_by_symbol.get("KRX:" + code, [])
        _need(set(semantics) == {row.receipt for row in expected_rows}, "independent_filing_population_differs")
        for row in expected_rows:
            metadata = json.loads(row.metadata)
            title, report_type, prefixes, observed = raw_identity[row.receipt]
            _need(metadata["title"] == title and metadata["report_type"] == report_type
                  and metadata["version_prefixes"] == prefixes and row.observation_date.isoformat() == observed,
                  "independent_filing_row_identity_differs")
            event_type = (sources.TYPE_MAP.get(metadata["report_type"])
                          if not metadata["version_prefixes"] and
                          semantics[row.receipt] == "plain_original_title_grammar" else None)
            _need(row.semantic_state == semantics[row.receipt] and row.event_type == event_type
                  and metadata["semantic_state"] == semantics[row.receipt],
                  "independent_filing_semantic_admission_differs")


def reconstruct_context(universe: sources.UniverseCalendar, source: sources.FilingSource,
                        coordinate: sources.TargetCoordinate) -> sources.OpeningContextRecord:
    scenario, symbol, day = coordinate.scenario, coordinate.symbol, coordinate.session
    lag = {"S1": 1, "S2": 2}[scenario]
    opening = calendar.session_open(day)
    index = universe.sessions.index(day)
    admitted, annotations = [], False
    for row in source.rows:
        if row.symbol != symbol:
            continue
        event_index = bisect_right(universe.sessions, row.observation_date.isoformat()) + lag - 1
        if event_index >= len(universe.sessions) or universe.sessions[event_index] != day:
            continue
        annotations = annotations or row.semantic_state != "plain_original_title_grammar"
        if row.event_type is not None:
            admitted.append(row)
    covered = index >= lag and universe.sessions[index - lag] >= source.window_start.isoformat()
    if symbol.removeprefix("KRX:") not in source.complete_codes:
        state, reason = "UNKNOWN", "query_unknown_or_unqueried_symbol"
    elif annotations:
        state, reason = "UNKNOWN", "unadjudicated_report_cell_annotation"
    elif not covered or day > source.window_end.isoformat():
        state, reason = "UNKNOWN", "outside_defined_query_or_prior_date_boundary"
    elif admitted:
        state, reason = "ELIGIBLE_ORIGINAL", "unprefixed_current_list_original"
    else:
        state, reason = "KNOWN_NO_WHITELISTED_LIST_EVENT", "conditional_complete_current_query_only"
    context = inputs.OpeningContext(symbol, opening, state,
        opening.replace(hour=8, minute=30) if state != "UNKNOWN" else None,
        tuple(sorted({row.event_type for row in admitted})) if state == "ELIGIBLE_ORIGINAL" else (),
        tuple(sorted(row.receipt for row in admitted)) if state == "ELIGIBLE_ORIGINAL" else (),
        opening if state == "ELIGIBLE_ORIGINAL" else None)
    metadata = {"scenario": scenario, "available_at_assumption": "date-only-strict-future-session-0830",
        "source_event_at": None, "first_seen_at": None, "is_final": None,
        "publication_sha256": source.publication_sha256, "evidence_level": "current_query_only",
        "query_metadata": json.loads(source.query_metadata).get(symbol.removeprefix("KRX:")),
        "filing_rows": [json.loads(row.metadata) for row in admitted],
        "historical_issuer_binding_certified": False, "historical_completeness_certified": False}
    return sources.OpeningContextRecord(context, reason, _canonical(metadata))


def _slice(session: inputs.SessionInputs, offset: int, count: int, decision: datetime, *,
           volume: bool = False) -> tuple[tuple[inputs.Minute, ...], tuple[inputs.Issue, ...]]:
    rows, issues = [], []
    for index in range(offset, offset + count):
        at = session.opening + index * MINUTE
        row = session.minutes.get(at)
        if row is None:
            issues.append(inputs.Issue(session.symbol, at, "missing_minute"))
            continue
        _need(row.symbol == session.symbol and row.start_at == at and
              at + MINUTE <= row.available_at <= decision, "independent_minute_clock_differs")
        if row.price_state != "observed":
            issues.append(inputs.Issue(session.symbol, at, "price_" + row.price_state))
        if volume and row.volume_state != "observed":
            issues.append(inputs.Issue(session.symbol, at, "volume_" + row.volume_state))
        rows.append(row)
    return tuple(rows), tuple(issues)


def reconstruct_parent(session: inputs.SessionInputs) -> inputs.ParentScan:
    if session.closing - 80 * MINUTE < session.opening + 30 * MINUTE:
        return inputs.ParentScan("known_no_parent")
    bars: list[Bar] = []
    end = session.opening + 5 * MINUTE
    while end <= session.closing - 80 * MINUTE:
        offset = int((end - session.opening) // MINUTE) - 5
        decision = max(end + 2 * MINUTE, session.opening + 32 * MINUTE)
        rows, issues = _slice(session, offset, 5, decision)
        if issues:
            return inputs.ParentScan("parent_identity_unknown", issues=issues)
        prices = [row.ohlc for row in rows]
        bar = Bar(end - 5 * MINUTE, end, max(end + 2 * MINUTE, *(row.available_at for row in rows)),
                  prices[0][0], max(row[1] for row in prices), min(row[2] for row in prices), prices[-1][3])
        if len(bars) >= 5 and bar.close > max(prior.high for prior in bars[-5:]):
            return inputs.ParentScan("parent", inputs.H1Parent(session.symbol, session.opening, bar, tuple(bars[-5:])))
        bars.append(bar)
        end += 5 * MINUTE
    return inputs.ParentScan("known_no_parent")


def _return(rows: tuple[inputs.Minute, ...]) -> Fraction:
    return Fraction(rows[-1].ohlc[3]) / Fraction(rows[0].ohlc[0]) - 1


def _median20(values: Iterable[Fraction]) -> Fraction:
    ordered = sorted(values)
    _need(len(ordered) == 20, "independent_twenty_expected_required")
    return (ordered[9] + ordered[10]) / 2


def _categories(context: inputs.OpeningContext, parent: inputs.H1Parent):
    if context.state == "UNKNOWN":
        return None, None, None
    if context.state == "KNOWN_NO_WHITELISTED_LIST_EVENT":
        return (Fraction(1),) + (Fraction(0),) * 3, (Fraction(1),) + (Fraction(0),) * 10, None
    age = int((parent.decision_at - context.event_opening) // MINUTE)
    _need(30 <= age < 330, "independent_event_age_outside_vocabulary")
    return (tuple(Fraction(int(kind in context.original_types)) for kind in inputs.EVENT_TYPE_NAMES),
            tuple(Fraction(int(index == age // 30)) for index in range(11)), age)


def reconstruct_window(current: inputs.SessionInputs, parent: inputs.H1Parent, *,
                       original_peers: tuple[str, ...], peers: Mapping[str, inputs.SessionInputs | None],
                       expected: tuple[datetime, ...], references: Mapping[datetime, inputs.SessionInputs],
                       context: inputs.OpeningContext) -> inputs.H1WindowInputs:
    """Independently calculate the fixed common30 peer set and exact channels."""
    _need(len(expected) == 20, "independent_twenty_expected_required")
    offset = int((parent.trigger.end_at - current.opening) // MINUTE) - 30
    target, target_issues = _slice(current, offset, 30, parent.decision_at, volume=True)
    common, peer_rows, peer_issues = [], [], []
    for name in original_peers:
        peer = peers.get(name)
        if peer is None:
            peer_issues.append(inputs.Issue(name, current.opening, "missing_peer_session"))
            continue
        rows, issues = _slice(peer, offset, 30, parent.decision_at)
        peer_issues.extend(issues)
        if not issues:
            common.append(name)
            peer_rows.append(rows)
    issues = list(target_issues)
    if len(common) < 5:
        issues.extend(peer_issues)
        issues.append(inputs.Issue(current.symbol, parent.trigger.end_at, "fewer_than_five_common_peers"))
    prior_rows = []
    for opening in expected:
        prior = references.get(opening)
        if prior is None:
            issues.append(inputs.Issue(current.symbol, opening, "missing_expected_session"))
            continue
        rows, prior_issues = _slice(prior, offset, 30, parent.decision_at, volume=True)
        issues.extend(prior_issues)
        prior_rows.append(rows)
    types, ages, age = _categories(context, parent)
    if issues:
        return inputs.H1WindowInputs(parent, tuple(common), None, (), None, context, types, ages, age, tuple(issues))
    denominators = tuple(_median20(Fraction(rows[index].shares) for rows in prior_rows) for index in range(30))
    last5 = _median20(sum((Fraction(row.shares) for row in rows[-5:]), Fraction()) for rows in prior_rows)
    if last5 <= 0 or any(value <= 0 for value in denominators):
        return inputs.H1WindowInputs(parent, tuple(common), None, (), None, context, types, ages, age,
            (inputs.Issue(current.symbol, parent.trigger.end_at, "nonpositive_volume_denominator"),))
    peer_returns = tuple(_return(rows) for rows in peer_rows)
    peer30 = sum(peer_returns, Fraction()) / len(common)
    peer15 = sum((_return(rows[15:]) for rows in peer_rows), Fraction()) / len(common)
    dynamic = []
    for index, row in enumerate(target):
        op, hi, lo, cl = map(Fraction, row.ohlc)
        market = sum((Fraction(rows[index].ohlc[3]) / Fraction(rows[index].ohlc[0]) - 1
                      for rows in peer_rows), Fraction()) / len(common)
        dynamic.append((cl / op - 1, (hi - lo) / op, Fraction() if hi == lo else (cl - lo) / (hi - lo),
                        Fraction(int(hi == lo)), Fraction(row.shares) / denominators[index], market))
    past_returns = tuple(_return(rows) for rows in prior_rows)
    mean = sum(past_returns, Fraction()) / 20
    variance = sum(((value - mean) ** 2 for value in past_returns), Fraction()) / 20
    target_return = _return(target)
    static = (
        ("target_30m_return", target_return), ("peer_30m_return", peer30), ("peer_last15_return", peer15),
        ("peer_positive_30m_fraction", Fraction(sum(value > 0 for value in peer_returns), len(common))),
        ("target_minus_peer_30m_return", target_return - peer30),
        ("last5_share_volume_ratio", sum((Fraction(row.shares) for row in target[-5:]), Fraction()) / last5),
        ("fraction_of_session_elapsed", Fraction((parent.decision_at - current.opening) // MINUTE,
                                                (current.closing - current.opening) // MINUTE)),
        ("structural_stop_gap_from_trigger_close", (Fraction(parent.trigger.close) -
         Fraction(parent.structural_stop)) / Fraction(parent.trigger.close)),
        ("common_peer_count_over_original_peer_count", Fraction(len(common), len(original_peers))),
        ("past20_sameclock5_share_volume_median", last5))
    unknown = () if types is not None else (inputs.Issue(current.symbol, current.opening, "opening_context_unknown"),)
    return inputs.H1WindowInputs(parent, tuple(common), tuple(dynamic), static, variance, context,
                                 types, ages, age, unknown)


def independent_features(window: inputs.H1WindowInputs) -> tuple[float, ...] | None:
    if window.issues or window.dynamic is None or window.event_type_bits is None:
        return None
    static = tuple(float(value) for _, value in window.static_exact)
    vector = tuple(float(value) for row in window.dynamic for value in row)
    vector += static[:6] + (sqrt(float(window.past20_return_variance)),) + static[6:]
    vector += tuple(float(value) for value in window.event_type_bits + window.event_age_bits)
    _need(len(vector) == 206 and all(isfinite(value) for value in vector), "independent_feature_boundary_differs")
    return vector


def reconstruct_label(candidate: replay.EDCandidate, minutes: Mapping[datetime, inputs.Minute]) -> replay.EDUnitLabel:
    """Rebuild close-triggered delayed-stop path; future lows do not select exits."""
    symbol = candidate.symbol

    def opening(at):
        row = minutes.get(at)
        return Fraction(row.ohlc[0]) if row is not None and row.price_state == "observed" else None

    entry = opening(candidate.entry_at)
    if entry is None:
        return replay.EDUnitLabel(candidate, "unknown", "entry_price_unknown",
            first_unknown=(symbol, candidate.entry_at), entry_reason="entry_price_unknown")
    if entry <= candidate.structural_stop:
        return replay.EDUnitLabel(candidate, "known_declined_entry", "invalidated_entry", entry,
            mature_at=candidate.entry_at + 3 * MINUTE, gross=Fraction(), net33=Fraction(), net43=Fraction(),
            entry_reason="invalidated_entry")
    exit_at, exit_reason = candidate.cap_exit_at, "time_cap"
    observations = []
    low, touched = None, False
    observed_at = candidate.entry_at + 3 * MINUTE
    while observed_at < exit_at:
        start = observed_at - 3 * MINUTE
        row = minutes.get(start)
        issue = ("missing_minute" if row is None else "price_" + row.price_state
                 if row.price_state != "observed" else "close_not_available"
                 if row.available_at > observed_at else None)
        observations.append(replay.EDPathObservation(start, observed_at, row, issue))
        if issue:
            return replay.EDUnitLabel(candidate, "unknown", issue, entry,
                path=tuple(observations), first_unknown=(symbol, start),
                minimum_observed_low_return=low, low_touched_stop=touched)
        value = Fraction(row.ohlc[2]) / entry - 1
        low = value if low is None else min(low, value)
        touched = touched or row.ohlc[2] <= candidate.structural_stop
        if row.ohlc[3] <= candidate.structural_stop:
            exit_at = min(observed_at + MINUTE, candidate.cap_exit_at)
            exit_reason = "delayed_structural_stop" if exit_at < candidate.cap_exit_at else "stop_at_time_cap"
        observed_at += MINUTE
    exit_price = opening(exit_at)
    if exit_price is None:
        return replay.EDUnitLabel(candidate, "unknown", "exit_price_unknown", entry,
            exit_at=exit_at, path=tuple(observations), first_unknown=(symbol, exit_at),
            minimum_observed_low_return=low, low_touched_stop=touched, exit_reason=exit_reason,
            exit_source="caller_supplied_Minute.ohlc[0]_scheduled_OPEN_proxy")
    gross = exit_price / entry - 1
    return replay.EDUnitLabel(candidate, "known_traded", exit_reason, entry, exit_price, exit_at,
        exit_at + 3 * MINUTE, gross, gross - Fraction(33, 10_000), gross - Fraction(43, 10_000),
        tuple(observations), minimum_observed_low_return=low, low_touched_stop=touched,
        exit_reason=exit_reason, exit_source="caller_supplied_Minute.ohlc[0]_scheduled_OPEN_proxy")


def reconstruct_coordinate(row: sources.TargetCoordinate, context: sources.OpeningContextRecord, *,
                           current: inputs.SessionInputs, peers: Mapping[str, inputs.SessionInputs | None],
                           expected: tuple[datetime, ...],
                           references: Mapping[datetime, inputs.SessionInputs]) -> dict:
    """Return an independently reconstructed serialized scientific coordinate."""
    original_peers = tuple(sorted(peers))
    if row.stored_row_count is None:
        scan = inputs.ParentScan("parent_identity_unknown", issues=(
            inputs.Issue(row.symbol, current.opening, "original_coordinate_unavailable"),))
    else:
        scan = reconstruct_parent(current)
    window = features = candidate = label = None
    if scan.parent is None:
        status = "decline" if scan.status == "known_no_parent" else "unknown"
        first_unknown = min((issue.minute for issue in scan.issues), default=current.opening)
        containing_bar_end = current.opening + (
            ((first_unknown - current.opening) // (5 * MINUTE) + 1) * 5) * MINUTE
        at = (current.closing - 78 * MINUTE if status == "decline" else
              max(current.opening + 32 * MINUTE, containing_bar_end + 2 * MINUTE))
        actions = {name: replay.EDAction(at, status, scan.status, symbol=row.symbol,
                   parent_id=f"ED-H1:{row.session}:{row.symbol}") for name in ("rule", "without_filing")}
        feature_reason = scan.status
    else:
        parent = scan.parent
        entry_at = parent.trigger.end_at + 5 * MINUTE
        candidate = replay.EDCandidate(f"ED-H1:{row.session}:{row.symbol}", row.symbol,
            current.opening, current.closing, parent.trigger.end_at + 2 * MINUTE, entry_at,
            entry_at + 60 * MINUTE, Fraction(min(bar.low for bar in parent.preceding)))
        if len(expected) == 20:
            window = reconstruct_window(current, parent, original_peers=original_peers, peers=peers,
                expected=expected, references=references, context=context.context)
            features = independent_features(window)
            feature_reason = None if features is not None else "required_predecision_input_unknown"
        else:
            feature_reason = "insufficient_expected_prior_sessions"
        actions = {}
        for name in ("rule", "without_filing"):
            if features is None:
                status = "unknown"
            else:
                static = dict(window.static_exact)
                accepted = (name == "without_filing" or context.context.state == "ELIGIBLE_ORIGINAL")
                accepted = accepted and static["target_minus_peer_30m_return"] > 0
                accepted = accepted and static["last5_share_volume_ratio"] > 1
                status = "accept" if accepted else "decline"
            actions[name] = replay.EDAction(candidate.decision_at, status,
                "common_support_unknown" if status == "unknown" else f"fixed_{name}_{status}", candidate)
        # Future path completeness cannot change any value calculated above.
        label = reconstruct_label(candidate, current.minutes)
    return {"kind": "coordinate", "coordinate": _project(row), "opening_context": _project(context),
        "original_peers": _project(original_peers), "expected_prior_openings": _project(expected),
        "parent_scan": _project(scan), "window_inputs": _project(window), "features": _project(features),
        "common_support": features is not None, "feature_reason": feature_reason,
        "candidate": _project(candidate), "label": _project(label), "actions": _project(actions)}


def verify_h1_snapshot(records: Iterable[dict], *, universe: sources.UniverseCalendar,
                       filing_source: sources.FilingSource, raw_reader: sources.AuditedChunkReader,
                       binding: Mapping, checkpoint: Callable[[str], object],
                       raw_filing_files: Mapping[str, bytes]) -> dict:
    """Exhaustively check all coordinates and source views, then consume EOF.

    ``records`` may be the strict saved-snapshot reader. Its compressed and
    decoded pin checks run on exhaustion. No successful receipt is returned
    from a prefix, a missing footer or an extra record. There are no writes.
    External raw/source/runtime authentication remains the caller's obligation.
    """
    _need(type(universe) is sources.UniverseCalendar and universe.sessions == tuple(sorted(set(universe.sessions)))
          and bool(universe.sessions), "independent_original_calendar_differs")
    declared = tuple(sorted(universe.targets, key=lambda row: (row.session, row.symbol, row.scenario)))
    identities = tuple((row.session, row.symbol, row.scenario) for row in declared)
    _need(len(set(identities)) == len(identities) and
          all(row.session in universe.sessions and row.scenario in ("S1", "S2") for row in declared),
          "independent_original_coordinates_differ")
    _need(isinstance(raw_reader, sources.AuditedChunkReader) and
          raw_reader.receipt.get("verified") is True and
          raw_reader.receipt.get("schema") == "ED-raw-byte-cache-audit-v1" and
          raw_reader.receipt.get("full_eof_verified") is True, "independent_audited_raw_reader_required")
    _need(set(binding) == {"registration_sha256", "source_sha256", "runtime_sha256",
                          "input_manifest_sha256", "caller"} and binding["caller"] == "ED-H1-SNAPSHOT"
          and all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
                  for key, value in binding.items() if key != "caller"), "independent_snapshot_binding_differs")
    verify_filing_semantics(filing_source, raw_filing_files)
    assumptions = (
        "original_dated_population_including_unavailable_coordinates",
        "S1_S2_joint_universe_and_date_only_filing_clock_sensitivity_not_independent_samples",
        "assumed_session_schedule_not_session_auction_halt_or_VI_certification",
        "completed_minute_end_plus2_assumed_not_historical_publication_proof",
        "current_DART_query_not_historical_completeness_or_issuer_certification",
        "stored_vendor_price_and_volume_units_and_adjustment_basis_uncertified",
        "features_selected_before_future_unit_label_or_holding_path_support", *replay.ASSUMPTIONS)
    header = {"kind": "header", "schema": SCHEMA, "binding": dict(binding),
        "calendar": {"sessions": list(universe.sessions), "targets": _project(declared),
                     "raw_sha256": universe.raw_sha256},
        "feature_schema": inputs.SCHEMA_ID, "feature_names": list(inputs.FEATURE_NAMES),
        "source_audit": _project(raw_reader.receipt), "filing_source": _project(filing_source),
        "assumptions": list(assumptions)}
    stream, prefix = iter(records), hashlib.sha256()
    counts = {"session": 0, "coordinate": 0}
    science_counts: Counter[str] = Counter()

    def accept(expected: dict, reason: str) -> None:
        try:
            actual = next(stream)
        except StopIteration as error:
            raise SnapshotVerificationRefusal("independent_snapshot_truncated") from error
        if type(actual) is not dict or _canonical(actual) != _canonical(expected):
            raise SnapshotVerificationRefusal(reason + ":" + _difference_path(actual, expected))
        prefix.update(_canonical(actual) + b"\n")

    accept(header, "independent_snapshot_header_differs")
    # A verifier-owned cache stores only requested30 earlier-session minutes,
    # never producer scientific intermediates or a full-period minute panel.
    cache: OrderedDict[tuple[str, str, int], inputs.SessionInputs] = OrderedDict()

    def source(symbol: str, day: str) -> sources.SessionSourceView:
        checkpoint("verify_snapshot_raw_session")
        return reconstruct_session(symbol, day, raw_reader.load_raw_chunk("CC", symbol, day),
                                   raw_reader.load_raw_chunk("CX", symbol, day))

    def reference(symbol: str, day: str, offset: int) -> inputs.SessionInputs:
        key = symbol, day, offset
        if key not in cache:
            checkpoint("verify_snapshot_raw_reference")
            opening = calendar.session_open(day)
            requested = tuple(opening + (offset + index) * MINUTE for index in range(30))
            cache[key] = reconstruct_session(symbol, day, raw_reader.load_raw_chunk("CC", symbol, day),
                raw_reader.load_raw_chunk("CX", symbol, day), requested_starts=requested).session
            while len(cache) > 440:
                cache.popitem(last=False)
        else:
            cache.move_to_end(key)
        return cache[key]

    for index, day in enumerate(universe.sessions):
        checkpoint("verify_snapshot_day")
        rows = tuple(row for row in declared if row.session == day)
        names = tuple(sorted({row.symbol for row in rows}))
        views = {name: source(name, day) for name in names}
        accept({"kind": "session", "session": day, "opening": calendar.session_open(day).isoformat(),
                "closing": calendar.session_close(day).isoformat(),
                "sources": [_project(views[name]) for name in names]}, "independent_snapshot_sources_differ")
        counts["session"] += 1
        expected_days = universe.sessions[max(0, index - 20):index]
        expected = tuple(calendar.session_open(prior) for prior in expected_days)
        by_scenario = {(row.scenario, row.symbol): row for row in rows}
        for symbol in names:
            target_scan = reconstruct_parent(views[symbol].session)
            references = {}
            if (len(expected) == 20 and target_scan.parent is not None and
                    any(row.symbol == symbol and row.stored_row_count is not None for row in rows)):
                offset = int((target_scan.parent.trigger.end_at - views[symbol].session.opening) // MINUTE) - 30
                references = {calendar.session_open(prior): reference(symbol, prior, offset) for prior in expected_days}
            for row in (item for item in rows if item.symbol == symbol):
                peers = {name: views[name].session if original.stored_row_count is not None else None
                         for (scenario, name), original in by_scenario.items()
                         if scenario == row.scenario and name != symbol}
                expected_record = reconstruct_coordinate(row, reconstruct_context(universe, filing_source, row),
                    current=views[symbol].session, peers=peers, expected=expected, references=references)
                accept(expected_record, "independent_snapshot_science_differs")
                counts["coordinate"] += 1
                science_counts[expected_record["parent_scan"]["status"]] += 1
                science_counts["common_support"] += expected_record["common_support"]
                if expected_record["label"] is not None:
                    science_counts["label__" + expected_record["label"]["status"]] += 1
                checkpoint("verify_snapshot_coordinate")
    try:
        footer = next(stream)
    except StopIteration as error:
        raise SnapshotVerificationRefusal("independent_snapshot_footer_missing") from error
    _need(type(footer) is dict and _canonical(footer) == _canonical({"kind": "footer", "record_counts": counts,
                     "decoded_prefix_sha256": prefix.hexdigest()}), "independent_snapshot_footer_differs")
    eof = object()
    _need(next(stream, eof) is eof, "independent_snapshot_record_after_footer")
    return {"schema": "ED-H1-common-snapshot-verification-v1", "verified": True,
        "snapshot_schema": SCHEMA, "record_counts": counts, "science_counts": dict(science_counts),
        "decoded_prefix_sha256": prefix.hexdigest(), "all_original_coordinates_verified": True,
        "sampled_scientific_rows": False, "producer_scientific_helpers_called": False,
        "trusted_boundaries": "authenticated CC/CX decoding and audited raw-byte reader; fixed calendar; "
                              "HTML structure parser; declared datatypes/constants; canonical JSON"}
