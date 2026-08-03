"""Tests for `python/research/lineage.py` -- the curated `strategy_id` ->
research-family map and its resolution order (Strategy Research Task P,
`.planning/sr-p-trial-accounting.md`).

The map itself is deliberately hand-curated, not derived from
`runs/experiments.jsonl` (every 19-fold `run_walk_forward` record logs
`params` as `{}`/`{"symbol": ...}`/`{"candidates": ...}` -- the real
configuration lives in the `Trainable`'s constructor arguments and is
never logged), so these tests check the *resolution contract* and the
map's own internal well-formedness rather than trying to re-derive the
attributions.
"""

import json

import pytest

from research import lineage
from research.lineage import (
    CURATED_MAP_SOURCE,
    FAMILY_BY_STRATEGY_ID,
    INFRASTRUCTURE_PURPOSE,
    LOGGED_SOURCE,
    RESEARCH_PURPOSE,
    STRATEGY_FAMILY_KEY,
    UNMAPPED_SOURCE,
    resolve_family,
)


def test_resolve_family_prefers_a_logged_strategy_family_over_the_curated_map():
    # "key present" means "trust the record" -- a record that carries its
    # own lineage is self-describing and must win over the historical
    # curated attribution, which only exists to cover pre-lineage records.
    record = {"strategy_id": "ensemble-momentum", STRATEGY_FAMILY_KEY: "some-new-family"}

    resolution = resolve_family("ensemble-momentum", record)

    assert resolution.family == "some-new-family"
    assert resolution.source == LOGGED_SOURCE


def test_resolve_family_falls_back_to_the_curated_map_when_the_record_omits_the_key():
    # "key absent" unambiguously means "pre-lineage record, attribute via
    # the curated map" -- which is exactly why log_run omits the key
    # entirely rather than writing null.
    record = {"strategy_id": "ensemble-momentum"}

    resolution = resolve_family("ensemble-momentum", record)

    assert resolution.family == FAMILY_BY_STRATEGY_ID["ensemble-momentum"].family
    assert resolution.source == CURATED_MAP_SOURCE
    assert resolution.citation == FAMILY_BY_STRATEGY_ID["ensemble-momentum"].citation


def test_resolve_family_uses_the_curated_map_when_no_record_is_supplied_at_all():
    resolution = resolve_family("obv-trend")

    assert resolution.family == "volume"
    assert resolution.source == CURATED_MAP_SOURCE


def test_resolve_family_never_invents_a_family_for_an_unmapped_strategy_id():
    resolution = resolve_family("some-strategy-nobody-mapped")

    assert resolution.family == "some-strategy-nobody-mapped"
    assert resolution.source == UNMAPPED_SOURCE
    assert resolution.note is not None
    assert "some-strategy-nobody-mapped" in resolution.note
    assert resolution.citation is None


def test_resolve_family_ignores_a_null_strategy_family_value_and_uses_the_curated_map():
    # Defensive: nothing in this project writes `strategy_family: null`
    # (log_run omits the key entirely instead), but a hand-edited or
    # third-party record must degrade to the curated map rather than
    # resolving to a literal `None` family.
    record = {"strategy_id": "obv-trend", STRATEGY_FAMILY_KEY: None}

    resolution = resolve_family("obv-trend", record)

    assert resolution.family == "volume"
    assert resolution.source == CURATED_MAP_SOURCE


def test_resolve_family_derives_purpose_for_a_logged_family_that_the_curated_map_knows():
    record = {"strategy_id": "brand-new-e2e-check", STRATEGY_FAMILY_KEY: "infrastructure"}

    resolution = resolve_family("brand-new-e2e-check", record)

    assert resolution.family == "infrastructure"
    assert resolution.purpose == INFRASTRUCTURE_PURPOSE
    assert resolution.source == LOGGED_SOURCE


def test_resolve_family_defaults_a_logged_but_unknown_family_to_research_with_a_note():
    record = {"strategy_id": "brand-new-strategy", STRATEGY_FAMILY_KEY: "carry"}

    resolution = resolve_family("brand-new-strategy", record)

    assert resolution.family == "carry"
    assert resolution.purpose == RESEARCH_PURPOSE
    assert resolution.note is not None


def test_every_curated_entry_is_well_formed():
    assert FAMILY_BY_STRATEGY_ID, "the curated map must not be empty"
    for strategy_id, entry in FAMILY_BY_STRATEGY_ID.items():
        assert entry.family, f"{strategy_id}: empty family"
        assert entry.purpose in (RESEARCH_PURPOSE, INFRASTRUCTURE_PURPOSE), f"{strategy_id}: bad purpose"
        assert entry.citation.startswith(".planning/"), f"{strategy_id}: citation must point at a planning doc"


def test_every_curated_family_has_exactly_one_purpose():
    # A family that mixed "research" and "infrastructure" members would
    # make the project-level split by purpose meaningless.
    purposes_by_family: dict[str, set[str]] = {}
    for entry in FAMILY_BY_STRATEGY_ID.values():
        purposes_by_family.setdefault(entry.family, set()).add(entry.purpose)
    for family, purposes in purposes_by_family.items():
        assert len(purposes) == 1, f"family {family!r} has mixed purposes: {sorted(purposes)}"


def test_curated_map_covers_the_families_named_in_the_planning_doc():
    families = {entry.family for entry in FAMILY_BY_STRATEGY_ID.values()}
    assert families == {
        "trend-momentum",
        "mean-reversion",
        "volume",
        "funding",
        "daily-tsmom",
        "macro-conditioned",
        "infrastructure",
    }


def test_family_resolution_to_dict_is_json_serializable():
    resolution = resolve_family("ensemble-momentum")

    json.dumps(resolution.to_dict())
    assert resolution.to_dict()["strategy_id"] == "ensemble-momentum"


@pytest.mark.parametrize("strategy_id", sorted(FAMILY_BY_STRATEGY_ID))
def test_resolve_family_round_trips_every_curated_strategy_id(strategy_id):
    resolution = resolve_family(strategy_id)

    assert resolution.family == FAMILY_BY_STRATEGY_ID[strategy_id].family
    assert resolution.purpose == FAMILY_BY_STRATEGY_ID[strategy_id].purpose
    assert resolution.source == CURATED_MAP_SOURCE


def test_purpose_by_family_is_derived_from_the_curated_map_not_hardcoded_twice():
    derived = {entry.family: entry.purpose for entry in FAMILY_BY_STRATEGY_ID.values()}

    assert lineage.PURPOSE_BY_FAMILY == derived
