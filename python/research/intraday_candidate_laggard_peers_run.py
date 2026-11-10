"""Compare peer-relative weakness on the audited opening-laggard path."""

from datetime import datetime

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_laggard_path_run as previous
from research import intraday_candidate_laggard_peers as peers
from research import intraday_candidate_laggard_peers_report as report
from research import intraday_candidate_laggard_run as laggard
from research.intraday_candidate_laggard_stability_run import _parent_plans
from research.intraday_candidate_room_saved import _parent


PATH_UNIT = "EJ-ENTRY-LAGGARD-PATH"


def _session(reader, symbol, day, invented, audit):
    if invented:
        return previous._session(reader, symbol, day, True, audit)
    return laggard._project(reader, symbol, day, peers.PEER_OFFSETS, False, audit)


def _arm(baseline, decision, expected):
    state = decision["state"]
    if state in ("retain", expected):
        return baseline
    if state == "unresolved":
        return laggard._inactive_arm("unresolved", decision["reason"])
    wire.require(state in ("weaker", "other"), "EJ laggard peer choice differs")
    return laggard._inactive_arm("no_entry", "opening_peer_other_partition")


def _decisions(spec, scenarios, slot, invented, root, sessions, publication, audit_receipt, audit):
    classify = peers.reference_classify_day if audit else peers.classify_day
    release = spec["path_parent"]
    attempt = release["qualification" if invented else "actual"]["attempt"]
    path_slot = root / "output" / f"{PATH_UNIT}-{attempt}"
    original, _ = _parent(release, PATH_UNIT, root, invented, publication, audit_receipt, load_outputs=False)
    decisions, seals = {}, {}
    with sessions(spec, slot, invented, audit) as reader:
        cache = {}
        for scenario in scenarios:
            days, coordinates = [], {}
            source = _parent_plans(path_slot / f"{scenario}-pre-entry.jsonl", original[scenario]["path_seal"])
            for day, fold, plan in source:
                at, paths = plan["decision_at"], plan["symbols"]
                observed = {}
                if any(path["state"] == "weak" for path in paths.values()):
                    for symbol in paths:
                        key = (day, symbol)
                        if key not in cache:
                            cache[key] = _session(reader, symbol, day, invented, audit)
                        observed[symbol] = cache[key]
                choices = classify(paths, datetime.fromisoformat(at), observed)
                wire.require(set(choices) == set(paths), "EJ laggard peer roster differs")
                for symbol, choice in choices.items():
                    coordinates[day, symbol] = (fold, at, paths[symbol], choice)
                days.append((day, fold, {"decision_at": at, "symbols": choices}))
            wire.require(len(coordinates) == 3140, "EJ laggard peer original coordinate extent differs")
            decisions[scenario] = coordinates
            seals[scenario] = laggard._seal(slot, scenario, days, audit)
    return decisions, seals


def calculate(spec, parents, slot, invented, root, sessions, publication, audit_receipt, *, audit=False, stored=None):
    """Seal both scenarios' peer choices before opening the audited payoff rows."""
    scenarios = tuple(item["scenario"] for item in parents["inputs"])
    wire.require(len(scenarios) == 2 and set(scenarios) == {"S1", "S2"}, "EJ laggard peer scenarios differ")
    decisions, seals = _decisions(spec, scenarios, slot, invented, root, sessions, publication, audit_receipt, audit)
    original, prior = _parent(spec["path_parent"], PATH_UNIT, root, invented, publication, audit_receipt)
    reports, outputs = {}, {}
    for scenario in scenarios:
        rows = prior[scenario]
        coordinates = decisions[scenario]
        wire.require(len(rows) == len(coordinates), "EJ laggard peer parent extent differs")
        persisted = stored[scenario] if audit else [None] * len(rows)
        result = []
        for row, saved in zip(rows, persisted, strict=True):
            question, = row["questions"]
            selected = coordinates.pop((row["session"], row["symbol"]), None)
            wire.require(selected is not None, "EJ laggard peer parent coordinate differs")
            fold, at, path, choice = selected
            wire.require(row["fold"] == fold and question["decision_at"] == at
                         and wire.encode(question["path"]) == wire.encode(path),
                         "EJ laggard peer original path decision differs")
            baseline = question["weak"]
            changed = {**question, "baseline": baseline, "candidate": _arm(baseline, choice, "weaker"),
                       "other": _arm(baseline, choice, "other"), "peer": choice}
            rebuilt = {**row, "questions": [changed]}
            if audit:
                wire.require(wire.encode(rebuilt) == wire.encode(saved), "EJ laggard peer audited trade differs")
            result.append(rebuilt)

        contrasts = report.summarize(result, audit=audit)
        control = original[scenario]["hold60_vs_weak"]["comparison"]["groups"]["all"]["totals"]["candidate"]
        for name in ("weak_vs_weaker", "weak_vs_other"):
            comparison = contrasts[name]["comparison"]
            wire.require(tuple(comparison[key] for key in ("coordinates", "sessions", "weeks")) == (3140, 157, 34)
                         and comparison["groups"]["all"]["totals"]["baseline"] == control,
                         "EJ laggard peer control or evaluation geometry differs")
        reports[scenario] = {"peer_seal": seals[scenario], **contrasts}
        outputs[scenario] = result
    return reports, outputs
