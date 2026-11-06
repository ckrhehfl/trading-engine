"""One-day EG event projections over sealed coverage and typed original minutes."""

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import fields
from datetime import datetime
from fractions import Fraction

from research.intraday_campaign_wsl_transport import encode
from research.intraday_chart_labels import _read_minute
from research.intraday_ed_inputs import MINUTE, SessionInputs, _require
from research.intraday_highvol_coverage import project
from research.intraday_highvol_events import HISTORY_SESSIONS, OFFSETS, QUANTILE_INDEX, HighVolDecision, measure_window
from research.intraday_highvol_inputs import HighVolChart, HighVolPeers, build_chart_inputs, build_peer_inputs
from research.intraday_highvol_labels import HighVolPath, label_path
from research.intraday_highvol_scenarios import ScenarioAttempt, detect_scenario


TIMINGS = ("S1", "S2")
HOLD_STEPS = (5, 10, 15, 20, 25)
PRIMARY_HORIZON = 30
DIAGNOSTIC_HORIZON = 60
MINIMUM_PEERS = 4


def _starts(record: dict) -> list[tuple[dict, bool]]:
    decisions = record["decisions"]
    _require(type(decisions) is list and len(decisions) == len(OFFSETS), "scenario_coverage_grid_changed")
    starts = []
    previous, active = None, False
    for offset, row in zip(OFFSETS, decisions, strict=True):
        status = row["status"]
        _require(row["current"]["offset"] == offset and status in ("high", "not_high", "unavailable"),
                 "scenario_coverage_coordinate_changed")
        if status == "high" and not active:
            starts.append((row, previous != "not_high"))
            active = True
        elif status == "not_high":
            active = False
        previous = status
    saved = [(item["high_offsets"][0], item["left_censored"]) for item in record["episodes"]["episodes"]]
    _require(saved == [(row["current"]["offset"], left) for row, left in starts],
             "scenario_coverage_cluster_starts_changed")
    return starts


def _fraction(value: dict) -> Fraction:
    parts = value["fraction"]
    _require(type(parts) is list and len(parts) == 2 and all(type(part) is int for part in parts)
             and parts[1] > 0, "scenario_invalid_saved_fraction")
    return Fraction(*parts)


def _first_high(session: SessionInputs, saved: dict) -> HighVolDecision:
    offset = saved["current"]["offset"]
    metric = measure_window(session, offset, session.opening + (offset + 2) * MINUTE)
    threshold = _fraction(saved["threshold"])
    previous = tuple(_fraction(value) for value in saved["prior_energies"])
    _require(len(previous) == HISTORY_SESSIONS and min(previous) >= 0 and sorted(previous)[QUANTILE_INDEX] == threshold
             and metric.energy is not None and metric.energy > threshold, "scenario_first_high_not_supported")
    typed = HighVolDecision(
        metric, tuple(datetime.fromisoformat(value) for value in saved["expected_prior_openings"]),
        previous, threshold, "high", "strictly_above_q80", False, threshold == 0,
    )
    _require(encode(project(typed)) == encode(saved), "scenario_first_high_projection_changed")
    return typed


def _attempt_frame(attempt: ScenarioAttempt) -> dict:
    return project({
        "scenario": attempt.scenario,
        "state": attempt.state,
        "reason": attempt.reason,
        "decision_at": attempt.decision_at,
        "key_level": attempt.key_level,
        "stop": attempt.stop,
        "pullback_end_at": attempt.pullback.end_at if attempt.pullback else None,
        "observed_bars": tuple({
            "start_at": bar.start_at,
            "end_at": bar.end_at,
            "available_at": bar.available_at,
            "ohlc": tuple(Fraction(price) for price in (bar.open, bar.high, bar.low, bar.close)),
        } for bar in attempt.observed_bars),
        "confirmation": attempt.confirmation,
        "issue": attempt.issue,
    })


def _input_frame(value: HighVolChart | HighVolPeers) -> dict:
    frame = project({field.name: getattr(value, field.name) for field in fields(value)
                     if field.name != "sequence"})
    frame["sequence"] = None if value.sequence is None else [list(row) for row in value.sequence]
    return frame


def _context(session, decision_at, timings, sessions, original_rosters) -> dict:
    end = decision_at - 2 * MINUTE
    chart = build_chart_inputs(session, end, decision_at)
    last, _ = _read_minute(session, decision_at - 3 * MINUTE, decision_at)
    peers = {
        timing: build_peer_inputs(
            session, end, decision_at,
            expected_peer_ids=tuple(symbol for symbol in original_rosters[timing] if symbol != session.symbol),
            peer_sessions=sessions, minimum_known=MINIMUM_PEERS,
        )
        for timing in timings
    }
    return {"decision_at": project(decision_at),
            "last_close": None if last is None else project(Fraction(last.ohlc[3])),
            "chart": _input_frame(chart),
            "peers": {timing: _input_frame(value) for timing, value in peers.items()}}


def _endpoint_clock(session: SessionInputs, decision_at: datetime) -> tuple[datetime, datetime, bool]:
    entry_at = decision_at + MINUTE
    exit_at = entry_at + PRIMARY_HORIZON * MINUTE
    return entry_at, exit_at, exit_at < session.closing - 15 * MINUTE


