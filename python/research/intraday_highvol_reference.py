"""Independent EG coverage mathematics over caller-authenticated raw chunks.

Only record datatypes and wire serialization are shared with the producer. The
minute parser is the existing independent ED reconstruction; window, historical
rank, censoring and report mathematics are rebuilt here. This module neither
acquires data nor grants permission to open a market archive.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING

from research import intraday_ed_sources as sources
from research.intraday_ed_inputs import MINUTE, STATES, Issue, Minute, SessionInputs
from research.intraday_ed_snapshot_verify import reconstruct_session
from research.intraday_highvol_events import Episode, EpisodeSummary, HighVolDecision, WindowMetric

if TYPE_CHECKING:
    from research.intraday_highvol_coverage import CoverageSeal
    from research.intraday_highvol_population import CoverageRecord, MinuteCoverage
    from research.intraday_trader_inputs import AuditedChunkReader


GRID = tuple(15 + 5 * index for index in range(58))


class CoverageVerificationRefusal(ValueError):
    """A scientific disagreement is an error, never an unknown or zero result."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise CoverageVerificationRefusal(reason)


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def reference_window(session: SessionInputs, offset: int) -> WindowMetric:
    """Compute anchored sum-of-squares independently of the producer kernel."""
    _require(type(session) is SessionInputs and _aware(session.opening)
             and _aware(session.closing) and session.closing > session.opening,
             "reference_session_geometry")
    _require(type(offset) is int and offset in GRID, "reference_window_offset")
    end = session.opening + offset * MINUTE
    decision = end + 2 * MINUTE
    if end > session.closing:
        reason = (Issue(session.symbol, end, "outside_session_clock"),)
        return WindowMetric(session.symbol, session.opening, offset, decision, "outside_clock",
                            None, None, None, None, None, None, None, None, reason, reason)

    price_issues, volume_issues, rows = [], [], []
    for index in range(offset - 15, offset):
        at = session.opening + index * MINUTE
        row = session.minutes.get(at)
        if row is None:
            issue = Issue(session.symbol, at, "missing_minute")
            price_issues.append(issue)
            volume_issues.append(issue)
            continue
        _require(type(row) is Minute and row.symbol == session.symbol and row.start_at == at
                 and _aware(row.available_at) and at + 3 * MINUTE <= row.available_at <= decision,
                 "reference_minute_identity_or_availability")
        rows.append(row)
        for name, state, value, issues in (
            ("price", row.price_state, row.ohlc, price_issues),
            ("volume", row.volume_state, row.shares, volume_issues),
        ):
            _require(type(state) is str and state in STATES, "reference_minute_state")
            if state != "observed":
                _require(value is None, "reference_unknown_payload")
                issues.append(Issue(session.symbol, at, name + "_" + state))
            elif name == "price":
                _require(type(value) is tuple and len(value) == 4
                         and all(type(number) is Decimal and number.is_finite() and number > 0
                                 for number in value), "reference_ohlc_numbers")
                op, high, low, close = value
                _require(low <= op <= high and low <= close <= high, "reference_ohlc_order")
            else:
                _require(type(value) is Decimal and value.is_finite() and value >= 0,
                         "reference_volume_number")

    energy = move = efficiency = zero = span = price_at = None
    if not price_issues:
        _require(len(rows) == 15, "reference_price_length")
        anchor = Fraction(rows[0].ohlc[0])
        before = anchor
        squared = distance = Fraction(0)
        highs, lows = [], []
        for row in rows:
            close = Fraction(row.ohlc[3])
            step = close - before
            squared += step ** 2
            distance += abs(step)
            before = close
            highs.append(Fraction(row.ohlc[1]))
            lows.append(Fraction(row.ohlc[2]))
        energy, move = squared / anchor ** 2, (before - anchor) / anchor
        zero = not distance
        efficiency = abs(before - anchor) / distance if distance else None
        span = (max(highs) - min(lows)) / anchor
        price_at = max(row.available_at for row in rows)
    shares = volume_at = None
    if not volume_issues:
        _require(len(rows) == 15, "reference_volume_length")
        shares = sum((Fraction(row.shares) for row in rows), Fraction(0))
        volume_at = max(row.available_at for row in rows)
    return WindowMetric(session.symbol, session.opening, offset, decision,
                        "unavailable" if price_issues else "available", price_at, energy, move,
                        efficiency, zero, span, shares, volume_at,
                        tuple(price_issues), tuple(volume_issues))


