"""Pure EK exploratory reports over caller-supplied original opportunities.

All cash is nominal event cash, never concurrent portfolio NAV. Unknowns stay
in the denominator. Uncertainty uses an approximate normal interval, with
independent traded dates as clusters; it is not a discovery/promotion test.
"""

from collections import Counter, defaultdict
from datetime import date
from fractions import Fraction
from math import isfinite, sqrt


STATES = ("closed", "no_fill", "no_entry", "unresolved", "unsupported")
AMOUNTS = ("gross", "cost", "net", "entry_notional")


def _require(condition, reason):
    if not condition:
        raise ValueError(f"EK report: {reason}")


def _read(records, cost_bps):
    _require(type(records) is list and type(cost_bps) is int and cost_bps >= 0, "invalid records or cost")
    seen, rows = set(), []
    for record in records:
        _require(type(record) is dict and type(record.get("key")) is str and bool(record["key"]), "invalid key")
        _require(record["key"] not in seen, "duplicate opportunity key")
        seen.add(record["key"])
        day = date.fromisoformat(record["session"])
        _require(day.isoformat() == record["session"] and type(record["symbol"]) is str and bool(record["symbol"])
                 and type(record["fold"]) is int and record["fold"] > 0, "invalid original identity")
        status = record["status"]
        _require(status in STATES and type(record["selected"]) is bool, "invalid outcome state")
        amounts = {}
        for key in AMOUNTS:
            encoded = record[key]
            _require(encoded is None or type(encoded) is str, "exact amounts required")
            amounts[key] = None if encoded is None else Fraction(encoded)
        if status in ("unresolved", "unsupported"):
            _require(all(value is None for value in amounts.values()), "unknown outcome has numeric payoff")
        elif status == "closed":
            _require(all(value is not None for value in amounts.values()) and record["selected"],
                     "closed outcome must have selected known payoff")
            _require(amounts["entry_notional"] > 0 and amounts["cost"] == amounts["entry_notional"]
                     * Fraction(cost_bps, 10000) and amounts["net"] == amounts["gross"] - amounts["cost"],
                     "closed exact accounting differs")
        else:
            _require(all(value == 0 for value in amounts.values())
                     and record["selected"] == (status == "no_fill"), "flat state or selection differs")
        truth, probability, prediction = (record["path_truth"], record["probabilities"], record["predicted_gross_pct"])
        _require(truth is None or type(truth) is int and 0 <= truth < 3, "invalid path truth")
        if probability is not None:
            _require(type(probability) is list and len(probability) == 3
                     and all(type(p) in (float, int) and isfinite(p) and 0 <= p <= 1 for p in probability)
                     and abs(sum(probability) - 1) <= 1e-12, "invalid path probabilities")
        _require(prediction is None or type(prediction) in (float, int) and isfinite(prediction),
                 "invalid payoff prediction")
        rows.append({**record, **amounts})
    return rows


def _calendar(calendar):
    _require(type(calendar) is list and all(type(day) is str for day in calendar), "ISO evaluation calendar required")
    days = [date.fromisoformat(day) for day in calendar]
    _require(days == sorted(set(days)) and all(day.isoformat() == raw for day, raw in zip(days, calendar)),
             "calendar must be unique and chronological")
    return days


def _week(day):
    year, week, _ = day.isocalendar()
    return f"{year:04d}-W{week:02d}"


def _panel(rows):
    counts = Counter(row["status"] for row in rows)
    closed = [row for row in rows if row["status"] == "closed"]
    n = len(closed)
    return {
        "all_opportunities": len(rows), "selected": sum(row["selected"] for row in rows),
        "known_economics": sum(counts[state] for state in ("closed", "no_fill", "no_entry")),
        "selected_known": counts["closed"] + counts["no_fill"], "filled_closed": n,
        **{state: counts[state] for state in STATES if state != "closed"},
        "cost_clearing": sum(row["net"] > 0 for row in closed),
        "mean_gross_pct": str(sum((row["gross"] / row["entry_notional"] * 100 for row in closed), Fraction()) / n)
        if n else None,
        "mean_net_pct": str(sum((row["net"] / row["entry_notional"] * 100 for row in closed), Fraction()) / n)
        if n else None,
        **{f"nominal_{key}_cash": str(sum((row[key] for row in closed), Fraction()))
           for key in ("gross", "cost", "net")},
    }


