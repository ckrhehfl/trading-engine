"""Reconstruct EG scenario snapshot science from separately decoded originals."""

from collections import Counter
from contextlib import ExitStack
from datetime import date
from fractions import Fraction
from math import log1p

from research import intraday_campaign_wsl_transport as wire
from research.intraday_ed_inputs import MINUTE, Issue
from research.intraday_ed_snapshot_verify import reconstruct_session
from research.intraday_highvol_coverage import open_coverage, project
from research.intraday_highvol_reference import GRID, reference_window
from research.intraday_highvol_snapshot_io import ROLES, SCHEMAS, open_snapshot


FIVE = 5 * MINUTE
TIMINGS = ("S1", "S2")
HOLD_STEPS = (5, 10, 15, 20, 25)


class ScenarioVerificationRefusal(ValueError):
    """A sealed record disagrees with independent scenario reconstruction."""


def _require(condition, reason):
    if not condition:
        raise ScenarioVerificationRefusal(reason)


def _same(actual, expected, reason):
    _require(wire.encode(actual) == wire.encode(expected), reason)


def _starts(frame):
    decisions = frame["decisions"]
    _require(tuple(row["current"]["offset"] for row in decisions) == GRID
             and all(row["status"] in ("high", "not_high", "unavailable") for row in decisions),
             "reference_scenario_coverage_grid")
    stops = [-1, *(i for i, row in enumerate(decisions) if row["status"] == "not_high"), len(GRID)]
    starts = []
    for left, right in zip(stops, stops[1:]):
        index = next((i for i in range(left + 1, right) if decisions[i]["status"] == "high"), None)
        if index is not None:
            starts.append((index, left == -1 or index > left + 1))
    _same([[episode["high_offsets"][0], episode["left_censored"]] for episode in frame["episodes"]["episodes"]],
          [[GRID[index], censored] for index, censored in starts], "reference_scenario_cluster_starts")
    return [(GRID[index], decisions[index], censored) for index, censored in starts]


def _first_high(session, offset, saved, censored):
    window = reference_window(session, offset)
    _same(saved["current"], project(window), "reference_scenario_first_high_metric")
    prior = sorted(Fraction(*item["fraction"]) for item in saved["prior_energies"])
    _require(len(prior) == 20 and prior[0] >= 0 and window.energy is not None and window.energy > prior[15],
             "reference_scenario_high_threshold")
    _same(saved["threshold"], project(prior[15]), "reference_scenario_high_threshold_pin")
    return window, {"current": window, "threshold": prior[15], "left_censored": censored}


def _bar(session, end):
    start, cutoff = end - FIVE, end + 2 * MINUTE
    values = []
    for index in range(5):
        at = start + index * MINUTE
        row = session.minutes.get(at)
        if row is None:
            return None, {"minute": at, "reason": "missing_minute"}
        if row.available_at > cutoff:
            return None, {"minute": at, "reason": "price_unavailable_by_bar_deadline"}
        if row.price_state != "observed":
            return None, {"minute": at, "reason": "price_" + row.price_state}
        values.append(tuple(Fraction(price) for price in row.ohlc))
    return {"start_at": start, "end_at": end, "available_at": cutoff,
            "ohlc": (values[0][0], max(row[1] for row in values), min(row[2] for row in values),
                     values[-1][3])}, None


