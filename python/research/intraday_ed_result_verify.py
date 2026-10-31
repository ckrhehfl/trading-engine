"""Independent summaries of already verified ED labels and account histories.

No source access, model fit, account replay or experiment logging occurs here.
The completed-report boundary authenticates externally pinned publications and
streams the corresponding gzip bodies through EOF before returning any result.
An artifact digest authenticates bytes, not science: the original independent
snapshot/account/model receipts must also be present and bound to those bytes.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
import gzip
import hashlib
import json
from typing import BinaryIO

from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_comparison as policy
from research import intraday_ed_comparison_unit as contract
from research import intraday_ed_replay as replay
from research import intraday_ed_snapshot as snapshot
from research.intraday_campaign_ml_labels import MLCalendar, fixed_calendar
from research.intraday_pattern_calibration import dispersion


SCHEMA = "ED-independent-result-verification-v1"
REPORT_SCHEMA = "ED-H1-method-results-v1"
DOMAINS = ("full_original_population", "observed_predecision_support_conditional")
CAPITAL = replay.cash.INITIAL_CAPITAL
MAXIMUM = 512 * wire.MIB


class ResultRefusal(ValueError):
    """Changed identities, calendars or aggregates are not missing results."""


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise ResultRefusal(reason)


def _encoded(value):
    return snapshot.encode(value)


def _same(actual, expected, name: str) -> None:
    _need(snapshot.canonical(_encoded(actual)) == snapshot.canonical(_encoded(expected)), name)


def _plain(value):
    """Decode only the existing nonexecutable scalar wire tags."""
    if not isinstance(value, (dict, list)):
        value = _encoded(value)
    if type(value) is dict:
        for kind, cls in (("fraction", Fraction), ("decimal", Decimal),
                          ("float64", float)):
            if set(value) == {kind}:
                return snapshot._decode(cls, value)
        return {key: _plain(item) for key, item in value.items()}
    if type(value) is list:
        return [_plain(item) for item in value]
    return value


def _clock(value) -> datetime:
    result = value if type(value) is datetime else datetime.fromisoformat(value)
    _need(result.utcoffset() is not None, "account clock must have a timezone")
    return result


def _calendar(calendar: MLCalendar) -> tuple[str, ...]:
    _need(type(calendar) is MLCalendar and len(calendar.source_reference_sessions) == 257
          and len(calendar.evaluation_reference_sessions) == 157, "original257/157 calendar required")
    _same(calendar, fixed_calendar(list(calendar.source_reference_sessions)), "fixed calendar differs")
    return calendar.evaluation_reference_sessions


def _mean(values):
    return sum(values, Fraction()) / len(values) if values else None


def _accepted(calendar, rows, plan):
    dates = _calendar(calendar)
    _need(type(plan) is policy.PolicyPlan and plan.scenario in ("S1", "S2")
          and plan.evaluation_sessions == dates, "policy calendar/scenario differs")
    lookup = {}
    for row in rows:
        _need(type(row) is policy.PolicyOpportunity, "typed verified opportunity required")
        coordinate = row.scenario, row.symbol, row.session
        _need(coordinate not in lookup and row.session in calendar.source_reference_sessions,
              "duplicate/outside original opportunity")
        lookup[coordinate] = row
    expected = {coordinate for coordinate in lookup if coordinate[0] == plan.scenario
                and coordinate[2] in dates}
    seen, accepted = set(), []
    for decision in plan.decisions:
        coordinate = decision.scenario, decision.symbol, decision.session
        _need(coordinate in expected and coordinate not in seen
              and decision.status in ("accept", "decline", "unknown", "no_parent"),
              "decision coverage differs")
        seen.add(coordinate)
        if decision.status == "accept":
            row = lookup[coordinate]
            _need(row.candidate is not None and row.common_support
                  and decision.parent_id == row.candidate.parent_id,
                  "accepted fixed common parent required")
            accepted.append(row)
    _need(seen == expected and tuple(sorted(set(plan.unavailable_sessions))) == plan.unavailable_sessions
          and set(plan.unavailable_sessions) <= set(dates), "missing/duplicate policy decisions or dates")
    return accepted


def _policy_expected(calendar, rows, plan):
    accepted = _accepted(calendar, rows, plan)
    dates = calendar.evaluation_reference_sessions
    weekly = dict.fromkeys(calendar.evaluation_reference_weeks, 0)
    for row in accepted:
        parsed = date.fromisoformat(row.session)
        week = (parsed - timedelta(days=parsed.weekday())).isoformat()
        _need(week in weekly, "accepted week outside original calendar")
        weekly[week] += 1
    groups = {}
    for name, selected in (("all", accepted),
            ("event", [r for r in accepted if "NO_EVENT" not in r.event_types]),
            ("known_no_event", [r for r in accepted if r.event_types == ("NO_EVENT",)])):
        known = [r for r in selected if r.label is not None and r.label.status != "unknown"]
        _need(all(type(r.label) is replay.EDUnitLabel and r.label.status in
                  ("known_traded", "known_declined_entry") for r in known), "verified unit labels required")
        outcomes = {}
        for field in ("gross", "net33", "net43"):
            values = [getattr(r.label, field) for r in known]
            _need(all(type(v) is Fraction for v in values), "exact unit outcomes required")
            outcomes[field] = {"mean": _mean(values), "positive_count": sum(v > 0 for v in values)}
        groups[name] = {"accepted_opportunities": len(selected), "known_outcomes": len(known),
            "unknown_outcomes": len(selected) - len(known),
            "known_traded": sum(r.label.status == "known_traded" for r in known),
            "known_declined_entry": sum(r.label.status == "known_declined_entry" for r in known),
            "before_mean_sample_plan": dispersion([{"session": r.session, "label": None
                if r.label is None or r.label.status == "unknown" else r.label.net33} for r in selected], list(dates)),
            "outcomes": outcomes, "small_effect_or_edge_claim": False}
    return {"arm": plan.arm, "scenario": plan.scenario, "weekly_accepted_opportunities": weekly,
        "weekly_average": Fraction(len(accepted), len(weekly)),
        "weeks_at_least15_observed_opportunities": sum(n >= 15 for n in weekly.values()),
        "weekly15_is_execution_guarantee": False, "decision_counts": dict(Counter(d.status for d in plan.decisions)),
        "groups": groups, "unavailable_sessions": plan.unavailable_sessions,
        "denominator": "original157 evaluation dates/weeks; accepted opportunities, not fills",
        "exploratory_only": True, "account_PnL": False, "S1_S2_independent": False,
        "actual_fills_certified": False}


def _concentration_expected(calendar, rows, plan):
    accepted = _accepted(calendar, rows, plan)
    buckets = {}
    for row in accepted:
        fold = next(f.index for f in calendar.folds if row.session in f.test_sessions)
        half = "first" if row.session in calendar.evaluation_reference_sessions[:78] else "second"
        group = "known_no_event" if row.event_types == ("NO_EVENT",) else "event"
        for dimension, value in (("date", row.session), ("name", row.symbol), ("fold", str(fold)),
                                 ("chronological_half", half), ("event_group", group)):
            key = dimension + ":" + value
            if key not in buckets:
                buckets[key] = {"accepted": 0, "known": 0, "unknown": 0,
                                "gross": Fraction(), "net33": Fraction(), "net43": Fraction()}
            bucket = buckets[key]
            bucket["accepted"] += 1
            known = row.label is not None and row.label.status != "unknown"
            bucket["known" if known else "unknown"] += 1
            if known:
                for field in ("gross", "net33", "net43"):
                    bucket[field] += getattr(row.label, field)
    return {"arm": plan.arm, "scenario": plan.scenario, "buckets": buckets,
            "unit_exposure_contributions_not_account_PnL": True, "selected_winning_subgroups": False}


def verify_policy_summary(calendar, opportunities, plan, summary, *, concentration=None) -> dict:
    """Reconstruct exact counts/means, event groups and all original weeks."""
    expected = _policy_expected(calendar, opportunities, plan)
    _same(summary, expected, "independent policy summary differs")
    concentration_pin = None
    if concentration is not None:
        expected_concentration = _concentration_expected(calendar, opportunities, plan)
        _same(concentration, expected_concentration, "independent policy concentration differs")
        concentration_pin = wire.digest(snapshot.canonical(_encoded(expected_concentration)))
    return {"schema": SCHEMA, "verified": True, "arm": plan.arm, "scenario": plan.scenario,
            "original_evaluation_sessions": 157, "original_weeks": len(calendar.evaluation_reference_weeks),
            "producer_summary_used_as_oracle": False, "account_replayed": False,
            "verified_summary_sha256": wire.digest(snapshot.canonical(_encoded(expected))),
            "verified_concentration_sha256": concentration_pin}


def _key(key):
    _need(type(key) in (tuple, list) and len(key) == 5 and key[0] in replay.ARMS
          and key[1] in ("S1", "S2") and type(key[2]) is int and key[2] in (33, 43)
          and key[3] in DOMAINS and type(key[4]) is bool, "fixed account key required")
    return tuple(key)


def _day_expected(key, result, session, prior=None):
    key = _key(key)
    result, session = _plain(result), _plain(session)
    prior = None if prior is None else _plain(prior)
    state, events, navs = result["state"], result["events"], result["snapshots"]
    _need(navs and state["book"]["arm"] == key[0] and state["cost_bps"] == key[2]
          and state["risk_scope"] == ("day_local_counterfactual" if key[4] else "continuous")
          and result["scenario"] == key[3] + ("/fresh_day_counterfactual_never_continuous_NAV"
              if key[4] else "/continuous_sticky_unknown"), "account identity/domain differs")
    _need(not key[4] or prior is None, "fresh-day state cannot carry")
    first, last = navs[0], navs[-1]
    opening, closing = _clock(session["opening"]), _clock(session["closing"])
    _need(_clock(first["at"]) == opening and _clock(last["at"]) == closing,
          "account original day boundaries differ")
    start_cash = CAPITAL if prior is None else prior["book"]["cash"]
    costs = Fraction() if prior is None else prior["total_entry_cost"]
    _need(state["book"]["cash"] == start_cash + sum((e["cash_delta"] for e in events), Fraction())
          == last["cash"] and state["total_entry_cost"] == costs + sum(
              (e["assumed_cost"] for e in events if e["kind"] == "entry"), Fraction()),
          "independent final cash/cost identity differs")
    _need(state["book"]["cash"] == CAPITAL + state["realized_gross_pnl"] - state["total_entry_cost"]
          - sum((lot["quantity"] * lot["entry_price"] for lot in state["book"]["lots"]), Fraction()),
          "independent realized cash identity differs")
    observed = last["observed_nav"]
    if not state["book"]["lots"]:
        _need(observed == state["book"]["cash"], "flat observed NAV differs from cash")
    identified = not state["book"]["indeterminate_reasons"] and observed is not None
    nav = observed if identified else None
    gross_nav = nav + state["total_entry_cost"] if nav is not None else None
    _need(last["full_nav"] == nav and last["same_quantity_gross_nav"] == gross_nav,
          "independent net/gross NAV identity differs")
    lots = [] if prior is None else prior["book"]["lots"]
    starts = {lot["candidate"]["parent_id"]: (opening, lot["quantity"] * lot["entry_price"]) for lot in lots}
    exposure, overlap, positions, previous = Fraction(), 0, len(lots), opening
    for event in events:
        at = _clock(event["at"])
        _need(previous <= at <= closing, "account events not chronological")
        if positions >= 2:
            overlap += int((at - previous).total_seconds() // 60)
        if event["quantity"] > 0 and event["kind"] == "entry":
            _need(event["parent_id"] not in starts, "duplicate open parent")
            starts[event["parent_id"]] = at, -event["cash_delta"] - event["assumed_cost"]
            positions += 1
        elif event["quantity"] > 0 and event["kind"] == "exit":
            _need(event["parent_id"] in starts, "exit without observed entry")
            start, amount = starts.pop(event["parent_id"])
            exposure += amount * int((at - start).total_seconds() // 60)
            positions -= 1
        previous = at
    exposure += sum((amount * int((closing - start).total_seconds() // 60)
                     for start, amount in starts.values()), Fraction())
    if positions >= 2:
        overlap += int((closing - previous).total_seconds() // 60)
    known_return = identified and first["full_nav"] is not None and first["full_nav"] > 0
    return {"arm": key[0], "scenario": key[1], "cost_bps": key[2], "domain": key[3],
        "scope": "fresh_day_counterfactual_diagnostic" if key[4] else "continuous",
        "session": closing.date().isoformat(), "day_open_nav": first["full_nav"], "day_close_nav": nav,
        "identified_day_return": (nav - first["full_nav"]) / first["full_nav"] if known_return else None,
        "same_quantity_gross_day_return": (gross_nav - first["same_quantity_gross_nav"])
            / first["full_nav"] if known_return else None,
        "observed_nav": observed, "same_quantity_gross_nav": gross_nav, "cash": last["cash"],
        "entry_cost_to_date": state["total_entry_cost"],
        "realized_same_quantity_gross_pnl_to_date": state["realized_gross_pnl"],
        "notional_minutes_observed": exposure, "overlap_minutes_observed": overlap,
        "entry_reason_counts": dict(Counter(e["reason"] for e in events if e["kind"] == "entry")),
        "entries": sum(e["kind"] == "entry" and e["quantity"] > 0 for e in events),
        "unknown_event_count": sum(e["kind"] == "unknown" for e in events),
        "first_unknown_at": state["first_unknown_at"], "identified": identified, "fresh_days_stitched": False}


def verify_account_day_summary(key, result, session, summary, prior=None) -> dict:
    """Use verified events/state, not the producer's day-summary helper."""
    expected = _day_expected(key, result, session, prior)
    _same(summary, expected, "independent account day summary differs")
    return {"schema": SCHEMA, "verified": True, "session": expected["session"],
            "producer_summary_used_as_oracle": False, "account_replayed": False}


