"""Preserve original source support before selecting any EM event or outcome."""

from collections import Counter


def summarize_source_support(coordinates, family_support, roster):
    seen, support_seen = {}, set()
    prices, states = Counter(), Counter()
    totals = dict.fromkeys(("coordinates", "continuous_minutes", "price_absent_coordinates",
        "price_partial_coordinates", "price_complete_coordinates", "first60_price_complete_coordinates"), 0)
    for row in coordinates:
        key, length = (row["session"], row["symbol"]), row["continuous_length"]
        if key in seen or type(length) is not int or length <= 0:
            raise ValueError("EM original coordinate duplicate or length differs")
        seen[key] = length
        minutes = row["minute_states"][:length]
        if [item["offset"] for item in minutes] != list(range(length)):
            raise ValueError("EM original coordinate minutes differ")
        counts = Counter(item["price"] for item in minutes)
        observed = counts["observed"]
        if observed != row["continuous_price_observed"]:
            raise ValueError("EM original observed count differs")
        totals["coordinates"] += 1
        totals["continuous_minutes"] += length
        totals["price_absent_coordinates"] += observed == 0
        totals["price_partial_coordinates"] += 0 < observed < length
        totals["price_complete_coordinates"] += observed == length
        totals["first60_price_complete_coordinates"] += length >= 60 and all(item["price"] == "observed" for item in minutes[:60])
        prices.update(counts)
    for row in family_support:
        key = row["session"], row["symbol"]
        if (key not in seen or key in support_seen or row["family"] != "C_FIRSTPULLBACK"
                or row["scheduled_minutes"] != seen[key]
                or any(type(value) is not int or value < 0 for value in row["counts"].values())
                or sum(row["counts"].values()) != seen[key]):
            raise ValueError("EM C support coordinate/counts differ")
        support_seen.add(key)
        states.update(row["counts"])
    event_coordinates = {(row["session"], row["symbol"]) for row in roster.values()}
    if support_seen != seen.keys() or not event_coordinates <= seen.keys():
        raise ValueError("EM source support lost original coordinates")
    return {**totals, "coordinates_with_events": len(event_coordinates),
            "coordinates_without_events": len(seen) - len(event_coordinates),
            "continuous_price_states": dict(sorted(prices.items())), "c_support_minutes": dict(sorted(states.items()))}
