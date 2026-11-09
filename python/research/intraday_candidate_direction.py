"""EJ strict five-minute close direction on the sealed activity-selected entries."""

from datetime import date
from fractions import Fraction

from research import intraday_candidate_activity as activity
from research import intraday_candidate_room as room
from research.intraday_context_features import HISTORY_MINUTES


DIRECTION_MINUTES = 5
RISING = "rising_close_in_R"
NOT_RISING = "flat_or_falling_close_in_R"
UNAVAILABLE = "direction_window_unavailable"


def _exact_price(value):
    if type(value) is str:
        try:
            exact = Fraction(value)
        except (ValueError, ZeroDivisionError):
            return None
        if str(exact) == value:
            return exact
    return None


def _closes(feature):
    # An activity select has already checked all thirty availability clocks.
    states = feature.get("price_states")
    prices = feature.get("prices_in_R")
    if (feature.get("reasons") != [] or type(states) is not list
            or states != ["observed"] * HISTORY_MINUTES
            or type(prices) is not list or len(prices) != HISTORY_MINUTES):
        return None

    question = feature["question"]
    anchor = question["episode_id"][0][1]
    level = room._amount(anchor["price"])
    width = room._amount(anchor["pre_peak_range"])
    close = room._amount(question.get("start_close"))
    if level is None or width is None or close is None or room._amount(question["path"]["level"]) != level:
        return None

    closes = []
    for raw in prices:
        if type(raw) is not list or len(raw) != 4:
            return None
        op, high, low, last = map(_exact_price, raw)
        if (None in (op, high, low, last) or not low <= min(op, last) <= max(op, last) <= high
                or level + low * width <= 0):
            return None
        closes.append(last)
    if closes[-1] != (close - level) / width:
        return None
    return closes[-1], closes[-1 - DIRECTION_MINUTES]


def _inputs(coordinate, control):
    roster = coordinate["roster"]
    day = date.fromisoformat(roster["session"])
    for opportunity in roster["questions"]:
        feature = opportunity["feature"]
        identity, _, _ = room._identity(feature, roster["symbol"], day)
        choice = control[identity]
        yield identity, choice, _closes(feature) if choice["state"] == "select" else None


def selections(coordinate):
    """Keep an activity-selected original entry only when the known close rose."""
    result = {}
    for identity, choice, closes in _inputs(coordinate, activity.selections(coordinate)):
        activity_state, activity_reason = choice["state"], choice["reason"]
        state, reason, delta = activity_state, activity_reason, None
        if activity_state == "select":
            if closes is None:
                state, reason = "unresolved", UNAVAILABLE
            else:
                delta = closes[0] - closes[1]
                state, reason = ("select", RISING) if delta > 0 else ("decline", NOT_RISING)
        result[identity] = {**choice, "state": state, "reason": reason, "activity_state": activity_state,
                            "activity_reason": activity_reason,
                            "direction_delta_in_R": None if delta is None else str(delta)}
    return result


def control_choices(choices):
    """Recover the original activity-only selections from the sealed direction choices."""
    return {
        identity: {**{key: choice[key] for key in room.METADATA}, "state": choice["activity_state"],
                   "reason": choice["activity_reason"], "activity_ratio": choice["activity_ratio"]}
        for identity, choice in choices.items()
    }


def verify_selections(coordinate, produced):
    """Check the original activity control and the direction cross-products independently."""
    try:
        control = control_choices(produced)
    except (AttributeError, KeyError, TypeError) as exc:
        raise ValueError("EJ direction: sealed selection differs") from exc
    activity.verify_selections(coordinate, control)
    expected = {}
    for identity, choice, closes in _inputs(coordinate, control):
        state, reason = choice["state"], choice["reason"]
        delta = None
        if closes is not None:
            last, earlier = closes
            left = last.numerator * earlier.denominator
            right = earlier.numerator * last.denominator
            state, reason = ("decline", NOT_RISING) if left <= right else ("select", RISING)
            delta = str(Fraction(left - right, last.denominator * earlier.denominator))
        elif state == "select":
            state, reason = "unresolved", UNAVAILABLE
        expected[identity] = {**choice, "state": state, "reason": reason,
                              "activity_state": choice["state"], "activity_reason": choice["reason"],
                              "direction_delta_in_R": delta}
    if room._encode(produced) != room._encode(expected):
        raise ValueError("EJ direction: sealed selection differs")


def invented_decision(row, roster):
    """Attach invented price windows to the existing invented activity decisions."""
    coordinate, adapted = activity.invented_decision(row, roster)
    variant = (int(roster["symbol"]) - 100000) % 4
    moves = (Fraction(1, 4), Fraction(0), Fraction(-1, 4), None)
    move = moves[variant]
    for opportunity in coordinate["roster"]["questions"]:
        feature = opportunity["feature"]
        question = feature["question"]
        anchor = question["episode_id"][0][1]
        latest = (Fraction(question["start_close"]) - Fraction(anchor["price"])) / Fraction(anchor["pre_peak_range"])
        states = ["observed"] * HISTORY_MINUTES
        prices = [[str(latest)] * 4 for _ in states]
        earlier = -1 - DIRECTION_MINUTES
        if move is None:
            states[earlier], prices[earlier] = "missing", None
        else:
            prices[earlier] = [str(latest - move)] * 4
        feature.update(reasons=[], price_states=states, prices_in_R=prices)
    return coordinate, adapted