def reference_decision(current: WindowMetric, expected: tuple[datetime, ...],
                       history: Mapping[datetime, WindowMetric | None], *,
                       absent_reasons: Mapping[datetime, str] | None = None) -> HighVolDecision:
    """Use the exact declared original-calendar prefix, never older substitutes."""
    _require(type(expected) is tuple and len(expected) <= 20
             and all(_aware(at) and at.date() < current.opening.date() for at in expected)
             and tuple(sorted(set(expected))) == expected
             and len({at.date() for at in expected}) == len(expected), "reference_prior_calendar")
    _require(isinstance(history, Mapping) and set(history) <= set(expected),
             "reference_history_scope")
    absent = {} if absent_reasons is None else absent_reasons
    _require(set(absent) <= set(expected), "reference_absent_scope")
    values, issues = [], []
    for at in expected:
        metric = history.get(at)
        if metric is None:
            values.append(None)
            issues.append(Issue(current.symbol, at + (current.offset - 15) * MINUTE,
                                absent.get(at, "prior_window_absent")))
        else:
            _require(metric.symbol == current.symbol and metric.opening == at
                     and metric.offset == current.offset and metric.decision_at < current.decision_at
                     and at not in absent, "reference_prior_metric_identity")
            values.append(metric.energy)
            if metric.price_status != "available":
                issues.append(Issue(current.symbol, at + (current.offset - 15) * MINUTE,
                                    "prior_window_price_unavailable"))
    complete = len(values) == 20 and not issues and all(value is not None for value in values)
    threshold = sorted(values)[15] if complete else None
    status, reason = "unavailable", "current_price_unavailable"
    if current.price_status == "available":
        if len(expected) < 20:
            reason = "insufficient_original_history"
        elif not complete:
            reason = "prior_history_unavailable"
        elif current.energy > 0 and current.energy > threshold:
            status, reason = "high", "strictly_above_q80"
        else:
            status, reason = "not_high", "not_strictly_above_q80"
    tie = None if not complete or current.energy is None else current.energy == threshold
    return HighVolDecision(current, expected, tuple(values), threshold, status, reason, tie,
                           threshold == 0 if complete else None, tuple(issues))


def reference_episodes(decisions: tuple[HighVolDecision, ...]) -> EpisodeSummary:
    """Partition by observed quiet windows, then classify each surviving block."""
    _require(type(decisions) is tuple and len(decisions) == 58, "reference_full_grid")
    first = decisions[0].current
    for offset, decision in zip(GRID, decisions, strict=True):
        _require((decision.current.symbol, decision.current.opening, decision.current.offset)
                 == (first.symbol, first.opening, offset)
                 and decision.status in {"high", "not_high", "unavailable"},
                 "reference_episode_coordinates")
    quiet = [-1] + [index for index, row in enumerate(decisions) if row.status == "not_high"] + [58]
    episodes = []
    for left, right in zip(quiet, quiet[1:]):
        highs = [index for index in range(left + 1, right) if decisions[index].status == "high"]
        if not highs:
            continue
        start, finish = highs[0], highs[-1]
        bridges = tuple(GRID[index] for index in range(start + 1, finish)
                        if decisions[index].status == "unavailable")
        trailing = tuple(GRID[index] for index in range(finish + 1, right))
        censored = start == 0 or decisions[start - 1].status != "not_high"
        episodes.append(Episode(tuple(GRID[index] for index in highs), censored,
                                bridges, trailing, right == 58 or bool(trailing)))
    unknown = tuple(row.current.offset for row in decisions if row.status == "unavailable")
    confirmed = sum(not episode.left_censored for episode in episodes)
    left_censored = sum(episode.left_censored for episode in episodes)
    return EpisodeSummary(first.symbol, first.opening, tuple(episodes), unknown,
                          sum(row.status == "high" for row in decisions), confirmed, left_censored,
                          None if unknown or left_censored else confirmed)


