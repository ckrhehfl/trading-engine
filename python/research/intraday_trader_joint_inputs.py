"""Pure original-population joint inputs with one caller-owned day raw cache.

No IO, ranking or successful-path mask. The shared raw parser and immutable
candidate schema are trusted boundaries; independent reconstruction calls only
the existing independent detection/peer/reentry mathematics on fresh decodes.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from fractions import Fraction
from types import MappingProxyType

from research import intraday_ed_snapshot as codec
from research import intraday_ed_sources as sources
from research.intraday_ed_inputs import SessionInputs
from research import intraday_trader_followup_inputs as followup
from research import intraday_trader_followups as peers
from research import intraday_trader_followup_reference as peer_math
from research import intraday_trader_joint_account as account
from research import intraday_trader_reference as math
from research import intraday_trader_reentry as reentry
from research import intraday_trader_reentry_reference as reentry_math
from research import intraday_trader_scenarios as detectors
from research import intraday_trader_session as sessions


A_POLICIES = ("EE-A", "EE-A-2R-TARGET", "EE-A-2R-WITH-ONE-REENTRY")
B_POLICIES = ("EE-B", "EE-B-POSITIVE-PEERS", "EE-B-WITH-ONE-REENTRY")
BUNDLES = {A_POLICIES[2]: ("EE-A2R-FIRST", "EE-A2R-ADDITIONAL"),
           B_POLICIES[2]: ("EE-B-BASE-FIRST", "EE-B-BASE-ADDITIONAL")}


def selected_pair(pair: tuple[str, str]) -> tuple[str, str]:
    sources._need(type(pair) is tuple and len(pair) == 2
                  and pair[0] in A_POLICIES and pair[1] in B_POLICIES,
                  "joint_canonical_frozen_pair_required")
    return pair


@dataclass(frozen=True)
class LegSupport:
    source_policy_id: str
    attempt: int
    state: str
    reason: str
    eligibility: str
    candidate: account.Candidate | None
    detection_metadata: bytes
    reset_metadata: None | bytes = None


@dataclass(frozen=True)
class SlotSupport:
    slot: str
    policy_id: str
    legs: tuple[LegSupport, ...]
    peer_context_metadata: None | bytes = None


@dataclass(frozen=True)
class CoordinateSupport:
    symbol: str
    targets: tuple[sources.TargetCoordinate, ...]
    source_metadata: bytes
    price_chunk_missing: bool
    slots: tuple[SlotSupport, SlotSupport]


@dataclass(frozen=True)
class DayInputs:
    day: str
    coordinates: tuple[CoordinateSupport, ...]
    sessions: Mapping[str, SessionInputs]

    def candidates(self, clock: str, enabled_slots: tuple[str, ...]):
        return tuple(leg.candidate for coordinate in self.coordinates
            if any(target.scenario == clock for target in coordinate.targets)
            for slot in coordinate.slots if slot.slot in enabled_slots
            for leg in slot.legs if leg.candidate is not None)


class Prices(Mapping):
    """Coordinate-only view over today's immutable typed session observations."""
    def __init__(self, day: DayInputs, *, opening: bool = False):
        self.sessions, self.opening = day.sessions, opening

    def get(self, key, default=None):
        symbol, at = key
        session = self.sessions.get(symbol)
        row = None if session is None else session.minutes.get(at)
        if not self.opening:
            return row
        return row.ohlc[0] if row is not None and row.price_state == "observed" else default

    def __getitem__(self, key):
        raise TypeError("joint_prices_coordinate_get_only")

    def __iter__(self):
        raise TypeError("joint_prices_must_not_materialize")

    def __len__(self):
        raise TypeError("joint_prices_must_not_materialize")


def _leg(slot, policy, source_policy, attempt, detection, eligible, reset=None):
    # This is a closed input projection, not an exit/path/outcome admission.
    data = {key: value for key, value in detection.items() if key != "_eligible"}
    candidate = data["candidate"]
    causal = None
    if candidate is not None and eligible is True:
        signal = candidate["signal"]
        causal = account.Candidate(slot, policy, candidate["symbol"], signal["end_at"],
            signal["available_at"], Fraction(candidate["stop"]), Fraction(candidate["target"]))
    return LegSupport(source_policy, attempt, data["state"], data["reason"],
        "absent" if candidate is None else "unknown" if eligible is None else "true" if eligible else "false",
        causal, codec.canonical(codec.encode(data)), reset)


def _additional_absent(source_policy, reset):
    unknown = reset["state"] in ("unknown", "refusal")
    return LegSupport(source_policy, 2, "unknown" if unknown else "known_no_candidate",
        "additional_eligibility:" + reset["reason"], "unknown" if unknown else "absent",
        None, b"null", codec.canonical(codec.encode(reset)))


def _as_dict(value):
    from research.intraday_campaign_independent import _plain
    return _plain(value)


def _eligible(candidate):
    if candidate is None:
        return None
    event, info = candidate["event_session_admitted"], candidate["information"]
    if event is not True:
        return event
    return None if info["state"] == "unknown" else info["state"] == "pass" or not info["applied"]


