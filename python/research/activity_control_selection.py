"""Pure counted control selection over a proved prefix of the full population.

The caller verifies source evidence and the original pre-opening ACTIVITY target
declarations before this boundary. Eligibility removes only the activity test;
these structural checks establish no classification or source truth. No quote,
fill, cash value, IO or replay enters selection. Existing selection contracts
remain unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
import hashlib
import json
import re

from research.activity_partition_selection import PartitionSelection, select_partition
from research.activity_timing import KST


SEED = 20261005
SLOTS = 20


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip()


def _pin(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def population_fingerprint(population: tuple[tuple[str, str], ...]) -> str:
    """Identify every source code/ISIN, including exclusions and unknown tails."""
    return _fingerprint(population)


def activity_targets_fingerprint(*, arm: str, formation_on: date, decision_on: date,
                                 selection_at: datetime, targets: tuple[str, ...], seed: int) -> str:
    """Bind the caller-verified original targets and timing, independently of fills."""
    return _fingerprint({"arm": arm, "formation_on": formation_on.isoformat(),
        "decision_on": decision_on.isoformat(), "selection_at": selection_at.isoformat(),
        "targets": targets, "seed": seed})


def _rank(codes, formation_on: date, seed: int) -> tuple[str, ...]:
    # Keep the existing selector's exact hash input, digest ordering and no salt.
    return tuple(sorted(codes, key=lambda code: hashlib.sha256(f"{seed}:{formation_on}:{code}".encode()).digest()))


@dataclass(frozen=True)
class CountedControlSelection:
    """An immutable full-population identity and minimally resolved rank prefix.

    ``prior_potential_codes`` is fixed during this formation and must contain
    every possible retained held code, including compulsory successors. The
    caller extends it with ``possible_origin_codes`` and relevant successors
    only for subsequent formations. Each prefix triple is code, independently
    reviewed eligible/excluded disposition, and its evidence SHA-256. Unknown
    candidates are permitted only in the unexamined population tail.
    """

    arm: str
    formation_on: date
    decision_on: date
    selection_at: datetime
    seed: int
    activity_targets: tuple[str, ...]
    activity_targets_sha256: str
    source_population: tuple[tuple[str, str], ...]
    source_population_count: int
    source_population_sha256: str
    source_partition_sha256: str
    prior_potential_codes: frozenset[str]
    resolved_prefix: tuple[tuple[str, str, str], ...]

    def __post_init__(self) -> None:
        _require(self.arm in ("D1", "D2") and type(self.formation_on) is date
            and type(self.decision_on) is date and self.formation_on < self.decision_on
            and isinstance(self.selection_at, datetime) and self.selection_at.utcoffset() is not None
            and self.selection_at.astimezone(KST) == datetime.combine(self.decision_on, time(8, 30), KST),
            "explicit paired schedule and pre-opening cutoff required")
        _require(type(self.seed) is int and self.seed == SEED, "unchanged original selection seed required")
        population = self.source_population
        _require(type(population) is tuple and all(type(row) is tuple and len(row) == 2
            and all(_text(value) for value in row) for row in population), "immutable complete code/ISIN population required")
        codes, isins = tuple(row[0] for row in population), tuple(row[1] for row in population)
        _require(codes == tuple(sorted(set(codes))) and len(isins) == len(set(isins))
            and type(self.source_population_count) is int and self.source_population_count == len(population)
            and _pin(self.source_population_sha256)
            and population_fingerprint(population) == self.source_population_sha256
            and _pin(self.source_partition_sha256), "complete unique population count/fingerprint required")
        _require(type(self.activity_targets) is tuple and len(self.activity_targets) <= SLOTS
            and all(_text(code) for code in self.activity_targets)
            and len(self.activity_targets) == len(set(self.activity_targets))
            and set(self.activity_targets) <= set(codes)
            and self.activity_targets == _rank(self.activity_targets, self.formation_on, self.seed)
            and _pin(self.activity_targets_sha256)
            and self.activity_targets_sha256 == activity_targets_fingerprint(arm=self.arm,
                formation_on=self.formation_on, decision_on=self.decision_on,
                selection_at=self.selection_at, targets=self.activity_targets, seed=self.seed),
            "original ordered pre-opening activity targets and identity required")
        _require(type(self.prior_potential_codes) is frozenset
            and all(_text(code) for code in self.prior_potential_codes), "immutable prior potential held-code bound required")
        _require(type(self.resolved_prefix) is tuple, "immutable resolved prefix required")
        k = len(self.activity_targets)
        if k == 0:
            _require(self.resolved_prefix == (), "zero target ceiling requires no eligibility prefix")
            return
        ordered = _rank(codes, self.formation_on, self.seed)
        outside = 0
        for index, row in enumerate(self.resolved_prefix):
            _require(type(row) is tuple and len(row) == 3 and index < len(ordered)
                and row[0] == ordered[index] and row[1] in ("eligible", "excluded") and _pin(row[2]),
                "complete ordered prefix with resolved dispositions and evidence required")
            _require(row[0] not in self.activity_targets or row[1] == "eligible",
                "original activity target cannot be excluded from the same-universe control")
            outside += row[1] == "eligible" and row[0] not in self.prior_potential_codes
            _require(outside < k or index == len(self.resolved_prefix) - 1,
                "prefix must stop at the first k eligible candidates outside fixed prior bound")
        _require(outside == k or len(self.resolved_prefix) == len(ordered),
            "prefix must resolve k eligible candidates outside prior bound or the whole population")

    @property
    def eligible_codes(self) -> tuple[str, ...]:
        """Every eligible prefix code remains possible, including codes inside U."""
        return tuple(row[0] for row in self.resolved_prefix if row[1] == "eligible")

    @property
    def possible_origin_codes(self) -> frozenset[str]:
        return frozenset(self.eligible_codes)


def select_control(selection: CountedControlSelection, *, arm: str, formation_on: date,
                   decision_on: date, selection_at: datetime, held_codes: set[str], seed: int,
                   free_slots: int) -> tuple[str, ...]:
    """Use the control's retained holdings and slots, capped by original targets.

    For H contained in fixed U, the prefix contains k eligible codes outside H
    unless the whole population is resolved. The full-universe top min(k, free)
    after held exclusion is therefore inside this prefix, regardless of the
    number of held component codes. A later failed fill never changes targets.
    """
    _require(isinstance(selection, CountedControlSelection)
        and arm == selection.arm and type(formation_on) is date and type(decision_on) is date
        and formation_on == selection.formation_on and decision_on == selection.decision_on
        and selection_at == selection.selection_at and type(seed) is int and seed == selection.seed,
        "control selection arm/date/cutoff/seed mismatch")
    _require(type(held_codes) is set and all(_text(code) for code in held_codes)
        and held_codes <= selection.prior_potential_codes
        and type(free_slots) is int and 0 <= free_slots <= SLOTS,
        "held codes must be within prior potential bound and free slots within unchanged book capacity")
    if not selection.activity_targets:
        return ()
    partition = PartitionSelection(selection.formation_on, selection.decision_on, selection.selection_at,
        selection.eligible_codes, selection.source_population_count, selection.source_partition_sha256)
    return select_partition(partition, formation_on=formation_on, decision_on=decision_on,
        selection_at=selection_at, held_codes=held_codes, seed=seed,
        free_slots=min(len(selection.activity_targets), free_slots))
