"""Two masks for the fixed EL confirmation arm near its first frozen peak."""

from fractions import Fraction

from research import intraday_event_headroom as headroom


UNIT = "EL-C-NEAR-PULLBACK-1"


def decision(query):
    saved = headroom.decision(query)
    frame = query["frame"]
    info = frame["conditions"]
    peak = Fraction(saved["peak"])
    floor = Fraction(info["retained_advance_floor"])
    base = 2 * floor - peak
    trough = Fraction(frame["structure"])
    age = frame["offset"] - info["pullback_at"]
    headroom.require(0 < base < floor <= trough < peak and 2 <= age <= 10 and age == info["pullback_age"],
                     "Original confirmation retracement geometry differs")
    limit = None if saved["reference"] is None else Fraction(saved["reference"])
    near, screened = "unknown", "unknown"
    if limit is not None:
        near = "pass" if 10_000 * abs(peak - limit) < 40 * limit else "reject"
        shallow = 3 * (peak - trough) <= peak - base
        screened = "pass" if near == "pass" and shallow and age <= 5 else "reject"
    return {**saved, "base": str(base), "trough": str(trough), "age": age, "near_status": near, "status": screened}


def near_decisions(decisions):
    return {key: {**saved, "status": saved["near_status"]} for key, saved in decisions.items()}


def summarize(calendar, decisions, ledgers):
    return {"near": headroom.summarize(calendar, near_decisions(decisions), ledgers),
            "screened": headroom.summarize(calendar, decisions, ledgers)}
