"""Invented summary mutations and sealed completed-output gate contracts only."""
from dataclasses import replace
from datetime import date, datetime, timedelta
from fractions import Fraction
import gzip
import hashlib
import io
import json
from types import SimpleNamespace

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_comparison as comparison
from research import intraday_ed_comparison_unit as contract
from research import intraday_ed_method_unit as producer
from research import intraday_ed_replay as replay
from research import intraday_ed_result_verify as verify
from research import intraday_ed_snapshot as snapshot
from research.intraday_campaign_ml_labels import fixed_calendar


def calendar():
    days, day = [], date(2025, 9, 4)
    while len(days) < 257:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return fixed_calendar(days)


def opportunity(index=100, *, symbol="KRX:000001", event=True, known=True, declined=False):
    day = calendar().source_reference_sessions[index]
    opening = datetime.fromisoformat(day + "T09:00:00+09:00")
    candidate = replay.EDCandidate(symbol + day, symbol, opening, opening + timedelta(minutes=390),
        opening + timedelta(minutes=32), opening + timedelta(minutes=35), opening + timedelta(minutes=95), Fraction(90))
    gross = Fraction() if declined else Fraction(1, 100)
    label = None if not known else replay.EDUnitLabel(candidate,
        "known_declined_entry" if declined else "known_traded", "invented", Fraction(100), Fraction(101),
        candidate.cap_exit_at, candidate.cap_exit_at + timedelta(minutes=3), gross,
        Fraction() if declined else gross - Fraction(33, 10000),
        Fraction() if declined else gross - Fraction(43, 10000))
    return comparison.PolicyOpportunity("S1", symbol, day, "parent", None, candidate, True, (0.25,) * 206,
        ("PERIODIC_ANNUAL",) if event else ("NO_EVENT",), "accepted", "accepted", label,
        Fraction(1), Fraction(100))


def test_policy_exact_gold_counts_and_entire_calendar_without_producer_oracle(monkeypatch):
    cal = calendar()
    rows = (opportunity(), opportunity(symbol="KRX:000002", event=False, declined=True),
            opportunity(101, known=False))
    plan = comparison.plan_rule(cal, rows, scenario="S1")
    actual = comparison.unit_summary(cal, rows, plan)
    concentration = producer._concentration(cal, rows, plan)
    assert actual["groups"]["all"]["outcomes"]["net33"]["mean"] == Fraction(67, 20000)
    assert actual["groups"]["all"]["known_outcomes"] == 2
    assert actual["groups"]["event"]["unknown_outcomes"] == 1
    assert actual["groups"]["known_no_event"]["known_declined_entry"] == 1
    def forbidden(*_args, **_kwargs):
        raise AssertionError("producer aggregate used as oracle")
    monkeypatch.setattr(comparison, "unit_summary", forbidden)
    monkeypatch.setattr(producer, "_concentration", forbidden)
    assert verify.verify_policy_summary(cal, rows, plan, actual, concentration=concentration)["verified"]


@pytest.mark.parametrize("mutation", ["mean", "count", "week", "group", "flag", "concentration", "duplicate", "date"])
def test_policy_summary_mutations_refused(mutation):
    cal, rows = calendar(), (opportunity(), opportunity(101, event=False))
    plan = comparison.plan_rule(cal, rows, scenario="S1")
    actual, concentration = comparison.unit_summary(cal, rows, plan), producer._concentration(cal, rows, plan)
    if mutation == "mean":
        actual["groups"]["all"]["outcomes"]["net33"]["mean"] += Fraction(1, 10000)
    elif mutation == "count":
        actual["groups"]["all"]["outcomes"]["net43"]["positive_count"] = 0
    elif mutation == "week":
        del actual["weekly_accepted_opportunities"][cal.evaluation_reference_weeks[-1]]
    elif mutation == "group":
        actual["groups"]["event"]["accepted_opportunities"] += 1
    elif mutation == "flag":
        actual["account_PnL"] = True
    elif mutation == "concentration":
        next(iter(concentration["buckets"].values()))["net33"] += 1
    elif mutation == "duplicate":
        rows += rows[:1]
    else:
        plan = replace(plan, evaluation_sessions=plan.evaluation_sessions[:-1])
    with pytest.raises(verify.ResultRefusal):
        verify.verify_policy_summary(cal, rows, plan, actual, concentration=concentration)


