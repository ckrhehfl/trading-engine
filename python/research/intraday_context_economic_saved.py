"""Read-only audit of saved EI economic counts and exact cash arithmetic."""

from collections import Counter, defaultdict
from fractions import Fraction
from hashlib import sha256
from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research.intraday_campaign_readiness import _week
from research.intraday_chart_cash import INITIAL_CAPITAL
from research.intraday_context_economic_unit import ECONOMIC_SCHEMA, MAXIMUM_LEDGER_BYTES
from research.intraday_scenario_snapshot_unit import BundleSeal, _sealed_input


def _amount(value):
    wire.require(type(value) is str and 0 < len(value) <= 128, "EI saved economic exact amount required")
    amount = Fraction(value)
    wire.require(str(amount) == value, "EI saved economic exact amount encoding differs")
    return amount


def _saved(value):
    return str(value) if type(value) is Fraction else value


def _same(actual, expected, label):
    wire.require(wire.encode(actual) == wire.encode(expected), "EI saved economic " + label + " differs")


def _line(stream, maximum=4 * wire.MIB):
    raw = stream.readline(maximum + 1)
    wire.require(0 < len(raw) <= maximum and raw.endswith(b"\n"), "EI saved economic record bound differs")
    return bridge.canonical(raw)


def _ledger_holding(row, cost, symbol):
    gross = fees = basis = Fraction()
    quantity = 0
    for leg in row["realized_exits"]:
        original, proceeds, fee = map(_amount, (leg["entry_basis"], leg["gross_proceeds"], leg["allocated_entry_cost"]))
        wire.require(leg["symbol"] == symbol and type(leg["quantity"]) is int and leg["quantity"] > 0
                     and original > 0 and proceeds > 0 and fee == original * cost,
                     "EI saved economic sale basis or fee differs")
        basis += original
        gross += proceeds - original
        fees += fee
        quantity += leg["quantity"]
    if row["status"] == "unresolved":
        return None
    wire.require(row["status"] in ("closed", "not_reentered") and quantity > 0,
                 "EI saved economic resolved holding has no sale")
    net = gross - fees
    wire.require(_amount(row["cash"]) == INITIAL_CAPITAL + net, "EI saved economic final cash differs")
    return gross, fees, net, basis, quantity


def _entry(row, cost, symbol):
    status, fill = row["status"], row["fill"]
    filled = fill is not None and fill["outcome"] == "modeled"
    if filled:
        wire.require(type(fill["quantity"]) is int and fill["quantity"] > 0 and _amount(fill["cost"]) > 0,
                     "EI saved economic fill quantity or cost differs")
    holding = None if row["holding"] is None else _ledger_holding(row["holding"], cost, symbol)
    observed = tuple(row[key] for key in ("gross", "cost", "net", "entry_notional"))
    if status == "closed":
        wire.require(filled and holding is not None and holding[:4] == tuple(map(_amount, observed))
                     and holding[4] == fill["quantity"] and _amount(fill["cost"]) == holding[1],
                     "EI saved economic closed entry ledger differs")
        return status, filled, holding[:4]
    if status in ("no_entry", "no_fill"):
        _same(observed, ["0"] * 4, "known non-entry payoff")
        wire.require(not filled and row["holding"] is None and
                     (fill is None if status == "no_entry" else fill is not None and fill["outcome"] == "declined"),
                     "EI saved economic non-entry fill differs")
        return status, False, (Fraction(),) * 4
    _same(observed, [None] * 4, "unresolved or unsupported payoff")
    wire.require(holding is None, "EI saved economic unpriced entry cannot have resolved holding")
    if status == "unresolved":
        wire.require((row["holding"] is not None) is filled
                     and (filled or fill is None or fill["outcome"] == "indeterminate"),
                     "EI saved economic unresolved entry state differs")
    else:
        wire.require(status in ("unsupported", "feature_unsupported", "question_unsupported", "overlap_suppressed")
                     and fill is None and row["holding"] is None, "EI saved economic unsupported entry state differs")
    return status, filled, None


