"""Seal the pre-entry room gate and project already audited EJ outcomes."""

from collections import Counter
from fractions import Fraction
from hashlib import sha256
from itertools import zip_longest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_exit as cash_summary
from research import intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_room as room
from research import intraday_context_cost_census_inputs as saved


def _choice_row(fold, coordinate, selector):
    roster = coordinate["roster"]
    choices = selector.selections(coordinate)
    selector.verify_selections(coordinate, choices)
    return {"fold": fold, "coordinate": [roster[k] for k in ("session", "symbol", "coordinate_status", "original_frames")],
            "roster_sha256": wire.digest(wire.encode(roster)), "choices": choices}


def _roster_bytes(selection):
    fields = ("status", "stage", "decision_at", "episode_origin_context")
    questions = [[key, *(choice[field] for field in fields)] for key, choice in selection["choices"].items()]
    return wire.encode([*selection["coordinate"], questions])


def _decision_rows(root, parent, selector=room):
    """Read only pre-entry files, with the same byte and coordinate bounds as EI."""
    _, _, cache = saved._directories(root, parent)
    prior = ("", "")
    last_day = ""
    for fold, seal in enumerate(parent["decisions"], 1):
        path = cache / f"ei-entry-decisions-fold-{fold}.jsonl"
        day_counts = Counter()
        roster_hash = sha256()
        with saved._source(path, 128 * wire.MIB) as (stream, size):
            rows = saved._Rows(stream)
            first = rows.next()
            header = first["header"]
            wire.require(size == seal["size"] and first["schema"] == "ei-context-entry-decisions-draft-v1"
                         and header["scenario"] == parent["scenario"] and header["cost_bps"] == 33
                         and header["execution_policy_sha256"] == saved.POLICY_SHA
                         and header["coordinates"] == seal["coordinates"] and header["fold"]["index"] == fold,
                         "EJ pre-entry decision header differs")
            for _ in range(seal["coordinates"]):
                result = _choice_row(fold, rows.next()["coordinate"], selector)
                key = tuple(result["coordinate"][:2])
                wire.require(key > prior and key[0] > last_day and saved.WINDOW_START <= key[0] <= saved.WINDOW_END,
                             "EJ decision roster order or dates differ")
                prior = key
                day_counts[key[0]] += 1
                roster_hash.update(_roster_bytes(result))
                yield result
            rows.finish(seal)
        wire.require(all(n == 20 for n in day_counts.values())
                     and roster_hash.hexdigest() == header["question_roster_sha256"],
                     "EJ original decision roster proof differs")
        last_day = prior[0]


def _seal_choices(slot, scenario, selections, audit):
    path = slot / f"{scenario}-pre-entry.jsonl"
    raw = b"".join(wire.encode(row) for row in selections)
    digest = sha256(raw).hexdigest()
    wire.require(len(selections) == 3140 and 0 < len(raw) <= 128 * wire.MIB, "EJ choice extent differs")
    if not audit:
        wire.exclusive_bytes(path, raw)
    wire.require(wire.file_digest(path, 128 * wire.MIB, lambda _: None) == (digest, len(raw)),
                 "EJ sealed pre-entry decisions differ")
    return {"sha256": digest, "size": len(raw), "coordinates": len(selections)}


def _parent(spec, root, invented, publication, audit_receipt):
    release = spec["exit_parent"]
    pins = release["qualification" if invented else "actual"]
    slot = root / "output" / f"EJ-EXIT-66-{pins['attempt']}"
    done = saved._metadata(slot / "completion.json", pins["completion_sha256"])
    audit = saved._metadata(slot / "audit.json", pins["audit_sha256"])
    identity = done["identity"]
    wire.require(identity["attempt"] == ("qualification1" if invented else "actual1") == pins["attempt"]
                 and identity["invented"] is invented
                 and all(identity[key] == release[key] for key in ("merge", "reviewed", "tree"))
                 and audit == audit_receipt(done), "EJ exit parent audit or release differs")
    report, reread, outcomes = publication(identity)
    wire.require(reread == done, "EJ exit parent completion changed")
    return report["scenarios"], outcomes