def test_independent_matched_pair_freezes_before_unknown_outcomes(monkeypatch):
    cal = calendar()
    rows = (opportunity(0), opportunity(), opportunity(symbol="KRX:000002", event=False, known=False))
    pairs = comparison.match_non_event(cal, rows, scenario="S1")
    summary = {"scenario": "S1", "original_pairs": 1, "matched": 1, "known": 0,
        "means": dict.fromkeys(("gross_difference", "net33_difference", "net43_difference")),
        "unknown_outcomes_rematched": False, "edge_claim": False}
    monkeypatch.setattr(comparison, "match_non_event", lambda *_a, **_k: pytest.fail("producer match called"))
    assert verify.verify_matched_pairs(cal, rows, "S1", pairs, summary)["verified"]
    with pytest.raises(verify.ResultRefusal):
        verify.verify_matched_pairs(cal, rows, "S1", (replace(pairs[0], control_parent_id=None),), summary)


def test_account_day_cash_and_summary_are_independent(monkeypatch):
    row = opportunity()
    session = replay.EDSession(row.candidate.opening, row.candidate.closing)
    result = replay.replay_ed_account((), {}, (session,), arm=comparison.RULE, compress_idle_minutes=True)
    result = replace(result, scenario="full_original_population/continuous_sticky_unknown")
    key = comparison.RULE, "S1", 33, verify.DOMAINS[0], False
    actual = producer._account_day_summary(key, result, session)
    monkeypatch.setattr(producer, "_account_day_summary", lambda *_a: pytest.fail("producer day called"))
    assert verify.verify_account_day_summary(key, result, session, actual)["verified"]
    with pytest.raises(verify.ResultRefusal, match="day summary"):
        verify.verify_account_day_summary(key, result, session, {**actual, "cash": actual["cash"] + 1})
    with pytest.raises(verify.ResultRefusal, match="cash/cost"):
        verify.verify_account_day_summary(key, replace(result,
            state=replace(result.state, total_entry_cost=Fraction(1))), session, actual)


def _seal(records):
    counts, prefix, lines = {}, hashlib.sha256(), []
    for record in records:
        line = snapshot.canonical(snapshot.encode(record)) + b"\n"
        counts[record["kind"]] = counts.get(record["kind"], 0) + 1
        prefix.update(line)
        lines.append(line)
    lines.append(snapshot.canonical({"kind": "footer", "record_counts": counts,
        "decoded_prefix_sha256": prefix.hexdigest()}) + b"\n")
    counts = {**counts, "footer": 1}
    decoded = b"".join(lines)
    return gzip.compress(decoded, compresslevel=1, mtime=0), hashlib.sha256(decoded).hexdigest(), counts


def _day(key, day, prior, *, gain=1, unknown=False):
    """Small invented cash path; original bound replay receipt is a fixture."""
    opening = datetime.fromisoformat(day + "T09:00:00+09:00")
    closing = opening + timedelta(minutes=390)
    count = 0 if prior is None else prior["invented_day_count"]
    count += 1
    before = verify.CAPITAL + (count - 1) * gain
    cost, entry = Fraction(1), Fraction(100)
    cash = before + gain
    reasons = ["invented_unknown"] if unknown else []
    state = {"book": {"arm": key[0], "cash": cash, "lots": [], "indeterminate_reasons": reasons},
        "cost_bps": Fraction(key[2]), "risk_scope": "day_local_counterfactual" if key[4] else "continuous",
        "total_entry_cost": count * cost, "realized_gross_pnl": count * (gain + cost),
        "first_unknown_at": opening.isoformat() if unknown else None, "invented_day_count": count}
    events = [{"at": (opening + timedelta(minutes=35)).isoformat(), "kind": "entry", "reason": "invented",
        "parent_id": day, "quantity": 1, "cash_delta": -entry - cost, "assumed_cost": cost},
        {"at": (opening + timedelta(minutes=95)).isoformat(), "kind": "exit", "reason": "invented",
        "parent_id": day, "quantity": 1, "cash_delta": entry + cost + gain, "assumed_cost": Fraction()}]
    navs = [{"at": opening.isoformat(), "full_nav": None if unknown else before,
        "observed_nav": before, "cash": before,
        "same_quantity_gross_nav": None if unknown else before + (count - 1) * cost},
        {"at": closing.isoformat(), "full_nav": None if unknown else cash,
        "observed_nav": cash, "cash": cash, "same_quantity_gross_nav": None if unknown else cash + count * cost}]
    result = {"state": state, "events": events, "snapshots": navs,
        "scenario": key[3] + ("/fresh_day_counterfactual_never_continuous_NAV" if key[4] else "/continuous_sticky_unknown")}
    summary = verify._day_expected(key, result, {"opening": opening, "closing": closing}, prior)
    receipt = {"schema": "ED-independent-account-verification-v1", "verified": True,
        "producer_state_used_as_input": False, "arm": key[0], "cost_bps": Fraction(key[2]), "domain": key[3],
        "scope": state["risk_scope"], "session": day, "verified_days": count}
    return {"kind": "account_day", "key": key, "result": result, "summary": summary,
        "independent_verification": receipt, "admission_verification": {"verified": True,
            "test_labels_read": False, "admission_from_raw_predecision_fields": True}}, state