def _entry_totals(rows):
    summary = {"original_opportunities": len(rows)}
    for index, mode in enumerate(("baseline", "selected")):
        arms = [row[index] for row in rows]
        fills = sum(filled for _, filled, _ in arms)
        closed = [value for status, _, value in arms if status == "closed"]
        totals = tuple(sum((item[i] for item in closed), Fraction()) for i in range(4))
        summary[mode] = {"status_counts": dict(sorted(Counter(status for status, _, _ in arms).items())),
            "modeled_fills": fills, "resolved_fills": len(closed), "fills_with_unknown_outcome": fills - len(closed),
            "closed_gross_pnl": _saved(totals[0]) if closed else None,
            "closed_net_pnl": _saved(totals[2]) if closed else None,
            "closed_assumed_cost": _saved(totals[1]) if closed else None,
            "mean_closed_trade_net_bps": _saved(10000 * sum((row[2] / row[3] for row in closed), Fraction())
                                                / len(closed)) if closed else None,
            "closed_fixed_notional_break_even_cost_bps": _saved(10000 * totals[0] / totals[3]) if closed else None}
    base, select = (summary[mode]["modeled_fills"] for mode in ("baseline", "selected"))
    paired = [(left, right) for left, right in rows if left[2] is not None and right[2] is not None]
    return {**summary, "modeled_fill_reduction_fraction": _saved(Fraction(base - select, base)) if base else None,
            "both_known_opportunities": len(paired), "unpaired_opportunities": len(rows) - len(paired),
            "paired_selected_minus_baseline_net_pnl": _saved(sum((right[2][2] - left[2][2] for left, right in paired),
                                                                Fraction())) if paired else None,
            "paired_baseline_modeled_fills": sum(left[1] for left, _ in paired),
            "paired_selected_modeled_fills": sum(right[1] for _, right in paired)}


def _decision_rosters(directory, seals, expected_coordinates, checkpoint):
    wire.require(type(seals) is list and len(seals) == 5, "EI saved economic fold seal count differs")
    pinned_rosters = {}
    for index, decision in enumerate(seals, 1):
        keys = tuple(key for key in expected_coordinates if key[0] == index)
        path = directory / f"ei-entry-decisions-fold-{index}.jsonl"
        wire.require(type(decision) is dict and set(decision) == {"coordinates", "sha256", "size"}
                     and decision["coordinates"] == len(keys) and wire.valid_sha(decision["sha256"])
                     and wire.file_digest(path, MAXIMUM_LEDGER_BYTES, checkpoint) == (decision["sha256"], decision["size"]),
                     "EI saved economic sealed decision stream differs")
        with _sealed_input(path) as (source, size):
            head = _line(source, wire.MIB)
            wire.require(size == decision["size"] and head["schema"] == "ei-context-entry-decisions-draft-v1"
                         and head["header"]["fold"]["index"] == index
                         and head["header"]["coordinates"] == len(keys), "EI saved economic decision header differs")
            pin = head["header"]["question_roster_sha256"]
            wire.require(wire.valid_sha(pin), "EI saved economic sealed question roster pin differs")
            pinned_rosters[index] = pin
    return pinned_rosters


def _decision_coordinate_bytes(row):
    observed = [[question[key] for key in ("question_sha256", "status", "stage", "decision_at", "episode_origin_context")]
                for question in row["questions"]]
    return wire.encode([row["session"], row["symbol"], row["coordinate_status"], row["original_frames"], observed])


def _verify_sales_basis(legs, quantity, price, closed):
    wire.require(type(quantity) is int and quantity > 0 and type(price) is Fraction and price > 0,
                 "EI saved economic positive modeled fill required")
    sold = sum(leg["quantity"] for leg in legs)
    wire.require(0 <= sold <= quantity and (not closed or sold == quantity)
                 and all(_amount(leg["entry_basis"]) == leg["quantity"] * price for leg in legs),
                 "EI saved economic same-entry sale quantity or basis differs")


