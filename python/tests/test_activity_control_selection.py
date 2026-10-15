"""Adversarial synthetic rank-prefix proofs; no actual source or market IO."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta
import hashlib
from itertools import combinations

import pytest

from research import activity_control_selection as module
from research.activity_partition_selection import PartitionSelection, select_partition
from research.activity_timing import KST


FORMATION = date(2020, 3, 2)
DECISION = date(2020, 3, 3)
CUTOFF = datetime(2020, 3, 3, 8, 30, tzinfo=KST)


def ordered(codes, formation=FORMATION, seed=20261005):
    return tuple(sorted(codes, key=lambda code: hashlib.sha256(f"{seed}:{formation}:{code}".encode()).digest()))


def declaration(*, count=12, k=3, prior=frozenset(), excluded=frozenset(), arm="D1", formation=FORMATION):
    population = tuple((f"{number:06d}", f"KR7{number:06d}000") for number in range(count))
    order = ordered(tuple(row[0] for row in population), formation)
    targets = tuple(code for code in order if code not in excluded)[:k]
    prefix, outside = [], 0
    if k:
        for code in order:
            disposition = "excluded" if code in excluded else "eligible"
            prefix.append((code, disposition, "b" * 64))
            outside += disposition == "eligible" and code not in prior
            if outside == k:
                break
    decision = formation + timedelta(days=1 if arm == "D1" else 2)
    cutoff = datetime.combine(decision, CUTOFF.timetz())
    values = dict(arm=arm, formation_on=formation, decision_on=decision, selection_at=cutoff,
        seed=20261005, activity_targets=targets,
        activity_targets_sha256=module.activity_targets_fingerprint(arm=arm, formation_on=formation,
            decision_on=decision, selection_at=cutoff, targets=targets, seed=20261005),
        source_population=population, source_population_count=len(population),
        source_population_sha256=module.population_fingerprint(population), source_partition_sha256="a" * 64,
        prior_potential_codes=prior, resolved_prefix=tuple(prefix))
    return module.CountedControlSelection(**values)


def select(selection, *, held=None, free=20, **changes):
    options = dict(arm=selection.arm, formation_on=selection.formation_on, decision_on=selection.decision_on,
        selection_at=selection.selection_at, seed=selection.seed, held_codes=set() if held is None else held,
        free_slots=free)
    return module.select_control(selection, **{**options, **changes})


def test_prefix_matches_full_population_for_every_possible_held_subset_and_capacity():
    base = declaration(count=9)
    ranked = ordered(tuple(row[0] for row in base.source_population))
    prior = frozenset(ranked[:4])
    excluded = frozenset((ranked[1], ranked[7]))
    selection = declaration(count=9, prior=prior, excluded=excluded)
    eligible = tuple(code for code in ranked if code not in excluded)
    for width in range(len(prior) + 1):
        for subset in combinations(prior, width):
            held = set(subset)
            for free in (0, 1, 2, 3, 4, 20):
                expected = tuple(code for code in eligible if code not in held)[:min(3, free)]
                assert select(selection, held=held, free=free) == expected
    assert prior & selection.possible_origin_codes
    assert selection.prior_potential_codes == prior
    with pytest.raises(FrozenInstanceError): selection.seed = 42


def test_many_automatic_component_codes_do_not_break_the_rank_bound():
    base = declaration(count=50, k=4)
    order = ordered(tuple(row[0] for row in base.source_population))
    # Component code count is not investment slot count; H can exceed twenty.
    prior = frozenset(order[:31]) | frozenset(("successor_outside_formation",))
    selection = declaration(count=50, k=4, prior=prior)
    assert len(selection.resolved_prefix) == 35
    assert select(selection, held=set(prior), free=2) == order[31:33]
    assert selection.possible_origin_codes == frozenset(order[:35])
    assert select(selection, held=set(), free=20) == order[:4]


def test_current_prior_bound_is_fixed_and_new_origins_are_for_later_formations():
    first = declaration(count=9, k=1)
    assert len(first.resolved_prefix) == 1 and first.prior_potential_codes == frozenset()
    post = first.prior_potential_codes | first.possible_origin_codes | frozenset(("new_successor",))
    assert first.prior_potential_codes == frozenset()
    second = declaration(count=9, k=1, prior=post, formation=FORMATION + timedelta(days=7))
    assert select(second, held=set(post)) == tuple(code for code in second.eligible_codes if code not in post)[:1]
    # Adding current candidates to U invalidates the old stopping certificate.
    with pytest.raises(ValueError, match="resolve k eligible"):
        replace(first, prior_potential_codes=post)


def test_no_eligible_outside_prior_requires_complete_resolved_population():
    population = declaration(count=8).source_population
    prior = frozenset(row[0] for row in population)
    selection = declaration(count=8, prior=prior)
    assert len(selection.resolved_prefix) == len(population)
    assert select(selection, held=set(prior), free=20) == ()
    assert select(selection, held=set(), free=20) == ordered(tuple(prior))[:3]


def test_zero_ceiling_uses_no_eligibility_prefix_but_retains_complete_population_identity():
    selection = declaration(count=100, k=0, prior=frozenset(("automatic_successor",)))
    assert selection.resolved_prefix == selection.eligible_codes == ()
    assert selection.source_population_count == 100 and selection.possible_origin_codes == frozenset()
    assert select(selection, held={"automatic_successor"}) == ()
    with pytest.raises(ValueError, match="zero target ceiling"):
        replace(selection, resolved_prefix=(("000000", "unknown", "b" * 64),))


def test_unknown_tail_stays_in_identity_without_being_claimed_eligible():
    selection = declaration(count=100, k=1)
    assert len(selection.resolved_prefix) == 1 and len(selection.eligible_codes) == 1
    assert len(selection.source_population) == 100
    assert select(selection) == selection.eligible_codes
    changed = list(selection.source_population)
    unknown = next(i for i, row in enumerate(changed) if row[0] not in selection.eligible_codes)
    changed[unknown] = (changed[unknown][0], "different_tail_identity")
    with pytest.raises(ValueError, match="population count/fingerprint"):
        replace(selection, source_population=tuple(changed))


@pytest.mark.parametrize("in_prior", [False, True])
def test_original_activity_target_cannot_be_declared_excluded_in_control_prefix(in_prior):
    base = declaration(count=12, k=1)
    head, second = ordered(tuple(row[0] for row in base.source_population))[:2]
    selection = declaration(count=12, k=1, prior=frozenset((head,)) if in_prior else frozenset())
    # This still satisfies the rank and k-outside-U stopping rule. The sole
    # contradiction is removing a known same-universe ACTIVITY target.
    prefix = ((head, "excluded", "b" * 64), (second, "eligible", "b" * 64))
    assert selection.activity_targets == (head,)
    with pytest.raises(ValueError, match="original activity target cannot be excluded"):
        replace(selection, resolved_prefix=prefix)


def test_original_activity_target_in_unneeded_tail_does_not_expand_control_prefix():
    selection = declaration(count=12, k=1)
    tail = ordered(tuple(row[0] for row in selection.source_population))[-1]
    targets = (tail,)
    changed = replace(selection, activity_targets=targets,
        activity_targets_sha256=module.activity_targets_fingerprint(arm=selection.arm,
            formation_on=selection.formation_on, decision_on=selection.decision_on,
            selection_at=selection.selection_at, targets=targets, seed=selection.seed))
    assert changed.resolved_prefix == selection.resolved_prefix
    assert tail not in changed.eligible_codes and tail not in changed.possible_origin_codes
    assert select(changed) == selection.eligible_codes


@pytest.mark.parametrize("in_prior", [False, True])
def test_unknown_front_cannot_be_skipped_even_inside_potential_held_bound(in_prior):
    first = declaration(count=9)
    head = first.resolved_prefix[0][0]
    selection = declaration(count=9, prior=frozenset((head,)) if in_prior else frozenset())
    rows = list(selection.resolved_prefix)
    rows[0] = (head, "unknown", "b" * 64)
    with pytest.raises(ValueError, match="resolved dispositions"):
        replace(selection, resolved_prefix=tuple(rows))


@pytest.mark.parametrize("mutation", ["missing_first", "missing_last", "duplicate", "unordered", "unexpected", "bad_proof", "extra_tail"])
def test_prefix_requires_exact_rank_coverage_and_first_stopping_boundary(mutation):
    selection = declaration(count=12)
    rows = list(selection.resolved_prefix)
    if mutation == "missing_first": rows.pop(0)
    elif mutation == "missing_last": rows.pop()
    elif mutation == "duplicate": rows[1] = rows[0]
    elif mutation == "unordered": rows.reverse()
    elif mutation == "unexpected": rows[0] = ("not_in_population", "eligible", "b" * 64)
    elif mutation == "bad_proof": rows[0] = (rows[0][0], "eligible", "not_sha256")
    else:
        order = ordered(tuple(row[0] for row in selection.source_population))
        rows.append((order[len(rows)], "unknown", "b" * 64))
    with pytest.raises(ValueError): replace(selection, resolved_prefix=tuple(rows))


@pytest.mark.parametrize("mutation", ["missing", "duplicate_code", "duplicate_isin", "order", "count", "bool_count", "hash", "partition_hash", "mutable"])
def test_population_preserves_every_key_and_rejects_ambiguous_identity(mutation):
    selection = declaration()
    rows = list(selection.source_population)
    changes = {}
    if mutation == "missing": rows.pop()
    elif mutation == "duplicate_code": rows[1] = (rows[0][0], rows[1][1])
    elif mutation == "duplicate_isin": rows[1] = (rows[1][0], rows[0][1])
    elif mutation == "order": rows.reverse()
    elif mutation == "count": changes["source_population_count"] = 100
    elif mutation == "bool_count": changes["source_population_count"] = True
    elif mutation == "hash": changes["source_population_sha256"] = "f" * 64
    elif mutation == "partition_hash": changes["source_partition_sha256"] = "bad"
    if mutation == "mutable": changes["source_population"] = rows
    else: changes["source_population"] = tuple(rows)
    with pytest.raises(ValueError): replace(selection, **changes)


@pytest.mark.parametrize("change", [dict(seed=7), dict(seed=True), dict(arm="CONTROL"),
    dict(formation_on=datetime(2020, 3, 2)), dict(decision_on=FORMATION),
    dict(selection_at=datetime(2020, 3, 3, 8, 31, tzinfo=KST)),
    dict(selection_at=datetime(2020, 3, 3, 8, 30)), dict(activity_targets_sha256="f" * 64),
    dict(activity_targets=("unknown",)), dict(activity_targets=("000000", "000000")),
    dict(prior_potential_codes={"000000"}), dict(resolved_prefix=[])])
def test_declaration_fixes_original_seed_target_ceiling_and_preopening_schedule(change):
    with pytest.raises(ValueError): replace(declaration(), **change)


@pytest.mark.parametrize("change", [dict(held_codes={"unknown"}), dict(held_codes=frozenset()),
    dict(free_slots=True), dict(free_slots=-1), dict(free_slots=21), dict(seed=1), dict(seed=True),
    dict(arm="D2"), dict(formation_on=FORMATION + timedelta(days=1)),
    dict(decision_on=DECISION + timedelta(days=1)), dict(selection_at=CUTOFF + timedelta(minutes=1))])
def test_runtime_book_arguments_cannot_escape_bound_or_change_identity(change):
    with pytest.raises(ValueError): select(declaration(), **change)


@pytest.mark.parametrize("arm", ["D1", "D2"])
def test_selector_delegates_original_hash_after_held_exclusion_and_count_cap(monkeypatch, arm):
    first = declaration(count=15, arm=arm)
    prior = frozenset(row[0] for row in first.source_population[:8])
    selection = declaration(count=15, prior=prior, arm=arm)
    engine, calls = module.select_partition, []
    def capture(partition, **kwargs):
        calls.append((partition, kwargs))
        return engine(partition, **kwargs)
    monkeypatch.setattr(module, "select_partition", capture)
    chosen = select(selection, held=set(prior), free=8)
    assert len(chosen) == 3 and len(calls) == 1 and calls[0][1]["free_slots"] == 3
    assert calls[0][0].eligible_codes == selection.eligible_codes
    assert calls[0][1]["seed"] == 20261005 and calls[0][1]["held_codes"] == set(prior)
    assert chosen == ordered(tuple(row[0] for row in selection.source_population if row[0] not in prior))[:3]
    # Failed fills have no input to this contract and cannot trigger substitution.
    assert select(selection, held=set(prior), free=8) == chosen
    assert select(selection, held=set(prior), free=1) == chosen[:1]


def test_existing_partition_contract_is_unchanged_and_does_not_gain_count_ceiling():
    selection = declaration(count=15, prior=frozenset(f"{i:06d}" for i in range(8)))
    legacy = PartitionSelection(selection.formation_on, selection.decision_on, selection.selection_at,
        selection.eligible_codes, selection.source_population_count, selection.source_partition_sha256)
    options = dict(formation_on=legacy.formation_on, decision_on=legacy.decision_on,
        selection_at=legacy.selection_at, held_codes=set(), seed=20261005, free_slots=20)
    assert len(select_partition(legacy, **options)) > len(select(selection)) == 3