def reference_minute_coverage(session: SessionInputs, start: int, end: int) -> MinuteCoverage:
    """Count the declared minute support separately from event readiness."""
    from research.intraday_highvol_population import MinuteCoverage

    duration = int((session.closing - session.opening) // MINUTE)
    _require(type(start) is int and type(end) is int and 0 <= start <= end <= duration,
             "reference_minute_coverage_geometry")
    prices, volumes = Counter(), Counter()
    zero = 0
    for offset in range(start, end):
        at = session.opening + offset * MINUTE
        row = session.minutes.get(at)
        if row is None:
            prices["missing_minute"] += 1
            volumes["missing_minute"] += 1
            continue
        _require(type(row) is Minute and row.symbol == session.symbol and row.start_at == at
                 and _aware(row.available_at) and row.available_at >= at + 3 * MINUTE,
                 "reference_coverage_identity_or_clock")
        for kind, state, value, counter in (
            ("price", row.price_state, row.ohlc, prices),
            ("volume", row.volume_state, row.shares, volumes),
        ):
            _require(type(state) is str and state in STATES, "reference_coverage_state")
            if state != "observed":
                _require(value is None, "reference_coverage_unknown_payload")
            elif kind == "price":
                _require(type(value) is tuple and len(value) == 4
                         and all(type(number) is Decimal and number.is_finite() and number > 0
                                 for number in value), "reference_coverage_ohlc_numbers")
                op, high, low, close = value
                _require(low <= min(op, close) <= max(op, close) <= high,
                         "reference_coverage_ohlc_order")
            else:
                _require(type(value) is Decimal and value.is_finite() and value >= 0,
                         "reference_coverage_volume_number")
            reason = ("late_at_assumed_clock" if row.available_at > at + 3 * MINUTE
                      else "observed" if state == "observed" else kind + "_" + state)
            counter[reason] += 1
            if kind == "volume" and reason == "observed" and value == 0:
                zero += 1
    return MinuteCoverage(start, end, tuple(sorted(prices.items())), tuple(sorted(volumes.items())), zero)


def iter_reference_records(universe: sources.UniverseCalendar, reader: AuditedChunkReader,
                           checkpoint: Callable[[str], object]) -> Iterator[CoverageRecord]:
    """Reparse original raw chunks with an independent twenty-day summary cache."""
    from research.intraday_highvol_population import CoverageRecord
    from research.intraday_trader_inputs import _groups

    # Existing metadata validation is a shared boundary, not scientific output.
    validated = _groups(universe)
    population = defaultdict(list)
    for (day, symbol), targets in validated.items():
        population[day].append((symbol, tuple(sorted(targets, key=lambda target: (target.scenario, target.rank)))))
    previous: deque[tuple[str, dict]] = deque(maxlen=20)
    for day in universe.sessions:
        expected = tuple(sources.calendar_source.session_open(past_day) for past_day, _ in previous)
        history = {sources.calendar_source.session_open(past_day): values for past_day, values in previous}
        new_day = {}
        for symbol, targets in sorted(population[day]):
            checkpoint("eg_reference_before_session")
            reader.assert_unchanged()
            try:
                price = reader.load_raw_chunk("CC", symbol, day)
                volume = reader.load_raw_chunk("CX", symbol, day)
                view = reconstruct_session(symbol, day, price, volume)
                session = view.session
                metrics = tuple(reference_window(session, offset) for offset in GRID)
                current = []
                for index, metric in enumerate(metrics):
                    prior, absence = {}, {}
                    for opening in expected:
                        old = history[opening].get(symbol)
                        if old is None:
                            absence[opening] = "prior_symbol_outside_preserved_population"
                        elif not old[0]:
                            absence[opening] = "prior_price_chunk_absent"
                        else:
                            prior[opening] = old[1][index]
                    current.append(reference_decision(metric, expected, prior, absent_reasons=absence))
                decisions = tuple(current)
                duration = int((session.closing - session.opening) // MINUTE)
                split = min(60, duration)
                support = (reference_minute_coverage(session, 0, split),
                           reference_minute_coverage(session, split, duration))
                result = CoverageRecord(day, symbol, session.opening, session.closing, targets,
                                        view.metadata, price is not None, volume is not None,
                                        support, decisions, reference_episodes(decisions))
                new_day[symbol] = (price is not None, metrics)
                del price, volume, view, session
            finally:
                reader.assert_unchanged()
                checkpoint("eg_reference_after_session")
            yield result
        previous.append((day, new_day))


def _week(day: str) -> str:
    year, week, _ = date.fromisoformat(day).isocalendar()
    return f"{year}-W{week:02d}"


class ReferenceSummary:
    """Independent accumulators for every counter published in the report."""

    def __init__(self, sessions: tuple[str, ...]):
        self.sessions = sessions
        self.rows = 0
        self.weeks = Counter(_week(day) for day in sessions)
        self.scenarios = {}
        for scenario in ("S1", "S2"):
            self.scenarios[scenario] = {
                "base": Counter(), "minutes": defaultdict(Counter), "windows": defaultdict(Counter),
                "episodes": Counter(), "early_episodes": Counter(),
                "weekly": {week: Counter() for week in sorted(self.weeks)},
            }

    def add(self, record: CoverageRecord) -> None:
        self.rows += 1
        for target in record.targets:
            account = self.scenarios[target.scenario]
            account["base"]["target_days"] += 1
            account["base"]["source_absent_price_days"] += not record.price_chunk_present
            account["base"]["source_absent_volume_days"] += not record.volume_chunk_present
            week = account["weekly"][_week(record.session)]
            week["original_target_days"] += 1
            for name, segment in zip(("first_hour", "remaining"), record.minute_coverage, strict=True):
                bucket = account["minutes"][name]
                bucket["expected_minutes"] += segment.end_offset_exclusive - segment.start_offset
                bucket["zero_volume_minutes"] += segment.zero_volume_minutes
                for state, count in segment.price_states:
                    bucket[("price_states", state)] += count
                for state, count in segment.volume_states:
                    bucket[("volume_states", state)] += count
            for decision in record.decisions:
                current = decision.current
                bucket = account["windows"]["first_hour" if current.offset <= 60 else "later"]
                bucket["expected_windows"] += 1
                bucket[("price_statuses", current.price_status)] += 1
                bucket[("decision_statuses", decision.status)] += 1
                bucket[("decision_reasons", decision.reason)] += 1
                bucket["threshold_ties"] += decision.threshold_tie is True
                bucket["zero_baselines"] += decision.baseline_zero is True
                bucket["current_zero_paths"] += current.zero_path is True
                bucket["known_volume_windows"] += current.shares is not None
                bucket["zero_volume_windows"] += current.shares == 0
                for issue in decision.history_issues:
                    bucket[("history_issue_counts", issue.reason)] += 1
            episodes = account["episodes"]
            episodes["unavailable_windows"] += len(record.episodes.unavailable_offsets)
            episodes["days_exact_onset_unknown"] += record.episodes.exact_onset_count is None
            week["unavailable_windows"] += len(record.episodes.unavailable_offsets)
            for episode in record.episodes.episodes:
                episodes["observed_clusters"] += 1
                episodes["confirmed_onsets"] += not episode.left_censored
                episodes["left_censored_clusters"] += episode.left_censored
                episodes["gap_linked_clusters"] += bool(episode.unknown_bridge_offsets)
                episodes["right_censored_clusters"] += episode.right_censored
                week["observed_clusters"] += 1
                week["confirmed_onsets"] += not episode.left_censored
                if episode.high_offsets[0] <= 60:
                    early = account["early_episodes"]
                    early["observed_clusters"] += 1
                    early["confirmed_onsets"] += not episode.left_censored
                    early["left_censored_clusters"] += episode.left_censored
                    early["gap_linked_clusters"] += bool(episode.unknown_bridge_offsets)
                    week["first_hour_observed_clusters"] += 1
                    week["first_hour_confirmed_onsets"] += not episode.left_censored

    @staticmethod
    def _section(counter: Counter, scalar_names: tuple[str, ...], map_names: tuple[str, ...] = ()) -> dict:
        section = {name: counter[name] for name in scalar_names}
        for name in map_names:
            section[name] = dict(sorted((key[1], count) for key, count in counter.items()
                                        if type(key) is tuple and key[0] == name))
        return section

    def result(self) -> dict:
        scenarios = {}
        for name, value in self.scenarios.items():
            report = self._section(value["base"],
                                   ("target_days", "source_absent_price_days", "source_absent_volume_days"))
            report["minute_segments"] = {segment: self._section(value["minutes"][segment],
                ("expected_minutes", "zero_volume_minutes"), ("price_states", "volume_states"))
                for segment in ("first_hour", "remaining")}
            report["window_segments"] = {segment: self._section(value["windows"][segment],
                ("expected_windows", "threshold_ties", "zero_baselines", "current_zero_paths",
                 "known_volume_windows", "zero_volume_windows"),
                ("price_statuses", "decision_statuses", "decision_reasons", "history_issue_counts"))
                for segment in ("first_hour", "later")}
            report["episodes"] = self._section(value["episodes"],
                ("observed_clusters", "confirmed_onsets", "left_censored_clusters", "gap_linked_clusters",
                 "right_censored_clusters", "unavailable_windows", "days_exact_onset_unknown"))
            report["first_hour_episodes"] = self._section(value["early_episodes"],
                ("observed_clusters", "confirmed_onsets", "left_censored_clusters", "gap_linked_clusters"))
            report["weekly"] = [{"iso_week": week, "calendar_sessions": self.weeks[week],
                **self._section(value["weekly"][week], ("original_target_days", "observed_clusters", "confirmed_onsets",
                    "first_hour_observed_clusters", "first_hour_confirmed_onsets", "unavailable_windows"))}
                for week in sorted(self.weeks)]
            report["observed_clusters_weeks_ge15"] = sum(row["observed_clusters"] >= 15 for row in report["weekly"])
            report["confirmed_onsets_weeks_ge15"] = sum(row["confirmed_onsets"] >= 15 for row in report["weekly"])
            scenarios[name] = report
        return {"schema": "EG-highvol-coverage-report-v1", "calendar_sessions": len(self.sessions),
                "union_records": self.rows,
                "claim": "predecision coverage and observed high clusters, not trades or profit",
                "scenarios": scenarios}


def _exact_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if is_dataclass(right) and not isinstance(right, type):
        return all(_exact_equal(getattr(left, item.name), getattr(right, item.name)) for item in fields(right))
    if isinstance(right, (tuple, list)):
        return len(left) == len(right) and all(_exact_equal(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(right, dict):
        return left.keys() == right.keys() and all(_exact_equal(left[key], right[key]) for key in right)
    if isinstance(right, datetime):
        return left.isoformat() == right.isoformat()
    if isinstance(right, Decimal):
        return left.as_tuple() == right.as_tuple()
    return left == right


def verify_records(stored: Iterable[dict], universe: sources.UniverseCalendar, reader: AuditedChunkReader,
                   checkpoint: Callable[[str], object]) -> dict:
    """Consume every saved row and compare it with separately reconstructed facts.

    The caller's storage reader authenticates the entire compressed artifact,
    header/footer and EOF. This function supplies independent scientific values
    and the independently accumulated report, not a storage authenticity claim.
    """
    from research.intraday_highvol_coverage import project

    actual = iter(stored)
    summary = ReferenceSummary(universe.sessions)
    for expected in iter_reference_records(universe, reader, checkpoint):
        row = next(actual, None)
        _require(row is not None and _exact_equal(row, project(expected)), "stored_highvol_record_differs")
        summary.add(expected)
    _require(next(actual, None) is None, "extra_stored_highvol_record")
    return summary.result()


def verify_coverage(path: Path, seal: CoverageSeal, universe: sources.UniverseCalendar, reader: AuditedChunkReader,
                    binding: dict, checkpoint: Callable[[str], object]) -> dict:
    """Check full saved framing and independently reconstruct its entire report."""
    from research.intraday_highvol_coverage import open_coverage

    expected_header = {"kind": "header", "schema": "EG-highvol-coverage-v1",
                       "universe": {"sessions": list(universe.sessions),
                                    "raw_sha256": universe.raw_sha256,
                                    "target_count": len(universe.targets)}, "binding": binding}
    with open_coverage(path, seal, checkpoint) as artifact:
        _require(_exact_equal(artifact.header, expected_header), "stored_highvol_header_differs")
        report = verify_records(artifact.records(), universe, reader, checkpoint)
        expected_footer = {"kind": "footer", "records": report["union_records"], "report": report}
        _require(_exact_equal(artifact.footer, expected_footer), "stored_highvol_footer_differs")
    return report