def audit_economic_ledger(path: Path, decision_directory: Path, *, pin: BundleSeal, snapshot_pin: BundleSeal,
                          forecast_pin: BundleSeal, scenario: str, cost_bps: int, include_reentry: bool,
                          expected_coordinates: tuple[tuple[int, str, str], ...], checkpoint,
                          expected_decision_seals: list | None = None) -> dict:
    """Verify saved exact ledgers and original evaluation roster, without replaying science."""
    wire.require(scenario in ("S1", "S2") and type(cost_bps) is int and cost_bps in (33, 43)
                 and type(include_reentry) is bool and callable(checkpoint)
                 and type(pin) is type(snapshot_pin) is type(forecast_pin) is BundleSeal
                 and len(expected_coordinates) == 3140 and tuple(sorted(set(expected_coordinates))) == expected_coordinates,
                 "fixed EI saved economic audit scope differs")
    wire.require(wire.file_digest(path, MAXIMUM_LEDGER_BYTES, checkpoint) == (pin.sha256, pin.size),
                 "EI saved economic ledger pin differs")
    cost = Fraction(cost_bps, 10000)
    modes = ("fixed", "chart", "chart_context") + (("reentry",) if include_reentry else ())
    entries, origins, daily, holding = [], defaultdict(list), defaultdict(list), []
    coordinate_statuses, question_statuses, day_counts = Counter(), Counter(), Counter()
    extra_fills = 0
    with _sealed_input(path) as (stream, size):
        wire.require(size == pin.size, "EI saved economic ledger size differs")
        header = _line(stream)
        _same({key: header[key] for key in ("schema", "scenario", "cost_bps", "snapshot", "forecast")},
              {"schema": ECONOMIC_SCHEMA, "scenario": scenario, "cost_bps": cost_bps,
               "snapshot": {"sha256": snapshot_pin.sha256, "size": snapshot_pin.size},
               "forecast": {"sha256": forecast_pin.sha256, "size": forecast_pin.size}}, "header")
        if expected_decision_seals is not None:
            _same(header["entry_decision_seals"], expected_decision_seals, "published decision seals")
        rosters = _decision_rosters(decision_directory, header["entry_decision_seals"], expected_coordinates, checkpoint)
        roster_hashes = {fold: sha256() for fold in rosters}
        for fold, day, symbol in expected_coordinates:
            checkpoint("ei_economic_audit_coordinate")
            row = _line(stream)
            _same([row["fold"], row["session"], row["symbol"], row["entry_independently_verified"]],
                  [fold, day, symbol, True], "coordinate or independent receipt")
            roster_hashes[fold].update(_decision_coordinate_bytes(row))
            coordinate_statuses[row["coordinate_status"]] += 1
            day_counts[day] += 1
            seen = set()
            for question in row["questions"]:
                key, status = question["question_sha256"], question["status"]
                wire.require(wire.valid_sha(key) and key not in seen, "EI saved economic question identity differs")
                seen.add(key)
                question_statuses[status] += 1
                left, right = (_entry(question[mode], cost, symbol) for mode in ("baseline", "selected"))
                delta = None if left[2] is None or right[2] is None else right[2][2] - left[2][2]
                _same(question["selected_minus_baseline_net"], _saved(delta), "matched entry delta")
                if status == "opportunity":
                    entries.append((left, right))
                    origins[question["episode_origin_context"]].append((left, right))
                    daily[day].append((left, right))
                arms = question["same_fill_management"]
                wire.require((arms is not None) is right[1], "EI saved economic matched initial fill differs")
                if arms is not None:
                    wire.require(set(arms) == {*modes, "independent_net_by_arm"}
                                 and arms["fixed"] == question["selected"]["holding"],
                                 "EI saved economic management arms or fixed entry differ")
                    values = {mode: _ledger_holding(arms[mode], cost, symbol) for mode in modes}
                    initial = question["selected"]["fill"]
                    quantity = initial["quantity"]
                    price = _amount(initial["cost"]) / cost / quantity
                    for mode in ("fixed", "chart", "chart_context"):
                        _verify_sales_basis(arms[mode]["realized_exits"], quantity, price, values[mode] is not None)
                    _same(arms["independent_net_by_arm"],
                          {mode: None if value is None else _saved(value[2]) for mode, value in values.items()},
                          "independently verified holding net")
                    holding.append((day, values))
                    if include_reentry:
                        reentry = arms["reentry"]
                        extra = reentry["extra_modeled_fill"]
                        trades = [event for event in reentry["events"]
                                  if event["action"] == "reenter" and event["fill_status"] == "modeled"]
                        parent = arms["chart_context"]["realized_exits"]
                        suffix = reentry["realized_exits"][len(parent):]
                        wire.require(type(extra) is bool and len(trades) == int(extra)
                                     and reentry["realized_exits"][:len(parent)] == parent and (extra or not suffix),
                                     "EI saved economic reentry fill or original sales differ")
                        statuses = ("closed", "unresolved") if extra else ("not_reentered", "unresolved")
                        wire.require(reentry["status"] in statuses and (not extra or arms["chart_context"]["status"] == "closed"),
                                     "EI saved economic reentry status transition differs")
                        if extra:
                            fresh, = trades
                            _verify_sales_basis(suffix, fresh["quantity"], _amount(fresh["open_price"]), values["reentry"] is not None)
                        extra_fills += extra
        _same(_line(stream, 65536), {"records": len(expected_coordinates)}, "footer")
        wire.require(not stream.read(1), "EI saved economic ledger trailing data")
    _same({fold: digest.hexdigest() for fold, digest in roster_hashes.items()}, rosters, "sealed question roster")
    report = header["report"]
    entry = report["entry"]
    _same(entry["overall"], _entry_totals(entries), "entry totals")
    _same(entry["by_episode_origin"], {key: _entry_totals(origins[key]) for key in ("A", "B", "other")}, "origin totals")
    _same(entry["coordinate_statuses"], dict(coordinate_statuses), "coordinate states")
    _same(entry["question_statuses"], dict(question_statuses), "question states")
    _same([entry["original_evaluation_sessions"], entry["original_evaluation_coordinates"]],
          [len(day_counts), len(expected_coordinates)], "original evaluation denominators")
    weeks = {}
    date_contributions = []
    for day, coordinates in day_counts.items():
        totals = _entry_totals(daily[day])
        week = weeks.setdefault(_week(day), Counter())
        week.update(sessions=1, original_coordinates=coordinates, original_opportunities=totals["original_opportunities"],
                    baseline_modeled_fills=totals["baseline"]["modeled_fills"],
                    selected_modeled_fills=totals["selected"]["modeled_fills"])
        date_contributions.append({"session": day, "opportunities": totals["original_opportunities"],
                                   "both_known_opportunities": totals["both_known_opportunities"],
                                   "paired_net_delta": totals["paired_selected_minus_baseline_net_pnl"]})
    _same(entry["weekly"], [{"week": key, **value} for key, value in weeks.items()], "weekly fills")
    _same(entry["date_contributions"], date_contributions, "date contributions")
    daily_means = [_amount(row["paired_net_delta"]) / row["both_known_opportunities"]
                   for row in date_contributions if row["both_known_opportunities"]]
    _same([entry["dates_with_paired_opportunities"], entry["mean_of_daily_paired_opportunity_net_deltas"]],
          [len(daily_means), _saved(sum(daily_means, Fraction()) / len(daily_means)) if daily_means else None],
          "date-weighted paired returns")
    management = report["holding"]
    _same([management["same_modeled_initial_fills"], management["extra_modeled_reentry_fills"]],
          [len(holding), extra_fills], "management counts")
    for mode in modes:
        known = [values[mode] for _, values in holding if values[mode] is not None]
        _same(management["arms"][mode], {"known": len(known), "unknown": len(holding) - len(known),
              **{key: _saved(sum((row[index] for row in known), Fraction())) if known else None
                 for index, key in enumerate(("gross", "assumed_cost", "net"))}}, "holding totals")
    comparisons = (("fixed", "chart"), ("fixed", "chart_context"), ("chart", "chart_context"))
    if include_reentry:
        comparisons += (("chart_context", "reentry"),)
    for (first, second), contrast in zip(comparisons, management["contrasts"], strict=True):
        by_date = defaultdict(list)
        for day, values in holding:
            if values[first] is not None and values[second] is not None:
                by_date[day].append(values[second][2] - values[first][2])
        pairs = sum(map(len, by_date.values()))
        _same(contrast, {"first": first, "second": second, "paired": pairs, "unpaired": len(holding) - pairs,
              "paired_net_delta": _saved(sum((sum(rows, Fraction()) for rows in by_date.values()), Fraction())) if pairs else None,
              "date_contributions": [{"session": day, "count": len(rows), "net_delta": _saved(sum(rows, Fraction()))}
                                     for day, rows in sorted(by_date.items())]}, "matched holding contrast")
    return {"verified": True, "original_evaluation_coordinates": len(expected_coordinates),
            "original_evaluation_sessions": len(day_counts), "original_opportunities": len(entries),
            "same_modeled_initial_fills": len(holding), "science_reexecuted": False}
