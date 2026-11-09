"""EJ entry selection on range expansion and a breakout in sealed EI prices."""

from datetime import datetime
from fractions import Fraction

from research import intraday_candidate_room as room


PRIOR_MINUTES = 25
RECENT_MINUTES = 5
WINDOW_MINUTES = PRIOR_MINUTES + RECENT_MINUTES
RANGE_MULTIPLE = 2


def _observed_window(feature, at, opening):
    end = at - 2 * room.MINUTE
    start = end - WINDOW_MINUTES * room.MINUTE
    clocks = feature.get("minute_available_at")
    states = feature.get("price_states")
    prices = feature.get("prices_in_R")
    if (opening is None or start < opening or feature.get("reasons") != []
            or feature.get("observation_end") != end.isoformat()
            or feature.get("window_start") != start.isoformat()
            or any(type(items) is not list or len(items) != WINDOW_MINUTES for items in (clocks, states, prices))):
        return None

    question = feature["question"]
    anchor = question["episode_id"][0][1]
    level = room._amount(anchor["price"])
    scale = room._amount(anchor["pre_peak_range"])
    close = room._amount(question.get("start_close"))
    if level is None or scale is None or close is None or room._amount(question["path"]["level"]) != level:
        return None

    bars = []
    for index, (clock, state, raw) in enumerate(zip(clocks, states, prices, strict=True)):
        if (clock != (start + (index + 3) * room.MINUTE).isoformat() or state != "observed"
                or type(raw) is not list or len(raw) != 4 or any(type(value) is not str for value in raw)):
            return None
        try:
            op, high, low, last = bar = tuple(map(Fraction, raw))
        except (ValueError, ZeroDivisionError):
            return None
        if (list(map(str, bar)) != raw or level + low * scale <= 0
                or not low <= min(op, last) <= max(op, last) <= high):
            return None
        bars.append(bar)
    return bars if bars[-1][3] == (close - level) / scale else None


def selections(coordinate):
    """Seal original request decisions without reading volume or outcomes."""
    result = {}
    opening = room._clock(coordinate.get("opening"))
    for (identity, metadata, (state, reason, _, _)), opportunity in zip(
            room._inputs(coordinate), coordinate["roster"]["questions"], strict=True):
        ratio = distance = None
        if state == "priced":
            at = datetime.fromisoformat(metadata["decision_at"])
            bars = _observed_window(opportunity["feature"], at, opening)
            if bars is None:
                state, reason = "unresolved", "range_window_unavailable"
            else:
                prior, recent = bars[:PRIOR_MINUTES], bars[PRIOR_MINUTES:]
                prior_mean = sum(bar[1] - bar[2] for bar in prior) / PRIOR_MINUTES
                if prior_mean == 0:
                    state, reason = "unresolved", "nonpositive_prior_range"
                else:
                    recent_mean = sum(bar[1] - bar[2] for bar in recent) / RECENT_MINUTES
                    ratio = recent_mean / prior_mean
                    distance = recent[-1][3] - max(bar[1] for bar in prior)
                    if ratio < RANGE_MULTIPLE:
                        state, reason = "decline", "range_below_twice_prior_mean"
                    elif distance <= 0:
                        state, reason = "decline", "close_not_above_prior_high"
                    else:
                        state, reason = "select", "expanded_range_and_breakout"
        result[identity] = {**metadata, "state": state, "reason": reason,
                            "range_ratio": None if ratio is None else str(ratio),
                            "breakout_distance_in_R": None if distance is None else str(distance)}
    return result


