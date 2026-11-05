"""Bounded EF frames over a caller-authenticated original calendar and reader."""
from __future__ import annotations

from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research import intraday_ed_sources as sources
from research.intraday_chart_inputs import ChartInputs, PeerContext, PreviousSessionLevels, _previous, build_chart_inputs
from research.intraday_chart_labels import ChartLabels, HORIZONS_MINUTES, _read_minute, build_chart_labels
from research.intraday_chart_tensors import ChartTensor, PeerTensor, chart_tensor, peer_tensor
from research.intraday_ed_inputs import MINUTE, Issue, SessionInputs, window
from research.intraday_trader_inputs import AuditedChunkReader, _groups, _require


FEATURE_OFFSETS = tuple(range(60, 316, 5))
ENTRY_OFFSETS = tuple(range(60, 301, 15))
QUOTE_OFFSETS = tuple(range(60, 331, 5))
BEFORE_DAY = "ef_chart_day_before"
AT_CLOCK = "ef_chart_clock"
AFTER_DAY = "ef_chart_day_after"
_BUFFER = 15 * MINUTE
_GROSS_HORIZON = max(HORIZONS_MINUTES) * MINUTE


@dataclass(frozen=True)
class ChartIssues:
    latest: tuple[Issue, ...]
    volume: tuple[Issue, ...]
    coarse: tuple[Issue, ...]
    previous: tuple[Issue, ...]
    previous_expected_opening: datetime | None


@dataclass(frozen=True)
class ScenarioPeer:
    scenario: str
    tensor: PeerTensor | None
    expected_peer_ids: tuple[str, ...]
    common_peer_ids: tuple[str, ...]
    denominator: int
    issues: tuple[Issue, ...]


@dataclass(frozen=True)
class ChartFrame:
    day: str
    symbol: str
    offset: int
    bar_end: datetime
    decision_at: datetime
    entry_clock_eligible: bool
    chart: ChartTensor | None
    chart_issues: ChartIssues | None
    peers: tuple[ScenarioPeer, ...]
    labels: ChartLabels | None
    execution_at: datetime
    execution_open: Fraction | None
    execution_state: Literal["available", "unavailable", "outside_clock"]
    execution_issue: Issue | None
    execution_mature_at: datetime | None


@dataclass(frozen=True)
class ChartDay:
    day: str
    opening: datetime
    closing: datetime
    targets: tuple[sources.TargetCoordinate, ...]
    provenance: tuple[tuple[str, bytes], ...]
    frames: Iterator[ChartFrame]


def _issues(chart: ChartInputs, session: SessionInputs, offset: int) -> ChartIssues:
    # A price gap can prevent ChartInputs from retaining the independent volume
    # observation at that minute. Keep the actual CX state in that case.
    volume = (window(session, offset - 60, 60, chart.decision_at, price=False, volume=True).issues
              if chart.issues else tuple(Issue(chart.symbol, row.start_at, f"volume_{row.state}")
                                         for row in chart.volume if row.state != "observed"))
    return ChartIssues(chart.issues, volume, tuple(issue for bar in chart.coarse for issue in bar.issues),
                       chart.previous.issues, chart.previous.expected_opening)


def _scenario_peers(
    charts: dict[str, ChartInputs], rosters: dict[str, tuple[str, ...]],
) -> dict[str, list[ScenarioPeer]]:
    # Chart body/anchor-close channels already equal the two exact peer returns.
    complete = {symbol: tuple((row.channels[4], row.channels[3], int(row.channels[3] > 0))
                              for row in chart.sequence)
                for symbol, chart in charts.items() if chart.sequence is not None}
    result: dict[str, list[ScenarioPeer]] = {symbol: [] for symbol in charts}
    by_roster: dict[tuple[str, ...], list[ScenarioPeer]] = {}
    zero = ((Fraction(0), Fraction(0), 0),) * 60
    for scenario, roster in rosters.items():
        if roster in by_roster:
            for symbol, peer in zip(roster, by_roster[roster], strict=True):
                result[symbol].append(replace(peer, scenario=scenario))
            continue
        projected: list[ScenarioPeer] = []
        common = tuple(symbol for symbol in roster if symbol in complete)
        unavailable = tuple(symbol for symbol in roster if symbol not in complete)
        totals = tuple((sum(row[0] for row in rows), sum(row[1] for row in rows), sum(row[2] for row in rows))
                       for rows in zip(*(complete[symbol] for symbol in common), strict=True)) if len(common) >= 5 else ()
        for symbol in roster:
            chart = charts[symbol]
            expected_peers = tuple(peer for peer in roster if peer != symbol)
            common_peers = tuple(peer for peer in common if peer != symbol)
            count = len(common_peers)
            issues = tuple(issue for peer in unavailable if peer != symbol for issue in charts[peer].issues)
            oc = cumulative = positive = None
            if count >= 5:
                channels = tuple(((total[0] - own[0]) / count, (total[1] - own[1]) / count,
                                  Fraction(total[2] - own[2], count))
                                 for total, own in zip(totals, complete.get(symbol, zero), strict=True))
                oc, cumulative, positive = zip(*channels, strict=True)
            else:
                issues += (Issue(symbol, chart.bar_end, "fewer_than_five_complete_original_peers"),)
            context = PeerContext(symbol, chart.opening, chart.bar_end, chart.decision_at,
                                  expected_peers, common_peers, len(expected_peers),
                                  "available" if oc is not None else "unavailable",
                                  oc, cumulative, positive, issues)
            peer = ScenarioPeer(scenario, peer_tensor(context), expected_peers, common_peers,
                                len(expected_peers), issues)
            result[symbol].append(peer)
            projected.append(peer)
        by_roster[roster] = projected
    return result