def _gate_impact(parent, output, choice_field):
    """Use common known cash, including a known zero for a declined trade."""
    impact = {}
    for arm in ("baseline", "candidate"):
        count, before, after, removed = 0, Fraction(), Fraction(), Fraction()
        for source_row, new_row in zip(parent, output, strict=True):
            for source, new in zip(source_row["questions"], new_row["questions"], strict=True):
                if source["status"] != "opportunity":
                    continue
                a, b = source[arm]["net"], new[arm]["net"]
                if a is None or b is None:
                    continue
                count += 1
                before += Fraction(a)
                after += Fraction(b)
                if new[choice_field]["state"] == "decline":
                    removed -= Fraction(a)
        wire.require(after - before == removed, "EJ gate changed a retained payoff")
        impact[arm] = {"known_pairs": count, "ungated_net_cash": str(before), "gated_net_cash": str(after),
                       "net_cash_delta": str(removed)}
    return impact


def _selection_counts(selections):
    states = ("select", "decline", "unresolved", "retain")
    totals = dict.fromkeys(states, 0)
    by_session, by_fold = {}, {}
    for row in selections:
        day = row["coordinate"][0]
        session = by_session.setdefault(day, dict.fromkeys(states, 0))
        fold = by_fold.setdefault(str(row["fold"]), dict.fromkeys(states, 0))
        for choice in row["choices"].values():
            if choice["status"] == "opportunity":
                state = choice["state"]
                totals[state] += 1
                session[state] += 1
                fold[state] += 1
    return {"totals": totals, "by_session": by_session, "by_fold": by_fold}


def calculate_saved_gate(spec, parents, slot, invented, root, publication, audit_receipt, *, selector=room,
                         choice_field="room_selection", control_choices=None, audit=False, stored=None):
    choices, seals = {}, {}
    for parent in parents["inputs"]:
        scenario = parent["scenario"]
        if invented:
            choices[scenario] = [_choice_row(row["fold"], selector.invented_decision(row, roster)[0], selector)
                                 for row, roster in saved.invented_rows()]
        else:
            choices[scenario] = list(_decision_rows(root, parent, selector))
        seals[scenario] = _seal_choices(slot, scenario, choices[scenario], audit)

    # Both scenario selections are durable before opening any prior payoff ledger.
    earlier, prior = _parent(spec, root, invented, publication, audit_receipt)
    reports, outputs = {}, {}
    for scenario, selections in choices.items():
        originals, projected, controls = [], [], []
        persisted = stored[scenario] if audit else [None] * len(selections)
        for outcome, selection, result in zip(prior[scenario], selections, persisted, strict=True):
            if invented:
                _, outcome = selector.invented_decision(outcome, outcome["roster"])
            wire.require(outcome["fold"] == selection["fold"]
                         and wire.digest(wire.encode(outcome["roster"])) == selection["roster_sha256"],
                         "EJ outcome coordinate and sealed selection roster differ")
            chosen = selection["choices"]
            if not audit:
                result = room.project(outcome, chosen, choice_field=choice_field)
            room.verify_projection(outcome, chosen, result, choice_field=choice_field)
            if control_choices is not None:
                control = control_choices(chosen)
                projected_control = room.project(outcome, control, choice_field="activity_selection")
                room.verify_projection(outcome, control, projected_control, choice_field="activity_selection")
                controls.append(projected_control)
            originals.append(outcome)
            projected.append(result)
        comparison = cash_summary.summarize(projected)
        cash_reference.verify_summary(projected, comparison)
        wire.require(tuple(comparison[k] for k in ("coordinates", "sessions", "weeks")) == (3140, 157, 34),
                     "EJ room comparison population differs")
        reports[scenario] = {"selection_seal": seals[scenario], "opportunity_selection": _selection_counts(selections),
                             "ungated": earlier[scenario]["comparison"], "comparison": comparison,
                             "gate_impact": _gate_impact(originals, projected, choice_field)}
        if control_choices is not None:
            control_report = cash_summary.summarize(controls)
            cash_reference.verify_summary(controls, control_report)
            reports[scenario]["activity_control"] = control_report
            reports[scenario]["direction_impact"] = _gate_impact(controls, projected, choice_field)
        outputs[scenario] = projected
    return reports, outputs