def _bundle(unit, *, gain=2, unknown=False):
    cal = calendar()
    arms = (comparison.RULE, comparison.CONTROL) if unit == "ED-H1-RULES" else (comparison.LEARNER,)
    minimum = set(contract.MINIMUM_PUBLIC_SOURCES) | {"python/research/intraday_ed_result_verify.py",
        "python/research/intraday_ed_method_unit.py", "python/research/intraday_ed_models.py",
        "python/research/intraday_ed_account_verify.py"}
    hashes = dict.fromkeys(sorted(minimum), "a" * 64)
    source = wire.digest(wire.encode(hashes))
    context = {"expected_source_sha256": source, "expected_snapshot_sha256": "c" * 64,
        "expected_runtime_sha256": "b" * 64, "expected_registration_sha256": "a" * 64}
    records = [{"kind": "header", "schema": verify.REPORT_SCHEMA, "calendar": cal, "invented_only": True,
        "full_geometry": True, "edge_claim": False, "promotion_allowed": False,
        "binding": {"caller": unit, "source_sha256": source, "runtime_sha256": "b" * 64,
            "registration_sha256": "a" * 64, "input_manifest_sha256": "d" * 64},
        "snapshot_reference": {"compressed_sha256": "c" * 64, "decoded_sha256": "e" * 64,
            "publication_sha256": "f" * 64,
            "producer_binding": {"caller": "ED-H1-SNAPSHOT", "source_sha256": source,
                "runtime_sha256": "b" * 64, "registration_sha256": "a" * 64,
                "input_manifest_sha256": "e" * 64}}}]
    if arms == (comparison.LEARNER,):
        for scenario in ("S1", "S2"):
            for fold in cal.folds:
                binding = {"source_sha256": source, "runtime_sha256": "b" * 64, "snapshot_sha256": "c" * 64,
                    "registration_sha256": "a" * 64, "fold_index": fold.index, "scenario": scenario, "context_mode": "full"}
                payload = {"schema": "ed-h1-fixed-lightgbm-v1", "learner": "lightgbm", "binding": binding}
                digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=True).encode()).hexdigest()
                payload["artifact_sha256"] = digest
                records.append({"kind": "model_fold", "arm": comparison.LEARNER, "scenario": scenario,
                    "fold_view": {"fold": fold, "source_reference_sessions": cal.source_reference_sessions, "scenario": scenario},
                    "fit": {"status": "ready", "artifact": {"payload": payload}},
                    "predictions": {"scenario": scenario, "fold_index": fold.index, "status": "ready",
                        "evaluation_sessions": fold.test_sessions},
                    "independent_verification": {"membership": {"verified": True, "test_labels_read": False,
                        "model_fitted": False, "fold": fold.index, "scenario": scenario, "training_rows": 0, "testing_rows": 0},
                        "numerics": {"verified": True, "schema": "ed-h1-model-verification-v1", "status": "ready",
                            "artifact_sha256": digest}}})
    accounts = {}
    for arm in arms:
        for scenario in ("S1", "S2"):
            decisions = tuple(comparison.PolicyDecision(scenario, f"KRX:{index:06d}", day,
                "no_parent", "known_no_broad_parent", None, None, ("NO_EVENT",))
                for day in cal.evaluation_reference_sessions for index in range(20))
            records.extend({"kind": "decision", "arm": arm, "scenario": scenario, "decision": decision}
                           for decision in decisions)
            empty_plan = comparison.PolicyPlan(arm, scenario, cal.evaluation_reference_sessions, decisions, ())
            no_parents = tuple(comparison.PolicyOpportunity(scenario, decision.symbol, decision.session,
                "known_no_parent", None, None, False, None, ("NO_EVENT",), "declined", "declined", None)
                for decision in decisions)
            summary = comparison.unit_summary(cal, no_parents, empty_plan)
            concentration = producer._concentration(cal, no_parents, empty_plan)
            receipt = verify.verify_policy_summary(cal, no_parents, empty_plan, summary, concentration=concentration)
            records.append({"kind": "unit_summary", "summary": summary, "concentration": concentration,
                            "independent_verification": receipt})
            for cost in (33, 43):
                for domain in verify.DOMAINS:
                    for fresh in (False, True):
                        key = arm, scenario, cost, domain, fresh
                        days, prior = [], None
                        for day in cal.evaluation_reference_sessions:
                            record, state = _day(key, day, None if fresh else prior,
                                gain=gain if arm == comparison.LEARNER else 1,
                                unknown=unknown and domain == verify.DOMAINS[0] and not fresh)
                            # Fresh oracle's day counter persists while its capital resets.
                            record["independent_verification"]["verified_days"] = len(days) + 1
                            records.append(record)
                            days.append(record["summary"])
                            prior = state
                        accounts[key] = days
                        records.append({"kind": "account_summary", **verify._account_expected(cal, key, days)})
    records.extend({"kind": kind, **payload} for kind, payload in verify.paired_account_records(cal, accounts))
    return records, context, hashes


