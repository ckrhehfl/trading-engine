"""Pure, empty-start BS cohorts over a proved full-population rank prefix.

The caller verifies the original ACTIVITY report, pins and complete eligibility.
These checks establish metadata consistency, not source truth. Each formation
starts independently, without a prior-held exclusion or a later-fill input.
An unresolved candidate can remain only beyond the minimally required prefix.
No market values, IO, replay, performance or promotion enter this boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
import hashlib
import re

from research.activity_control_selection import (
    SEED,
    SLOTS,
    activity_targets_fingerprint,
    population_fingerprint,
)
from research.activity_timing import KST


CONTROL_IDS = ("C01", "C02", "C03", "C04", "C05")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip()


def _pin(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def rank_control_codes(codes: tuple[str, ...], *, control_id: str,
                       formation_on: date, seed: int = SEED) -> tuple[str, ...]:
    """Rank unique codes by the fixed BS-v1 bytes, with no input-order effect."""
    _require(type(control_id) is str and control_id in CONTROL_IDS,
        "fixed BS control ID required")
    _require(type(formation_on) is date and type(seed) is int and seed == SEED,
        "formation date and unchanged original selection seed required")
    _require(type(codes) is tuple and all(_text(code) for code in codes)
        and len(codes) == len(set(codes)), "immutable unique candidate codes required")
    return tuple(sorted(codes, key=lambda code: (
        hashlib.sha256(f"{seed}:BS-v1:{control_id}:{formation_on}:{code}".encode()).digest(), code)))


@dataclass(frozen=True)
class CohortSelection:
    """One formation's immutable original ceiling and independently proved targets.

    ``resolved_prefix`` triples contain code, eligible/excluded disposition and
    an independently reviewed evidence SHA-256. ACTIVITY keeps its exact
    original ordered targets and needs no CONTROL prefix. CONTROL stops at the
    first k eligible ranks, or resolves the complete population on exhaustion.
    A caller reserves k/20 capital even if fewer than k targets are available;
    it must leave unselected budget in cash instead of renormalizing weights.
    """

    group_id: str
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
    resolved_prefix: tuple[tuple[str, str, str], ...]

    def __post_init__(self) -> None:
        _require(type(self.group_id) is str and self.group_id in ("ACTIVITY", *CONTROL_IDS),
            "ACTIVITY or fixed BS control ID required")
        _require(type(self.arm) is str and self.arm in ("D1", "D2")
            and type(self.formation_on) is date and type(self.decision_on) is date
            and self.formation_on < self.decision_on
            and isinstance(self.selection_at, datetime) and self.selection_at.utcoffset() is not None
            and self.selection_at.astimezone(KST) == datetime.combine(self.decision_on, time(8, 30), KST),
            "explicit paired schedule and pre-opening cutoff required")
        _require(type(self.seed) is int and self.seed == SEED,
            "unchanged original selection seed required")
        population = self.source_population
        _require(type(population) is tuple and all(type(row) is tuple and len(row) == 2
            and all(_text(value) for value in row) for row in population),
            "immutable complete code/ISIN population required")
        codes, isins = tuple(row[0] for row in population), tuple(row[1] for row in population)
        _require(codes == tuple(sorted(set(codes))) and len(isins) == len(set(isins))
            and type(self.source_population_count) is int and self.source_population_count == len(population)
            and _pin(self.source_population_sha256)
            and self.source_population_sha256 == population_fingerprint(population)
            and _pin(self.source_partition_sha256), "complete unique population count/fingerprint required")
        targets = self.activity_targets
        _require(type(targets) is tuple and len(targets) <= SLOTS
            and all(_text(code) for code in targets) and len(targets) == len(set(targets))
            and set(targets) <= set(codes)
            and targets == tuple(sorted(targets, key=lambda code:
                hashlib.sha256(f"{self.seed}:{self.formation_on}:{code}".encode()).digest()))
            and _pin(self.activity_targets_sha256)
            and self.activity_targets_sha256 == activity_targets_fingerprint(arm=self.arm,
                formation_on=self.formation_on, decision_on=self.decision_on,
                selection_at=self.selection_at, targets=targets, seed=self.seed),
            "original ordered pre-opening activity targets and identity required")
        _require(type(self.resolved_prefix) is tuple, "immutable resolved prefix required")
        if self.group_id == "ACTIVITY":
            _require(self.resolved_prefix == (), "ACTIVITY requires an empty CONTROL eligibility prefix")
            return
        k = self.target_ceiling
        if k == 0:
            _require(self.resolved_prefix == (), "zero target ceiling requires no eligibility prefix")
            return
        ordered = rank_control_codes(codes, control_id=self.group_id,
            formation_on=self.formation_on, seed=self.seed)
        eligible = 0
        for index, row in enumerate(self.resolved_prefix):
            _require(type(row) is tuple and len(row) == 3 and index < len(ordered)
                and row[0] == ordered[index] and row[1] in ("eligible", "excluded") and _pin(row[2]),
                "complete ordered prefix with resolved dispositions and evidence required")
            _require(row[0] not in targets or row[1] == "eligible",
                "original activity target cannot be excluded from the same-universe control")
            eligible += row[1] == "eligible"
            _require(eligible < k or index == len(self.resolved_prefix) - 1,
                "prefix must stop at the first k eligible candidates")
        _require(eligible == k or len(self.resolved_prefix) == len(ordered),
            "prefix must resolve k eligible candidates or the whole population")

    @property
    def target_ceiling(self) -> int:
        return len(self.activity_targets)

    @property
    def chosen_codes(self) -> tuple[str, ...]:
        if self.group_id == "ACTIVITY":
            return self.activity_targets
        return tuple(row[0] for row in self.resolved_prefix if row[1] == "eligible")

    @property
    def eligible_shortfall(self) -> int:
        return self.target_ceiling - len(self.chosen_codes)

    @property
    def population_identity(self) -> tuple[int, str, str]:
        return (self.source_population_count, self.source_population_sha256,
            self.source_partition_sha256)


def build_cohort_selection(*, group_id: str, arm: str, formation_on: date,
                          decision_on: date, selection_at: datetime, seed: int,
                          activity_targets: tuple[str, ...], activity_targets_sha256: str,
                          source_population: tuple[tuple[str, str], ...],
                          source_population_count: int, source_population_sha256: str,
                          source_partition_sha256: str,
                          dispositions: tuple[tuple[str, str, str], ...]) -> CohortSelection:
    """Resolve only needed metadata ranks; name the exact missing/unknown code.

    Dispositions may be supplied in any order and may retain unknown tail rows.
    Evidence pins are required for every supplied row; missing rows remain
    unresolved. This helper never reviews that evidence or certifies nominees.
    """
    _require(type(group_id) is str and group_id in ("ACTIVITY", *CONTROL_IDS),
        "ACTIVITY or fixed BS control ID required")
    values = dict(arm=arm, formation_on=formation_on, decision_on=decision_on,
        selection_at=selection_at, seed=seed, activity_targets=activity_targets,
        activity_targets_sha256=activity_targets_sha256, source_population=source_population,
        source_population_count=source_population_count, source_population_sha256=source_population_sha256,
        source_partition_sha256=source_partition_sha256)
    # Reuse the complete metadata boundary before examining any rank disposition.
    original = CohortSelection(group_id="ACTIVITY", resolved_prefix=(), **values)
    _require(type(dispositions) is tuple and all(type(row) is tuple and len(row) == 3
        and _text(row[0]) and row[1] in ("eligible", "excluded", "unknown") and _pin(row[2])
        for row in dispositions), "immutable code dispositions and evidence pins required")
    rows = {row[0]: row for row in dispositions}
    codes = tuple(row[0] for row in source_population)
    _require(len(rows) == len(dispositions) and set(rows) <= set(codes),
        "unique dispositions within the complete population required")
    _require(all(code not in rows or rows[code][1] != "excluded" for code in activity_targets),
        "original activity target cannot be excluded from the same-universe control")
    if group_id == "ACTIVITY":
        return original
    prefix: list[tuple[str, str, str]] = []
    eligible = 0
    if original.target_ceiling:
        for code in rank_control_codes(codes, control_id=group_id, formation_on=formation_on, seed=seed):
            _require(code in rows and rows[code][1] != "unknown",
                f"unresolved BS prefix code: {code}")
            row = rows[code]
            prefix.append(row)
            eligible += row[1] == "eligible"
            if eligible == original.target_ceiling:
                break
    return CohortSelection(group_id=group_id, resolved_prefix=tuple(prefix), **values)
