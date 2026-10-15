"""Counted prefix and complete synthetic controls use the existing book equally."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
import hashlib

import pytest

from research import activity_replay as replay
from research.activity_accounting import Lot, PriceBasis
from research.activity_book import ActivityBook, CompulsorySpinOff
from research.activity_control_selection import CountedControlSelection, activity_targets_fingerprint, population_fingerprint
from research.activity_partition_selection import PartitionSelection
from research.activity_timing import SessionLagPolicy


SNAPSHOT = "a" * 64


def ranked(codes, formation, seed):
    return tuple(sorted(codes, key=lambda code: hashlib.sha256(f"{seed}:{formation}:{code}".encode()).digest()))


def declarations(calendar, params, policy, codes, ks, initial_potential, *, full=False):
    population = tuple((code, f"synthetic_ISIN_{code}") for code in sorted(codes))
    end = calendar.index(date.fromisoformat(params["end"]))
    indices = policy.formation_indices(calendar, lookback=params["lookback"], holding_sessions=params["holding_sessions"], end_index=end)
    prior, result = frozenset(initial_potential), []
    for index, k in zip(indices, ks, strict=True):
        formation, decision, cutoff = calendar[index], policy.execution_on(calendar, index), policy.selection_at(calendar, index)
        order = ranked(codes, formation, params["seed"])
        bound = prior | frozenset(codes) if full else prior
        prefix, outside = [], 0
        if k:
            for code in order:
                prefix.append((code, "eligible", "b" * 64))
                outside += code not in bound
                if outside == k:
                    break
        targets = order[:k]
        selection = CountedControlSelection(f"D{policy.session_lag}", formation, decision, cutoff, params["seed"], targets,
            activity_targets_fingerprint(arm=f"D{policy.session_lag}", formation_on=formation, decision_on=decision,
                selection_at=cutoff, targets=targets, seed=params["seed"]), population, len(population),
            population_fingerprint(population), "c" * 64, bound, tuple(prefix))
        result.append(selection)
        prior |= selection.possible_origin_codes
    return tuple(result)


def ordinary(lag=1, *, ks=(2, 1)):
    calendar = tuple(date(2024, 1, 1) + timedelta(days=i) for i in range(13))
    params = dict(lookback=2, holding_sessions=3, slots=20, seed=20261005, threshold=3.0,
        commission_bps_per_side=1.77, slippage_bps_per_side=5.0, end=calendar[10].isoformat())
    policy = SessionLagPolicy(lag, paired=True)
    prices = (D(100),) * len(calendar)
    series = replay.DecimalSeries(prices, prices, (D(10),) * len(calendar),
        (False,) * len(calendar), (False,) * len(calendar), (True,) * len(calendar))
    panel = {f"C{i:02d}": series for i in range(12)}
    selected = declarations(calendar, params, policy, tuple(panel), ks, frozenset())
    full = declarations(calendar, params, policy, tuple(panel), ks, frozenset(), full=True)
    initial = ActivityBook(D(1), (), calendar[2])
    return calendar, panel, params, policy, selected, full, initial


def run(values, *, selected=None, initial=None, events=()):
    calendar, panel, params, policy, prefix, _, book = values
    return replay.replay_synthetic(calendar, panel, params, prefix if selected is None else selected,
        initial_book=book if initial is None else initial, timing_policy=policy, events=events, dataset_sha256=SNAPSHOT)


@pytest.mark.parametrize("lag", [1, 2])
@pytest.mark.parametrize("failure", ["none", "absent_entry", "frozen_entry", "halted_due", "cash"])
def test_prefix_and_complete_control_produce_identical_books_under_failed_fills_and_halts(lag, failure):
    values = ordinary(lag)
    calendar, panel, params, policy, selected, full, initial = values
    entry, due = 2 + lag, 5 + lag
    chosen = selected[0].eligible_codes[0]
    series = panel[chosen]
    if failure == "absent_entry":
        panel[chosen] = replace(series, opens=tuple(None if i == entry else value for i, value in enumerate(series.opens)),
            closes=tuple(None if i == entry else value for i, value in enumerate(series.closes)),
            turnover=tuple(None if i == entry else value for i, value in enumerate(series.turnover)),
            frozen=tuple(None if i == entry else value for i, value in enumerate(series.frozen)),
            locked=tuple(None if i == entry else value for i, value in enumerate(series.locked)),
            observed=tuple(i != entry for i in range(len(calendar))))
    elif failure == "frozen_entry": panel[chosen] = replace(series, frozen=tuple(i == entry for i in range(len(calendar))))
    elif failure == "halted_due": panel[chosen] = replace(series, frozen=tuple(i >= due for i in range(len(calendar))))
    elif failure == "cash": initial = replace(initial, cash=D(0))
    prefix_result = run(values, initial=initial)
    full_result = run(values, selected=full, initial=initial)
    assert prefix_result == full_result
    assert len(prefix_result.selections[0][1]) == 2 and len(prefix_result.selections[1][1]) <= 1
    if failure in ("absent_entry", "frozen_entry"):
        first = next(book for book in prefix_result.books if book.as_of == calendar[entry])
        assert chosen not in {lot.code for lot in first.lots} and len(first.lots) == 1
        assert dict(prefix_result.diagnostics)["unfilled_entries"] >= 1
    if failure == "halted_due":
        assert chosen in {lot.code for lot in prefix_result.books[-1].lots}
    if failure == "cash": assert all(book.lots == () for book in prefix_result.books)


@pytest.mark.parametrize("lag", [1, 2])
def test_book_keeps_twenty_slot_allocation_and_freezes_targets_before_execution_quotes(lag, monkeypatch):
    values = ordinary(lag)
    calendar, panel, params, _, selected, _, _ = values
    entry = 2 + lag
    choose, quote, selected_at_entry = replay.select_control, replay._quote, []
    def capture(selection, **kwargs):
        assert kwargs["held_codes"] == set() and kwargs["free_slots"] == 20
        result = choose(selection, **kwargs)
        selected_at_entry.append(kwargs["decision_on"])
        return result
    def before_quote(series, index):
        if index == entry:
            assert calendar[entry] in selected_at_entry
        return quote(series, index)
    monkeypatch.setattr(replay, "select_control", capture)
    monkeypatch.setattr(replay, "_quote", before_quote)
    result = run(values)
    first = next(book for book in result.books if book.as_of == calendar[entry])
    assert len(first.lots) == 2 and first.slot_count == 2
    cost = D(str(params["commission_bps_per_side"])) + D(str(params["slippage_bps_per_side"]))
    assert all(lot.shares == D(1) / 20 / (100 * (1 + cost / 10000)) for lot in first.lots)
    assert first.cash == D("0.90")


@pytest.mark.parametrize("lag", [1, 2])
def test_compulsory_spin_offs_create_more_than_twenty_held_codes_with_the_same_prefix_books(lag, monkeypatch):
    values = list(ordinary(lag, ks=(1, 1)))
    calendar, panel, params, policy, _, _, _ = values
    series = next(iter(panel.values()))
    old_codes, new_codes = tuple(f"OLD{i:02d}" for i in range(19)), tuple(f"NEW{i:02d}" for i in range(19))
    for code in (*old_codes, *new_codes): panel[code] = series
    # New components are unquoted until delivery; selection never uses this fact.
    for code in new_codes:
        panel[code] = replace(series,
            opens=tuple(None if i < 5 else D(100) for i in range(len(calendar))),
            closes=tuple(None if i < 5 else D(100) for i in range(len(calendar))),
            turnover=tuple(None if i < 5 else D(10) for i in range(len(calendar))),
            frozen=tuple(None if i < 5 else False for i in range(len(calendar))),
            locked=tuple(None if i < 5 else False for i in range(len(calendar))),
            observed=tuple(i >= 5 for i in range(len(calendar))))
    lots = tuple(Lot(f"lot-{code}", code, D("0.01"), D(100), calendar[2], calendar[0],
        calendar[10], calendar[0], SNAPSHOT) for code in old_codes)
    initial = ActivityBook(D(1), lots, calendar[2])
    events = []
    for old, new in zip(old_codes, new_codes):
        def basis(code, index): return PriceBasis(code, calendar[index], D(100), D(100), SNAPSHOT, "synthetic raw/raw basis")
        events.append(CompulsorySpinOff(f"spin-{old}", D("0.5"), D("0.5"), basis(old, 3), basis(old, 4), basis(new, 5),
            calendar[3], calendar[4], calendar[4], calendar[4], calendar[5], D("0.5"),
            "synthetic fixed carry", "assumed", "synthetic final terms"))
    prior = frozenset((*old_codes, *new_codes))
    values[4] = declarations(calendar, params, policy, tuple(panel), (1, 1), prior)
    values[5] = declarations(calendar, params, policy, tuple(panel), (1, 1), prior, full=True)
    values[6] = initial
    # Keep some delivered components halted after the event; their slots persist.
    panel[new_codes[0]] = replace(panel[new_codes[0]], frozen=tuple(None if i < 5 else i >= 6 for i in range(len(calendar))))
    choose, calls = replay.select_control, []
    def capture(selection, **kwargs):
        calls.append((len(kwargs["held_codes"]), kwargs["free_slots"]))
        return choose(selection, **kwargs)
    monkeypatch.setattr(replay, "select_control", capture)
    result = run(values, events=tuple(events))
    assert result == run(values, selected=values[5], events=tuple(events))
    assert any(held >= 38 and free == 1 for held, free in calls)
    assert max(book.slot_count for book in result.books) <= 20
    assert max(len(book.lots) for book in result.books) >= 38


def test_zero_target_formation_keeps_prior_inventory_marks_and_events():
    values = list(ordinary(1, ks=(0, 0)))
    calendar, panel, params, policy, selected, _, initial = values
    held = "C00"
    successor = "C01"
    prior = frozenset((held, successor))
    values[4] = declarations(calendar, params, policy, tuple(panel), (0, 0), prior)
    values[6] = replace(initial, lots=(Lot("held", held, D("0.001"), D(100), calendar[2], calendar[0],
        calendar[10], calendar[0], SNAPSHOT),))
    def basis(code, index): return PriceBasis(code, calendar[index], D(100), D(100), SNAPSHOT, "synthetic basis")
    event = CompulsorySpinOff("zero-target-spin", D("0.5"), D("0.5"), basis(held, 3), basis(held, 4), basis(successor, 5),
        calendar[3], calendar[4], calendar[4], calendar[4], calendar[5], D("0.5"), "fixed synthetic carry", "assumed", "synthetic terms")
    result = run(values, events=(event,))
    assert all(not targets for _, targets in result.selections)
    assert result.books[0].lots and result.closed_trades == 2
    assert any(len(book.lots) == 2 and book.slot_count == 1 for book in result.books)
    assert "zero-target-spin" in result.books[-1].applied_event_ids
    assert dict(result.diagnostics).get("entries", 0) == 0


@pytest.mark.parametrize("lag", [1, 2])
def test_all_zero_control_needs_no_quotes_or_eligible_selector(lag, monkeypatch):
    from research import activity_control_selection as control

    values = ordinary(lag, ks=(0, 0))
    calendar, _, params, policy, selected, _, _ = values
    monkeypatch.setattr(control, "select_partition", lambda *args, **kwargs: pytest.fail("zero ceiling read eligible selector"))
    result = replay.replay_synthetic(calendar, {}, params, selected, dataset_sha256=None, timing_policy=policy)
    assert all(book.cash == book.nav == D(1) and book.lots == () for book in result.books)
    assert all(targets == () for _, targets in result.selections)


@pytest.mark.parametrize("change", ["missing_panel", "wrong_arm", "implicit_timing", "unpaired", "slots", "seed", "mixed", "held_outside_U"])
def test_control_cannot_bypass_original_book_or_explicit_selection_boundaries(change):
    values = list(ordinary())
    calendar, panel, params, policy, selected, full, initial = values
    if change == "missing_panel": panel.pop(selected[0].eligible_codes[-1])
    elif change == "wrong_arm": values[4] = tuple(replace(row, arm="D2", activity_targets_sha256=activity_targets_fingerprint(
        arm="D2", formation_on=row.formation_on, decision_on=row.decision_on, selection_at=row.selection_at,
        targets=row.activity_targets, seed=row.seed)) for row in selected)
    elif change == "implicit_timing": values[3] = None
    elif change == "unpaired": values[3] = SessionLagPolicy(1)
    elif change == "slots": params["slots"] = 2
    elif change == "seed": params["seed"] = 42
    elif change == "mixed":
        row = selected[1]
        legacy = PartitionSelection(row.formation_on, row.decision_on, row.selection_at, row.eligible_codes,
            row.source_population_count, row.source_partition_sha256)
        values[4] = (selected[0], legacy)
    else:
        values[6] = replace(initial, lots=(Lot("outside", "C00", D("0.001"), D(100), calendar[2], calendar[0],
            calendar[10], calendar[0], SNAPSHOT),))
    with pytest.raises(ValueError): run(values)