def _publish(records, unit, context, hashes):
    body, decoded, counts = _seal(records)
    pin = hashlib.sha256(body).hexdigest()
    publication = {"unit_id": unit, "status": "completed", "verification_complete": True,
        "invented_only": True, "full_bridge": True, "local_pipe_test": False,
        "resource_profile": wire.ED_H1_600.id, "input_contract": wire.ED_SNAPSHOT_CONTRACT,
        "geometry": contract.FULL_GEOMETRY, "candidate_counts": contract.CANDIDATE_BOUNDS,
        "source_hashes": hashes, "local_identity": {"runtime_sha256": context["expected_runtime_sha256"]},
        "output_sha256": pin, "output_bytes": len(body), "independent_verification": {"verified": True,
            "full_eof_verified": True, "full_geometry": True, "scientific_report_complete": True,
            "policy_summaries_independently_verified": True, "all_account_days_verified": 157,
            "producer_state_used_as_oracle_input": False, "invented_only": True, "stored_decoded_sha256": decoded,
            "stored_compressed_sha256": pin, "input_manifest_sha256": "d" * 64, "record_counts": counts}}
    raw = wire.encode(publication)
    return body, raw, wire.digest(raw)


@pytest.fixture(scope="module")
def completed():
    rules, context, hashes = _bundle("ED-H1-RULES")
    learner, _, _ = _bundle("ED-H1-LIGHTGBM")
    return rules, learner, context, hashes


def _compare_fixture(completed, *, mutate=None):
    rules, learner, context, hashes = completed
    if mutate is not None:
        rules = verify._plain(snapshot.encode(rules))
        learner = verify._plain(snapshot.encode(learner))
        mutate(rules, learner)
    a, apub, apin = _publish(rules, "ED-H1-RULES", context, hashes)
    b, bpub, bpin = _publish(learner, "ED-H1-LIGHTGBM", context, hashes)
    kwargs = {**context, "rules_publication": apub, "rules_publication_sha256": apin,
        "learner_publication": bpub, "learner_publication_sha256": bpin}
    return a, b, kwargs


def test_completed_positive_gate_and_manual_conditions_cannot_open(completed, monkeypatch):
    a, b, kwargs = _compare_fixture(completed)
    def forbidden(*_args, **_kwargs):
        pytest.fail("producer summary/matching used as oracle")
    for owner, name in ((comparison, "unit_summary"), (comparison, "match_non_event"),
                        (producer, "_account_day_summary"), (producer, "_paired_accounts"), (producer, "_concentration")):
        monkeypatch.setattr(owner, name, forbidden)
    result = verify.compare_completed_reports(io.BytesIO(a), io.BytesIO(b), **kwargs)
    assert result["status"] == "triggered" and result["additional_model_fits"] == 0
    conditions = result["conditions"]
    assert len(conditions["conditions"]) == 4
    assert all(row["learner_terminal_net_return"] == Fraction(314, verify.CAPITAL)
               for row in conditions["conditions"])
    evidence = SimpleNamespace(**{key: kwargs[key] for key in ("rules_publication", "rules_publication_sha256",
        "learner_publication", "learner_publication_sha256")}, rules_output_sha256=wire.digest(a),
        learner_output_sha256=wire.digest(b), snapshot_sha256=kwargs["expected_snapshot_sha256"],
        source_sha256=kwargs["expected_source_sha256"], reconstructed_conditions=b'{"status":"triggered"}\n')
    with pytest.raises(verify.ResultRefusal, match="reconstructed completed"):
        verify.validate_conditional_gate(evidence, io.BytesIO(a), io.BytesIO(b),
            **{key: value for key, value in kwargs.items() if key.startswith("expected_")})


