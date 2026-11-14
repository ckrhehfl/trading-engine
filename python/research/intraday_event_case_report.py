"""Assemble private, outcome-blind examples from independently audited shards.

The caller authenticates each dataset audit with the dataset runner. This
readout binds those receipts to the complete original geometry and verifies
the saved card streams. It has no raw market or publication interface.
"""

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research.intraday_event_dataset import CAUSAL_FILES, CONTRACT, FILES, _read_stream
from research.intraday_event_dataset_run import ATTEMPTS, SHARDS, UNIT, attempt_name
from research.intraday_event_scenarios import FAMILIES


PER_FAMILY = CONTRACT["case_examples_per_family"]
ROSTER = "case-roster.json"
BEFORE = "before-cards.jsonl"
AFTER = "after-cards.jsonl"
LABELS = "labels.jsonl"
ONSET_FIELDS = ("family", "event_id", "series_id", "onset_offset", "initial_available_offset")
SCALARS = ("coordinates", "series_clusters", "regular_minutes", "continuous_minutes", "original_episodes",
           "queries_supported", "queries_unsupported", "entry_queries", "held_queries", "peer_supported", "peer_unknown")
COUNT_MAPS = ("scenario_coordinates", "events_by_family", "broad_minutes_by_family", "frames_by_state")


def _digest(value):
    return sha256(wire.encode(value)).hexdigest()


def _count(value):
    wire.require(type(value) is int and value >= 0, "EL invalid case coverage count")
    return value


def _pin(pin, filename):
    wire.require(pin["filename"] == filename and wire.valid_sha(pin["sha256"])
                 and _count(pin["bytes"]) > 0 and _count(pin["rows"]) > 0,
                 "EL case stream pin differs")
    return pin


def _check_manifest(manifest, bounds):
    pins = manifest["files"]
    wire.require(manifest["schema"] == "EL-event-dataset-v1"
                 and manifest["session_indices"] == list(bounds)
                 and manifest["scientific_contract"] == CONTRACT and set(pins) == set(FILES)
                 and manifest["stage_order"] == ["causal", "case_roster", "before_cards", "outcomes", "after_cards"]
                 and wire.valid_sha(manifest["universe_sha256"]) and wire.valid_sha(manifest["calendar_sha256"]),
                 "EL case dataset manifest differs")
    for name, pin in pins.items():
        _pin(pin, name)
    wire.require(manifest["causal_sha256"] == _digest({name: pins[name] for name in CAUSAL_FILES})
                 and manifest["before_sha256"] == _digest({name: pins[name] for name in (*CAUSAL_FILES, ROSTER, BEFORE)}),
                 "EL case stage seals differ")
    manifest_pin = _pin(manifest["manifest_file"], "dataset-manifest.json")
    body = {key: value for key, value in manifest.items() if key != "manifest_file"}
    raw = wire.encode(body)
    wire.require((len(raw), sha256(raw).hexdigest(), 1) ==
                 (manifest_pin["bytes"], manifest_pin["sha256"], manifest_pin["rows"]),
                 "EL case manifest pin differs")


def _rank(onset, universe):
    identity = tuple(onset[name] for name in ONSET_FIELDS)
    family, event, series, offset, clock = identity
    wire.require(family in FAMILIES and type(offset) is int and type(clock) is int and 0 <= offset < clock
                 and series == f"EL:{universe}:{onset['session']}:{onset['symbol']}"
                 and event == f"{series}:{family}:{offset}", "EL original onset identity differs")
    rank = sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    wire.require(rank == onset["selection_hash"], "EL case selection hash differs")
    return rank, identity


