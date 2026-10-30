"""Independent raw H4/H5 detection and complete fictional-account verification.

No producer detector, replay, summary or price parser supplies expectations.
Raw chunks are authenticated against both original report projections and are
reconstructed one day at a time. The existing low-level independent cash oracle
is reused, while its core-three callback and finisher remain unchanged.
Source-file bytes, historical availability and corporate-action units remain
the caller's separate obligations; this library performs no IO or activation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
import re

from research import intraday_campaign_independent as account_oracle
from research.intraday_campaign_account import CampaignCandidate
from research.intraday_campaign_context_readiness import ReadinessInputs
from research.intraday_pattern_calibration import Session


VerificationError = account_oracle.VerificationError
_equal, _plain = account_oracle._equal, account_oracle._plain
MINUTE = timedelta(minutes=1)
FIVE = 5 * MINUTE
KST = timezone(timedelta(hours=9))
POLICIES = ("D+1", "D+2")
LABELS = ("net_pnl", "gross_pnl_same_fills")
UNITS = {"P11": ("H4", "original_h4", "H4-compression-expansion-breakout"),
         "P12": ("H5", "original_h5", "H5-late-session-morning-strength")}
SOURCE_FIELDS = ("observation_date", "available_at", "retrieved_at", "source", "is_final", "evidence_level")
ROW_FIELDS = {"symbol", "interval", "open_time_ms", "open", "high", "low", "close", "fetched_at"}


def _need(condition, reason):
    if not condition:
        raise VerificationError(reason)


def _opening(day):
    return datetime.combine(date.fromisoformat(day), datetime.min.time(), KST).replace(
        hour=10 if day in ("2025-11-13", "2026-01-02") else 9)


def _closing(day):
    return _opening(day).replace(hour=16 if day == "2025-11-13" else 15, minute=30)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _digest(value):
    """Retain scalar types, including Fraction-vs-int, in compact row evidence."""
    def typed(item):
        if type(item) is Fraction:
            return ["fraction", str(item.numerator), str(item.denominator)]
        if type(item) is datetime:
            return ["datetime", item.isoformat()]
        if isinstance(item, Mapping):
            _need(all(type(key) is str for key in item), "row string keys required")
            return ["mapping", [[key, typed(item[key])] for key in sorted(item)]]
        if type(item) in (tuple, list):
            return ["sequence", [typed(value) for value in item]]
        _need(type(item) in (str, int, bool, type(None)), "unsupported row scalar")
        return [type(item).__name__, item]
    return hashlib.sha256(_json(typed(value))).digest()


def _raw_session(chunk, day, symbol):
    """Independent exact buckets; unique OPEN survives invalid later HLC."""
    _need(type(chunk) is dict and set(chunk) == {*SOURCE_FIELDS, "symbol", "session", "rows"},
          "raw chunk fields changed")
    _need(chunk["session"] == chunk["observation_date"] == day and chunk["symbol"] == symbol
          and type(symbol) is str and re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol)
          and chunk["available_at"] is None and chunk["is_final"] is None
          and chunk["source"] == "collector klines" and chunk["evidence_level"] == "assumed",
          "raw source identity changed")
    try:
        retrieved = datetime.fromisoformat(chunk["retrieved_at"])
    except (TypeError, ValueError):
        raise VerificationError("invalid raw retrieval clock") from None
    _need(retrieved.utcoffset() is not None, "aware raw retrieval clock required")
    rows = chunk["rows"]
    _need(type(rows) is list and len(rows) <= 600, "raw row budget changed")
    opening, closing = _opening(day), _closing(day)
    count = int((closing - opening) / MINUTE)
    first = (opening - datetime(1970, 1, 1, tzinfo=timezone.utc)) // MINUTE
    buckets, offsets, linkage = {}, [], []
    for row in rows:
        _need(type(row) is dict and set(row) == ROW_FIELDS, "raw row fields changed")
        _need(row["symbol"] == symbol and row["interval"] == "1m"
              and type(row["open_time_ms"]) is int
              and (row["fetched_at"] is None or type(row["fetched_at"]) is str),
              "raw row identity changed")
        stamp = row["open_time_ms"]
        index = stamp // 60000 - first
        _need(0 <= index < count, "raw row outside session")
        buckets.setdefault(index, []).append(row)
        offsets.append(stamp % 60000)
        linkage.append([stamp, row["fetched_at"]])
    minutes, opens, gaps = {}, {}, []
    cache = {}
    for index in range(count):
        at = opening + index * MINUTE
        found = buckets.get(index, [])
        prices, reason = None, "missing"
        if len(found) > 1:
            reason = "conflict"
        elif found:
            values = tuple(found[0][key] for key in ("open", "high", "low", "close"))
            try:
                if not all(type(value) is str for value in values):
                    raise ValueError
                prices = cache.get(values)
                if prices is None:
                    parsed = tuple(Decimal(value) for value in values)
                    op, hi, lo, cl = parsed
                    if not all(value.is_finite() and value > 0 for value in parsed) \
                            or not lo <= min(op, cl) <= max(op, cl) <= hi:
                        raise ValueError
                    if len(cache) >= 128:
                        cache.clear()
                    cache[values] = parsed
                    prices = parsed
            except (ValueError, InvalidOperation):
                prices, reason = None, "invalid_price"
        minutes[at] = prices
        if prices is None:
            if gaps and gaps[-1]["end"] == at and gaps[-1]["state"] == reason:
                gaps[-1]["end"] = at + MINUTE
            else:
                gaps.append({"start": at, "end": at + MINUTE, "state": reason})
        if found:
            opened = None
            if len(found) == 1 and type(found[0]["open"]) is str:
                try:
                    value = Decimal(found[0]["open"])
                    if value.is_finite() and value > 0:
                        opened = value
                except InvalidOperation:
                    pass
            opens[at] = opened
    bars = []
    for offset in range(0, count - 4, 5):
        values = [minutes[opening + (offset + index) * MINUTE] for index in range(5)]
        if any(value is None for value in values):
            break
        end = opening + (offset + 5) * MINUTE
        bars.append({"start_at": opening + offset * MINUTE, "end_at": end,
                     "available_at": end + 2 * MINUTE, "open": values[0][0],
                     "high": max(value[1] for value in values), "low": min(value[2] for value in values),
                     "close": values[-1][3]})
    source = {"session": day, "symbol": symbol,
              "data_vintage": hashlib.sha256(_json(chunk) + b"\n").hexdigest(),
              "raw_chunk_metadata": {key: chunk[key] for key in SOURCE_FIELDS},
              "input_rows": len(rows), "row_linkage_sha256": hashlib.sha256(_json(linkage)).hexdigest(),
              "source_row_metadata_location": "original_pinned_archive_chunk"}
    return {"opening": opening, "closing": closing, "minutes": minutes, "opens": opens,
            "gaps": tuple(gaps), "offsets": tuple(offsets), "bars": bars, "source": source}


def _detect(data, mode):
    """Original H4/H5 state machines over separately reconstructed raw bars."""
    _need(mode in ("H4", "H5"), "unregistered raw detector")
    opening, closing, bars = data["opening"], data["closing"], data["bars"]
    cutoff = closing - 78 * MINUTE if mode == "H5" else opening + 240 * MINUTE
    observed = None
    def result(state, reason, decision, signal=None):
        return {"signal_state": state, "signal_reason": reason, "decision_at": decision,
                "observed_through": observed,
                "signal_bar_start": signal["start_at"] if signal is not None else None,
                "signal_bar_end": signal["end_at"] if signal is not None else None,
                "setup_bar_end": None}
    if opening + 32 * MINUTE > cutoff:
        return result("unknown", "clock_infeasible", cutoff)
    required = (cutoff - opening - 2 * MINUTE) // FIVE
    history = []
    high = low = None
    morning = False
    for index in range(required):
        end = opening + (index + 1) * FIVE
        if end > closing:
            return result("unknown", "clock_infeasible", end + 2 * MINUTE)
        if index >= len(bars):
            return result("unknown", "missing_prefix", end + 2 * MINUTE)
        current = bars[index]
        observed = current["end_at"]
        if index < 6:
            history.append(current)
            if index == 5:
                high = max(Fraction(bar["high"]) for bar in history)
                low = min(Fraction(bar["low"]) for bar in history)
                morning = Fraction(current["close"]) > Fraction(history[0]["open"])
                if mode == "H4" and high == low:
                    return result("known_no_signal", "initial_range_zero", current["available_at"])
            continue
        if mode == "H4" and end >= opening + 90 * MINUTE:
            previous = history[-6:]
            band_high = max(Fraction(bar["high"]) for bar in previous)
            band_low = min(Fraction(bar["low"]) for bar in previous)
            if (band_high - band_low) * 2 <= high - low and Fraction(current["close"]) > band_high:
                return result("signal", "compression_expanded", current["available_at"], current)
        history.append(current)
    if mode == "H5":
        latest = history[-1]
        if morning and Fraction(latest["close"]) > high:
            return result("signal", "late_strength", cutoff, latest)
        return result("known_no_signal", "fixed_condition_false", cutoff)
    return result("known_no_signal", "decision_cutoff", cutoff)


class H45AccountVerifier:
    """Own all expectations, original coverage and complete-unit acceptance."""

    def __init__(self, policy_id, a, b, *, authentication_reference):
        _need(type(policy_id) is str and policy_id in UNITS, "unregistered H45 policy")
        _need(type(authentication_reference) is str and bool(authentication_reference.strip()),
              "authentication reference required")
        self.policy_id = policy_id
        self.mode, self.arm, self.hypothesis_id = UNITS[policy_id]
        self.authentication_reference = authentication_reference
        self.originals = {}
        for value, setup in ((a, "EA-A"), (b, "EA-B")):
            _need(type(value) is ReadinessInputs and value.setup_id == setup,
                  "original ReadinessInputs required")
            _need(type(value.authentication_reference) is str and bool(value.authentication_reference.strip()),
                  "original authentication required")
            self.originals[setup] = {"account_projection": _plain(value.account_projection),
                                     "request_metadata": _plain(value.request_metadata)}
            _need(self.originals[setup]["account_projection"].get("mode") == "ec-stored-account-inputs"
                  and self.originals[setup]["account_projection"].get("setup_id") == setup,
                  "original account projection changed")
        self.account = self.originals["EA-A"]["account_projection"]
        for field in ("reference_sessions", "reference_weeks", "source_target_matrix", "upstream_calendar", "sources"):
            _equal(self.originals["EA-B"]["account_projection"][field], self.account[field], f"original.{field}")
        self.reference = self.account["reference_sessions"]
        _need(bool(self.reference) and self.reference == sorted(set(self.reference)), "original calendar required")
        self.weeks = sorted({account_oracle._week(day) for day in self.reference})
        _equal(self.account["reference_weeks"], self.weeks, "original.weeks")
        self.sources = self.account["sources"]
        self.source_indices = {(row["session"], row["symbol"]): index for index, row in enumerate(self.sources)}
        _need(len(self.source_indices) == len(self.sources), "duplicate original source")
        self.source_by_day = {day: {} for day in self.reference}
        for source in self.sources:
            _need(source["session"] in self.source_by_day, "source outside calendar")
            self.source_by_day[source["session"]][source["symbol"]] = source
        self.members = {}
        for timing in POLICIES:
            days = self.account["source_target_matrix"][timing]["days"]
            _equal([row["session"] for row in days], self.reference, "membership.calendar")
            for index, membership in enumerate(days):
                session = self.reference[index]
                targets = membership["targets"]
                _need(len(targets) == 20 and len({row["symbol"] for row in targets}) == 20
                      and all(type(row["rank"]) is int for row in targets)
                      and {row["rank"] for row in targets} == set(range(1, 21)), "original target ranks changed")
                _need(datetime.fromisoformat(membership["available_at"])
                      <= datetime.fromisoformat(membership["selected_at"]) < _opening(session),
                      "noncausal original membership")
                self.members[timing, session] = targets
        for session in self.reference:
            union = {row["symbol"] for timing in POLICIES for row in self.members[timing, session]}
            _equal(set(self.source_by_day[session]), union, "original source union")
        self.records = {(timing, session): [] for timing in POLICIES for session in self.reference}
        self._states, self._days, self._plans = {}, {}, {}
        self._visited, self._day_identity, self._current_session = [], None, None
        self._raw_day = self._detections = None
        self._day_callbacks = set()
        self._closed = False

    def _bind_day(self, day):
        _need(not self._closed, "verifier already closed")
        session = day["session"]
        if self._current_session == session:
            _need(id(day) == self._day_identity and self._raw_day is not None,
                  "raw day binding changed or day already closed")
            return
        if self._current_session is not None:
            _need(self._raw_day is None and len(self._day_callbacks) == 8, "previous day incomplete")
            self._rows_complete(self._current_session)
        _need(len(self._visited) < len(self.reference) and session == self.reference[len(self._visited)],
              "raw day order changed")
        expected_sources = self.source_by_day[session]
        _need(isinstance(day["original_chunks"], Mapping)
              and set(day["original_chunks"]) == set(expected_sources), "raw target chunks changed")
        _equal(day["original_sources"], expected_sources, "day.original_sources")
        _equal(set(day["original_sessions"]), set(expected_sources), "day.original_sessions")
        _equal(day["sources"], [expected_sources[symbol] for symbol in sorted(expected_sources)], "day.sources")
        _equal(day["session_open"], _opening(session), "day.opening")
        _equal(day["session_close"], _closing(session), "day.closing")
        _equal(day["retained_price_symbols"], sorted(expected_sources), "day.all_target_prices")
        _equal(day["consumed_target_chunks"], len(expected_sources), "day.chunk_count")
        _equal(day["consumed_price_rows"], sum(row["input_rows"] for row in expected_sources.values()), "day.row_count")
        _equal(day["original_union_complete"], len(self._visited) == len(self.reference) - 1, "day.EOF")
        minutes, opens, detections = {}, {}, {}
        for symbol in sorted(expected_sources):
            raw = _raw_session(day["original_chunks"][symbol], session, symbol)
            _equal(raw["source"], expected_sources[symbol], "raw.source_receipt")
            supplied = day["original_sessions"][symbol]
            _need(type(supplied) is Session, "original raw Session required")
            _equal(supplied.opening, raw["opening"], "raw.session.opening")
            _equal(supplied.minutes, raw["minutes"], "raw.session.minutes")
            _equal(supplied.prefix, raw["bars"], "raw.session.prefix")
            _equal(supplied.gaps, raw["gaps"], "raw.session.gaps")
            _equal(supplied.source_offsets_ms, raw["offsets"], "raw.session.offsets")
            minutes.update({(symbol, at): values for at, values in raw["minutes"].items()})
            opens.update({(symbol, at): value for at, value in raw["opens"].items()})
            detections[symbol] = _detect(raw, self.mode)
            del raw
        _equal(day["minute_bars"], minutes, "raw.all_minute_bars")
        _equal(day["open_prices"], opens, "raw.all_open_prices")
        self._raw_day = {"session": session, "session_open": _opening(session), "session_close": _closing(session),
                         "minute_bars": minutes, "open_prices": opens}
        self._detections = detections
        self._current_session, self._day_identity = session, id(day)
        self._day_callbacks = set()
        self._visited.append(session)

    def _rows_complete(self, session):
        for timing in POLICIES:
            _need(len(self.records[timing, session]) == len(self.members[timing, session]),
                  "original signal rows incomplete")

    def verify_signal_row(self, *, day_input, timing_policy, original_target, day_index, compact_row, candidate):
        self._bind_day(day_input)
        session = day_input["session"]
        _need(timing_policy in POLICIES and type(day_index) is int
              and 0 <= day_index < len(self.reference) and self.reference[day_index] == session,
              "original membership index changed")
        records = self.records[timing_policy, session]
        targets = self.members[timing_policy, session]
        _need(len(records) < len(targets), "duplicate or extra signal row")
        target = targets[len(records)]
        _equal(original_target, target, "row.original_target")
        symbol = target["symbol"]
        expected = {"session": session, "symbol": symbol, "membership_day_index": day_index,
                    "source_index": self.source_indices[session, symbol], "membership_target": target,
                    **self._detections[symbol], "entry_at": None, "cap_exit_at": None,
                    "structural_stop": None, "clock_eligible": None, "clock_ineligible_reason": None}
        plan = None
        if expected["signal_state"] == "signal":
            decision = expected["decision_at"]
            opening, closing = _opening(session), _closing(session)
            entry = opening + ((decision - opening) // FIVE + 1) * FIVE
            cap = entry + 60 * MINUTE
            eligible = opening < decision < entry < cap < closing
            if self.mode == "H4":
                _need(decision <= opening + 240 * MINUTE, "H4 cutoff changed")
            else:
                _equal((decision, entry, cap), (closing - 78 * MINUTE, closing - 75 * MINUTE,
                                               closing - 15 * MINUTE), "H5 fixed clocks")
            expected.update(entry_at=entry, cap_exit_at=cap, clock_eligible=eligible,
                            clock_ineligible_reason=None if eligible else "same_day_cap_infeasible")
            if eligible:
                _need(type(candidate) is CampaignCandidate, "typed H45 candidate required")
                plan = {"strategy": self.mode, "symbol": symbol, "decision_at": decision,
                        "entry_at": entry, "cap_exit_at": cap, "structural_stop": None}
        _equal(candidate, plan, "row.original_candidate")
        _equal(compact_row, expected, "signal_row")
        records.append({"row": expected, "plan": plan})
        return {"verified": True, "policy_id": self.policy_id, "timing_policy": timing_policy,
                "session": session, "symbol": symbol}

    def _plans_and_coverage(self, timing, session):
        records = self.records[timing, session]
        _need(len(records) == len(self.members[timing, session]), "signal rows incomplete before replay")
        states = Counter(record["row"]["signal_state"] for record in records)
        ineligible = sum(record["row"]["clock_eligible"] is False for record in records)
        plans = sorted((record["plan"] for record in records if record["plan"] is not None),
                       key=lambda row: (row["decision_at"], row["symbol"], row["strategy"]))
        coverage = {"by_setup": {self.mode: {"targets": len(records), "signals": states["signal"],
                    "known_no_signal": states["known_no_signal"], "unknown_signals": states["unknown"],
                    "clock_ineligible": ineligible}}, "unknown_signal_units": states["unknown"],
                    "clock_ineligible_units": ineligible, "recorded_signal_units": states["signal"]}
        return plans, coverage

    def verify_day(self, *, day_input, candidates, initial_state, result, compact, scenario):
        self._bind_day(day_input)
        _need(isinstance(scenario, Mapping) and set(scenario) == {
            "timing_policy", "cost_bps", "mode", "risk_scope", "compress_idle_minutes"}, "scenario fields changed")
        timing, cost, mode, scope = (scenario[key] for key in ("timing_policy", "cost_bps", "mode", "risk_scope"))
        _need(timing in POLICIES and type(cost) is int and cost in (33, 43) and mode == self.mode
              and scope in account_oracle.SCOPES and scenario["compress_idle_minutes"] is True,
              "unregistered H45 scenario")
        key = timing, cost, mode, scope
        _need(key not in self._day_callbacks, "duplicate day callback")
        session = day_input["session"]
        plans, coverage = self._plans_and_coverage(timing, session)
        _equal(candidates, plans, "day.original_candidates")
        _equal(compact["coverage"], coverage, "day.coverage")
        initial = (self._states.get(key, account_oracle._fresh(mode, cost, scope)) if scope == "continuous"
                   else account_oracle._fresh(mode, cost, scope))
        _equal(initial_state, initial, "initial_state")
        state, events, snapshots, closed = account_oracle._reconstruct(
            self._raw_day, plans, initial, compress_idle_minutes=True)
        _equal(result.state, state, "result.state")
        _equal(result.events, events, "result.events")
        _equal(result.snapshots, snapshots, "result.snapshots")
        _equal(result.scenario, "fresh_daily_counterfactual_no_continuous_weekly_monthly_claim"
               if scope == "day_local_counterfactual" else "observed_candidates_conditional_on_archive_availability",
               "result.scenario")
        expected = account_oracle._compact(self._raw_day, state, events, snapshots, closed, coverage)
        _equal(compact, expected, "compact")
        self._states[key] = state
        self._days.setdefault(key, []).append(expected)
        self._plans.setdefault(key, {})[session] = plans
        self._day_callbacks.add(key)
        if len(self._day_callbacks) == 8:
            self._rows_complete(session)
            self._raw_day = self._detections = None
        return {"verified": True, "session": session, "scenario": dict(scenario), "events": len(events)}

    def finish_verify(self, report):
        _need(not self._closed, "verifier already closed")
        _equal(self._visited, self.reference, "complete_original_calendar")
        _need(self._raw_day is None and len(self._day_callbacks) == 8, "last day callbacks incomplete")
        self._rows_complete(self.reference[-1])
        expected_keys = {(timing, cost, self.mode, scope) for timing in POLICIES
                         for cost in (33, 43) for scope in account_oracle.SCOPES}
        _equal(set(self._days), expected_keys, "verified_scenario_keys")
        fields = {"mode": "ec-h45-original-signal-cash-account", "policy_id": self.policy_id,
            "hypothesis_id": self.hypothesis_id, "arm": self.arm,
            "authentication_reference": self.authentication_reference,
            "reference_sessions": self.reference, "reference_weeks": self.weeks,
            "initial_capital": account_oracle.CAPITAL, "consumed_reference_sessions": len(self.reference),
            "consumed_target_chunks": len(self.sources), "consumed_price_rows": sum(row["input_rows"] for row in self.sources),
            "original_union_complete": True, "day_replay_count": len(self.reference) * 8,
            "idle_minutes_compressed": True, "performance_computed": True,
            "historical_timing_certified": False, "corporate_action_units_certified": False,
            "policy_results_not_pooled": True, "strategy_pass": False, "promotion_allowed": False,
            "historical_brokerage_account_claim": False, "actual_execution_authorized": False,
            "account_path_scope": "all_original_dated_targets_detected_conditioned_on_archive_availability",
            "continuous_scope": "identified_prefix_freezes_at_first_unknown_without_next_day_reset",
            "daily_scope": "fresh_initial_capital_day_local_gate_only_no_continuous_account_claim",
            "gross_scope": "net_plus_assumed_cost_on_same_fills_no_zero_cost_rerun",
            "original_requests": {setup: value["request_metadata"] for setup, value in self.originals.items()},
            **{name: self.account[name] for name in ("source_target_matrix", "upstream_calendar", "sources", "volume_sources")}}
        expected_fields = {*fields, "signal_rows", "signal_counts", "scenarios", "comparisons"}
        _need(isinstance(report, Mapping) and set(report) in (expected_fields, expected_fields | {"independent_verification"}),
              "report field set changed")
        for field, expected in fields.items():
            _equal(report[field], expected, field)
        _equal(set(report["signal_rows"]), set(POLICIES), "signal_rows.timing_keys")
        counts = {}
        for timing in POLICIES:
            rows = [record["row"] for session in self.reference for record in self.records[timing, session]]
            _equal([_digest(row) for row in report["signal_rows"][timing]], [_digest(row) for row in rows],
                   f"signal_rows.{timing}.original_rows")
            states = Counter(row["signal_state"] for row in rows)
            counts[timing] = {"targets": len(rows), "signals": states["signal"],
                "known_no_signal": states["known_no_signal"], "unknown_signals": states["unknown"],
                "clock_ineligible": sum(row["clock_eligible"] is False for row in rows)}
        _equal(report["signal_counts"], counts, "signal_counts")
        _equal(set(report["scenarios"]), {f"{timing}|{cost}" for timing in POLICIES for cost in (33, 43)},
               "scenario_keys")
        for key, days in self._days.items():
            timing, cost, _, scope = key
            _equal([day["session"] for day in days], self.reference, "scenario.full_calendar")
            result = report["scenarios"][f"{timing}|{cost}"]
            _equal(set(result), {"timing_policy", "cost_bps", "mode", "arm", *account_oracle.SCOPES}, "scenario.fields")
            for field, expected in {"timing_policy": timing, "cost_bps": cost, "mode": self.mode, "arm": self.arm}.items():
                _equal(result[field], expected, f"scenario.{field}")
            _equal(set(result[scope]), {"days", "own_sizing", "summary"}, "scenario.scope_fields")
            _equal(result[scope]["days"], days, "scenario.days")
            _equal(result[scope]["own_sizing"], {label: account_oracle._sizing(days, self.reference, label)
                                                for label in LABELS}, "scenario.own_sizing")
            for day in days:
                plans, coverage = self._plans_and_coverage(timing, day["session"])
                _equal(self._plans[key][day["session"]], plans, "scenario.original_plans")
                _equal(day["coverage"], coverage, "scenario.original_coverage")
        comparisons, comparison_days = {}, {}
        for timing in POLICIES:
            for cost in (33, 43):
                days = self._days[timing, cost, self.mode, "day_local_counterfactual"]
                identified = [row for row in days if row["status"] == "identified"]
                common = [row["session"] for row in identified]
                key = f"{timing}|{cost}"
                comparison_days[key] = identified
                comparisons[key] = {"timing_policy": timing, "cost_bps": cost,
                    "scope": "identified_fresh_daily_vs_uninvested_cash_reference",
                    "reference_sessions": self.reference, "reference_weeks": self.weeks,
                    "common_sessions": common, "excluded_sessions": [day for day in self.reference if day not in common],
                    "own_sizing": {self.arm: {label: account_oracle._sizing(identified, self.reference, label)
                                              for label in LABELS}}, "paired_sizing": {},
                    "cash_reference_net_per_initial_capital": Fraction(),
                    "status": "conditional_descriptive_only" if common else "no_common_identified_days"}
        # The complete 24 plans above precede every independent profit mean.
        for key, expected in comparisons.items():
            expected["accounts"] = {self.arm: account_oracle._summary(comparison_days[key], self.weeks, self.reference)}
            expected["paired_mean_daily_net_per_initial_capital"] = {}
        _equal(report["comparisons"], comparisons, "comparisons")
        for (timing, cost, _, scope), days in self._days.items():
            _equal(report["scenarios"][f"{timing}|{cost}"][scope]["summary"],
                   account_oracle._summary(days, self.weeks, self.reference), "scenario.summary")
        self._closed = True
        self._day_identity = self._current_session = None
        receipt = {"status": "verified", "verified": True, "policy_id": self.policy_id,
            "day_replays": len(self.reference) * 8,
            "unit_work": {"policy_families": 1, "account_mode": self.mode, "arms": [self.arm],
                "timing_policies": list(POLICIES), "assumed_cost_bps": [33, 43],
                "scopes": list(account_oracle.SCOPES), "scenario_scopes": 8,
                "own_label_planning_count": 24, "model_fits": 0, "threshold_searches": 0},
            "verified_components": ["raw_OHLC_and_unique_OPEN", "raw_source_metadata_and_ordered_linkage",
                "original_target_calendar_and_union", "original_H4_H5_detection", "all_signal_states_and_clocks",
                "mandatory_no_stop_original_plans", "independent_coverage", "complete_events",
                "initial_and_terminal_state", "minute_NAV", "current_risk_gates", "prefix_drawdown", "exposure",
                "compact_PnL_and_lots", "all_own_sizing_before_means", "aggregate_statistics",
                "identified_fresh_daily_cash_reference"],
            "statistical_float_tolerance": {"relative": 1e-12, "absolute": 1e-15},
            "unverified": ["source_byte_authentication", "original_report_to_frozen_projection_authentication",
                           "historical_market_availability", "corporate_action_price_unit_certification"]}
        if "independent_verification" in report:
            _equal(report["independent_verification"], receipt, "independent_verification")
        return receipt
