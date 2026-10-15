"""Synthetic BS rank/identity proofs, without actual source or market access."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
from itertools import combinations

import pytest

from research import activity_cohort_selection as module
from research import activity_control_selection as legacy
from research.activity_timing import KST


FORMATION = date(2020, 3, 2)


def original_order(codes):
    return tuple(sorted(codes, key=lambda code:
        hashlib.sha256(f"20261005:{FORMATION}:{code}".encode()).digest()))


def control_order(codes, control_id="C01"):
    return tuple(sorted(codes, key=lambda code:
        hashlib.sha256(f"20261005:BS-v1:{control_id}:{FORMATION}:{code}".encode()).digest()))


def metadata(*, count=12, k=3, arm="D1", targets=None):
    population = tuple((f"{number:06d}", f"KR7{number:06d}000") for number in range(count))
    codes = tuple(row[0] for row in population)
    if targets is None:
        targets = original_order(codes)[:k]
    decision = FORMATION + timedelta(days=1 if arm == "D1" else 2)
    cutoff = datetime.combine(decision, datetime.min.time(), KST).replace(hour=8, minute=30)
    return dict(arm=arm, formation_on=FORMATION, decision_on=decision, selection_at=cutoff,
        seed=20261005, activity_targets=targets,
        activity_targets_sha256=legacy.activity_targets_fingerprint(arm=arm, formation_on=FORMATION,
            decision_on=decision, selection_at=cutoff, targets=targets, seed=20261005),
        source_population=population, source_population_count=count,
        source_population_sha256=legacy.population_fingerprint(population),
        source_partition_sha256="a" * 64)


def dispositions(values, *, excluded=(), unknown=()):
    return tuple((code, "excluded" if code in excluded else "unknown" if code in unknown else "eligible",
        "b" * 64) for code, _ in values["source_population"])


def declaration(*, control_id="C01", excluded=(), unknown=(), **kwargs):
    values = metadata(**kwargs)
    return module.build_cohort_selection(group_id=control_id,
        dispositions=dispositions(values, excluded=excluded, unknown=unknown), **values)


def test_frozen_control_roster_and_original_hash_helpers_are_unchanged():
    assert module.CONTROL_IDS == ("C01", "C02", "C03", "C04", "C05")
    assert len(set(module.CONTROL_IDS)) == 5
    assert module.SEED == 20261005 and module.SLOTS == 20
    assert module.activity_targets_fingerprint is legacy.activity_targets_fingerprint
    assert module.population_fingerprint is legacy.population_fingerprint
    selection = declaration()
    assert selection.activity_targets == legacy._rank(selection.activity_targets, FORMATION, module.SEED)
    with pytest.raises(FrozenInstanceError):
        selection.group_id = "C02"


@pytest.mark.parametrize("control_id", module.CONTROL_IDS)
@pytest.mark.parametrize("arm", ("D1", "D2"))
def test_all_unfiltered_population_matches_literal_hash_top_k(control_id, arm):
    selection = declaration(control_id=control_id, arm=arm, count=100, k=20)
    expected = control_order(tuple(row[0] for row in selection.source_population), control_id)[:20]
    assert selection.chosen_codes == expected
    assert selection.target_ceiling == 20 and selection.eligible_shortfall == 0
    assert tuple(row[0] for row in selection.resolved_prefix) == expected
    assert selection.population_identity == (100, selection.source_population_sha256, "a" * 64)


def test_bs_literal_hash_pin_and_reordering_determinism():
    raw = b"20261005:BS-v1:C01:2020-03-02:000001"
    assert hashlib.sha256(raw).hexdigest() == "ff6f22a58cb5d272fcd6ecf314cd12405c2a9b1f03005586666d4227643e2352"
    values = metadata(count=100)
    rows = dispositions(values)
    first = module.build_cohort_selection(group_id="C01", dispositions=rows, **values)
    second = module.build_cohort_selection(group_id="C01", dispositions=tuple(reversed(rows)), **values)
    assert first == second
    codes = tuple(row[0] for row in values["source_population"])
    assert module.rank_control_codes(codes, control_id="C01", formation_on=FORMATION) == control_order(codes)
    assert module.rank_control_codes(tuple(reversed(codes)), control_id="C01", formation_on=FORMATION) == control_order(codes)
    assert len({declaration(count=100, control_id=identifier).chosen_codes for identifier in module.CONTROL_IDS}) == 5


def test_control_removes_only_exclusions_and_stops_at_first_k():
    values = metadata(count=8, k=2)
    codes = tuple(row[0] for row in values["source_population"])
    available_exclusions = tuple(code for code in codes if code not in values["activity_targets"])
    for width in range(len(available_exclusions) + 1):
        for excluded in combinations(available_exclusions, width):
            selection = module.build_cohort_selection(group_id="C01",
                dispositions=dispositions(values, excluded=excluded), **values)
            expected = tuple(code for code in control_order(codes) if code not in excluded)[:2]
            assert selection.chosen_codes == expected
            assert selection.resolved_prefix[-1][0] == expected[-1]


def test_activity_keeps_exact_original_targets_without_a_control_prefix():
    selection = declaration(control_id="ACTIVITY")
    assert selection.chosen_codes == selection.activity_targets
    assert selection.resolved_prefix == () and selection.eligible_shortfall == 0
    with pytest.raises(ValueError, match="ACTIVITY requires an empty"):
        replace(selection, resolved_prefix=((selection.activity_targets[0], "eligible", "b" * 64),))


@pytest.mark.parametrize("control_id", ("ACTIVITY", *module.CONTROL_IDS))
def test_zero_target_ceiling_resolves_nothing_and_preserves_complete_identity(control_id):
    selection = declaration(control_id=control_id, count=100, k=0,
        unknown=tuple(f"{number:06d}" for number in range(100)))
    assert selection.chosen_codes == selection.resolved_prefix == ()
    assert selection.source_population_count == 100 and selection.target_ceiling == selection.eligible_shortfall == 0
    with pytest.raises(ValueError):
        replace(selection, resolved_prefix=(("000000", "unknown", "b" * 64),))


def test_complete_exhaustion_resolves_every_code_including_excluded_ranks():
    values = metadata(count=8, k=2)
    codes = tuple(row[0] for row in values["source_population"])
    last_two = control_order(codes)[-2:]
    values = metadata(count=8, targets=original_order(last_two))
    excluded = tuple(code for code in codes if code not in last_two)
    selection = module.build_cohort_selection(group_id="C01",
        dispositions=dispositions(values, excluded=excluded), **values)
    assert len(selection.resolved_prefix) == 8
    assert selection.chosen_codes == last_two and selection.eligible_shortfall == 0
    with pytest.raises(ValueError, match="resolve k eligible"):
        replace(selection, resolved_prefix=selection.resolved_prefix[:-1])


@pytest.mark.parametrize("missing", (False, True))
def test_needed_unknown_or_missing_rank_reports_exact_code_without_skipping(missing):
    values = metadata(count=12)
    head = control_order(tuple(row[0] for row in values["source_population"]))[0]
    rows = dispositions(values, unknown=(head,))
    if missing:
        rows = tuple(row for row in rows if row[0] != head)
    with pytest.raises(ValueError, match=f"^unresolved BS prefix code: {head}$"):
        module.build_cohort_selection(group_id="C01", dispositions=rows, **values)


def test_unknown_tail_is_unexamined_but_remains_in_complete_population_identity():
    values = metadata(count=100, k=1)
    ordered = control_order(tuple(row[0] for row in values["source_population"]))
    selection = module.build_cohort_selection(group_id="C01",
        dispositions=dispositions(values, unknown=ordered[1:]), **values)
    assert selection.chosen_codes == (ordered[0],)
    assert len(selection.source_population) == 100 and len(selection.resolved_prefix) == 1
    # Tail evidence is not required to prove the prefix; no complete eligibility is claimed.
    minimal = module.build_cohort_selection(group_id="C01",
        dispositions=((ordered[0], "eligible", "b" * 64),), **values)
    assert minimal == selection
    population = list(selection.source_population)
    tail_index = next(index for index, row in enumerate(population) if row[0] == ordered[-1])
    population[tail_index] = (ordered[-1], "different_tail_identity")
    with pytest.raises(ValueError, match="population count/fingerprint"):
        replace(selection, source_population=tuple(population))


def test_original_activity_target_cannot_be_excluded_in_a_needed_prefix():
    values = metadata(count=12, k=1)
    head, second = control_order(tuple(row[0] for row in values["source_population"]))[:2]
    values = metadata(count=12, targets=(head,))
    with pytest.raises(ValueError, match="original activity target cannot be excluded"):
        module.CohortSelection(group_id="C01",
            resolved_prefix=((head, "excluded", "b" * 64), (second, "eligible", "b" * 64)), **values)


def test_supplied_tail_exclusion_cannot_contradict_an_original_target():
    values = metadata(count=12, k=1)
    ordered = control_order(tuple(row[0] for row in values["source_population"]))
    values = metadata(count=12, targets=(ordered[-1],))
    with pytest.raises(ValueError, match="original activity target cannot be excluded"):
        module.build_cohort_selection(group_id="C01",
            dispositions=dispositions(values, excluded=(ordered[-1],)), **values)


@pytest.mark.parametrize("mutation", ("missing_first", "missing_last", "duplicate", "unordered",
    "unexpected", "unknown", "bad_proof", "extra_tail", "mutable"))
def test_prefix_requires_contiguous_resolved_rank_and_minimal_stopping(mutation):
    selection = declaration()
    rows = list(selection.resolved_prefix)
    if mutation == "missing_first": rows.pop(0)
    elif mutation == "missing_last": rows.pop()
    elif mutation == "duplicate": rows[1] = rows[0]
    elif mutation == "unordered": rows.reverse()
    elif mutation == "unexpected": rows[0] = ("not_in_population", "eligible", "b" * 64)
    elif mutation == "unknown": rows[0] = (rows[0][0], "unknown", "b" * 64)
    elif mutation == "bad_proof": rows[0] = (rows[0][0], "eligible", "bad_sha256")
    elif mutation == "extra_tail":
        order = control_order(tuple(row[0] for row in selection.source_population))
        rows.append((order[len(rows)], "eligible", "b" * 64))
    with pytest.raises(ValueError):
        replace(selection, resolved_prefix=rows if mutation == "mutable" else tuple(rows))


@pytest.mark.parametrize("mutation", ("missing", "duplicate_code", "duplicate_isin", "order", "count",
    "bool_count", "hash", "partition_hash", "mutable"))
def test_population_rejects_incomplete_or_ambiguous_identity(mutation):
    selection = declaration()
    rows, changes = list(selection.source_population), {}
    if mutation == "missing": rows.pop()
    elif mutation == "duplicate_code": rows[1] = (rows[0][0], rows[1][1])
    elif mutation == "duplicate_isin": rows[1] = (rows[1][0], rows[0][1])
    elif mutation == "order": rows.reverse()
    elif mutation == "count": changes["source_population_count"] = 100
    elif mutation == "bool_count": changes["source_population_count"] = True
    elif mutation == "hash": changes["source_population_sha256"] = "f" * 64
    elif mutation == "partition_hash": changes["source_partition_sha256"] = "bad"
    changes["source_population"] = rows if mutation == "mutable" else tuple(rows)
    with pytest.raises(ValueError):
        replace(selection, **changes)


@pytest.mark.parametrize("change", (dict(group_id="CONTROL"), dict(group_id="C00"), dict(group_id="C06"),
    dict(group_id="C1"), dict(group_id="c01"), dict(seed=7), dict(seed=True), dict(arm="CONTROL"),
    dict(formation_on=datetime(2020, 3, 2)), dict(decision_on=FORMATION),
    dict(selection_at=datetime(2020, 3, 3, 8, 31, tzinfo=KST)),
    dict(selection_at=datetime(2020, 3, 3, 8, 30)), dict(activity_targets_sha256="f" * 64),
    dict(activity_targets=("unknown",)), dict(activity_targets=("000000", "000000"))))
def test_metadata_fixes_ids_seed_original_targets_and_aware_preopening_schedule(change):
    with pytest.raises(ValueError):
        replace(declaration(), **change)


def test_original_order_rejection_is_independent_of_a_recomputed_target_pin():
    selection = declaration()
    targets = tuple(reversed(selection.activity_targets))
    pin = module.activity_targets_fingerprint(arm=selection.arm, formation_on=selection.formation_on,
        decision_on=selection.decision_on, selection_at=selection.selection_at, targets=targets, seed=selection.seed)
    with pytest.raises(ValueError, match="original ordered"):
        replace(selection, activity_targets=targets, activity_targets_sha256=pin)


def test_equivalent_aware_cutoff_preserves_its_original_fingerprint_representation():
    selection = declaration()
    cutoff = selection.selection_at.astimezone(timezone.utc)
    pin = module.activity_targets_fingerprint(arm=selection.arm, formation_on=selection.formation_on,
        decision_on=selection.decision_on, selection_at=cutoff, targets=selection.activity_targets, seed=selection.seed)
    changed = replace(selection, selection_at=cutoff, activity_targets_sha256=pin)
    assert changed.chosen_codes == selection.chosen_codes
    assert changed.activity_targets_sha256 != selection.activity_targets_sha256


@pytest.mark.parametrize("mutation", ("duplicate", "outside", "bad_pin", "bad_state", "mutable"))
def test_builder_rejects_ambiguous_or_unpinned_dispositions_even_in_tail(mutation):
    values = metadata()
    rows = list(dispositions(values))
    if mutation == "duplicate": rows.append(rows[-1])
    elif mutation == "outside": rows[-1] = ("outside_population", "unknown", "b" * 64)
    elif mutation == "bad_pin": rows[-1] = (rows[-1][0], "unknown", "bad")
    elif mutation == "bad_state": rows[-1] = (rows[-1][0], "pass", "b" * 64)
    with pytest.raises(ValueError):
        module.build_cohort_selection(group_id="C01",
            dispositions=rows if mutation == "mutable" else tuple(rows), **values)


@pytest.mark.parametrize("change", (dict(control_id="ACTIVITY"), dict(control_id="C06"),
    dict(seed=1), dict(seed=True), dict(formation_on=datetime(2020, 3, 2))))
def test_rank_helper_refuses_policy_or_type_changes(change):
    options = dict(control_id="C01", formation_on=FORMATION, seed=20261005)
    with pytest.raises(ValueError):
        module.rank_control_codes(("000001", "000002"), **{**options, **change})


@pytest.mark.parametrize("codes", (("000001", "000001"), ["000001"], (" 000001",)))
def test_rank_helper_refuses_duplicate_or_mutable_codes(codes):
    with pytest.raises(ValueError):
        module.rank_control_codes(codes, control_id="C01", formation_on=FORMATION)