def _uncertainty(rows):
    by_day = defaultdict(list)
    for row in rows:
        if row["status"] == "closed":
            by_day[row["session"]].append(row["net"] / row["entry_notional"] * 100)
    values = [value for group in by_day.values() for value in group]
    n, groups = len(values), len(by_day)
    mean = sum(values, Fraction()) / n if n else None
    naive = sqrt(float(sum(((value - mean) ** 2 for value in values), Fraction()) / (n * (n - 1)))) if n > 1 else None
    cluster = None
    if groups > 1:
        scores = [sum(group, Fraction()) - len(group) * mean for group in by_day.values()]
        cluster = sqrt(float(Fraction(groups, groups - 1) * sum((s * s for s in scores), Fraction()) / (n * n)))
    interval = lambda se: None if se is None else [float(mean) - 1.96 * se, float(mean) + 1.96 * se]
    return {"closed_trades": n, "traded_dates": groups, "mean_net_pct": None if mean is None else str(mean),
            "naive_se_pct": naive, "clustered_day_se_pct": cluster,
            "clustered_to_naive_se_ratio": cluster/naive if naive and cluster is not None else None,
            "naive_approx95_pct": interval(naive), "clustered_day_approx95_pct": interval(cluster),
            "method": "Trade-weighted mean; date-cluster sandwich with G/(G-1); normal 1.96 interval. "
                      "Approximate; no multiple-search correction or confirmation claim."}


def _forecast(rows):
    brier, errors = defaultdict(list), []
    bins = [[[] for _ in range(5)] for _ in range(3)]
    for row in rows:
        if row["path_truth"] is not None and row["probabilities"] is not None:
            brier[row["session"]].append(sum((p - (k == row["path_truth"])) ** 2
                                               for k, p in enumerate(row["probabilities"])))
            for index, probability in enumerate(row["probabilities"]):
                bins[index][min(int(5*probability),4)].append((row["session"],probability,row["path_truth"] == index))
        if row["status"] == "closed" and row["predicted_gross_pct"] is not None:
            truth = float(row["gross"] / row["entry_notional"] * 100)
            errors.append((row["session"], row["predicted_gross_pct"] - truth))
    return {
        "path_known": sum(row["path_truth"] is not None for row in rows),
        "probability_supported": sum(row["probabilities"] is not None for row in rows),
        "scored_paths": sum(map(len, brier.values())), "scored_path_dates": len(brier),
        "dateweighted_brier": sum(sum(group) / len(group) for group in brier.values()) / len(brier) if brier else None,
        "reliability": [[{"lower":i/5,"upper":(i+1)/5,"rows":len(group),
                           "dates":len({day for day,_,_ in group}),
                           "mean_probability":sum(p for _,p,_ in group)/len(group) if group else None,
                           "observed_fraction":sum(y for _,_,y in group)/len(group) if group else None}
                          for i,group in enumerate(outcome)] for outcome in bins],
        "payoff_prediction_supported": sum(row["predicted_gross_pct"] is not None for row in rows),
        "scored_payoffs": len(errors), "scored_payoff_dates": len({day for day, _ in errors}),
        "payoff_mae_pct": sum(abs(error) for _, error in errors) / len(errors) if errors else None,
        "payoff_rmse_pct": sqrt(sum(error * error for _, error in errors) / len(errors)) if errors else None,
    }


def _concentration(panels, total):
    positive = {key: Fraction(panel["nominal_net_cash"]) for key, panel in panels.items()
                if Fraction(panel["nominal_net_cash"]) > 0}
    largest = min(positive, key=lambda key: (-positive[key], key)) if positive else None
    positive_cash = sum(positive.values(), Fraction())
    return {"largest_positive_group": largest, "largest_positive_cash": str(positive[largest]) if largest else None,
            "net_cash_without_largest_positive": str(total - positive[largest]) if largest else None,
            "largest_positive_cash_fraction": str(positive[largest] / positive_cash) if largest else None,
            "largest_trade_fraction": str(Fraction(max((p["filled_closed"] for p in panels.values()), default=0),
                                                   sum(p["filled_closed"] for p in panels.values())))
            if any(p["filled_closed"] for p in panels.values()) else None}


def summarize(records: list[dict], calendar: list[str], *, cost_bps: int = 33) -> dict:
    """Summarize executed arm states; unselected opportunities use no_entry."""
    rows, days = _read(records, cost_bps), _calendar(calendar)
    allowed = set(calendar)
    _require(all(row["session"] in allowed for row in rows), "opportunity outside evaluation calendar")
    total = _panel(rows)
    by_date = {day: _panel([row for row in rows if row["session"] == day]) for day in calendar}
    weekly = {week: _panel([row for row in rows if _week(date.fromisoformat(row["session"])) == week])
              for week in sorted({_week(day) for day in days})}
    by_fold = {str(fold): _panel([row for row in rows if row["fold"] == fold])
               for fold in sorted({row["fold"] for row in rows})}
    by_symbol = {symbol: _panel([row for row in rows if row["symbol"] == symbol])
                 for symbol in sorted({row["symbol"] for row in rows})}
    net = Fraction(total["nominal_net_cash"])
    return {"evaluation_dates": len(days), "weeks": len(weekly), "cost_bps": cost_bps,
            "totals": total, "weekly": weekly, "by_date": by_date, "by_fold": by_fold, "by_symbol": by_symbol,
            "zero_trade_weeks": sum(panel["filled_closed"] == 0 for panel in weekly.values()),
            "zero_cost_clearing_weeks": sum(panel["cost_clearing"] == 0 for panel in weekly.values()),
            "concentration": {"date": _concentration(by_date, net), "symbol": _concentration(by_symbol, net)},
            "uncertainty": _uncertainty(rows), "forecast": _forecast(rows),
            "claim": "Exploratory original opportunities; nominal event cash, not portfolio NAV; no automatic pass."}


