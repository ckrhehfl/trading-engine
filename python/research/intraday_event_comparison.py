"""One chronological EL comparison on caller-admitted inputs.

The caller owns receipts, data authentication and resource limits. Policies
are sealed before the market replay or the evaluation target reader runs.
"""

from collections import Counter
from hashlib import sha256
from itertools import islice

from research import intraday_event_evaluation as evaluation
from research import intraday_event_model_workflow as workflow
from research import intraday_event_models as models
from research import intraday_event_sequence_model as sequence


def _held_predictor(scope, causal_queries, artifact, predict):
    queries = (query for query in causal_queries if workflow.supported_in_scope(query, scope))
    saved = {}
    while batch := tuple(islice(queries, workflow.BATCH)):
        by_key = {query.key: sha256(evaluation._json(query)).digest() for query in batch}
        rows = predict(artifact, batch)
        if len(rows) != len(batch) or {row["key"] for row in rows} != by_key.keys() or saved.keys() & by_key.keys():
            raise ValueError("Holding prediction roster differs from its causal queries")
        saved.update((row["key"], (by_key[row["key"]], row)) for row in rows)

    def lookup(query):
        if not workflow.supported_in_scope(query, scope):
            return {"key": query.key, "session": query.session, "query_status": "unsupported_peer_context",
                    "predictions": dict.fromkeys(models.HEADS),
                    "head_status": dict.fromkeys(models.HEADS, "unsupported_peer_context")}
        entry = saved.get(query.key)
        if entry is None or entry[0] != sha256(evaluation._json(query)).digest():
            raise ValueError("Holding replay features differ from the scored causal query")
        return entry[1]

    return lookup


def _replay_days(policies, markets, artifact, predict, verify_cash_day):
    ledgers = {policy.control: [] for policy in policies}
    outcomes = {policy.control: Counter() for policy in policies}
    totals = {policy.control: Counter() for policy in policies}
    days_by_policy = [evaluation.VerifiedReplayPolicy(policy).days() for policy in policies]
    for days in zip(*days_by_policy, strict=True):
        if any(day.session != days[0].session or (day.start, day.stop) != (days[0].start, days[0].stop) for day in days):
            raise ValueError("Comparison policies disagree on their sealed session coverage")
        for day in days:
            policy = day.policy
            held_predict = None if artifact is None else _held_predictor(
                policy.scope, evaluation.held_queries_day(day, markets), artifact, predict)
            ledger = evaluation.replay_policy_day(
                day, markets, held_predict=held_predict, prediction_binding=policy.prediction_binding)
            if verify_cash_day is not None:
                check = verify_cash_day(
                    day, markets, ledger, held_predict=held_predict, prediction_binding=policy.prediction_binding)
                totals[policy.control].update(episodes=check["episodes"], ledgers=check["ledgers"])
                outcomes[policy.control].update(check["outcome_counts"])
            ledgers[policy.control].extend(ledger)
    ordered = tuple(row for policy in policies for row in ledgers[policy.control])
    checks = [] if verify_cash_day is None else [
        {"episodes": totals[policy.control]["episodes"], "ledgers": totals[policy.control]["ledgers"],
         "outcome_counts": dict(sorted(outcomes[policy.control].items()))} for policy in policies]
    return ordered, checks


