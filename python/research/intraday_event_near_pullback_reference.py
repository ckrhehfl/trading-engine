"""Independent verification of the confirmed EL near-peak pullback screen."""

from fractions import Fraction


def _check(condition, message):
    if not condition:
        raise ValueError(message)


def _price(value):
    _check(type(value) in (str, int), "Near-pullback prices must be exact")
    price = Fraction(value)
    _check(price > 0, "Near-pullback prices must be positive")
    return price


def classify(saved, *, near_only=False):
    """Compute the registered status independently from sealed decision fields."""
    peak, base, trough = (_price(saved[name]) for name in ("peak", "base", "trough"))
    age = saved["age"]
    _check(base < peak and trough < peak and 2 * trough >= base + peak
           and type(age) is int and 2 <= age <= 10,
           "Near-pullback geometry does not describe a confirmed original event")
    if saved["reference"] is None:
        return "unknown"
    quote = _price(saved["reference"])
    if not 249 * quote < 250 * peak < 251 * quote:
        return "reject"
    return "pass" if near_only or (3 * trough >= 2 * peak + base and age <= 5) else "reject"


def verify_fields(saved, query, first_pullback, onset, terminal):
    """Reconstruct geometry from independently archived states and check the seal."""
    initial, first, end = (row["frame"] for row in (onset, first_pullback, terminal))
    event, clock = end["event_id"], end["decision_offset"]
    session, symbol, group = terminal["session"], terminal["symbol"], end["overlap_group_id"]
    _check(query["frame"] == end and query["key"] == f"{event}@{clock}"
           and (initial["kind"], initial["state"]) == ("at_onset", "WAITING_PULLBACK")
           and (first["kind"], first["state"]) == ("first_pullback", "PULLBACK")
           and (end["kind"], end["state"]) == ("terminal", "CONFIRMED"),
           "Near-pullback archived state sequence differs")
    for row in (onset, first_pullback, terminal, query):
        frame = row["frame"]
        _check((row["session"], row["symbol"], frame["event_id"], frame["overlap_group_id"], frame["family"])
               == (session, symbol, event, group, "C_FIRSTPULLBACK")
               and frame["onset_offset"] == initial["offset"] and frame["offset"] < frame["decision_offset"],
               "Near-pullback archived state identity or causal timer differs")
    age = end["offset"] - first["offset"]
    conditions = end["conditions"]
    _check(initial["offset"] < first["offset"] < end["offset"]
           and initial["decision_offset"] <= first["decision_offset"] <= clock
           and first["offset"] == first["conditions"]["pullback_at"] == conditions["pullback_at"]
           and age == conditions["pullback_age"], "Near-pullback archived timing differs")

    base, peak, trough = (_price(x) for x in (initial["conditions"]["base_close"], first["level"], end["structure"]))
    _check(peak == _price(end["level"]) and trough <= _price(first["structure"])
           and 2 * _price(conditions["retained_advance_floor"]) == peak + base,
           "Near-pullback archived price geometry differs")
    _check((saved["event_id"], saved["session"], saved["symbol"], saved["cluster_id"], saved["decision_offset"])
           == (event, session, symbol, group, clock)
           and (_price(saved["base"]), _price(saved["peak"]), _price(saved["trough"]), saved["age"])
           == (base, peak, trough, age)
           and saved["reference"] == query["reference_close"],
           "Sealed near-pullback fields differ from archived states")
    _check(saved["near_status"] == classify(saved, near_only=True) and saved["status"] == classify(saved),
           "Sealed near-pullback decisions differ from independent predicates")