def _roster(slot, manifest):
    counts = manifest["counts"]["events_by_family"]
    wire.require(set(counts) == set(FAMILIES), "EL case family population differs")
    total = sum(_count(count) for count in counts.values())
    rows = list(_read_stream(slot, manifest["files"][ROSTER]))
    wire.require(len(rows) == 1, "EL case roster document differs")
    roster = rows[0]
    examples = roster["examples"]
    wire.require(roster["schema"] == "EL-onset-case-roster-v1" and roster["per_family"] == PER_FAMILY
                 and roster["candidate_episodes"] == total == manifest["counts"]["original_episodes"]
                 and roster["causal_sha256"] == manifest["causal_sha256"]
                 and roster["selection"] == CONTRACT["case_selection"] and roster["inference_sample"] is False
                 and manifest["counts"]["cases"] == len(examples)
                 and Counter(row["family"] for row in examples) == {family: min(PER_FAMILY, count)
                                                                    for family, count in counts.items() if count},
                 "EL shard case roster differs from the registered selection")
    ranks = [(row["family"], _rank(row, manifest["universe_sha256"])) for row in examples]
    by_event = {row["event_id"]: row for row in examples}
    wire.require(len(by_event) == len(examples) and ranks == sorted(set(ranks)),
                 "EL shard case identities or ranks are duplicated or unordered")
    return by_event


def _cards(slot, manifest, roster, filename):
    pins = manifest["files"]
    before = filename == BEFORE
    header = {"kind": "header", "case_roster": pins[ROSTER]}
    if before:
        header["causal_sha256"] = manifest["causal_sha256"]
    else:
        header["labels"] = pins[LABELS]
    rows = list(_read_stream(slot, pins[filename]))
    wire.require(len(rows) == len(roster) + 2 and rows[0] == header
                 and rows[-1] == {"kind": "footer", "cases": len(roster)}, "EL case card stream differs")
    cards = {}
    for row in rows[1:-1]:
        onset = row["selected_onset"]
        event = onset["event_id"]
        wire.require(row["kind"] == ("before_card" if before else "after_card")
                     and event not in cards and roster.get(event) == onset, "EL case card onset differs")
        if before:
            clock = onset["initial_available_offset"]
            card = row["card"]
            frame = card["frame"]
            wire.require(card["schema"] == "EL-before-decision-card-v1" and card["as_of_offset"] == clock
                         and frame["family"] == onset["family"] and frame["overlap_group_id"] == onset["series_id"]
                         and frame["decision_offset"] == clock and row["decision_query_key"] == f"{frame['event_id']}@{clock}"
                         and all(0 <= value["offset"] < clock for value in card["observations"]),
                         "EL before card crosses its decision clock or identity")
        else:
            target = row["targets"]
            wire.require(row["selection_changed_after_outcome"] is False and target["kind"] == "labels"
                         and target["query_kind"] == "entry" and target["key"] == row["decision_query_key"]
                         and (target["session"], target["symbol"]) == (onset["session"], onset["symbol"])
                         and all(onset["initial_available_offset"] < value["offset"] <= onset["initial_available_offset"] + 31
                                 for value in row["future_observations"]), "EL after card crosses its target clock or identity")
        cards[event] = row
    return cards


def _timelines(slot, manifest, selected):
    timelines = defaultdict(list)
    for row in _read_stream(slot, manifest["files"]["event-states.jsonl"]):
        if row["kind"] != "event_state":
            continue
        frame = row["frame"]
        onset = selected.get(frame["event_id"])
        if onset is not None:
            wire.require((row["session"], row["symbol"], frame["family"], frame["overlap_group_id"], frame["onset_offset"])
                         == (onset["session"], onset["symbol"], onset["family"], onset["series_id"], onset["onset_offset"]),
                         "EL selected timeline identity differs")
            timelines[frame["event_id"]].append(frame)
    for event, onset in selected.items():
        frames = timelines[event]
        wire.require(bool(frames) and frames[0]["kind"] == "at_onset"
                     and frames[0]["decision_offset"] == onset["initial_available_offset"],
                     "EL selected timeline has no original onset")
    return timelines