def verify_summary(records: list[dict], calendar: list[str], result: dict, *, cost_bps: int = 33) -> None:
    """Independent pass verifies exact aggregate counts, means and cash panels."""
    rows, days = _read(records, cost_bps), _calendar(calendar)
    _require(all(row["session"] in set(calendar) for row in rows), "opportunity outside evaluation calendar")
    _require(set(result["by_date"]) == set(calendar)
             and set(result["weekly"]) == {_week(day) for day in days}, "calendar denominator differs")
    groups = [(result["totals"], rows)]
    for key, field in (("by_date", "session"), ("by_fold", "fold"), ("by_symbol", "symbol")):
        keys = set(calendar) if key == "by_date" else {str(row[field]) for row in rows}
        _require(set(result[key]) == keys, "group denominator differs")
        groups.extend((panel, [row for row in rows if str(row[field]) == label])
                      for label, panel in result[key].items())
    groups.extend((panel, [row for row in rows if _week(date.fromisoformat(row["session"])) == label])
                  for label, panel in result["weekly"].items())
    for panel, group in groups:
        states = [row["status"] for row in group]
        exact = {"all_opportunities": len(group), "selected": sum(row["selected"] for row in group),
                 "known_economics": sum(state not in ("unresolved", "unsupported") for state in states),
                 "selected_known": sum(state in ("closed", "no_fill") for state in states),
                 "filled_closed": states.count("closed"), **{s: states.count(s) for s in STATES if s != "closed"}}
        gross_sum = cost_sum = net_sum = gross_pct = net_pct = Fraction()
        clearing = 0
        for row in group:
            if row["status"] != "closed":
                continue
            gross_sum += row["gross"]
            cost_sum += row["cost"]
            net_sum += row["net"]
            gross_pct += 100 * row["gross"] / row["entry_notional"]
            net_pct += 100 * row["net"] / row["entry_notional"]
            clearing += row["gross"] > row["entry_notional"] * Fraction(cost_bps, 10000)
        n = states.count("closed")
        exact.update(cost_clearing=clearing, nominal_gross_cash=str(gross_sum), nominal_cost_cash=str(cost_sum),
                     nominal_net_cash=str(net_sum), mean_gross_pct=str(gross_pct / n) if n else None,
                     mean_net_pct=str(net_pct / n) if n else None)
        _require(panel == exact, "exact aggregate differs")
    _require(result["evaluation_dates"] == len(days) and result["weeks"] == len(result["weekly"])
             and result["cost_bps"] == cost_bps, "report geometry differs")
    _require(result["zero_trade_weeks"] == sum(panel["filled_closed"] == 0 for panel in result["weekly"].values())
             and result["zero_cost_clearing_weeks"]
             == sum(panel["cost_clearing"] == 0 for panel in result["weekly"].values()), "zero week count differs")


def pair(compare_records: list[dict], baseline_records: list[dict], *, cost_bps: int = 33) -> dict:
    """Separate common closed effects from selection and unknown-support changes."""
    candidate = {row["key"]: row for row in _read(compare_records, cost_bps)}
    baseline = {row["key"]: row for row in _read(baseline_records, cost_bps)}
    _require(candidate.keys() == baseline.keys(), "paired original roster differs")
    common_known, common_closed = [], []
    selection_changed = candidate_unknown_only = baseline_unknown_only = 0
    for key, new in candidate.items():
        old = baseline[key]
        _require(all(new[field] == old[field] for field in ("session", "symbol", "fold")), "paired identity differs")
        selection_changed += new["selected"] != old["selected"]
        new_unknown, old_unknown = new["net"] is None, old["net"] is None
        candidate_unknown_only += new_unknown and not old_unknown
        baseline_unknown_only += old_unknown and not new_unknown
        if not new_unknown and not old_unknown:
            common_known.append((old, new))
            if old["status"] == new["status"] == "closed":
                common_closed.append((old, new))
    def cash(group):
        before = sum((old["net"] for old, _ in group), Fraction())
        after = sum((new["net"] for _, new in group), Fraction())
        return {"coordinates": len(group), "baseline_net_cash": str(before), "compare_net_cash": str(after),
                "delta_net_cash": str(after - before)}
    return {"all_opportunities": len(candidate), "selection_changed": selection_changed,
            "compare_unknown_only": candidate_unknown_only, "baseline_unknown_only": baseline_unknown_only,
            "both_unknown": sum(candidate[k]["net"] is None and baseline[k]["net"] is None for k in candidate),
            "common_known": cash(common_known), "common_closed": cash(common_closed),
            "baseline_counts": _panel(list(baseline.values())), "compare_counts": _panel(list(candidate.values())),
            "claim": "Common closed is outcome overlap, not proof of matched entry or portfolio NAV."}