def _immediate_label(session: SessionInputs, entry_at: datetime, exit_at: datetime, supported: bool) -> dict:
    entry = exit_row = entry_issue = exit_issue = None
    if supported:
        entry, entry_issue = _read_minute(session, entry_at, session.closing)
        exit_row, exit_issue = _read_minute(session, exit_at, session.closing)
    entry_open = None if entry is None else Fraction(entry.ohlc[0])
    exit_open = None if exit_row is None else Fraction(exit_row.ohlc[0])
    observed = entry is not None and exit_row is not None
    status = "outside_clock" if not supported else ("available" if observed else "unavailable")
    return project({
        "status": status,
        "entry_at": entry_at, "exit_at": exit_at, "entry_open": entry_open, "exit_open": exit_open,
        "gross": exit_open / entry_open - 1 if observed else None,
        "mature_at": max(entry.available_at, exit_row.available_at) if observed else None,
        "entry_issue": entry_issue, "exit_issue": exit_issue,
    })


def _hold_label(session: SessionInputs, primary: HighVolPath, step: int) -> dict:
    next_open_at = primary.entry_at + step * MINUTE
    value = mature_at = issue = next_open = next_open_mature_at = None
    if primary.status != "admitted":
        status = "entry_not_admitted"
    else:
        row, issue = _read_minute(session, next_open_at, session.closing)
        if row is None:
            status = "next_open_unavailable"
        else:
            next_open, next_open_mature_at = Fraction(row.ohlc[0]), row.available_at
            if primary.fixed_exit_open is None:
                status = "cap_unavailable"
            else:
                status = "available"
                value = (primary.fixed_exit_open - next_open) / primary.entry_open
                mature_at = max(primary.fixed_mature_at, next_open_mature_at)
    return project({
        "step_minutes": step, "decision_at": next_open_at - MINUTE, "next_open_at": next_open_at,
        "status": status, "next_open": next_open, "next_open_mature_at": next_open_mature_at,
        "value": value, "mature_at": mature_at, "issue": issue,
    })


def iter_scenario_day(
    records: Iterable[dict], sessions: Mapping[str, SessionInputs],
    original_rosters: Mapping[str, tuple[str, ...]], checkpoint: Callable[[str], object],
) -> Iterator[tuple[dict, dict]]:
    """Yield one separate feature and outcome frame for each observed cluster.

    The caller supplies one original day, checks coverage order and seals the
    two streams independently. Entry/holding features are formed before any
    outcome reads. The immediate high control uses its own decision clock.
    """
    _require(set(original_rosters) == set(TIMINGS)
             and all(type(roster) is tuple and all(type(symbol) is str for symbol in roster)
                     and len(set(roster)) == len(roster) for roster in original_rosters.values()),
             "scenario_original_rosters_required")
    for record in records:
        checkpoint("eg_scenario_record")
        day, symbol = record["session"], record["symbol"]
        session = sessions.get(symbol)
        _require(type(session) is SessionInputs and session.symbol == symbol
                 and session.opening.date().isoformat() == day
                 and project(session.opening) == record["opening"] and project(session.closing) == record["closing"],
                 "scenario_source_session_identity_changed")
        timings = tuple(timing for timing in TIMINGS if symbol in original_rosters[timing])
        _require(timings and timings == tuple(item["scenario"] for item in record["targets"]),
                 "scenario_original_target_membership_changed")
        for saved, left_censored in _starts(record):
            checkpoint("eg_scenario_event")
            high = _first_high(session, saved)
            attempt = detect_scenario(session, high, left_censored=left_censored)
            key = [day, symbol, high.current.offset]
            immediate_entry, immediate_exit, immediate_ok = _endpoint_clock(session, high.current.decision_at)
            feature = {
                "key": key,
                "timings": list(timings),
                "first_high": project({"current": high.current, "threshold": high.threshold,
                                       "left_censored": left_censored}),
                "attempt": _attempt_frame(attempt),
                "immediate": project({"decision_at": high.current.decision_at, "entry_at": immediate_entry,
                                      "exit_at": immediate_exit,
                                      "status": "supported" if immediate_ok else "outside_clock"}),
                "entry": None,
                "holding": [],
            }
            signal = attempt.confirmation
            hold_steps = ()
            if signal is not None:
                feature["entry"] = _context(session, signal.decision_at, timings, sessions, original_rosters)
                primary_clock_supported = _endpoint_clock(session, signal.decision_at)[2]
                feature["entry"]["primary_clock_supported"] = primary_clock_supported
                if primary_clock_supported:
                    hold_steps = HOLD_STEPS
                    for step in hold_steps:
                        checkpoint("eg_scenario_holding_context")
                        context = _context(session, signal.decision_at + step * MINUTE,
                                           timings, sessions, original_rosters)
                        feature["holding"].append({"step_minutes": step, **context})

            primary = diagnostic = None
            if signal is not None:
                primary, diagnostic = (
                    label_path(session, signal.decision_at, signal.stop, signal.target,
                               horizon_minutes=horizon, label_cutoff=session.closing)
                    for horizon in (PRIMARY_HORIZON, DIAGNOSTIC_HORIZON)
                )
            label = {
                "key": list(key),
                "immediate": _immediate_label(session, immediate_entry, immediate_exit, immediate_ok),
                "primary": project(primary),
                "diagnostic": project(diagnostic),
                "holding": [_hold_label(session, primary, step) for step in hold_steps],
            }
            yield feature, label