def assemble_cases(shards):
    """Read nine ordered (slot, audited publication, audit SHA256) triples.

    Global membership is sealed before after-card or later-state streams are
    opened. Whole-population counts come from the audited dataset manifests.
    """
    shards = tuple(shards)
    wire.require(len(shards) == len(SHARDS), "EL all nine audited case shards are required")
    coverage, count_maps = Counter(), {name: Counter() for name in COUNT_MAPS}
    provenance, release, universe_calendar = [], None, None
    manifests, candidates, seen, pins_seen = [], [], set(), set()
    for index, ((slot, report, audit_sha), bounds) in enumerate(zip(shards, SHARDS, strict=True)):
        identity, manifest = report["identity"], report["dataset"]
        slot = Path(slot)
        current_release = {key: value for key, value in identity.items() if key not in ("shard", "attempt", "session_indices")}
        if release is None:
            release = current_release
            universe_calendar = manifest["universe_sha256"], manifest["calendar_sha256"]
        wire.require(current_release == release and identity["shard"] == index
                     and identity["session_indices"] == list(bounds) and type(identity["invented"]) is bool
                     and wire.valid_sha(audit_sha) and audit_sha not in pins_seen
                     and (manifest["universe_sha256"], manifest["calendar_sha256"]) == universe_calendar,
                     "EL case shards do not share one audited release and original population")
        if not identity["invented"]:
            wire.require(identity["attempt"] in {attempt_name(base, index) for base in ATTEMPTS}
                         and slot.name == f"{UNIT}-{identity['attempt']}"
                         and wire.valid_sha(identity["qualification_sha256"]), "EL actual case publication identity differs")
        pins_seen.add(audit_sha)
        _check_manifest(manifest, bounds)
        roster = _roster(slot, manifest)
        wire.require(not seen.intersection(roster), "EL case onset appears in multiple shards")
        seen.update(roster)
        candidates.extend((row, index) for row in roster.values())
        for name in SCALARS:
            coverage[name] += _count(manifest["counts"][name])
        for name in COUNT_MAPS:
            count_maps[name].update({key: _count(value) for key, value in manifest["counts"][name].items()})
        provenance.append({"shard": index, "session_indices": list(bounds), "audit_sha256": audit_sha,
                           "manifest_sha256": manifest["manifest_file"]["sha256"]})
        manifests.append((slot, manifest, roster))
    wire.require(coverage["coordinates"] == coverage["series_clusters"] == 5158
                 and count_maps["scenario_coordinates"] == {"S1": 5140, "S2": 5140}, "EL original case geometry differs")

    selected = [pair for family in FAMILIES for pair in sorted(
        (pair for pair in candidates if pair[0]["family"] == family),
        key=lambda pair: _rank(pair[0], universe_calendar[0]))[:PER_FAMILY]]
    selection = {"algorithm": CONTRACT["case_selection"], "per_family": PER_FAMILY,
                 "inference_sample": False, "onsets": [row for row, _ in selected]}
    selection_sha = _digest(selection)

    wanted = defaultdict(dict)
    for onset, index in selected:
        wanted[index][onset["event_id"]] = onset
    before_cards, after_cards, timelines = {}, {}, {}
    for index in wanted:
        before_cards.update(_cards(*manifests[index], BEFORE))
    for index, onsets in wanted.items():
        slot, manifest, roster = manifests[index]
        after_cards.update(_cards(slot, manifest, roster, AFTER))
        timelines.update(_timelines(slot, manifest, onsets))

    cases, family_counts = [], Counter()
    for onset, index in selected:
        event = onset["event_id"]
        before, after = before_cards[event], after_cards[event]
        wire.require(before["decision_query_key"] == after["decision_query_key"], "EL before and after case keys differ")
        family_counts[onset["family"]] += 1
        cases.append({"shard": index, "onset": onset, "before": before, "after": after, "timeline": timelines[event]})
    return {"schema": "EL-private-case-report-v1", "release": release, "shards": provenance,
            "universe_sha256": universe_calendar[0], "calendar_sha256": universe_calendar[1],
            "selection": selection, "selection_sha256": selection_sha, "cases": cases,
            "timeline_clock": "original state receipts; simultaneous states can coalesce into a single decision",
            "coverage": {"sessions": SHARDS[-1][1], **dict(coverage), **{name: dict(values) for name, values in count_maps.items()}},
            "families": {family: {"original_episodes": count_maps["events_by_family"][family],
                                  "selected_cases": family_counts[family],
                                  "unfilled_example_slots": PER_FAMILY - family_counts[family],
                                  "shards_without_onsets": sum(not manifest["counts"]["events_by_family"][family]
                                                               for _, manifest, _ in manifests)} for family in FAMILIES}}
