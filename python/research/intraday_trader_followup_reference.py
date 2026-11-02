"""Independent EE followup mathematics; no producer detector/path/feature calls.

Existing independent A/B detection and path primitives remain the mathematical
reference. The shared source identity parser and closed scalar codec are trusted
boundaries. Peer atoms, arithmetic and admissions are reconstructed here.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from re import fullmatch

from research import intraday_ed_snapshot as codec
from research import intraday_ed_sources as sources
from research import intraday_trader_reference as math
from research.intraday_ed_inputs import SessionInputs
from research.intraday_trader_followups import POLICIES, PeerInput


MINUTE = timedelta(minutes=1)


def _peer_expected(session: SessionInputs, end: datetime, decision: datetime,
                   universe: sources.UniverseCalendar, peers: Iterable[PeerInput]) -> dict:
    members = sorted((item for item in universe.targets if item.scenario == "S2"
                      and item.session == session.opening.date().isoformat()
                      and item.symbol != session.symbol), key=lambda item: item.rank)
    supplied = iter(peers)
    windows, changes = [], []
    math._require(end + 2 * MINUTE == decision, "peer_decision_clock_differs")
    for member in members:
        item = next(supplied, None)
        math._require(item is not None and math._canonical(item.membership) == math._canonical(member),
                      "reference_original_peer_basket_differs")
        evidence = codec._json(member.metadata)
        available, selected = map(sources._clock, (evidence["available_at"], evidence["selected_at"]))
        math._require(available <= selected < session.opening and selected <= decision,
                      "peer_membership_unavailable")
        chunk = item.raw
        raw_rows, source_metadata, digest = [], b"null", None
        if chunk is not None:
            symbol, day, _, _ = sources._raw_identity(chunk, "CC")
            math._require(symbol == member.symbol and day == member.session, "peer_source_identity_differs")
            raw_rows = chunk["rows"]
            source_metadata = codec.canonical({name: chunk[name] for name in chunk if name != "rows"})
            digest = sources._sha(codec.canonical(chunk) + b"\n")
        by_start = {}
        for row in raw_rows:
            key = datetime.fromtimestamp((row["open_time_ms"] // 60000) * 60, end.tzinfo)
            by_start.setdefault(key, []).append(row)
        observations, endpoints, failures = [], [], []
        for position in range(15):
            at = end + (position - 15) * MINUTE
            rows = by_start.get(at, [])
            state = "observed"
            parsed = None
            if len(rows) == 0:
                state = "missing"
            elif len(rows) != 1:
                state = "conflict"
            else:
                try:
                    values = [rows[0][name] for name in ("open", "high", "low", "close")]
                    if not all(type(value) is str for value in values):
                        state = "invalid"
                    else:
                        parsed = tuple(Decimal(value) for value in values)
                        if not all(value.is_finite() and value > 0 for value in parsed):
                            state = "invalid"
                        elif not (parsed[2] <= parsed[0] <= parsed[1] and parsed[2] <= parsed[3] <= parsed[1]):
                            state = "invalid"
                except InvalidOperation:
                    state = "invalid"
            if at + 3 * MINUTE > decision:
                state = "unavailable"
            observations.append(dict(start_at=at, end_at=at + MINUTE, available_at=at + 3 * MINUTE,
                                     state=state, decoded_rows=codec.canonical(rows)))
            endpoints.append(parsed)
            failures.append(state)
        state = "complete"
        for failure in ("unavailable", "invalid", "conflict", "missing"):
            if failure in failures:
                state = failure
                break
        change = None
        if state == "complete":
            first = Fraction(endpoints[0][0])
            change = (Fraction(endpoints[-1][3]) - first) / first
            changes.append(change)
        windows.append(dict(symbol=member.symbol, membership_metadata=member.metadata,
            membership_available_at=available, membership_selected_at=selected,
            source_metadata=source_metadata, decoded_chunk_sha256=digest, state=state,
            observations=tuple(observations), return_value=change))
    math._require(next(supplied, None) is None, "reference_extra_peer")
    mean = None
    if len(changes) >= 5:
        mean = sum(changes, Fraction(0, 1)) / len(changes)
    return dict(basket_clock="S2", window_start=end - 15 * MINUTE, window_end=end,
                decision_at=decision, windows=tuple(windows), complete_count=len(changes),
                state="unknown" if mean is None else "known", mean=mean)


def expected_session(session: SessionInputs, *, universe: sources.UniverseCalendar,
                     peers: Iterable[PeerInput], source_metadata: bytes) -> tuple[dict, dict | None]:
    # Validate directly; invoking the original complete result builder here
    # would compute irrelevant old outcomes. No original outcome admits a followup.
    math._require(isinstance(session, SessionInputs), "typed_session_required")
    math._require(type(session.symbol) is str and fullmatch(r"KRX:[0-9A-Z]{6}", session.symbol) is not None,
                  "invalid_scenario_or_symbol")
    math._require(type(source_metadata) is bytes, "opaque_source_metadata_bytes_required")
    math._clock(session.opening)
    math._clock(session.closing)
    math._require(session.opening.second == session.opening.microsecond == 0
                  and session.closing.second == session.closing.microsecond == 0
                  and session.closing > session.opening
                  and session.opening.date() == session.closing.date()
                  and session.opening.utcoffset() == session.closing.utcoffset()
                  and (session.closing - session.opening) % (5 * MINUTE) == timedelta()
                  and isinstance(session.minutes, Mapping), "invalid_session_clock_or_minutes")
    detections = [math._detect(session, family, None, None, None) for family in ("EE-A", "EE-B")]
    context = None
    if detections[1][0]["candidate"] is not None:
        signal = detections[1][0]["candidate"]["signal"]
        context = _peer_expected(session, signal["end_at"], signal["available_at"], universe, peers)
    results = []
    for index, policy in enumerate(POLICIES):
        original, issues = detections[0 if index == 0 else 1]
        detection = {name: value for name, value in original.items() if name != "_eligible"}
        detection["scenario_id"] = policy
        candidate = detection["candidate"]
        eligible = None
        if candidate is not None:
            candidate = dict(candidate, scenario_id=policy)
            if index == 0:
                candidate["target"] = math._decimal(Fraction(candidate["signal"]["close"]) +
                    2 * (Fraction(candidate["signal"]["close"]) - Fraction(candidate["stop"])))
                detection["trace"] = (*detection["trace"], dict(kind="risk_target_frozen",
                    bar_end_at=candidate["signal"]["end_at"], available_at=candidate["signal"]["available_at"],
                    level=candidate["target"]))
            else:
                known = context["mean"] is not None
                passed = known and (index == 2 or context["mean"] > Fraction())
                candidate["information"] = dict(state="unknown" if not known else "pass" if passed else "decline",
                    reason="peer_quorum_unknown" if not known else "peer_common_support" if index == 2 else
                    "positive_peer_mean" if passed else "peer_mean_not_positive", applied=True)
            info = candidate["information"]
            eligible = None if info["state"] == "unknown" else info["state"] == "pass" or not info["applied"]
            detection["candidate"] = candidate
        path, refusal = (None, None) if candidate is None else math._path(session, policy, candidate, eligible)
        results.append(dict(scenario_id=policy, detection=detection, path=path, path_refusal=refusal, issues=issues))
    return dict(symbol=session.symbol, opening=session.opening, closing=session.closing,
                source_metadata=source_metadata, results=tuple(results)), context


def verify_session_result(actual, context, session: SessionInputs, **kwargs) -> math.VerificationReceipt:
    try:
        expected, peer_expected = expected_session(session, **kwargs)
        math._record_shape(actual, expected)
        math._record_shape(context, peer_expected)
        if math._canonical(actual) != math._canonical(expected) or math._canonical(context) != math._canonical(peer_expected):
            raise math.ReferenceRefusal("followup_scientific_signature_mismatch")
        for policy, compared in zip(actual.results, expected["results"], strict=True):
            candidate = policy.detection.candidate
            if candidate is not None:
                info = compared["detection"]["candidate"]["information"]
                eligible = None if info["state"] == "unknown" else info["state"] == "pass" or not info["applied"]
                if (math._canonical(candidate.eligible) != math._canonical(eligible)
                        or math._canonical(candidate.available_at) != math._canonical(compared["detection"]["decision_at"])):
                    raise math.ReferenceRefusal("candidate_derived_property_mismatch")
        return math.VerificationReceipt(POLICIES, len(POLICIES))
    except math._InputError as error:
        raise math.ReferenceRefusal(str(error)) from error
