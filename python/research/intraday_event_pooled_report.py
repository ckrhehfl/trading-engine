"""Pool audited EL fold comparisons on their original held-out calendars."""

from collections import Counter, defaultdict
from fractions import Fraction
from itertools import product
from typing import NamedTuple

from research import intraday_event_evaluation as evaluation
from research.intraday_event_execution import ENTRY_POLICIES, MANAGEMENT_POLICIES
from research.intraday_event_model_workflow import FOLDS, KINDS, SCOPES
from research.intraday_event_scenarios import FAMILIES


_ARMS = frozenset(product(evaluation.CONTROLS, ENTRY_POLICIES, MANAGEMENT_POLICIES))
_COHORTS = tuple(product(KINDS, SCOPES))
_ROW_FIELDS = ("model", "scope", "control", "entry_policy", "management", "session", "original_episode_id",
               "symbol", "cluster_id", "family", "state", "action", "candidate_key", "initial_denominator")
_OUTCOME_FIELDS = ("net", "gross", "status", "filled_quantity", "entry_notional")
_END = object()


class _FixedBook(NamedTuple):
    session: str
    action: str
    candidate_key: str | None
    initial_denominator: str | None
    outcome: dict


def _next_unit(reports, kind, scope, fold):
    unit = next(reports, _END)
    if unit is _END:
        raise ValueError("EL pooled report requires all 36 registered comparison units")
    if (unit["schema"] != "EL-fold-comparison-v1" or type(unit["fold"]) is not int
            or (unit["kind"], unit["scope"], unit["fold"]) != (kind, scope, fold)):
        raise ValueError("Unexpected, duplicate or unordered EL comparison unit")
    return unit


def _unit_rows(unit, days, fixed):
    """Verify a ledger once and retain only fields needed by the summaries."""
    if tuple(unit["evaluation_sessions"]) != days:
        raise ValueError("EL fold calendar differs from the registered slice")
    policies = {p["control"]: p for p in unit["policies"]}
    if len(unit["policies"]) != len(evaluation.CONTROLS) or policies.keys() != set(evaluation.CONTROLS):
        raise ValueError("EL fold decision controls are incomplete or duplicated")
    rosters = {p["roster_sha256"] for p in policies.values()}
    if len(rosters) != 1 or not all(evaluation._sha(p["sha256"]) for p in policies.values()):
        raise ValueError("EL fold decision policy seals differ")
    roster_sha = next(iter(rosters))
    if not evaluation._sha(roster_sha):
        raise ValueError("Invalid EL candidate roster seal")
    status = unit["model_status"]
    if status not in ("completed", "unsupported_empty_training"):
        raise ValueError("Unknown EL fold model status")
    statuses = unit["head_statuses"]
    support = evaluation.policy_support(statuses)
    if (status == "unsupported_empty_training") != (set(statuses.values()) == {"unsupported_empty_training"}):
        raise ValueError("EL model and head support statuses differ")
    episodes, arms, rows = {}, defaultdict(set), []
    allowed_days = set(days)
    for row in evaluation.supported_cash_rows(unit["ledgers"], support):
        arm = row["control"], row["entry_policy"], row["management"]
        episode = row["original_episode_id"]
        identity = row["session"], row["symbol"], row["family"], row["cluster_id"]
        if (row["model"] != unit["kind"] or row["scope"] != unit["scope"]
                or identity[0] not in allowed_days or identity[2] not in FAMILIES
                or arm not in _ARMS or row["policy_sha256"] != policies[arm[0]]["sha256"]
                or arm in arms[episode] or episodes.setdefault(episode, identity) != identity):
            raise ValueError("EL fold ledger identity or arm differs")
        arms[episode].add(arm)
        outcome = row["outcome"]
        rows.append({**{name: row[name] for name in _ROW_FIELDS},
                     "outcome": {name: outcome[name] for name in _OUTCOME_FIELDS}})
        if arm[2] == "fixed_exit":
            key = (unit["kind"], unit["scope"], *arm[:2])
            book = _FixedBook(row["session"], row["action"], row["candidate_key"], row["initial_denominator"], outcome)
            for family in ("all", row["family"]):
                fixed[key, family][episode] = book
    if unit["original_episodes"] != len(episodes) or any(found != _ARMS for found in arms.values()):
        raise ValueError("EL fold original opportunity or cash arm is missing")
    return episodes, roster_sha, rows, support


def _paired_effect(left, right, calendar, unavailable_dates, *, fixed_control):
    if left.keys() != right.keys():
        raise ValueError("EL paired comparison changed the original opportunities")
    daily = dict.fromkeys(calendar, Fraction())
    known, unavailable, unknown_dates = 0, 0, set()
    for event in sorted(left):
        a, b = left[event], right[event]
        day = a.session
        x, y = a.outcome, b.outcome
        if fixed_control and a != b:
            raise ValueError("EL fixed decision control changed across matched model units")
        if "model_unavailable" in (x["status"], y["status"]):
            unavailable += 1
        elif x["net"] is None or y["net"] is None:
            unknown_dates.add(day)
        else:
            daily[day] += Fraction(y["net"]) - Fraction(x["net"])
            known += 1
    total = sum(daily.values(), Fraction())
    return {"original_opportunities": len(left), "joint_known_opportunities": known,
            "model_unavailable_opportunities": unavailable,
            "unknown_outcome_opportunities": len(left) - known - unavailable,
            "alternative_minus_baseline_known_cash": str(total) if known else None,
            "all_opportunity_cash_difference": str(total) if known == len(left) and known else None,
            "paired_mean_cash_difference": str(total / known) if known else None,
            "unknown_outcome_dates": sorted(unknown_dates), "model_unavailable_dates": sorted(unavailable_dates),
            "complete_date_uncertainty": evaluation._blocks([
                float(cash) for day, cash in daily.items() if day not in unknown_dates and day not in unavailable_dates]
                if known else [])}


