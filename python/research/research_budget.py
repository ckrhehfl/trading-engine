"""Fail-closed resource checks for one saved-input research process.

Create one budget before the pipeline and keep it across producer and verifier.
Call ``checkpoint`` before/after each stage and inside expensive loops, then
``finish`` before declaring success. CPU ceilings apply to absolute process
CPU, including all threads; baseline deltas are diagnostic, never a new CPU
allowance. Subprocess work is outside this single-process contract.

The OS hard CPU limit can kill without running Python cleanup. Callers must
durably preserve started/partial evidence before work and save ``error.receipt``
on a caught BudgetError. This module performs no experiment or evidence writes.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import resource
import time
from typing import Any


class BudgetError(RuntimeError):
    """A resource failure, with the last measured evidence for preservation."""

    def __init__(self, message: str, receipt: dict[str, Any]) -> None:
        super().__init__(message)
        self.receipt = receipt


def _address_space_bytes() -> int:
    """Linux current virtual size; RLIMIT_AS does not evict existing mappings."""
    pages = int(Path("/proc/self/statm").read_text().split()[0])
    return pages * os.sysconf("SC_PAGE_SIZE")


@dataclass(frozen=True)
class _Sample:
    pid: int
    monotonic: float
    process_cpu: float
    user_cpu: float
    system_cpu: float
    peak_rss_kib: int
    address_space_bytes: int
    cpu_limit: tuple[int, int]
    as_limit: tuple[int, int]
    nice: int

    def counters(self) -> dict[str, float | int]:
        return {
            "pid": self.pid,
            "monotonic_seconds": self.monotonic,
            "cpu_process_seconds": self.process_cpu,
            "cpu_user_seconds": self.user_cpu,
            "cpu_system_seconds": self.system_cpu,
            "cpu_rusage_seconds": self.user_cpu + self.system_cpu,
            "peak_rss_kib": self.peak_rss_kib,
            "address_space_bytes": self.address_space_bytes,
        }


class ResearchBudget:
    """One non-resettable pipeline budget with explicit registered CPU scope.

    Limits are intentionally not restored: the process is dedicated to this
    research pipeline, and restoring them could weaken its remaining checks.
    The current Linux process identity and exact limit readbacks must hold at
    every checkpoint. A failure remains sticky even if later readings recover.
    The fixed EB scopes and the EC P01-P03 scope permit their operator-approved CPU ceiling;
    other parameters may only tighten the ordinary ceilings. A scope is the
    caller's explicit assertion backed by reviewed registration, not proof of
    permission or a replacement for that review.
    """

    def __init__(
        self,
        *,
        cpu_seconds: int = 300,
        address_space_bytes: int = 512 * 1024**2,
        wall_seconds: float = 1200,
        operator_cpu_scope: str | None = None,
    ) -> None:
        if operator_cpu_scope is not None and (
            type(operator_cpu_scope) is not str
            or operator_cpu_scope not in (
                "EB-EA-A-operator-2026-10-28",
                "EB-EA-B-operator-2026-10-28",
                "EC-P01-P03-operator-2026-10-29",
            )
        ):
            raise ValueError("operator_cpu_scope must be an approved fixed study scope or None")
        cpu_ceiling = 300 if operator_cpu_scope is None else 600
        if type(cpu_seconds) is not int or not 1 <= cpu_seconds <= cpu_ceiling:
            raise ValueError(f"cpu_seconds must be an integer in [1, {cpu_ceiling}]")
        if type(address_space_bytes) is not int or not 1 <= address_space_bytes <= 512 * 1024**2:
            raise ValueError("address_space_bytes must be an integer in [1, 512 MiB]")
        if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or not 0 < wall_seconds <= 1200:
            raise ValueError("wall_seconds must be finite and in (0, 1200]")
        self._cpu_seconds = cpu_seconds
        self._operator_cpu_scope = operator_cpu_scope
        self._address_space_limit = address_space_bytes
        self._wall_seconds = wall_seconds
        self._failure: BudgetError | None = None
        self._finished = False
        self._baseline = self._sample()
        self._last = self._baseline
        for name, current, requested in (
            ("RLIMIT_CPU", self._baseline.cpu_limit, cpu_seconds),
            ("RLIMIT_AS", self._baseline.as_limit, address_space_bytes),
        ):
            if any(value != resource.RLIM_INFINITY and value < requested for value in current):
                self._fail("configure", [f"refusing to raise existing {name}"], self._baseline)
        if self._baseline.nice > 10:
            self._fail("configure", ["refusing to raise existing scheduling priority"], self._baseline)
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            resource.setrlimit(resource.RLIMIT_AS, (address_space_bytes, address_space_bytes))
            os.setpriority(os.PRIO_PROCESS, 0, 10)
        except (OSError, ValueError) as error:
            self._fail("configure", [f"limit setup failed: {type(error).__name__}"], self._sample())
        self.checkpoint("configured")

    @staticmethod
    def _sample() -> _Sample:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return _Sample(
            pid=os.getpid(),
            monotonic=time.monotonic(),
            process_cpu=time.process_time(),
            user_cpu=usage.ru_utime,
            system_cpu=usage.ru_stime,
            peak_rss_kib=usage.ru_maxrss,
            address_space_bytes=_address_space_bytes(),
            cpu_limit=resource.getrlimit(resource.RLIMIT_CPU),
            as_limit=resource.getrlimit(resource.RLIMIT_AS),
            nice=os.getpriority(os.PRIO_PROCESS, 0),
        )

    def _receipt(self, stage: str, sample: _Sample, violations: list[str], status: str) -> dict[str, Any]:
        baseline = self._baseline
        receipt = {
            "status": status,
            "stage": stage,
            "scope": "single_process_including_threads",
            "limits": {
                "cpu_seconds": self._cpu_seconds,
                "address_space_bytes": self._address_space_limit,
                "wall_seconds": self._wall_seconds,
                "nice": 10,
            },
            "baseline": baseline.counters(),
            "absolute": sample.counters(),
            "delta": {
                "wall_seconds": sample.monotonic - baseline.monotonic,
                "cpu_process_seconds": sample.process_cpu - baseline.process_cpu,
                "cpu_user_seconds": sample.user_cpu - baseline.user_cpu,
                "cpu_system_seconds": sample.system_cpu - baseline.system_cpu,
                "cpu_rusage_seconds": sample.user_cpu + sample.system_cpu - baseline.user_cpu - baseline.system_cpu,
            },
            "readback": {
                "rlimit_cpu": list(sample.cpu_limit),
                "rlimit_as": list(sample.as_limit),
                "nice": sample.nice,
            },
            "violations": violations,
        }
        if self._operator_cpu_scope is not None:
            receipt["operator_cpu_scope"] = self._operator_cpu_scope
        return receipt

    def _fail(self, stage: str, violations: list[str], sample: _Sample) -> None:
        self._last = sample
        self._failure = BudgetError("; ".join(violations), self._receipt(stage, sample, violations, "failed"))
        raise self._failure

    def checkpoint(self, stage: str) -> dict[str, Any]:
        """Measure and reject overruns/drift without restarting any allowance."""
        if self._failure is not None:
            raise self._failure
        if self._finished:
            raise BudgetError("budget already finished", self._receipt(stage, self._last, ["budget already finished"], "failed"))
        if not isinstance(stage, str) or not stage:
            raise ValueError("checkpoint stage must be a nonempty string")
        try:
            sample = self._sample()
        except (OSError, ValueError, IndexError) as error:
            self._fail(stage, [f"resource measurement failed: {type(error).__name__}"], self._last)
        violations = []
        if sample.pid != self._baseline.pid:
            violations.append("process identity changed")
        for name in ("monotonic", "process_cpu", "user_cpu", "system_cpu"):
            value = getattr(sample, name)
            if not math.isfinite(value) or value < 0 or value < getattr(self._last, name):
                violations.append(f"invalid or decreasing {name}")
        if sample.process_cpu >= self._cpu_seconds:
            violations.append("absolute process CPU budget exhausted")
        if sample.user_cpu + sample.system_cpu >= self._cpu_seconds:
            violations.append("absolute rusage CPU budget exhausted")
        if sample.monotonic - self._baseline.monotonic >= self._wall_seconds:
            violations.append("wall budget exhausted")
        if sample.address_space_bytes > self._address_space_limit:
            violations.append("address space budget exceeded")
        if sample.cpu_limit != (self._cpu_seconds, self._cpu_seconds):
            violations.append("RLIMIT_CPU readback drift")
        if sample.as_limit != (self._address_space_limit, self._address_space_limit):
            violations.append("RLIMIT_AS readback drift")
        if sample.nice != 10:
            violations.append("nice readback drift")
        if violations:
            self._fail(stage, violations, sample)
        self._last = sample
        return self._receipt(stage, sample, [], "within_budget")

    def finish(self) -> dict[str, Any]:
        """Return a successful receipt only after a fresh final conformance check."""
        receipt = self.checkpoint("finish")
        self._finished = True
        receipt["status"] = "complete"
        return receipt