def _account_expected(calendar, key, days):
    key, dates = _key(key), _calendar(calendar)
    days = [_plain(day) for day in days]
    _need(tuple(day["session"] for day in days) == dates, "account lost original157 calendar")
    for day in days:
        _need((day["arm"], day["scenario"], day["cost_bps"], day["domain"]) == key[:4]
              and day["scope"] == ("fresh_day_counterfactual_diagnostic" if key[4] else "continuous")
              and day["fresh_days_stitched"] is False, "account summary domain/scope differs")
    returns = [day["identified_day_return"] for day in days if day["identified_day_return"] is not None]
    complete = all(day["identified"] for day in days)
    return {"key": key, "evaluation_sessions": 157, "original_calendar": dates,
        "identified_days": sum(day["identified"] for day in days),
        "before_mean_sample_plan": dispersion([{"session": day["session"],
            "label": day["identified_day_return"]} for day in days], list(dates)),
        "identified_day_return_mean": _mean(returns),
        "mean_denominator": "identified-day subset; original157 always retained",
        "continuous_terminal_net_return": days[-1]["day_close_nav"] / CAPITAL - 1
            if complete and not key[4] else None,
        "continuous_terminal_same_quantity_gross_return": days[-1]["same_quantity_gross_nav"] / CAPITAL - 1
            if complete and not key[4] else None,
        "continuous_net_return_status": "fresh_day_not_a_continuous_account" if key[4] else
            "identified" if complete else "full_history_unknown",
        "fresh_days_stitched": False, "edge_claim": False}