def _cross_unit_effects(indexed, calendar, unavailable):
    pairs = [("peer_context", (kind, "common_chart"), (kind, "common_chart_peer")) for kind in KINDS]
    pairs.extend(("learner", ("empirical", scope), (kind, scope)) for scope in SCOPES for kind in KINDS[1:])
    effects = []
    for comparison, baseline, alternative in pairs:
        for control, entry, family in product(evaluation.CONTROLS, ENTRY_POLICIES, ("all", *FAMILIES)):
            left = indexed[(*baseline, control, entry), family]
            right = indexed[(*alternative, control, entry), family]
            if not left and not right:
                continue
            unavailable_dates = unavailable[baseline] | unavailable[alternative] if control == "model" else set()
            effects.append({"comparison": comparison, "baseline": list(baseline), "alternative": list(alternative),
                            "control": control, "entry_policy": entry, "management": "fixed_exit", "family": family,
                            **_paired_effect(left, right, calendar, unavailable_dates, fixed_control=control != "model")})
    return effects


def pool_cash(calendar, reports):
    """Rebuild 160-session cash and peer/learner contrasts from audited books.

    Caller authenticates the original calendar and each complete fold report.
    Consume reports once in registered kind, scope, then fold order.
    Retain cash rows for only one kind/scope and fixed-exit fields for pairs.
    Full-chart results are descriptive across different feature support. Peer
    increments are paired only between the two common-support scopes.
    """
    calendar = evaluation._calendar(calendar)
    if len(calendar) != FOLDS[-1][2]:
        raise ValueError("EL pooled report requires the original 257-session calendar")
    reports = iter(reports)
    test_days = calendar[FOLDS[0][1]:]
    support = {}
    episodes_by_family = Counter(dict.fromkeys(FAMILIES, 0))
    unavailable, fixed = defaultdict(set), defaultdict(dict)
    original, scope_rosters, seen_episodes = {}, {}, set()
    cash_by_cohort = {}
    for kind, scope in _COHORTS:
        rows = []
        unavailable_policies = defaultdict(set)
        for fold, (_, start, end) in enumerate(FOLDS):
            unit = _next_unit(reports, kind, scope, fold)
            days = calendar[start:end]
            episodes, roster_sha, books, policy_support = _unit_rows(unit, days, fixed)
            if fold not in original:
                if seen_episodes.intersection(episodes):
                    raise ValueError("EL original episode is repeated across outer folds")
                original[fold] = episodes
                seen_episodes.update(episodes)
                episodes_by_family.update(episode[2] for episode in episodes.values())
            elif episodes != original[fold]:
                raise ValueError("EL fold original episode roster differs across model units")
            comparable_scope = "full_chart" if scope == "full_chart" else "common_support"
            if scope_rosters.setdefault((fold, comparable_scope), roster_sha) != roster_sha:
                raise ValueError("EL matched scope candidate roster differs across model units")
            rows.extend(books)
            if not policy_support["entry"]:
                unavailable[kind, scope].update(days)
            for policy, available in policy_support.items():
                if not available:
                    unavailable_policies[kind, scope, policy].update(days)
            forecasts = [group for group in unit["forecast"]["groups"]
                         if group["dimension"] in ("all", "family", "phase", "family_phase")]
            support[fold, kind, scope] = {"kind": kind, "scope": scope, "fold": fold, "model_status": unit["model_status"],
                "original_episodes": len(episodes), "causal_frames": unit["causal_frames"], "scope_frames": unit["scope_frames"],
                "decision_support": unit["decision_support"],
                "fit_counts": unit["fit_counts"], "training_diagnostics": unit["training_diagnostics"],
                "head_statuses": unit["head_statuses"], "policy_support": policy_support, "forecast_support": forecasts}
            del unit, books
        cash_by_cohort[kind, scope] = evaluation.cash_report(rows, test_days, unavailable=unavailable_policies)
        del rows
    if next(reports, _END) is not _END:
        raise ValueError("Unexpected EL comparison unit after the registered roster")

    cash = {**cash_by_cohort[_COHORTS[0]]}
    for name in ("groups", "paired_differences"):
        cash[name] = [entry for cohort in sorted(_COHORTS) for entry in cash_by_cohort[cohort][name]]
    return {"schema": "EL-pooled-cash-v1", "evaluation_sessions": list(test_days), "comparison_units": len(support),
            "original_episodes": len(seen_episodes), "original_episodes_by_family": dict(episodes_by_family),
            "fold_support": [support[fold, kind, scope] for fold in range(len(FOLDS)) for kind, scope in _COHORTS],
            "cash": cash, "cross_unit_paired_differences": _cross_unit_effects(fixed, test_days, unavailable),
            "claim": evaluation.CLAIM}
