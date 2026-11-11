"""EL typed input and outcome-independent example selection; no file reader.

The caller authenticates original bytes and retains raw source provenance.
This adapter does not certify price units, historical publication or fills.
Every scheduled minute keeps its state; late receipts are rounded up, never
backdated. A full-session mask must not become a decision-time feature.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
import json

from research.intraday_ed_inputs import SessionInputs, STATES
from research.intraday_event_features import VolumeObservation
from research.intraday_event_scenarios import EventFrame, MinuteBar


MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class Observations:
    symbol: str
    session_length: int
    bars: dict[int, MinuteBar]
    volumes: dict[int, VolumeObservation]
    states: tuple[dict, ...]


def adapt_session(session: SessionInputs) -> Observations:
    if not isinstance(session, SessionInputs):
        raise TypeError("Authenticated typed session required")
    duration = session.closing - session.opening
    length = duration // MINUTE
    if (session.opening.utcoffset() is None or session.closing.utcoffset() is None
            or not 0 < length <= 600 or duration != length * MINUTE):
        raise ValueError("EL known integral session calendar required")
    bars, volumes, states = {}, {}, []
    for offset in range(length):
        at = session.opening + offset * MINUTE
        item = session.minutes.get(at)
        if item is None:
            states.append({"offset": offset, "price": "missing", "volume": "missing",
                           "available_offset": None})
            continue
        if (item.symbol != session.symbol or item.start_at != at
                or item.available_at.utcoffset() is None or item.available_at < at + MINUTE):
            raise ValueError("EL original observation identity or availability differs")
        if item.price_state not in STATES or item.volume_state not in STATES:
            raise ValueError("Explicit original price/volume state required")
        elapsed = item.available_at - session.opening
        available = elapsed // MINUTE + int(elapsed % MINUTE != timedelta())
        if item.price_state == "observed":
            if item.ohlc is None or len(item.ohlc) != 4:
                raise ValueError("Observed price without four exact values")
            bars[offset] = MinuteBar(offset, *item.ohlc, available)
        elif item.ohlc is not None:
            raise ValueError("Unsupported price cannot carry admitted numeric values")
        if item.volume_state == "observed":
            if (isinstance(item.shares, bool) or not isinstance(item.shares, (Decimal, Fraction, int))
                    or isinstance(item.shares, Decimal) and not item.shares.is_finite()):
                raise ValueError("Observed volume requires finite exact value")
            volumes[offset] = VolumeObservation(offset, Fraction(item.shares), available)
        elif item.shares is not None:
            raise ValueError("Unsupported volume cannot carry admitted numeric values")
        states.append({"offset": offset, "price": item.price_state, "volume": item.volume_state,
                       "available_offset": available})
    return Observations(session.symbol, length, bars, volumes, tuple(states))


def case_roster(frames, *, per_family=3):
    """Choose onset identities without reading terminal states or future prices.

    The caller seals this roster before producing any after-path cards. This
    selects illustrative examples, not a population for estimating performance.
    A later balanced success/loss display must not replace the original roster.
    """
    if type(per_family) is not int or per_family != 3:
        raise ValueError("EL first case roster fixes three examples per family")
    candidates = {}
    for frame in frames:
        if not isinstance(frame, EventFrame):
            raise TypeError("EL EventFrame required")
        if frame.kind != "at_onset":
            continue
        identity = (frame.family, frame.event_id, frame.overlap_group_id,
                    frame.onset_offset, frame.decision_offset)
        old = candidates.setdefault(frame.event_id, identity)
        if old != identity:
            raise ValueError("Conflicting original onset identity")
    families = {}
    for identity in candidates.values():
        encoded = json.dumps(identity, separators=(",", ":"), ensure_ascii=False).encode()
        families.setdefault(identity[0], []).append((sha256(encoded).hexdigest(), identity))
    selected = []
    for family, rows in sorted(families.items()):
        for digest, identity in sorted(rows)[:per_family]:
            selected.append({"family": family, "event_id": identity[1], "series_id": identity[2],
                             "onset_offset": identity[3], "initial_available_offset": identity[4],
                             "selection_hash": digest})
    body = {"schema": "EL-onset-case-roster-v1", "per_family": per_family,
            "selection": "smallest SHA256 of original onset identity; no terminal or path field",
            "candidate_episodes": len(candidates), "examples": selected,
            "claim": "illustrative onset-selected cases; never an outcome-balanced inference sample"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {**body, "sha256": sha256(raw).hexdigest()}


def prefix_card(frame, observations: Observations):
    """Only observations received by the frame clock, with no later labels."""
    if not isinstance(frame, EventFrame) or not isinstance(observations, Observations):
        raise TypeError("EL frame and observations required")
    rows = []
    for offset in range(max(0, frame.offset - 63), min(frame.decision_offset, observations.session_length)):
        bar = observations.bars.get(offset)
        volume = observations.volumes.get(offset)
        known = bar is not None and bar.available_offset <= frame.decision_offset
        vknown = volume is not None and volume.available_offset <= frame.decision_offset
        rows.append({"offset": offset, "price_known": known,
                     "ohlc": [str(getattr(bar, name)) for name in ("open", "high", "low", "close")] if known else None,
                     "volume_known": vknown, "reported_volume": str(volume.value) if vknown else None})
    return {"schema": "EL-before-decision-card-v1", "frame": frame.as_dict(), "observations": rows,
            "as_of_offset": frame.decision_offset,
            "claim": "available prefix only; proposed numerical scenario, not trader-authenticated rule"}