def _attempt(session, high):
    end = high.decision_at - 2 * MINUTE
    bars = []
    result = dict.fromkeys(("scenario", "state", "reason", "key_level", "stop", "pullback_end_at",
                            "confirmation", "issue"))
    result.update(decision_at=high.decision_at, observed_bars=bars)

    def terminal(state, reason):
        return {**result, "state": state, "reason": reason}

    for offset in range(-2, 7):
        bar_end = end + offset * FIVE
        if bar_end > session.closing:
            reason = "initial_context_outside_session" if offset <= 0 else "confirmation_window_truncated_by_close"
            return terminal("outside_clock", reason)
        if offset > 0:
            result["decision_at"] = bar_end + 2 * MINUTE
        bar, result["issue"] = _bar(session, bar_end)
        if bar is None:
            return terminal("unsupported", "initial_context_unavailable" if offset <= 0
                            else "confirmation_bar_unavailable")
        bars.append(bar)
        if offset < 0:
            continue
        _, _, low, close = bar["ohlc"]
        if offset == 0:
            upper = max(item["ohlc"][1] for item in bars[:2])
            lower = min(item["ohlc"][2] for item in bars[:2])
            if close > upper and high.signed_move > 0:
                result.update(scenario="A", key_level=upper)
                trigger = None
            elif close < lower and high.signed_move < 0:
                result.update(scenario="B", key_level=lower, stop=low)
                trigger = lower
            else:
                return terminal("no_setup", "no_directional_expansion_or_breakdown")
            continue
        if trigger is None:
            if close < bars[-2]["ohlc"][3]:
                if low <= result["key_level"]:
                    return terminal("invalidated", "pullback_failed_to_hold_key")
                result.update(stop=low, pullback_end_at=bar_end)
                trigger = bar["ohlc"][1]
        elif low < result["stop"]:
            return terminal("invalidated", "new_low_below_structural_stop")
        elif close > trigger:
            stop = result["stop"]
            decision = result["decision_at"]
            result["confirmation"] = {"decision_at": decision, "intended_entry_at": decision + MINUTE,
                                      "price": close, "stop": stop, "target": 3 * close - 2 * stop}
            return terminal("confirmed", "resumption" if result["scenario"] == "A" else "reclaim")
    return terminal("expired", "confirmation_deadline_elapsed")


def _quote(session, at):
    earliest = at + 3 * MINUTE
    row = None if earliest > session.closing else session.minutes.get(at)
    if earliest > session.closing:
        reason = "minute_not_mature_at_cutoff"
    elif row is None:
        reason = "missing_minute"
    elif row.available_at > session.closing:
        reason = "minute_available_after_cutoff"
    elif row.price_state != "observed":
        reason = "price_" + row.price_state
    else:
        return row, None
    return None, Issue(session.symbol, at, reason)


def _immediate(session, decision_at):
    entry, exit_at = decision_at + MINUTE, decision_at + 31 * MINUTE
    supported = exit_at < session.closing - 15 * MINUTE
    feature = {"status": "supported" if supported else "outside_clock", "decision_at": decision_at,
               "entry_at": entry, "exit_at": exit_at}
    label = dict.fromkeys(("entry_open", "exit_open", "gross", "mature_at", "entry_issue", "exit_issue"))
    label.update(entry_at=entry, exit_at=exit_at, status="outside_clock")
    if supported:
        start, label["entry_issue"] = _quote(session, entry)
        end, label["exit_issue"] = _quote(session, exit_at)
        label["entry_open"] = Fraction(start.ohlc[0]) if start else None
        label["exit_open"] = Fraction(end.ohlc[0]) if end else None
        label["status"] = "unavailable"
        if start is not None and end is not None:
            label.update(status="available", gross=(label["exit_open"] - label["entry_open"]) / label["entry_open"],
                         mature_at=max(start.available_at, end.available_at))
    return feature, label