def verify_account_summary(calendar, key, days, summary) -> dict:
    _same(summary, _account_expected(calendar, key, days), "independent account aggregate differs")
    return {"schema": SCHEMA, "verified": True, "original_evaluation_sessions": 157,
            "producer_summary_used_as_oracle": False}


def paired_account_records(calendar, accounts, *, treated_arm=policy.RULE, control_arm=policy.CONTROL):
    """Reconstruct same-budget full and conditional contrasts without replay."""
    dates = _calendar(calendar)
    for rawkey in sorted(accounts):
        key = _key(rawkey)
        if key[0] != treated_arm:
            continue
        control_key = (control_arm, *key[1:])
        _need(control_key in accounts, "same-calendar control account required")
        left, right = ([_plain(day) for day in accounts[k]] for k in (key, control_key))
        _need(tuple(d["session"] for d in left) == tuple(d["session"] for d in right) == dates,
              "paired original157 calendar differs")
        paired = []
        for a, b in zip(left, right, strict=True):
            known = a["identified_day_return"] is not None and b["identified_day_return"] is not None
            paired.append({"session": a["session"], "identified_intersection": known,
                "same_initial_budget_day_net_change_difference": ((a["day_close_nav"] - a["day_open_nav"])
                    - (b["day_close_nav"] - b["day_open_nav"])) / CAPITAL if known else None,
                "daily_return_difference": a["identified_day_return"] - b["identified_day_return"] if known else None,
                "notional_minutes_difference": a["notional_minutes_observed"] - b["notional_minutes_observed"],
                "overlap_minutes_difference": a["overlap_minutes_observed"] - b["overlap_minutes_observed"],
                "entries_difference": a["entries"] - b["entries"], "treated_entry_reasons": a["entry_reason_counts"],
                "control_entry_reasons": b["entry_reason_counts"], "treated_first_unknown_at": a["first_unknown_at"],
                "control_first_unknown_at": b["first_unknown_at"]})
        yield "paired_account_plan", {"treated_key": key, "control_key": control_key,
            "before_mean_sample_plan": dispersion([{"session": d["session"],
                "label": d["same_initial_budget_day_net_change_difference"]} for d in paired], list(dates)),
            "original_calendar": dates, "same_initial_budget": CAPITAL,
            "fewer_trades_implies_superior_selection": False,
            "observed_support_conditional_certifies_original_population": False}
        for day in paired:
            yield "paired_account_day", {"treated_key": key, "control_key": control_key, **day}
        known = [d["same_initial_budget_day_net_change_difference"] for d in paired if d["identified_intersection"]]
        contributions = {}
        partitions = [("fold:" + str(f.index), f.test_sessions) for f in calendar.folds]
        partitions += [("chronological_half:first", dates[:78]), ("chronological_half:second", dates[78:])]
        for name, partition in partitions:
            values = [d["same_initial_budget_day_net_change_difference"] for d in paired
                      if d["identified_intersection"] and d["session"] in partition]
            contributions[name] = {"identified_days": len(values), "same_budget_net_change_sum": sum(values, Fraction())}
        complete = len(known) == 157
        yield "paired_account_summary", {"treated_key": key, "control_key": control_key,
            "original_sessions": 157, "identified_intersection_sessions": len(known),
            "unknown_pair_sessions": 157 - len(known), "identified_intersection_fraction": Fraction(len(known), 157),
            "identified_intersection_mean_same_budget_day_net_change_difference": _mean(known),
            "continuous_terminal_net_increment": (left[-1]["day_close_nav"] - right[-1]["day_close_nav"])
                / CAPITAL if complete and not key[4] else None,
            "continuous_terminal_increment_status": "fresh_day_not_continuous" if key[4] else
                "identified" if complete else "one_or_both_account_histories_unknown",
            "concentration": contributions, "conditional_subset_mean_is_full_population_claim": False,
            "same_quantity_within_each_account_not_equal_quantities_between_arms": True,
            "fresh_days_stitched": False, "edge_claim": False}