def _reference_window(feature, decision, opening):
    minute = room.MINUTE
    end = decision - 2 * minute
    start = decision - 32 * minute
    times, states, prices = (feature.get(key) for key in ("minute_available_at", "price_states", "prices_in_R"))
    expected_times = [(decision - offset * minute).isoformat() for offset in range(29, -1, -1)]
    if (opening is None or opening > start or feature.get("window_start") != start.isoformat()
            or feature.get("observation_end") != end.isoformat() or feature.get("reasons") != []
            or times != expected_times or states != ["observed"] * 30 or type(prices) is not list or len(prices) != 30):
        return None

    question = feature["question"]
    anchor = question["episode_id"][0][1]
    values = (anchor["price"], anchor["pre_peak_range"], question.get("start_close"), question["path"]["level"])
    if any(type(value) is not str for value in values):
        return None
    try:
        level, scale, saved_close, path_level = geometry = tuple(Fraction(value) for value in values)
        if tuple(str(value) for value in geometry) != values or min(geometry) <= 0 or level != path_level:
            return None
        bars = []
        for raw in prices:
            if type(raw) is not list or len(raw) != 4 or any(type(value) is not str for value in raw):
                return None
            normalized = [Fraction(value) for value in raw]
            if [str(value) for value in normalized] != raw:
                return None
            op, high, low, close = (level + scale * value for value in normalized)
            if not (0 < low <= op <= high and low <= close <= high):
                return None
            bars.append((high, low, close))
    except (ValueError, ZeroDivisionError):
        return None
    return (bars, scale) if bars[-1][2] == saved_close else None


def verify_selections(coordinate, produced):
    """Reconstruct the observation clock and check the rule in exact price units."""
    expected = {}
    opening = room._clock(coordinate.get("opening"))
    for index, (identity, metadata, (state, reason, _, _)) in enumerate(room._inputs(coordinate)):
        ratio = distance = None
        if state == "priced":
            feature = coordinate["roster"]["questions"][index]["feature"]
            observed = _reference_window(feature, datetime.fromisoformat(metadata["decision_at"]), opening)
            if observed is None:
                state, reason = "unresolved", "range_window_unavailable"
            else:
                bars, scale = observed
                prior_total = sum(high - low for high, low, _ in bars[:-5])
                if prior_total == 0:
                    state, reason = "unresolved", "nonpositive_prior_range"
                else:
                    recent_total = sum(high - low for high, low, _ in bars[-5:])
                    last_close = bars[-1][2]
                    prior_high = max(high for high, _, _ in bars[:-5])
                    ratio = str(25 * recent_total / (5 * prior_total))
                    distance = str((last_close - prior_high) / scale)
                    if 25 * recent_total < 10 * prior_total:
                        state, reason = "decline", "range_below_twice_prior_mean"
                    elif last_close <= prior_high:
                        state, reason = "decline", "close_not_above_prior_high"
                    else:
                        state, reason = "select", "expanded_range_and_breakout"
        expected[identity] = {**metadata, "state": state, "reason": reason,
                              "range_ratio": ratio, "breakout_distance_in_R": distance}
    if room._encode(produced) != room._encode(expected):
        raise ValueError("EJ range: sealed selection differs")


def invented_decision(row, roster):
    """Attach synthetic accepted, declined, and unresolved price windows."""
    coordinate, adapted = room.invented_decision(row, roster)
    variant = (int(roster["symbol"]) - 100000) % 7
    for opportunity in coordinate["roster"]["questions"]:
        feature = opportunity["feature"]
        at = datetime.fromisoformat(feature["forecast_at"])
        end = at - 2 * room.MINUTE
        start = end - WINDOW_MINUTES * room.MINUTE
        question = feature["question"]
        anchor = question["episode_id"][0][1]
        close = (Fraction(question["start_close"]) - Fraction(anchor["price"])) / Fraction(anchor["pre_peak_range"])
        prior_high = close if variant == 6 else close - 1
        recent_range = 1 if variant == 1 else 2
        prior = [str(prior_high), str(prior_high), str(prior_high - 1), str(prior_high)]
        recent = [str(close), str(close), str(close - recent_range), str(close)]
        states = ["observed"] * WINDOW_MINUTES
        prices = [prior[:] for _ in range(PRIOR_MINUTES)] + [recent[:] for _ in range(RECENT_MINUTES)]
        if variant == 4:
            states[0], prices[0] = "missing", None
        feature.update(reasons=[], observation_end=end.isoformat(), window_start=start.isoformat(),
                       minute_available_at=[(start + (i + 3) * room.MINUTE).isoformat() for i in range(WINDOW_MINUTES)],
                       price_states=states, prices_in_R=prices)
    return coordinate, adapted
