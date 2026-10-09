"""Explicit historical-availability assumptions and trading-session timing.

These immutable declarations preserve uncertainty; they do not certify a
source or permit promotion. The caller supplies the complete trading calendar.
No weekdays, calendar-day delays, retrieval times or data vintages are inferred.
Execution uses a daily-open price proxy after the 08:30 KST selection cutoff,
not an observed 09:05 fill.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone


KST = timezone(timedelta(hours=9))


def _aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware timestamp")
    return value


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be explicit nonempty text")


@dataclass(frozen=True)
class AvailabilityMetadata:
    """One field/version's provenance, with actual and modeled times separate.

    retrieved_at='unknown' and data_vintage=None explicitly preserve missing
    metadata. An assumption may supply available_at while the true historical
    source_public_available_at remains None. A known later actual release can
    never be overridden by an earlier modeled availability assumption.
    """

    observation_date: date
    available_at: datetime
    retrieved_at: datetime | str
    source: str
    is_final: bool | None
    data_vintage: str | None
    evidence_level: str
    source_public_available_at: datetime | None = None
    availability_policy: str | None = None
    evidence_reference: str | None = None

    def __post_init__(self) -> None:
        if type(self.observation_date) is not date:
            raise ValueError("observation_date must be an explicit date")
        _aware(self.available_at, "available_at")
        if self.available_at.astimezone(KST).date() < self.observation_date:
            raise ValueError("available_at cannot precede observation_date")
        if self.retrieved_at != "unknown":
            _aware(self.retrieved_at, "retrieved_at (or explicit 'unknown')")
        _text(self.source, "source")
        if self.is_final is not None and type(self.is_final) is not bool:
            raise ValueError("is_final must be boolean or explicitly unknown")
        if self.data_vintage is not None:
            _text(self.data_vintage, "data_vintage")
        if self.evidence_level not in {"confirmed", "inferred", "assumed"}:
            raise ValueError("evidence_level must be confirmed, inferred or assumed")
        if self.source_public_available_at is not None:
            actual = _aware(self.source_public_available_at, "source_public_available_at")
            if actual.astimezone(KST).date() < self.observation_date:
                raise ValueError("source availability cannot precede observation_date")
            if actual > self.available_at:
                raise ValueError("known actual availability is later than modeled available_at")
        elif self.evidence_level == "confirmed":
            raise ValueError("confirmed availability needs the actual source public timestamp")
        for name in ("availability_policy", "evidence_reference"):
            value = getattr(self, name)
            if self.evidence_level in {"inferred", "assumed"} or value is not None:
                _text(value, name)

    def require_available(self, selection_at: datetime, *, observation_date: date) -> None:
        """Validate the particular consumed field at its actual selection cutoff."""
        selection = _aware(selection_at, "selection_at")
        if (type(observation_date) is not date or self.observation_date != observation_date
                or self.observation_date > selection.astimezone(KST).date()):
            raise ValueError("future or mismatched observation_date at selection")
        if self.available_at > selection:
            raise ValueError("available_at is later than selection_at")


@dataclass(frozen=True)
class SessionLagPolicy:
    """Fixed D+1 or D+2 trading-session delay; paired arms share lag-2 horizon.

    paired=True fixes the formation population using the maximum authorized
    delay (two sessions), so D+1 cannot gain an extra terminal formation. The
    policy only changes timing; sizing, screens, holdings and costs stay in the
    existing consumers. Calendar completeness is a caller evidence obligation.
    """

    session_lag: int = 1
    paired: bool = False

    def __post_init__(self) -> None:
        if type(self.session_lag) is not int or self.session_lag not in (1, 2):
            raise ValueError("session_lag must be exactly 1 or 2 trading sessions")
        if type(self.paired) is not bool:
            raise ValueError("paired timing must be explicit boolean")

    @staticmethod
    def _calendar(calendar: Sequence[date]) -> None:
        if (not calendar or any(type(day) is not date for day in calendar)
                or any(a >= b for a, b in zip(calendar, calendar[1:]))):
            raise ValueError("complete trading calendar must be strictly increasing")

    def execution_on(self, calendar: Sequence[date], formation_index: int) -> date:
        self._calendar(calendar)
        if (type(formation_index) is not int or formation_index < 0
                or formation_index + self.session_lag >= len(calendar)):
            raise ValueError("formation needs the policy's later trading session")
        return calendar[formation_index + self.session_lag]

    def selection_at(self, calendar: Sequence[date], formation_index: int) -> datetime:
        return datetime.combine(self.execution_on(calendar, formation_index), time(8, 30), KST)

    def formation_indices(
        self, calendar: Sequence[date], *, lookback: int, holding_sessions: int, end_index: int,
    ) -> tuple[int, ...]:
        self._calendar(calendar)
        if (any(type(n) is not int or n < 1 for n in (lookback, holding_sessions))
                or type(end_index) is not int or not lookback < end_index < len(calendar)):
            raise ValueError("invalid formation horizon")
        horizon_lag = 2 if self.paired else self.session_lag
        return tuple(range(lookback, end_index - holding_sessions - horizon_lag + 1,
                           holding_sessions))