def verify_matched_pairs(calendar, opportunities, scenario, pairs, summary, *, plan=None) -> dict:
    """Independently freeze training-CDF matches before comparing their labels."""
    dates = _calendar(calendar)
    _need(scenario in ("S1", "S2"), "fixed matching scenario required")
    rows = [r for r in opportunities if r.scenario == scenario and r.common_support]
    _need(all(type(r.volatility) is Fraction and r.volatility >= 0 and type(r.liquidity) is Fraction
              and r.liquidity > 0 for r in rows), "exact predecision matching inputs required")
    expected = []
    for fold in calendar.folds:
        training = [r for r in rows if r.session in fold.training_sessions]
        volatility = sorted(r.volatility for r in training)
        liquidity = sorted(r.liquidity for r in training)
        treated = sorted((r for r in rows if r.session in fold.test_sessions and r.rule_status == "accepted"
                          and "NO_EVENT" not in r.event_types),
                         key=lambda r: (r.session, r.candidate.decision_at, r.symbol, r.candidate.parent_id))
        controls = [r for r in rows if r.session in fold.test_sessions and r.event_types == ("NO_EVENT",)
                    and r.control_status == "accepted"]
        used = set()
        for row in treated:
            result = {"scenario": scenario, "fold": fold.index, "session": row.session,
                "treated_parent_id": row.candidate.parent_id, "control_parent_id": None,
                "reason": "training_CDF_unavailable" if not training else
                    "no_unused_same_session_control_within_fixed_calipers",
                "distance": None, "decision_minute_difference": None, "volatility_rank_difference": None,
                "liquidity_rank_difference": None, "gross_difference": None,
                "net33_difference": None, "net43_difference": None}
            choices = []
            if training:
                v = Fraction(bisect_right(volatility, row.volatility), len(training))
                l = Fraction(bisect_right(liquidity, row.liquidity), len(training))
                for control in controls:
                    if control.session != row.session or control.candidate.parent_id in used:
                        continue
                    minutes = int((control.candidate.decision_at - row.candidate.decision_at).total_seconds() // 60)
                    dv = Fraction(bisect_right(volatility, control.volatility), len(training)) - v
                    dl = Fraction(bisect_right(liquidity, control.liquidity), len(training)) - l
                    if abs(minutes) <= 5 and abs(dv) <= Fraction(1, 5) and abs(dl) <= Fraction(1, 5):
                        distance = Fraction(abs(minutes), 5) + 5 * abs(dv) + 5 * abs(dl)
                        choices.append((distance, control.candidate.decision_at, control.symbol,
                                        control.candidate.parent_id, minutes, dv, dl, control))
            if choices:
                distance, _, _, parent, minutes, dv, dl, control = min(choices, key=lambda x: x[:4])
                used.add(parent)
                result.update(control_parent_id=parent, distance=distance, decision_minute_difference=minutes,
                              volatility_rank_difference=dv, liquidity_rank_difference=dl)
                known = all(r.label is not None and r.label.status != "unknown" for r in (row, control))
                result["reason"] = "known_pair" if known else "matched_pair_outcome_unknown"
                if known:
                    for field in ("gross", "net33", "net43"):
                        result[field + "_difference"] = getattr(row.label, field) - getattr(control.label, field)
            expected.append(result)
    _same(pairs, expected, "independent non-event matches differ")
    aggregate = {"scenario": scenario, "original_pairs": len(expected),
        "matched": sum(r["control_parent_id"] is not None for r in expected),
        "known": sum(r["net33_difference"] is not None for r in expected),
        "means": {field: _mean([r[field] for r in expected if r[field] is not None])
                  for field in ("gross_difference", "net33_difference", "net43_difference")},
        "unknown_outcomes_rematched": False, "edge_claim": False}
    _same(summary, aggregate, "independent matched aggregate differs")
    if plan is not None:
        _same(plan, {"scenario": scenario, "before_mean_sample_plan": dispersion([
            {"session": r["session"], "label": r["net33_difference"]} for r in expected], list(dates)),
            "matching_is_not_account_or_causal_identification": True}, "independent matched sample plan differs")
    return {"schema": SCHEMA, "verified": True, "scenario": scenario,
            "pairs": len(expected), "outcomes_used_to_select_pairs": False}


class _HashedReader:
    def __init__(self, source):
        self.source, self.digest, self.size = source, hashlib.sha256(), 0

    def read(self, size=-1):
        _need(type(size) is int and size >= 0, "bounded compressed reads required")
        block = self.source.read(min(size, MAXIMUM - self.size + 1))
        self.size += len(block)
        _need(self.size <= MAXIMUM, "completed artifact compressed bound exceeded")
        self.digest.update(block)
        return block


def _publication(raw, pin, unit, expected_source, expected_runtime, expected_registration):
    _need(type(raw) is bytes and 0 < len(raw) <= 65536 and wire.valid_sha(pin)
          and wire.digest(raw) == pin, "externally pinned completed publication required")
    publication = snapshot._json(raw)
    _need(type(publication) is dict and publication.get("unit_id") == unit
          and publication.get("status") == "completed" and publication.get("verification_complete") is True
          and type(publication.get("invented_only")) is bool and publication.get("full_bridge") is True
          and publication.get("local_pipe_test") is False
          and publication.get("resource_profile") == wire.ED_H1_600.id
          and publication.get("input_contract") == wire.ED_SNAPSHOT_CONTRACT
          and publication.get("geometry") == contract.FULL_GEOMETRY
          and publication.get("candidate_counts") == contract.CANDIDATE_BOUNDS,
          "completed fixed full-bridge method publication required")
    hashes = publication.get("source_hashes")
    minimum = set(contract.MINIMUM_PUBLIC_SOURCES) | {
        "python/research/intraday_ed_result_verify.py", "python/research/intraday_ed_method_unit.py",
        "python/research/intraday_ed_models.py", "python/research/intraday_ed_account_verify.py"}
    _need(type(hashes) is dict and minimum <= set(hashes)
          and all(type(k) is str and wire.valid_sha(v) for k, v in hashes.items())
          and wire.digest(wire.encode(hashes)) == expected_source
          and hashes.get("configs/research/discovery/intraday-ed-h1-common-comparison-v1.json") == expected_registration
          and publication.get("local_identity", {}).get("runtime_sha256") == expected_runtime,
          "completed method source/registration/runtime differs")
    verified = publication.get("independent_verification")
    _need(type(verified) is dict and verified.get("verified") is True
          and verified.get("full_eof_verified") is True and verified.get("full_geometry") is True
          and verified.get("scientific_report_complete") is True
          and verified.get("policy_summaries_independently_verified") is True
          and verified.get("all_account_days_verified") == 157
          and verified.get("producer_state_used_as_oracle_input") is False
          and verified.get("invented_only") is publication["invented_only"]
          and wire.valid_sha(verified.get("stored_decoded_sha256"))
          and verified.get("stored_compressed_sha256") == publication.get("output_sha256")
          and wire.valid_sha(publication.get("output_sha256"))
          and type(publication.get("output_bytes")) is int and 0 < publication["output_bytes"] <= MAXIMUM,
          "bound independent scientific method verification required")
    return publication


def _read_report(source, publication, *, expected_source, expected_snapshot,
                 expected_runtime, expected_registration, checkpoint):
    """Keep summaries and one carried verified state per account; discard raw days."""
    unit = publication["unit_id"]
    arms = (policy.RULE, policy.CONTROL) if unit == "ED-H1-RULES" else (policy.LEARNER,)
    hashed, prefix, decoded, counts = _HashedReader(source), hashlib.sha256(), hashlib.sha256(), Counter()
    header, footer, decoded_bytes = None, False, 0
    accounts, priors, account_summaries, paired, unit_summaries = defaultdict(list), {}, {}, [], {}
    model_folds, model_rows, row_ids, decisions = {}, Counter(), set(), {arm: set() for arm in arms}
    decision_counts, accepted_weeks = defaultdict(Counter), defaultdict(Counter)
    with gzip.GzipFile(fileobj=hashed, mode="rb") as stream:
        while line := stream.readline(snapshot.MAX_LINE + 1):
            checkpoint("independent_completed_method_record")
            decoded_bytes += len(line)
            _need(not footer and line.endswith(b"\n") and len(line) <= snapshot.MAX_LINE
                  and decoded_bytes <= snapshot.MAX_STREAM, "completed method framing differs")
            record = snapshot._json(line)
            _need(type(record) is dict and snapshot.canonical(record) + b"\n" == line,
                  "completed method canonical framing differs")
            kind = record.get("kind")
            _need(type(kind) is str and ((not counts and kind == "header") or (counts and kind != "header")),
                  "completed method header order differs")
            if kind == "footer":
                _same(record, {"kind": "footer", "record_counts": dict(counts),
                    "decoded_prefix_sha256": prefix.hexdigest()}, "completed method footer/counts differ")
                footer = True
            else:
                prefix.update(line)
            decoded.update(line)
            counts[kind] += 1
            if kind == "header":
                header = record
                _need(header.get("schema") == REPORT_SCHEMA and header.get("invented_only") is publication["invented_only"]
                      and header.get("full_geometry") is True and header.get("edge_claim") is False
                      and header.get("promotion_allowed") is False, "completed method header contract differs")
                binding = header.get("binding", {})
                _need(binding.get("caller") == unit and binding.get("source_sha256") == expected_source
                      and binding.get("runtime_sha256") == expected_runtime
                      and binding.get("registration_sha256") == expected_registration
                      and binding.get("input_manifest_sha256") == publication["independent_verification"].get("input_manifest_sha256")
                      and header.get("snapshot_reference", {}).get("compressed_sha256") == expected_snapshot,
                      "completed method header/source/snapshot binding differs")
                reference = header["snapshot_reference"]
                producer_binding = reference.get("producer_binding", {})
                _need(producer_binding.get("caller") == "ED-H1-SNAPSHOT"
                      and producer_binding.get("source_sha256") == expected_source
                      and producer_binding.get("runtime_sha256") == expected_runtime
                      and producer_binding.get("registration_sha256") == expected_registration
                      and wire.valid_sha(producer_binding.get("input_manifest_sha256"))
                      and wire.valid_sha(reference.get("decoded_sha256"))
                      and wire.valid_sha(reference.get("publication_sha256")),
                      "completed snapshot producer clean source/runtime/registration differs")
                calendar = fixed_calendar(header["calendar"]["source_reference_sessions"])
                _same(header["calendar"], calendar, "completed method original calendar differs")
                _calendar(calendar)
            elif kind == "account_day":
                key = _key(record["key"])
                _need(key[0] in arms, "unregistered method account arm")
                days = accounts[key]
                _need(len(days) < 157, "duplicate/extra account day")
                expected_day = calendar.evaluation_reference_sessions[len(days)]
                result = _plain(record["result"])
                receipt, admission = _plain(record.get("independent_verification")), record.get("admission_verification", {})
                _need(type(receipt) is dict and receipt.get("schema") == "ED-independent-account-verification-v1"
                      and receipt.get("verified") is True and receipt.get("producer_state_used_as_input") is False
                      and (receipt.get("arm"), receipt.get("cost_bps"), receipt.get("domain")) == (key[0], key[2], key[3])
                      and receipt.get("scope") == ("day_local_counterfactual" if key[4] else "continuous")
                      and receipt.get("session") == expected_day and receipt.get("verified_days") == len(days) + 1
                      and admission.get("verified") is True and admission.get("test_labels_read") is False
                      and admission.get("admission_from_raw_predecision_fields") is True,
                      "bound independent daily account/admission receipt differs")
                session = {"opening": result["snapshots"][0]["at"], "closing": result["snapshots"][-1]["at"]}
                expected = _day_expected(key, result, session, None if key[4] else priors.get(key))
                _need(expected["session"] == expected_day, "completed account day/calendar differs")
                _same(record["summary"], expected, "completed independent day summary differs")
                days.append(expected)
                if not key[4]:
                    priors[key] = result["state"]
            elif kind == "account_summary":
                key = _key(record["key"])
                _need(key not in account_summaries, "duplicate account aggregate")
                account_summaries[key] = {k: v for k, v in record.items() if k != "kind"}
            elif kind in ("paired_account_plan", "paired_account_day", "paired_account_summary"):
                paired.append((kind, {k: v for k, v in record.items() if k != "kind"}))
            elif kind == "unit_summary":
                summary = record["summary"]
                key = summary.get("arm"), summary.get("scenario")
                _need(key not in unit_summaries and key[0] in arms and key[1] in ("S1", "S2")
                      and summary.get("exploratory_only") is True and summary.get("account_PnL") is False
                      and summary.get("S1_S2_independent") is False and summary.get("actual_fills_certified") is False,
                      "completed unit summary contract differs")
                receipt = record.get("independent_verification", {})
                _need(receipt.get("schema") == SCHEMA and receipt.get("verified") is True
                      and receipt.get("producer_summary_used_as_oracle") is False
                      and (receipt.get("arm"), receipt.get("scenario")) == key
                      and receipt.get("original_evaluation_sessions") == 157
                      and receipt.get("verified_summary_sha256") == wire.digest(snapshot.canonical(summary))
                      and receipt.get("verified_concentration_sha256") == wire.digest(
                          snapshot.canonical(record.get("concentration"))),
                      "completed unit summary/concentration independent receipt binding differs")
                unit_summaries[key] = summary
            elif kind == "decision":
                arm = record["arm"]
                _need(arm in arms, "unregistered completed decision arm")
                decision = record["decision"]
                _need(decision["scenario"] == record["scenario"] and decision["session"] in calendar.evaluation_reference_sessions,
                      "completed decision original calendar differs")
                coordinate = (record["scenario"], decision["symbol"], decision["session"], decision["parent_id"])
                _need(coordinate not in decisions[arm], "duplicate completed decision coordinate")
                decisions[arm].add(coordinate)
                decision_counts[arm, record["scenario"]][decision["status"]] += 1
                if decision["status"] == "accept":
                    day = date.fromisoformat(decision["session"])
                    week = (day - timedelta(days=day.weekday())).isoformat()
                    accepted_weeks[arm, record["scenario"]][week] += 1
            elif kind == "model_fold":
                _need(arms == (policy.LEARNER,) and record["arm"] == policy.LEARNER, "unregistered fitted arm")
                scenario, view, fit = record["scenario"], record["fold_view"], record["fit"]
                index = view["fold"]["index"]
                key = scenario, index
                _need(key not in model_folds and scenario in ("S1", "S2") and type(index) is int and 1 <= index <= 5,
                      "duplicate/extra model fold")
                _same(view["fold"], calendar.folds[index - 1], "completed model fold calendar differs")
                _same(view["source_reference_sessions"], calendar.source_reference_sessions,
                      "completed model source calendar differs")
                batch = record["predictions"]
                _need(view["scenario"] == scenario and batch["scenario"] == scenario
                      and batch["fold_index"] == index and batch["status"] == fit["status"],
                      "completed prediction model unit differs")
                _same(batch["evaluation_sessions"], calendar.folds[index - 1].test_sessions,
                      "completed prediction calendar differs")
                verification = record.get("independent_verification", {})
                membership, numerics = verification.get("membership", {}), verification.get("numerics", {})
                _need(membership.get("verified") is True and membership.get("test_labels_read") is False
                      and membership.get("model_fitted") is False and membership.get("fold") == index
                      and membership.get("scenario") == scenario and numerics.get("verified") is True
                      and numerics.get("schema") == "ed-h1-model-verification-v1"
                      and numerics.get("status") == fit.get("status") and fit.get("status") in ("ready", "unavailable"),
                      "independent fixed model receipt differs")
                if fit["status"] == "ready":
                    payload = _plain(fit["artifact"])["payload"]
                    binding = {"source_sha256": expected_source, "runtime_sha256": expected_runtime,
                        "snapshot_sha256": expected_snapshot, "registration_sha256": expected_registration,
                        "fold_index": index, "scenario": scenario, "context_mode": "full"}
                    _same(payload.get("binding"), binding, "completed model unit binding differs")
                    digest = hashlib.sha256(json.dumps({k: v for k, v in payload.items() if k != "artifact_sha256"},
                        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest()
                    _need(payload.get("schema") == "ed-h1-fixed-lightgbm-v1" and payload.get("learner") == "lightgbm"
                          and payload.get("artifact_sha256") == digest == numerics.get("artifact_sha256"),
                          "completed fixed model artifact identity differs")
                else:
                    _need(fit.get("artifact") is None and numerics.get("model_fitted") is False,
                          "unavailable model cannot have a fit")
                model_folds[key] = membership
            elif kind in ("model_training_row", "model_inference_row"):
                rowkey = record["scenario"], record["fold"]
                identity = kind, *rowkey, record["row"]["parent_id"]
                _need(record["arm"] == policy.LEARNER and identity not in row_ids, "duplicate/unregistered model row")
                row_ids.add(identity)
                model_rows[kind, *rowkey] += 1
            else:
                _need(kind in ("header", "footer", "matched_pair_plan", "matched_pair", "matched_pair_summary",
                               "numerical_environment"), "unregistered completed report record")
    verified = publication["independent_verification"]
    _need(footer and hashed.digest.hexdigest() == publication["output_sha256"]
          and hashed.size == publication["output_bytes"] and decoded.hexdigest() == verified["stored_decoded_sha256"]
          and dict(counts) == verified.get("record_counts"), "completed artifact SHA/EOF/counts differs")
    expected_keys = {(arm, scenario, cost, domain, fresh) for arm in arms for scenario in ("S1", "S2")
                     for cost in (33, 43) for domain in DOMAINS for fresh in (False, True)}
    _need(set(accounts) == set(account_summaries) == expected_keys
          and set(unit_summaries) == {(arm, scenario) for arm in arms for scenario in ("S1", "S2")},
          "missing/extra completed account or policy scope")
    for arm in arms:
        daily = Counter((scenario, day) for scenario, _symbol, day, _parent in decisions[arm])
        _need(daily == Counter({(scenario, day): 20 for scenario in ("S1", "S2")
                               for day in calendar.evaluation_reference_sessions}),
              "completed decisions lost the original dated20 target population")
        for scenario in ("S1", "S2"):
            summary = unit_summaries[arm, scenario]
            _same(summary["decision_counts"], dict(decision_counts[arm, scenario]),
                  "completed policy decision counts differ")
            weekly = {week: accepted_weeks[arm, scenario][week] for week in calendar.evaluation_reference_weeks}
            _same(summary["weekly_accepted_opportunities"], weekly, "completed original weeks/accepted counts differ")
    for key in expected_keys:
        verify_account_summary(calendar, key, accounts[key], account_summaries[key])
    expected_paired = list(paired_account_records(calendar, accounts))
    _same(paired, expected_paired, "completed independent paired account records differ")
    if unit == "ED-H1-LIGHTGBM":
        _need(set(model_folds) == {(s, i) for s in ("S1", "S2") for i in range(1, 6)},
              "all ten fixed learner fold slots required")
        for (scenario, index), membership in model_folds.items():
            _need(model_rows["model_training_row", scenario, index] == membership["training_rows"]
                  and model_rows["model_inference_row", scenario, index] == membership["testing_rows"],
                  "completed model row counts differ")
    else:
        _need(not model_folds and not model_rows, "rule unit cannot fit a learner")
    return {"calendar": calendar, "accounts": accounts, "decisions": decisions,
            "header": header, "publication": publication, "record_counts": dict(counts)}


def compare_completed_reports(rules_source: BinaryIO, learner_source: BinaryIO, *,
        rules_publication: bytes, rules_publication_sha256: str,
        learner_publication: bytes, learner_publication_sha256: str,
        expected_source_sha256: str, expected_snapshot_sha256: str,
        expected_runtime_sha256: str, expected_registration_sha256: str,
        checkpoint=lambda _stage: None) -> dict:
    """Fixed learner/control contrasts; no extra fit, source choice or ranking."""
    _need(all(wire.valid_sha(v) for v in (expected_source_sha256, expected_snapshot_sha256,
          expected_runtime_sha256, expected_registration_sha256)), "current immutable source context required")
    publications = [_publication(raw, pin, unit, expected_source_sha256, expected_runtime_sha256,
                    expected_registration_sha256) for raw, pin, unit in (
        (rules_publication, rules_publication_sha256, "ED-H1-RULES"),
        (learner_publication, learner_publication_sha256, "ED-H1-LIGHTGBM"))]
    _need(publications[0]["invented_only"] is publications[1]["invented_only"], "invented/actual reports cannot mix")
    parsed = [_read_report(stream, publication, expected_source=expected_source_sha256,
        expected_snapshot=expected_snapshot_sha256, expected_runtime=expected_runtime_sha256,
        expected_registration=expected_registration_sha256, checkpoint=checkpoint)
        for stream, publication in zip((rules_source, learner_source), publications, strict=True)]
    rules, learner = parsed
    _same(learner["calendar"], rules["calendar"], "completed units original calendar differs")
    _need(learner["decisions"][policy.LEARNER] == rules["decisions"][policy.RULE]
          == rules["decisions"][policy.CONTROL], "completed units fixed parent population differs")
    _same(learner["header"]["snapshot_reference"], rules["header"]["snapshot_reference"],
          "completed units common snapshot reference differs")
    accounts = {**rules["accounts"], **learner["accounts"]}
    comparisons = list(paired_account_records(rules["calendar"], accounts,
                                             treated_arm=policy.LEARNER, control_arm=policy.CONTROL))
    summaries = {tuple(record["treated_key"]): record for kind, record in comparisons if kind == "paired_account_summary"}
    conditions, reasons = [], []
    for scenario in ("S1", "S2"):
        for cost in (33, 43):
            key = policy.LEARNER, scenario, cost, DOMAINS[0], False
            own = _account_expected(rules["calendar"], key, accounts[key])
            pair = summaries[key]
            net, increment = own["continuous_terminal_net_return"], pair["continuous_terminal_net_increment"]
            identified = own["continuous_net_return_status"] == "identified" and pair["continuous_terminal_increment_status"] == "identified"
            passed = None if not identified else net > 0 and increment > 0
            reason = "full_original_continuous_history_unknown" if not identified else (
                "strict_positive_net_and_control_increment" if passed else "net_or_control_increment_not_strictly_positive")
            conditions.append({"scenario": scenario, "cost_bps": cost, "domain": DOMAINS[0], "scope": "continuous",
                "identified": identified, "learner_terminal_net_return": net,
                "terminal_increment_versus_without_filing_rule": increment, "passed": passed, "reason": reason})
            if passed is not True:
                reasons.append(scenario + "/" + str(cost) + ":" + reason)
    gate = {"schema": "ED-H1-fixed-conditional-gate-v1", "status": "not_triggered" if reasons else "triggered",
        "reason": ";".join(reasons) if reasons else "all_four_full_original_continuous_conditions_strictly_positive",
        "learner": policy.LEARNER, "hypothesis_priority": "H1_before_H2", "method_priority": "LightGBM_before_TCN",
        "source_sha256": expected_source_sha256, "snapshot_sha256": expected_snapshot_sha256,
        "runtime_sha256": expected_runtime_sha256, "registration_sha256": expected_registration_sha256,
        "rules_publication_sha256": rules_publication_sha256, "learner_publication_sha256": learner_publication_sha256,
        "rules_output_sha256": publications[0]["output_sha256"], "learner_output_sha256": publications[1]["output_sha256"],
        "invented_only": publications[0]["invented_only"], "conditions": conditions,
        "observed_support_conditional_can_open": False, "fresh_days_can_open": False,
        "learner_specific_information_claim": False, "promotion_allowed": False}
    return {"schema": SCHEMA, "verified": True, "status": gate["status"], "reason": gate["reason"],
        "conditions": gate, "comparisons": comparisons, "original_evaluation_sessions": 157,
        "full_gzip_eof_verified": True, "additional_model_fits": 0, "producer_aggregate_used_as_oracle": False,
        "unit_labels_and_account_state_graph": "original bound independent snapshot/account receipts",
        "uncertainty_boundary": "intraday_pattern_calibration.dispersion fixed statistical routine",
        "bootstrap_independently_reimplemented": False}


def validate_conditional_gate(evidence, rules_source: BinaryIO, learner_source: BinaryIO, *,
        expected_source_sha256: str, expected_snapshot_sha256: str,
        expected_runtime_sha256: str, expected_registration_sha256: str,
        checkpoint=lambda _stage: None) -> dict:
    """Revalidate full sealed prior bytes; a dataclass or boolean cannot open it."""
    _need(all(hasattr(evidence, name) for name in ("source_sha256", "snapshot_sha256",
        "rules_publication", "rules_publication_sha256", "rules_output_sha256", "learner_publication",
        "learner_publication_sha256", "learner_output_sha256", "reconstructed_conditions")),
        "complete immutable conditional evidence required; boolean cannot open")
    _need(evidence.source_sha256 == expected_source_sha256 and evidence.snapshot_sha256 == expected_snapshot_sha256,
          "conditional evidence current source/snapshot differs")
    result = compare_completed_reports(rules_source, learner_source,
        rules_publication=evidence.rules_publication, rules_publication_sha256=evidence.rules_publication_sha256,
        learner_publication=evidence.learner_publication, learner_publication_sha256=evidence.learner_publication_sha256,
        expected_source_sha256=expected_source_sha256, expected_snapshot_sha256=expected_snapshot_sha256,
        expected_runtime_sha256=expected_runtime_sha256, expected_registration_sha256=expected_registration_sha256,
        checkpoint=checkpoint)
    gate = result["conditions"]
    _need(evidence.rules_output_sha256 == gate["rules_output_sha256"]
          and evidence.learner_output_sha256 == gate["learner_output_sha256"]
          and type(evidence.reconstructed_conditions) is bytes
          and evidence.reconstructed_conditions == wire.encode(_encoded(gate)),
          "conditional evidence is not the independently reconstructed completed result")
    return result