def _frames(
    day: str, opening: datetime, closing: datetime, current: dict[str, SessionInputs],
    prior_levels: dict[str, PreviousSessionLevels], rosters: dict[str, tuple[str, ...]],
    checkpoint: Callable[[str], object],
) -> Iterator[ChartFrame]:
    cutoff = closing + 3 * MINUTE
    continuous_end = closing - _BUFFER
    for offset in QUOTE_OFFSETS:
        checkpoint(AT_CLOCK)
        bar_end = opening + offset * MINUTE
        decision_at = bar_end + 2 * MINUTE
        execution_at = bar_end + 3 * MINUTE
        has_feature_clock = offset in FEATURE_OFFSETS and bar_end <= closing
        entry_clock_eligible = offset in ENTRY_OFFSETS and execution_at + _GROSS_HORIZON < continuous_end
        charts = {symbol: replace(build_chart_inputs(session, bar_end, decision_at,
                                                     entry_grid=offset in ENTRY_OFFSETS),
                                  previous=prior_levels[symbol])
                  for symbol, session in current.items()} if has_feature_clock else {}
        peers = _scenario_peers(charts, rosters) if charts else {}
        for symbol, session in current.items():
            chart = charts.get(symbol)
            labels = (build_chart_labels(session, bar_end, decision_at, label_cutoff=cutoff)
                      if has_feature_clock and offset in ENTRY_OFFSETS else None)
            execution_open = execution_mature_at = None
            if execution_at >= continuous_end:
                execution_state = "outside_clock"
                execution_issue = Issue(symbol, execution_at, "execution_outside_continuous_cash_clock")
            else:
                observed, execution_issue = _read_minute(session, execution_at, cutoff)
                execution_state = "available" if observed is not None else "unavailable"
                if observed is not None:
                    execution_open = Fraction(observed.ohlc[0])
                    execution_mature_at = observed.available_at
            yield ChartFrame(day, symbol, offset, bar_end, decision_at, entry_clock_eligible,
                             chart_tensor(chart) if chart is not None else None,
                             _issues(chart, session, offset) if chart is not None else None,
                             tuple(peers.get(symbol, ())), labels, execution_at, execution_open,
                             execution_state, execution_issue, execution_mature_at)
        # Do not carry the prior clock's full chart/peer projections into the next one.
        del charts, chart, peers, labels


def iter_chart_days(
    universe: sources.UniverseCalendar, raw_reader: AuditedChunkReader,
    checkpoint: Callable[[str], object],
) -> Iterator[ChartDay]:
    """Validate the whole original scope, then stream one CC/CX pair per union key.

    Exhaust a day's frames before requesting the next day. Only the current and
    preceding original date supply decoded minutes. Archive authenticity and
    release authorization belong to the caller.
    """
    groups = _groups(universe)
    _require(callable(getattr(raw_reader, "load_raw_chunk", None))
             and callable(getattr(raw_reader, "assert_unchanged", None)), "raw_reader_contract_required")
    _require(callable(checkpoint), "checkpoint_callable_required")
    symbols_by_day: dict[str, list[str]] = {day: [] for day in universe.sessions}
    for day, symbol in sorted(groups):
        symbols_by_day[day].append(symbol)
    targets_by_day: dict[str, list[sources.TargetCoordinate]] = {day: [] for day in universe.sessions}
    for target in universe.targets:
        targets_by_day[target.session].append(target)

    previous: dict[str, SessionInputs] = {}
    previous_opening = None
    for day in universe.sessions:
        opening = sources.calendar_source.session_open(day)
        closing = sources.calendar_source.session_close(day)
        targets = tuple(targets_by_day[day])
        rosters = {scenario: tuple(sorted(target.symbol for target in targets if target.scenario == scenario))
                   for scenario in ("S1", "S2")}
        current = {}
        provenance = []
        checkpoint(BEFORE_DAY)
        raw_reader.assert_unchanged()
        try:
            for symbol in symbols_by_day[day]:
                view = sources.session_inputs(symbol, day, raw_reader.load_raw_chunk("CC", symbol, day),
                                              raw_reader.load_raw_chunk("CX", symbol, day))
                current[symbol] = view.session
                provenance.append((symbol, view.metadata))
            del view
            first_decision = opening + (FEATURE_OFFSETS[0] + 2) * MINUTE
            prior_levels = {symbol: _previous(session, first_decision, previous_opening, previous.get(symbol))
                            for symbol, session in current.items()}
            raw_reader.assert_unchanged()
            exhausted = False

            def day_frames() -> Generator[ChartFrame, None, None]:
                nonlocal exhausted
                yield from _frames(day, opening, closing, current, prior_levels, rosters, checkpoint)
                exhausted = True

            frames = day_frames()
            try:
                yield ChartDay(day, opening, closing, targets, tuple(provenance), frames)
                _require(exhausted, "chart_day_frames_must_be_consumed")
            finally:
                frames.close()
        finally:
            raw_reader.assert_unchanged()
            checkpoint(AFTER_DAY)
        previous, previous_opening = current, opening