def compare(unit, data, episodes, candidates, markets, evaluation_labels, *, verify_cash=None, verify_cash_day=None):
    """Compare controls and a previously fitted model on one outer fold.

    `markets` can load a session on demand. `evaluation_labels` is a callable
    returning exactly one target record for each supported evaluation query.
    The common scopes share the same peer-availability rule at each decision,
    including holding checkpoints. Every original episode remains counted.
    The day verifier receives a verified slice of the original sealed policy.
    The full-policy callback remains available to callers with that interface.
    """
    if verify_cash is not None and verify_cash_day is not None:
        raise ValueError("Use either the full-policy or day comparison verifier")
    _, start, end = workflow.FOLDS[data.fold]
    dates = data.calendar[start:end]
    days = set(dates)
    kind, scope = unit["kind"], data.scope
    if (kind not in workflow.KINDS or unit["scope"] != scope or unit["fold"] != data.fold
            or unit["evaluation_sessions"] != list(dates) or unit["support"] != data.support):
        raise ValueError("Model unit differs from its comparison cohort")
    episodes, candidates = tuple(episodes), tuple(candidates)
    if any(episode.session not in days for episode in episodes):
        raise ValueError("Comparison episode escaped the outer fold")

    evaluation_by_key = {row.key: row for row in data.evaluation}
    entry_keys = {row.key for row in data.evaluation if row.state not in models.HELD_STATES}
    selected = tuple(c for c in candidates if scope == "full_chart"
                     or workflow.supported_in_scope(c.query, scope))
    source_episodes = {c.episode.event_id for c in candidates}
    scope_episodes = {c.episode.event_id for c in selected if c.query is not None}
    chart_episodes = {c.episode.event_id for c in candidates if c.query is not None}
    decision_support = {"without_causal_frame": len(episodes) - len(source_episodes),
                        "without_supported_chart": len(source_episodes - chart_episodes),
                        "without_required_context": len(chart_episodes - scope_episodes)}
    selected_keys = {c.key for c in selected if c.query is not None}
    if (selected_keys != entry_keys or any(c.query != evaluation_by_key[c.key]
            for c in selected if c.query is not None)):
        raise ValueError("Policy inputs differ from the fitted unit's causal entry roster")
    predictions = unit["predictions"]
    artifact = unit["artifact"]
    if artifact is None:
        if unit["status"] != "unsupported_empty_training" or data.training or predictions:
            raise ValueError("Invalid unsupported model unit")
        binding = sha256(workflow.encode(unit)).hexdigest()
        head_statuses = dict.fromkeys(models.HEADS, "unsupported_empty_training")
    else:
        binding = unit["artifact_sha256"]
        if (unit["status"] != "completed" or binding != sha256(workflow.encode(artifact)).hexdigest()
                or unit["predictions_sha256"] != sha256(workflow.encode(predictions)).hexdigest()
                or len(predictions) != len(evaluation_by_key)
                or {row["key"] for row in predictions} != evaluation_by_key.keys()):
            raise ValueError("Prediction or model artifact identity differs")
        head_statuses = {name: head["status"] for name, head in artifact["heads"].items()}
    support = evaluation.policy_support(head_statuses)
    unavailable = {(kind, scope, policy): set(dates) for policy, available in support.items() if not available}
    fit_counts = {"fit_artifacts": unit["fit_artifacts"], **{name: artifact[name] for name in
        ("fit_head_count", "fit_attempt_head_count", "native_fit_head_count", "native_joint_fit_count", "native_updates",
         "constant_head_count", "unsupported_head_count", "failed_head_count") if artifact is not None and name in artifact}}
    training_diagnostics = None if artifact is None else artifact.get("training_diagnostics")
    if training_diagnostics is not None:
        training_diagnostics = {name: training_diagnostics[name] for name in
                                ("status", "sampled_rows", "heads", "completed_updates", "loss_blocks")}
    predict = sequence.predict if kind == "tcn" else models.predict

    policies = tuple(evaluation.freeze_policy(episodes, selected,
        (row for row in predictions if row["key"] in entry_keys) if control == "model" else (),
        control=control, model=kind, scope=scope, prediction_binding=binding)
        for control in evaluation.CONTROLS)

    if verify_cash is None:
        ledgers, checks = _replay_days(policies, markets, artifact, predict, verify_cash_day)
    else:
        ledgers, checks = [], []
        for policy in policies:
            held_predict = None if artifact is None else _held_predictor(
                policy.scope, evaluation.held_queries(policy, markets), artifact, predict)
            ledger = evaluation.replay_policy(policy, markets, held_predict=held_predict, prediction_binding=binding)
            checks.append(verify_cash(policy, markets, ledger, held_predict=held_predict, prediction_binding=binding))
            ledgers.extend(ledger)

    # Reporting labels cannot select a model, change its action, or drop a cash row.
    labels = tuple(evaluation_labels())
    if len(labels) != len(evaluation_by_key) or {row.key for row in labels} != evaluation_by_key.keys():
        raise ValueError("Evaluation target roster is incomplete or duplicated")
    onset_keys = tuple(c.key for c in selected if c.query is not None
                       and c.frame.decision_offset == c.episode.onset_decision_offset)
    forecast = evaluation.forecast_report(data.evaluation, predictions, labels, dates,
        model=kind, scope=scope, as_of_session=dates[-1], onset_keys=onset_keys)
    return {"schema": "EL-fold-comparison-v1", "kind": kind, "scope": scope, "fold": data.fold,
            "model_binding": binding, "model_status": unit["status"], "head_statuses": head_statuses,
            "evaluation_sessions": list(dates),
            "original_episodes": len(episodes), "causal_frames": len(candidates), "scope_frames": len(selected),
            "decision_support": decision_support,
            "fit_counts": fit_counts, "training_diagnostics": training_diagnostics,
            "policies": [{"control": p.control, "sha256": p.sha256, "roster_sha256": p.roster_sha256,
                          "trace": p.trace} for p in policies],
            "forecast": forecast, "cash": evaluation.cash_report(evaluation.supported_cash_rows(ledgers, support), dates,
                                                                  unavailable=unavailable),
            "ledgers": tuple(ledgers), "independent_cash_checks": checks}