def _first_touch(session, entry, cap, stop, target):
    maturity = entry.available_at
    for step in range((cap - entry.start_at) // MINUTE):
        at = entry.start_at + step * MINUTE
        row, issue = _quote(session, at)
        if issue is not None:
            return {"status": "unavailable", "minute": at, "mature_at": None, "issue": issue}
        maturity = max(maturity, row.available_at)
        op, high, low, _ = row.ohlc
        if op <= stop or op >= target:
            status = "stop_first" if op <= stop else "target_first"
        elif low <= stop and high >= target:
            status = "same_minute_unknown"
        elif low <= stop or high >= target:
            status = "stop_first" if low <= stop else "target_first"
        else:
            continue
        return {"status": status, "minute": at, "mature_at": maturity, "issue": None}
    return {"status": "time", "minute": None, "mature_at": maturity, "issue": None}


def _path(session, signal, horizon):
    entry_at = signal["intended_entry_at"]
    cap = entry_at + horizon * MINUTE
    label = dict.fromkeys(("entry_open", "fixed_exit_open", "fixed_gross", "fixed_mature_at",
                           "first_touch", "entry_issue", "fixed_exit_issue"))
    label.update(entry_at=entry_at, exit_at=cap, status="outside_clock")
    if cap >= session.closing - 15 * MINUTE:
        return label
    entry, label["entry_issue"] = _quote(session, entry_at)
    if entry is None:
        return {**label, "status": "entry_unavailable"}
    price = label["entry_open"] = Fraction(entry.ohlc[0])
    stop, target = signal["stop"], signal["target"]
    if price <= stop or price >= target:
        return {**label, "status": "entry_refused"}
    label.update(status="admitted", first_touch=_first_touch(session, entry, cap, stop, target))
    exit_row, label["fixed_exit_issue"] = _quote(session, cap)
    if exit_row is not None:
        fixed = Fraction(exit_row.ohlc[0])
        label.update(fixed_exit_open=fixed, fixed_gross=(fixed - price) / price,
                     fixed_mature_at=max(entry.available_at, exit_row.available_at))
    return label


def _holding_labels(session, primary):
    for step in HOLD_STEPS:
        next_open_at = primary["entry_at"] + step * MINUTE
        label = dict.fromkeys(("next_open", "next_open_mature_at", "value", "mature_at", "issue"))
        label.update(step_minutes=step, decision_at=next_open_at - MINUTE, next_open_at=next_open_at,
                     status="entry_not_admitted")
        if primary["status"] == "admitted":
            row, label["issue"] = _quote(session, next_open_at)
            if row is None:
                label["status"] = "next_open_unavailable"
            else:
                price = Fraction(row.ohlc[0])
                label.update(next_open=price, next_open_mature_at=row.available_at, status="cap_unavailable")
                cap = primary["fixed_exit_open"]
                if cap is not None:
                    label.update(status="available", value=(cap - price) / primary["entry_open"],
                                 mature_at=max(primary["fixed_mature_at"], row.available_at))
        yield label


def _prefix(session, end, decision):
    """Independently retain each visible price/volume slot and its missing reason."""
    rows, prices, states, issues = [], [], [], []
    for step in range(-60, 0):
        at = end + step * MINUTE
        row = session.minutes.get(at) if at >= session.opening else None
        if at < session.opening:
            state = "preopening"
        elif row is None:
            state = "missing_minute"
        elif row.available_at > decision:
            row, state = None, "minute_available_after_cutoff"
        else:
            state = "observed" if row.price_state == "observed" else "price_" + row.price_state
        rows.append(row)
        prices.append(tuple(Fraction(value) for value in row.ohlc) if state == "observed" else None)
        states.append(state)
        if state not in ("observed", "preopening"):
            issues.append(Issue(session.symbol, at, state))
    return rows, prices, states, issues


def _chart(identity, prefix):
    rows, prices, states, issues = prefix
    volume_states = [state if row is None else row.volume_state for row, state in zip(rows, states, strict=True)]
    sequence = None
    if all(value is not None for value in prices[-15:]):
        anchor = next(value[0] for value in prices if value is not None)
        known = sorted(Fraction(row.shares) for row in rows if row is not None
                       and row.shares is not None and row.shares > 0)
        scale = (known[len(known) // 2] + known[(len(known) - 1) // 2]) / 2 if known else Fraction(1)
        sequence = []
        for row, ohlc, state in zip(rows, prices, states, strict=True):
            channels = [0.0] * 8
            if ohlc is not None:
                op, high, low, close = ohlc
                span = high - low
                channels = [float(100 * (value - anchor) / anchor) for value in ohlc]
                channels += [float(100 * (close - op) / op), float(100 * span / op),
                             float((close - low) / span) if span else 0.0, float(not span)]
            shares = row.shares if row is not None else None
            channels += [log1p(float(Fraction(shares) / scale)) if shares is not None else 0.0,
                         float(shares is not None), float(ohlc is not None), float(state == "preopening")]
            sequence.append(channels)
    metadata = {**identity, "status": "unavailable" if sequence is None else "available",
                "price_states": states, "volume_states": volume_states, "price_issues": issues}
    return {**project(metadata), "sequence": sequence}


def _peer(identity, expected, prefixes):
    common, cohort, issues = [], [], []
    for symbol in expected:
        if symbol not in prefixes:
            issues.append(Issue(symbol, identity["bar_end"], "expected_peer_session_missing"))
            continue
        _, prices, _, gaps = prefixes[symbol]
        issues.extend(gaps)
        if all(row is not None for row in prices[-15:]):
            common.append(symbol)
            cohort.append((next(row[0] for row in prices if row is not None), prices))
    columns = [[(anchor, prices[index]) for anchor, prices in cohort if prices[index] is not None]
               for index in range(60)]
    counts = [len(column) for column in columns]
    sequence = None
    if len(common) < 4:
        issues.append(Issue(identity["symbol"], identity["bar_end"], "insufficient_complete_original_peers"))
    else:
        sequence = []
        for index, column in enumerate(columns):
            count = counts[index]
            cover = float(Fraction(count, len(expected)))
            if count < 4:
                preopen = identity["bar_end"] + (index - 60) * MINUTE < identity["opening"]
                sequence.append([0.0, 0.0, 0.0, cover, 0.0, float(preopen)])
                continue
            oc = sum(((prices[3] - prices[0]) / prices[0] for _, prices in column), Fraction())
            cumulative = sum(((prices[3] - anchor) / anchor for anchor, prices in column), Fraction())
            positive = sum(prices[3] > anchor for anchor, prices in column)
            sequence.append([float(100 * oc / count), float(100 * cumulative / count),
                             float(Fraction(positive, count)), cover, 1.0, 0.0])
    metadata = {**identity, "status": "unavailable" if sequence is None else "available",
                "expected_peer_ids": expected, "common_peer_ids": common, "denominator": len(expected),
                "known_counts": counts, "issues": issues}
    return {**project(metadata), "sequence": sequence}


def _context(session, decision, memberships, sessions, rosters):
    end = decision - 2 * MINUTE
    identity = {"symbol": session.symbol, "opening": session.opening, "bar_end": end, "decision_at": decision}
    target = _prefix(session, end, decision)
    expected = {timing: sorted(symbol for symbol in rosters[timing] if symbol != session.symbol)
                for timing in memberships}
    peers = {symbol: _prefix(sessions[symbol], end, decision)
             for symbol in set().union(*expected.values()) if sessions.get(symbol) is not None}
    close = target[1][-1]
    return {"decision_at": project(decision), "last_close": project(close[3] if close is not None else None),
            "chart": _chart(identity, target),
            "peers": {timing: _peer(identity, symbols, peers) for timing, symbols in expected.items()}}


def _event(session, offset, saved, censored, sessions, rosters, checkpoint):
    high, first_high = _first_high(session, offset, saved, censored)
    attempt = _attempt(session, high)
    immediate, immediate_label = _immediate(session, high.decision_at)
    key = [session.opening.date().isoformat(), session.symbol, offset]
    memberships = [timing for timing in TIMINGS if session.symbol in rosters[timing]]
    features = project({"key": key, "timings": memberships, "first_high": first_high, "attempt": attempt,
                        "immediate": immediate, "entry": None, "holding": []})
    labels = {"key": key, "immediate": immediate_label, "primary": None, "diagnostic": None, "holding": []}
    signal = attempt["confirmation"]
    if signal is not None:
        decision = signal["decision_at"]
        features["entry"] = _context(session, decision, memberships, sessions, rosters)
        supported = signal["intended_entry_at"] + 30 * MINUTE < session.closing - 15 * MINUTE
        features["entry"]["primary_clock_supported"] = supported
        for horizon, field in ((30, "primary"), (60, "diagnostic")):
            labels[field] = _path(session, signal, horizon)
        if supported:
            for step in HOLD_STEPS:
                checkpoint("eg_reference_holding")
                later = _context(session, decision + step * MINUTE, memberships, sessions, rosters)
                features["holding"].append({"step_minutes": step, **later})
            labels["holding"] = list(_holding_labels(session, labels["primary"]))
    return features, project(labels)


def _week(day):
    calendar = date.fromisoformat(day).isocalendar()
    return f"{calendar.year}-W{calendar.week:02d}"


class _Report:
    def __init__(self, days):
        self.counts = {timing: Counter() for timing in TIMINGS}
        self.weekly = {timing: {week: Counter() for week in sorted(set(map(_week, days)))} for timing in TIMINGS}
        self.events = 0

    def observe(self, feature, label):
        self.events += 1
        day, _, offset = feature["key"]
        attempt = feature["attempt"]
        branch, state = attempt["scenario"] or "none", attempt["state"]
        for timing in feature["timings"]:
            overall = self.counts[timing]
            week = self.weekly[timing][_week(day)]
            overall.update({"observed_clusters": 1, "first_hour_clusters": int(offset <= 60),
                            f"{branch}:{state}": 1})
            week.update(("observed_clusters",))
            if state == "confirmed":
                outcome = label["primary"]
                overall.update({f"{branch}:first_hour_confirmed": int(offset <= 60),
                                f"{branch}:primary:{outcome['status']}": 1})
                week.update((f"{branch}:confirmed",))
                if outcome["status"] == "admitted":
                    week.update((f"{branch}:admitted",))
                    overall.update({f"{branch}:fixed_exit_known": int(outcome["fixed_gross"] is not None),
                                    f"{branch}:first_touch:{outcome['first_touch']['status']}": 1})
                entry = feature["entry"]
                if (entry["primary_clock_supported"] and entry["chart"]["status"] == "available"
                        and entry["peers"][timing]["status"] == "available"):
                    overall.update((f"{branch}:common_support",))
                    week.update((f"{branch}:common_support",))

    def finish(self):
        return {"schema": "EG-scenario-snapshot-summary-v1", "union_events": self.events, "timings": {
            timing: {"counts": dict(self.counts[timing]), "weekly": [
                {"week": week, **dict(values)} for week, values in self.weekly[timing].items()]}
            for timing in TIMINGS}}


def verify_snapshot(directory, seals, bound, scope, coverage_path, coverage_seal, coverage_binding,
                    universe, raw_reader, checkpoint):
    """Check both full streams and reconstruct all events, inputs, labels and counts."""
    from research.intraday_highvol_snapshot_unit import source_days

    original_scope = {"sessions": list(universe.sessions), "raw_sha256": universe.raw_sha256,
                      "target_count": len(universe.targets)}
    _same(scope, original_scope, "reference_scenario_snapshot_scope")
    _require(set(seals) == set(ROLES), "reference_scenario_snapshot_roles")
    summary = _Report(universe.sessions)
    with ExitStack() as stack:
        coverage = stack.enter_context(open_coverage(coverage_path, coverage_seal, checkpoint))
        _same(coverage.header["universe"], scope, "reference_scenario_coverage_scope")
        _same(coverage.header["binding"], coverage_binding, "reference_scenario_coverage_binding")
        streams = {role: stack.enter_context(open_snapshot(directory, role, seals[role], checkpoint))
                   for role in ROLES}
        for role, stream in streams.items():
            _same(stream.header, {"kind": "header", "schema": SCHEMAS[role], "universe": scope, "binding": bound},
                  "reference_scenario_snapshot_header")
        features, labels = (streams[role].records() for role in ROLES)
        for frames, sessions, rosters in source_days(coverage.records(), universe, raw_reader, checkpoint,
                                                      decode=reconstruct_session):
            starts = [(offset, frame["symbol"], saved, censored) for frame in frames
                      for offset, saved, censored in _starts(frame)]
            for offset, symbol, saved, censored in sorted(starts, key=lambda item: item[:2]):
                checkpoint("eg_reference_scenario_event")
                _require(symbol in sessions, "reference_scenario_source_session_missing")
                feature, label = _event(sessions[symbol], offset, saved, censored, sessions, rosters, checkpoint)
                _same(next(features, None), feature, "reference_scenario_feature_differs")
                _same(next(labels, None), label, "reference_scenario_label_differs")
                summary.observe(feature, label)
        _require(next(features, None) is None and next(labels, None) is None, "reference_scenario_extra_event")
        report = summary.finish()
        for role, stream in streams.items():
            expected = {"union_events": summary.events} if role == "features" else report
            _same(stream.footer, {"kind": "footer", "records": summary.events, "report": expected},
                  "reference_scenario_snapshot_footer")
    return report
