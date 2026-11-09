"""Compare immediate, delayed and stability-checked entries on sealed laggards."""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_exit as cash_summary, intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_laggard_confirmation as stability
from research import intraday_candidate_laggard_execution as execution
from research import intraday_candidate_laggard_report as support
from research import intraday_candidate_laggard_run as laggard
from research.intraday_candidate_room_saved import _parent
from research.intraday_context_cost_census_inputs import _Rows, _source


PARENT_UNIT = "EJ-ENTRY-OPENING-LAGGARDS"
CONFIRMATION_OFFSETS = (62, 63, 64)
TRADE_OFFSETS = tuple(range(68, 94))


def _parent_plans(path, seal):
    days = []
    with _source(path, 16 * wire.MIB) as (stream, size):
        wire.require(size == seal["size"] and seal["sessions"] == 157, "EJ stability parent selection extent differs")
        rows = _Rows(stream)
        for _ in range(seal["sessions"]):
            item = rows.next()
            day, fold, plan = (item[key] for key in ("session", "fold", "plan"))
            wire.require((not days or day > days[-1][0]) and type(fold) is int and 1 <= fold <= 5
                         and len(plan["symbols"]) == 20, "EJ stability parent selection geometry differs")
            days.append((day, fold, plan))
        rows.finish(seal)
    return days


def _ready(plan):
    return plan["rank_state"] == "select" and plan["order_state"] == "ready"


def _confirmation_session(reader, symbol, day, invented, audit):
    session = laggard._project(reader, symbol, day, CONFIRMATION_OFFSETS, invented, audit)
    if invented and symbol == "100002":
        # Offset 62 was outside the parent plan and execution projections.
        first = min(session.minutes)
        minute = session.minutes[first]
        op, high, low, close = minute.ohlc
        raised = close + Decimal(1)
        minutes = {**session.minutes, first: replace(minute, ohlc=(op, max(high, raised), low, raised))}
        return replace(session, minutes=minutes)
    return session


def _confirmed_arm(waited, confirmation):
    if confirmation is None or confirmation["state"] == "select":
        return waited
    status = "unresolved" if confirmation["state"] == "unresolved" else "no_entry"
    return laggard._inactive_arm(status, confirmation["reason"])


def _comparison(rows):
    report = cash_summary.summarize(rows)
    cash_reference.verify_summary(rows, report)
    return report


def _contrasts(rows, audit):
    waiting_vs_stability = []
    for row in rows:
        question, = row["questions"]
        waiting_vs_stability.append({**row, "questions": [{**question, "baseline": question["candidate"],
                                                         "candidate": question["confirmed"]}]})
    return {
        "immediate_vs_wait": {"comparison": _comparison(rows), "execution": support.execution_summary(rows, audit=audit)},
        "wait_vs_stability": {"comparison": _comparison(waiting_vs_stability),
                              "execution": support.execution_summary(waiting_vs_stability, audit=audit)},
    }


def calculate(spec, parents, slot, invented, root, sessions, publication, audit_receipt, *, audit=False, stored=None):
    """Use the audited original controls and seal new decisions before payoff projection."""
    confirm = stability.reference_confirmation if audit else stability.confirmation
    execute = execution.reference_delayed_trade if audit else execution.execute_delayed_trade
    release = spec["laggard_parent"]
    attempt = release["qualification" if invented else "actual"]["attempt"]
    parent_slot = root / "output" / f"{PARENT_UNIT}-{attempt}"
    original, _ = _parent(release, PARENT_UNIT, root, invented, publication, audit_receipt, load_outputs=False)
    scenarios = tuple(item["scenario"] for item in parents["inputs"])
    original_plans = {scenario: _parent_plans(parent_slot / f"{scenario}-pre-entry.jsonl",
                                              original[scenario]["selection_seal"]) for scenario in scenarios}
    results, reports, seals, confirmation_by_scenario = {}, {}, {}, {}
    with sessions(spec, slot, invented, audit) as reader:
        cache = {}
        for scenario in scenarios:
            days = []
            for day, fold, plan in original_plans[scenario]:
                decision = datetime.fromisoformat(plan["decision_at"])
                checks = {}
                for symbol, entry in plan["symbols"].items():
                    if _ready(entry):
                        stop = Fraction(entry["stop"])
                        key = (day, symbol, decision, stop)
                        if key not in cache:
                            session = _confirmation_session(reader, symbol, day, invented, audit)
                            cache[key] = confirm(session, decision, stop)
                        checks[symbol] = cache[key]
                days.append((day, fold, checks))
            seals[scenario] = laggard._seal(slot, scenario, days, audit)
            confirmation_by_scenario[scenario] = {day: checks for day, _, checks in days}

        # Authenticate the unchanged immediate ledgers only after both new plans are durable.
        _, parent_rows = _parent(release, PARENT_UNIT, root, invented, publication, audit_receipt)
        delayed = {}
        for scenario in scenarios:
            plans = {(day, symbol): (fold, plan["decision_at"], entry)
                     for day, fold, plan in original_plans[scenario] for symbol, entry in plan["symbols"].items()}
            rows = parent_rows[scenario]
            wire.require(len(plans) == len(rows) == 3140, "EJ stability original coordinate extent differs")
            persisted = stored[scenario] if audit else [None] * len(rows)
            result = []
            for row, previous in zip(rows, persisted, strict=True):
                day, symbol = row["session"], row["symbol"]
                question, = row["questions"]
                fold, at, entry = plans.pop((day, symbol))
                wire.require(row["fold"] == fold and question["decision_at"] == at
                             and wire.encode(question["rank_decision"]) == wire.encode(entry),
                             "EJ stability original rank decision differs")
                immediate = question["baseline"]
                checked = confirmation_by_scenario[scenario][day].get(symbol)
                if checked is None:
                    wire.require(not _ready(entry), "EJ stability ready rank has no confirmation evidence")
                    waited = immediate
                else:
                    decision = datetime.fromisoformat(at)
                    limit, stop = Fraction(entry["limit"]), Fraction(entry["stop"])
                    key = day, symbol, decision, limit, stop
                    if key not in delayed:
                        session = laggard._project(reader, symbol, day, TRADE_OFFSETS, invented, audit)
                        delayed[key] = execute(symbol, decision, limit, stop, session)
                    waited = delayed[key]
                    wire.require(waited["requested_quantity"] == immediate["requested_quantity"],
                                 "EJ stability frozen quantity differs")
                changed = {**question, "candidate": waited, "confirmed": _confirmed_arm(waited, checked),
                           "confirmation": checked}
                rebuilt = {**row, "questions": [changed]}
                if audit:
                    wire.require(wire.encode(rebuilt) == wire.encode(previous), "EJ stability audited trade differs")
                result.append(rebuilt)
            contrasts = _contrasts(result, audit)
            wire.require(contrasts["immediate_vs_wait"]["comparison"]["groups"]["all"]["totals"]["baseline"]
                         == original[scenario]["comparison"]["groups"]["all"]["totals"]["baseline"],
                         "EJ stability immediate control cash differs")
            reports[scenario] = {"confirmation_seal": seals[scenario], **contrasts}
            results[scenario] = result
    return reports, results
