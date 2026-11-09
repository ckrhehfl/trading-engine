"""EJ pre-entry activity in the sealed EI stored volume units."""

from datetime import datetime
from fractions import Fraction

from research import intraday_candidate_room as room
from research.intraday_context_features import HISTORY_MINUTES
from research.intraday_ed_inputs import MINUTE


RECENT_MINUTES = 5
PRIOR_MINUTES = HISTORY_MINUTES - RECENT_MINUTES
ACTIVITY_MULTIPLE = 2


def _stored_volume(value):
    if type(value) is str:
        try:
            exact = Fraction(value)
        except (ValueError, ZeroDivisionError):
            return None
        if exact >= 0 and str(exact) == value:
            return exact
    return None


def _window(feature, at, opening):
    end = at - 2 * MINUTE
    start = end - HISTORY_MINUTES * MINUTE
    clocks = feature.get("minute_available_at")
    states = feature.get("volume_states")
    stored = feature.get("stored_volumes")
    if (start < opening or feature.get("observation_end") != end.isoformat()
            or feature.get("window_start") != start.isoformat()
            or any(type(items) is not list or len(items) != HISTORY_MINUTES
                   for items in (clocks, states, stored))):
        return None

    volumes = []
    for index, (clock, state, raw) in enumerate(zip(clocks, states, stored, strict=True)):
        if clock != (start + (index + 3) * MINUTE).isoformat() or state != "observed":
            return None
        volume = _stored_volume(raw)
        if volume is None:
            return None
        volumes.append(volume)
    return volumes


def _inputs(coordinate):
    opening = room._clock(coordinate.get("opening"))
    for (identity, metadata, (state, reason, _, _)), opportunity in zip(
            room._inputs(coordinate), coordinate["roster"]["questions"], strict=True):
        volumes = None
        if state == "priced":
            at = datetime.fromisoformat(metadata["decision_at"])
            volumes = _window(opportunity["feature"], at, opening)
            if volumes is None:
                state, reason = "unresolved", "activity_window_unavailable"
            elif not any(volumes[:PRIOR_MINUTES]):
                state, reason, volumes = "unresolved", "nonpositive_prior_activity", None
        yield identity, metadata, state, reason, volumes


def selections(coordinate):
    """Compare recent and prior mean observed volumes before joining payoffs."""
    result = {}
    for identity, metadata, state, reason, volumes in _inputs(coordinate):
        ratio = None
        if volumes is not None:
            recent_mean = sum(volumes[PRIOR_MINUTES:]) / RECENT_MINUTES
            prior_mean = sum(volumes[:PRIOR_MINUTES]) / PRIOR_MINUTES
            ratio = recent_mean / prior_mean
            state, reason = (("select", "at_least_twice_prior_mean") if ratio >= ACTIVITY_MULTIPLE
                             else ("decline", "below_twice_prior_mean"))
        result[identity] = {**metadata, "state": state, "reason": reason,
                            "activity_ratio": None if ratio is None else str(ratio)}
    return result


def verify_selections(coordinate, produced):
    """Verify identity, support and exact comparisons using cross-products."""
    expected = {}
    for identity, metadata, state, reason, volumes in _inputs(coordinate):
        ratio = None
        if volumes is not None:
            scaled_recent = 5 * sum(volumes[-5:])
            prior = sum(volumes[:-5])
            state, reason = (("decline", "below_twice_prior_mean") if scaled_recent < 2 * prior
                             else ("select", "at_least_twice_prior_mean"))
            ratio = str(scaled_recent / prior)
        expected[identity] = {**metadata, "state": state, "reason": reason, "activity_ratio": ratio}
    if room._encode(produced) != room._encode(expected):
        raise ValueError("EJ activity: sealed selection differs")


def invented_decision(row, roster):
    """Attach invented observed activity to the existing invented exit identity."""
    coordinate, adapted = room.invented_decision(row, roster)
    variant = (int(roster["symbol"]) - 100000) % 7
    for opportunity in coordinate["roster"]["questions"]:
        feature = opportunity["feature"]
        at = datetime.fromisoformat(feature["forecast_at"])
        end = at - 2 * MINUTE
        start = end - HISTORY_MINUTES * MINUTE
        clocks = [(start + (index + 3) * MINUTE).isoformat() for index in range(HISTORY_MINUTES)]
        states = ["observed"] * HISTORY_MINUTES
        volumes = ["1"] * PRIOR_MINUTES + ["1" if variant == 1 else "2"] * RECENT_MINUTES
        if variant == 4:
            states[0], volumes[0] = "missing", None
        feature.update(observation_end=end.isoformat(), window_start=start.isoformat(),
                       minute_available_at=clocks, volume_states=states, stored_volumes=volumes)
    return coordinate, adapted