@pytest.mark.parametrize("unknown,gain,reason", [(True, 2, "history_unknown"),
                                               (False, 0, "not_strictly_positive")])
def test_unknown_and_nonpositive_gate_never_use_conditional_or_fresh_success(completed, unknown, gain, reason):
    rules, _, context, hashes = completed
    learner, _, _ = _bundle("ED-H1-LIGHTGBM", gain=gain, unknown=unknown)
    a, b, kwargs = _compare_fixture((rules, learner, context, hashes))
    result = verify.compare_completed_reports(io.BytesIO(a), io.BytesIO(b), **kwargs)
    assert result["status"] == "not_triggered" and reason in result["reason"]
    for condition in result["conditions"]["conditions"]:
        assert condition["domain"] == "full_original_population" and condition["scope"] == "continuous"
        assert condition["passed"] is (None if unknown else False)
        if unknown:
            assert condition["learner_terminal_net_return"] is None
            assert condition["terminal_increment_versus_without_filing_rule"] is None
    assert result["conditions"]["observed_support_conditional_can_open"] is False
    assert result["conditions"]["fresh_days_can_open"] is False


@pytest.mark.parametrize("mutation", ["mean", "calendar", "cost", "domain", "duplicate", "missing", "model_source",
                                     "snapshot_source", "unit_mean", "concentration"])
def test_sealed_but_mutated_completed_results_refused(completed, mutation):
    def mutate(rules, learner):
        if mutation == "mean":
            next(r for r in learner if r["kind"] == "account_summary")["identified_day_return_mean"] += Fraction(1, 1000)
        elif mutation == "calendar":
            learner[0]["calendar"] = replace(calendar(), evaluation_reference_sessions=calendar().evaluation_reference_sessions[:-1])
        elif mutation == "cost":
            next(r for r in learner if r["kind"] == "account_day")["key"] = (comparison.LEARNER, "S1", 34, verify.DOMAINS[0], False)
        elif mutation == "domain":
            record = next(r for r in learner if r["kind"] == "account_day")
            record["summary"]["domain"] = verify.DOMAINS[1]
        elif mutation == "duplicate":
            learner.append(next(r for r in learner if r["kind"] == "account_day"))
        elif mutation == "missing":
            learner.remove(next(r for r in learner if r["kind"] == "account_day"))
        elif mutation == "model_source":
            next(r for r in learner if r["kind"] == "model_fold")["fit"]["artifact"]["payload"]["binding"]["source_sha256"] = "f" * 64
        elif mutation == "snapshot_source":
            learner[0]["snapshot_reference"]["producer_binding"]["source_sha256"] = "f" * 64
        elif mutation == "unit_mean":
            next(r for r in learner if r["kind"] == "unit_summary")["summary"]["groups"]["all"]["outcomes"]["net33"]["mean"] = Fraction(1)
        else:
            next(r for r in learner if r["kind"] == "unit_summary")["concentration"]["selected_winning_subgroups"] = True
    a, b, kwargs = _compare_fixture(completed, mutate=mutate)
    with pytest.raises(verify.ResultRefusal):
        verify.compare_completed_reports(io.BytesIO(a), io.BytesIO(b), **kwargs)


def test_completed_artifact_and_publication_pins_must_be_external(completed):
    a, b, kwargs = _compare_fixture(completed)
    with pytest.raises(verify.ResultRefusal, match="externally pinned"):
        verify.compare_completed_reports(io.BytesIO(a), io.BytesIO(b),
            **{**kwargs, "learner_publication_sha256": "0" * 64})
    with pytest.raises((EOFError, OSError, verify.ResultRefusal)):
        verify.compare_completed_reports(io.BytesIO(a), io.BytesIO(b[:-8]), **kwargs)