def _production_slot(session, slot, policy, cache, reentry_result):
    if policy in BUNDLES:
        family = reentry_result.families[slot == "B"]
        reset = _as_dict(family.reset)
        legs = [_leg(slot, policy, BUNDLES[policy][0], 1, _as_dict(family.first.detection),
                     None if family.first.detection.candidate is None else family.first.detection.candidate.eligible)]
        legs.append(_additional_absent(BUNDLES[policy][1], reset) if family.second is None else
            _leg(slot, policy, BUNDLES[policy][1], 2, _as_dict(family.second.detection),
                 family.second.detection.candidate.eligible if family.second.detection.candidate else None,
                 codec.canonical(codec.encode(reset))))
        return SlotSupport(slot, policy, tuple(legs))
    family = "EE-" + slot
    if policy == "EE-A-2R-TARGET":
        detection, _ = reentry._detection(session, family)
    else:
        detection = detectors.detect_bars(family, sessions._bars(session, {}, []),
            symbol=session.symbol, opening=session.opening, closing=session.closing)
    context = None
    if policy == "EE-B-POSITIVE-PEERS" and detection.candidate is not None:
        c = detection.candidate
        context = peers.peer_context(session.symbol, c.signal.end_at, c.available_at,
                                     cache.peers(session.symbol, session.opening.date().isoformat()))
        known = context.state == "known"
        info = detectors.InformationAdmission("unknown" if not known else "pass" if context.mean > 0 else "decline",
            "peer_quorum_unknown" if not known else "positive_peer_mean" if context.mean > 0 else "peer_mean_not_positive")
        detection = replace(detection, scenario_id=policy,
                            candidate=replace(c, scenario_id=policy, information=info))
    c = detection.candidate
    return SlotSupport(slot, policy, (_leg(slot, policy, policy, 1, _as_dict(detection),
        None if c is None else c.eligible),), None if context is None else codec.canonical(codec.encode(context)))


def _reference_slot(session, slot, policy, universe, cache, reentry_result):
    if policy in BUNDLES:
        family = reentry_result["families"][slot == "B"]
        reset = family["reset"]
        first = family["first"]["detection"]
        legs = [_leg(slot, policy, BUNDLES[policy][0], 1, first, _eligible(first["candidate"]))]
        second = family["second"]
        legs.append(_additional_absent(BUNDLES[policy][1], reset) if second is None else
            _leg(slot, policy, BUNDLES[policy][1], 2, second["detection"],
                 _eligible(second["detection"]["candidate"]), codec.canonical(codec.encode(reset))))
        return SlotSupport(slot, policy, tuple(legs))
    family = "EE-" + slot
    if policy == "EE-A-2R-TARGET":
        detection, _ = reentry_math._detection(session, family)
    else:
        detection, _ = math._detect(session, family, None, None, None)
    context = None
    if policy == "EE-B-POSITIVE-PEERS" and detection["candidate"] is not None:
        c = dict(detection["candidate"])
        context = peer_math._peer_expected(session, c["signal"]["end_at"], c["signal"]["available_at"],
            universe, cache.peers(session.symbol, session.opening.date().isoformat()))
        known = context["mean"] is not None
        c["information"] = dict(state="unknown" if not known else "pass" if context["mean"] > 0 else "decline",
            reason="peer_quorum_unknown" if not known else "positive_peer_mean" if context["mean"] > 0 else "peer_mean_not_positive",
            applied=True)
        c["scenario_id"] = policy
        detection = dict(detection, scenario_id=policy, candidate=c)
    return SlotSupport(slot, policy, (_leg(slot, policy, policy, 1, detection,
        _eligible(detection["candidate"])),), None if context is None else codec.canonical(codec.encode(context)))


def prepare_day(universe: sources.UniverseCalendar, cache: followup.DayRawCache,
                day: str, pair: tuple[str, str], *, independent: bool = False) -> DayInputs:
    """One original union day, including all absent/unknown original targets.

    The caller brackets this with reader identity checks. Separate calls decode
    fresh raw copies; producer SessionInputs never become reference inputs.
    """
    selected_pair(pair)
    sources._need(day in universe.sessions and type(independent) is bool, "joint_day_contract_differs")
    decoded, coordinates = {}, []
    for symbol in sorted(symbol for candidate_day, symbol in cache.groups if candidate_day == day):
        price, volume = cache.own(symbol, day)
        view = sources.session_inputs(symbol, day, price, volume)
        session = sources.full_reference_session_inputs(symbol, day, price, volume) if independent else view.session
        standalone = None
        if any(policy in BUNDLES for policy in pair):
            if independent:
                standalone = reentry_math.expected_session(session, source_metadata=view.metadata)
            else:
                raw_price, raw_volume = cache.own(symbol, day)
                separate = sources.full_reference_session_inputs(symbol, day, raw_price, raw_volume)
                standalone = reentry.evaluate_session(session, reference_session=separate, source_metadata=view.metadata)
        slots = tuple(_reference_slot(session, slot, policy, universe, cache, standalone) if independent else
                      _production_slot(session, slot, policy, cache, standalone)
                      for slot, policy in zip(("A", "B"), pair, strict=True))
        # This owned parser dictionary has no consumer outside this preparation.
        # Expose immutable day price views to all pure account arms; independent
        # preparation retains its separate raw copy/parser/session lineage.
        decoded[symbol] = replace(session, minutes=MappingProxyType(session.minutes))
        coordinates.append(CoordinateSupport(symbol, tuple(cache.groups[day, symbol]), view.metadata,
                                             price is None, slots))
    return DayInputs(day, tuple(coordinates), MappingProxyType(decoded))
