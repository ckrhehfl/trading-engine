"""Independent EC P04--P08 selection, account and aggregate reconstruction.

Only caller-authenticated, original-schema ReadinessInputs and raw day slots
supply expectations. No producer feature, selection, candidate, accounting or
summary function is used. Source bytes and raw-to-Session parsing remain the
source wrapper's obligations; historical availability is not certified here.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from fractions import Fraction
import hashlib
import json

from research import intraday_campaign_feature_verifier as feature_oracle
from research import intraday_campaign_independent as account_oracle
from research import intraday_campaign_policy_selection_verifier as selection_oracle
from research.intraday_campaign_account import CampaignCandidate
from research.intraday_campaign_context_readiness import ReadinessInputs
from research.intraday_context_experiment import PRICE_BASIS
from research import intraday_context_preparation as ea
from research import intraday_pattern_calibration as cc


VerificationError = account_oracle.VerificationError
POLICIES = ("D+1", "D+2")
LABELS = ("net_pnl", "gross_pnl_same_fills")
UNITS = {
    "P04": ("A", ("EA-A",), ("control", "selected")),
    "P05": ("A", ("EA-A",), ("control", "selected")),
    "P06": ("A", ("EA-A",), ("control", "volume_only", "market_only", "selected")),
    "P07": ("B", ("EA-B",), ("control", "selected")),
    "P08": ("A+B", ("EA-A", "EA-B"), ("control", "selected")),
}
REQUIRED = {
    "P04": (),
    "P05": ("target_open_to_signal_return", "peer_open_to_signal_return", "peer_last15_return"),
    "P06": ("target_open_to_signal_return", "peer_open_to_signal_return", "peer_last15_return"),
    "P07": ("peer_first30_return", "peer_last15_return"),
    "P08": ("peer_first30_return", "peer_open_to_signal_return", "peer_last15_return"),
}
_equal, _plain = account_oracle._equal, account_oracle._plain


def _need(condition, reason):
    if not condition:
        raise VerificationError(reason)


def _digest(row):
    def canonical(value):
        if type(value) is Fraction:
            return ["fraction", str(value.numerator), str(value.denominator)]
        if type(value) is datetime:
            return ["datetime", value.isoformat()]
        if isinstance(value, Mapping):
            _need(all(type(key) is str for key in value), "selection evidence string keys required")
            return ["mapping", [[key, canonical(value[key])] for key in sorted(value)]]
        if type(value) in (tuple, list):
            return ["sequence", [canonical(item) for item in value]]
        _need(type(value) in (str, int, bool, type(None)), "unsupported selection evidence scalar")
        return [type(value).__name__, value]

    encoded = json.dumps(canonical(row), sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).digest()


class PolicyAccountVerifier:
    """Check every original row before any arm replay, then verify publication."""

    def __init__(self, policy_id, a, b, *, authentication_reference):
        _need(type(policy_id) is str and policy_id in UNITS, "unregistered policy")
        _need(type(authentication_reference) is str and bool(authentication_reference.strip()),
              "authentication reference required")
        self.policy_id = policy_id
        self.mode, self.setups, self.arms = UNITS[policy_id]
        self.authentication_reference = authentication_reference
        self.originals = {}
        for value, setup in ((a, "EA-A"), (b, "EA-B")):
            _need(type(value) is ReadinessInputs and value.setup_id == setup,
                  "original ReadinessInputs required")
            _need(type(value.authentication_reference) is str and bool(value.authentication_reference.strip()),
                  "original authentication reference required")
            self.originals[setup] = {name: _plain(getattr(value, name)) for name in
                ("account_projection", "target_rows", "request_metadata", "volume_sidecar", "volume_verification")}
        self.account = self.originals["EA-A"]["account_projection"]
        for name in ("reference_sessions", "reference_weeks", "source_target_matrix", "upstream_calendar", "sources"):
            _equal(self.originals["EA-B"]["account_projection"][name], self.account[name], f"original.{name}")
        self.reference = self.account["reference_sessions"]
        _need(bool(self.reference) and self.reference == sorted(set(self.reference)), "original calendar required")
        self.weeks = sorted({account_oracle._week(day) for day in self.reference})
        _equal(self.account["reference_weeks"], self.weeks, "original.reference_weeks")
        self.sources = self.account["sources"]
        self.source_indices = {(row["session"], row["symbol"]): i for i, row in enumerate(self.sources)}
        _need(len(self.source_indices) == len(self.sources), "duplicate original source")
        self.source_by_day = {day: {} for day in self.reference}
        for source in self.sources:
            _need(source["session"] in self.source_by_day, "source outside calendar")
            self.source_by_day[source["session"]][source["symbol"]] = source
        sidecar = self.originals["EA-A"]["volume_sidecar"]
        _need(isinstance(sidecar, Mapping), "verified original A volume sidecar required")
        _equal(self.originals["EA-A"]["volume_verification"], feature_oracle._volume_receipt(sidecar),
               "original.volume_verification")
        self.volume = {(timing, row["session"], row["symbol"]): row for timing in POLICIES
                       for row in sidecar["variants"][timing]["days"]}
        self.rows = {(timing, day): [(setup, row) for setup in self.setups
                     for row in self.originals[setup]["target_rows"][timing] if row["session"] == day]
                     for timing in POLICIES for day in self.reference}
        self.records = {(timing, day): [] for timing in POLICIES for day in self.reference}
        self.row_digests = {timing: [] for timing in POLICIES}
        self.oracles = {arm: account_oracle.Verifier() for arm in self.arms}
        self.cell_counts = {timing: dict.fromkeys(("neither", "volume", "market", "both"), 0)
                            for timing in POLICIES} if policy_id == "P06" else {}
        self._day_identity = None
        self._current_session = None
        self._visited = []
        self._closed = False

    def _rows_complete(self, session):
        for timing in POLICIES:
            _need(len(self.records[timing, session]) == len(self.rows[timing, session]),
                  "original selection rows missing")

    def _bind_day(self, day):
        _need(not self._closed, "verifier already closed")
        session = day["session"]
        if self._current_session == session:
            _need(id(day) == self._day_identity, "raw day binding changed")
            return
        if self._current_session is not None:
            self._rows_complete(self._current_session)
        _need(len(self._visited) < len(self.reference)
              and session == self.reference[len(self._visited)], "raw day order changed")
        expected = self.source_by_day[session]
        _equal(day["original_sources"], expected, "day.original_sources")
        _need(isinstance(day["original_sessions"], Mapping)
              and set(day["original_sessions"]) == set(expected), "original raw session slots changed")
        _equal(day["sources"], [expected[symbol] for symbol in sorted(expected)], "day.sources")
        _equal(day["session_open"], cc.session_open(session), "day.opening")
        _equal(day["session_close"], cc.session_close(session), "day.closing")
        _equal(day["consumed_target_chunks"], len(expected), "day.consumed_target_chunks")
        _equal(day["consumed_price_rows"], sum(source["input_rows"] for source in expected.values()),
               "day.consumed_price_rows")
        _equal(day["original_union_complete"], len(self._visited) == len(self.reference) - 1, "day.EOF")
        retained = {row["symbol"] for original in self.originals.values() for timing in POLICIES
                    for row in original["target_rows"][timing]
                    if row["session"] == session and row["signal_state"] == "signal"}
        _equal(day["retained_price_symbols"], sorted(retained), "day.retained_price_symbols")
        # Retain identity only: keeping a raw day here would overlap tomorrow's
        # raw allocation before its first callback and defeat day streaming.
        self._day_identity = id(day)
        self._current_session = session
        self._visited.append(session)

    def _arguments(self, timing, setup, original, day):
        session = original["session"]
        index = original["membership_day_index"]
        _need(type(index) is int and 0 <= index < len(self.reference)
              and self.reference[index] == session, "original membership index changed")
        membership = self.account["source_target_matrix"][timing]["days"][index]
        _equal(membership["session"], session, "original.membership.session")
        _need(original["membership_target"] in membership["targets"], "original membership target changed")
        _equal(original["membership_target"]["symbol"], original["symbol"], "original.membership.symbol")
        source_index = original["source_index"]
        _need(type(source_index) is int and 0 <= source_index < len(self.sources), "invalid original source index")
        source = self.sources[source_index]
        _equal((source["session"], source["symbol"]), (session, original["symbol"]), "original.source_identity")
        raw = source["raw_chunk_metadata"]
        observed_day = date.fromisoformat(membership["observation_date"])
        observed = datetime.combine(observed_day, datetime.min.time(), cc.KST) + timedelta(days=1, microseconds=-1)
        provenance = ea.Provenance(observed, datetime.fromisoformat(membership["available_at"]),
            max(datetime.fromisoformat(value) for value in membership["retrieved_at"].values()),
            membership["source"], membership["is_final"],
            hashlib.sha256(feature_oracle._json_bytes(membership, "original.membership")).hexdigest(), "assumed")
        universe = ea.MonthlyUniverse(date.fromisoformat(session).replace(day=1),
            tuple(member["symbol"] for member in membership["targets"]),
            datetime.fromisoformat(membership["selected_at"]), provenance)
        target = ea.Target(original["symbol"], date.fromisoformat(session), timing,
            cc.session_open(session), cc.session_close(session), PRICE_BASIS,
            ea.PriceSource(datetime.fromisoformat(raw["retrieved_at"]), raw["source"], raw["is_final"],
                           source["data_vintage"], "assumed"))
        return {"target": target, "universe": universe, "sessions": day["original_sessions"],
                "sources": day["original_sources"], "setup_id": setup,
                "observed_end": datetime.fromisoformat(original["observed_through"]),
                "decision_at": datetime.fromisoformat(original["decision_at"]),
                "structural_stop": original["structural_stop"], "inputs_authenticated": True,
                "authentication_reference": self.authentication_reference, "price_unit": "KRW"}

    def verify_selection_row(self, *, day_input, timing_policy, setup_id, original_row,
                             prepared, decision, volume_value, candidate, auxiliary_decisions, compact_row):
        self._bind_day(day_input)
        key = (timing_policy, day_input["session"])
        _need(key in self.rows, "unregistered selection timing")
        offset = len(self.records[key])
        _need(offset < len(self.rows[key]), "extra original selection row")
        expected_setup, original = self.rows[key][offset]
        _equal(setup_id, expected_setup, "row.setup_id")
        _equal(original_row, original, "row.original")
        expected = {name: original[name] for name in ("session", "symbol", "original_row_index",
            "membership_day_index", "source_index", "signal_state", "signal_reason", "observed_through", "decision_at")}
        expected.update(setup_id=setup_id, clock_eligible=None, entry_at=None, cap_exit_at=None,
                        selection=None, auxiliary_decisions={}, interaction_cell=None, feature_evidence=None)
        plan, verified_decision, cell = None, None, None
        if original["signal_state"] == "signal":
            arguments = self._arguments(timing_policy, setup_id, original, day_input)
            end, at = arguments["observed_end"], arguments["decision_at"]
            opening, closing = arguments["target"].opening, arguments["target"].closing
            _need(end == datetime.fromisoformat(original["signal_bar_end"])
                  and end - datetime.fromisoformat(original["signal_bar_start"]) == 5 * cc.MINUTE
                  and datetime.fromisoformat(original["setup_bar_end"]) <= end - 5 * cc.MINUTE
                  and (end - opening) % (5 * cc.MINUTE) == timedelta(0)
                  and at == end + 2 * cc.MINUTE, "original signal clock changed")
            feature_oracle.verify_features(prepared, **arguments)
            stored = self.volume.get((timing_policy, original["session"], original["symbol"]))
            expected_volume = (stored["relative_volume"]["value"] if self.policy_id in ("P04", "P06")
                               and stored is not None and stored["volume_state"] == "observed" else None)
            _equal(volume_value, expected_volume, "row.volume_value")
            verified_decision = selection_oracle.verify_selection(self.policy_id, prepared,
                volume_value=expected_volume, selection=decision)["selection"]
            auxiliary = {}
            if self.policy_id == "P06" and verified_decision["state"] in ("selected", "rejected"):
                _need(isinstance(auxiliary_decisions, Mapping)
                      and set(auxiliary_decisions) == {"P04", "P05"}, "P06 auxiliary decisions required")
                for policy in ("P04", "P05"):
                    auxiliary[policy] = selection_oracle.verify_selection(policy, prepared,
                        volume_value=expected_volume, selection=auxiliary_decisions[policy])["selection"]
                volume, market = (auxiliary[name]["state"] == "selected" for name in ("P04", "P05"))
                cell = "both" if volume and market else "volume" if volume else "market" if market else "neither"
            _equal(auxiliary_decisions, auxiliary, "row.auxiliary_decisions")
            entry, cap = end + 5 * cc.MINUTE, end + 65 * cc.MINUTE
            eligible = opening < at < entry < cap < closing
            if eligible:
                _need(type(candidate) is CampaignCandidate, "typed original candidate required")
                plan = {"strategy": setup_id.removeprefix("EA-"), "symbol": original["symbol"],
                        "decision_at": at, "entry_at": entry, "cap_exit_at": cap,
                        "structural_stop": original["structural_stop"]}
            _equal(candidate, plan, "row.candidate")
            evidence = {"proxy_id": "EC-common-observed-peer-min5",
                "authentication_reference": self.authentication_reference,
                "common_peer_source_indices": [self.source_indices[original["session"], symbol]
                                                for symbol in prepared.common_peers],
                "required_values": {name: {"value": prepared.features[name].value,
                    "reasons": prepared.features[name].reasons,
                    "observation_end": opening + 30 * cc.MINUTE if name == "peer_first30_return" else end,
                    "available_at": at if prepared.features[name].value is not None else None}
                    for name in REQUIRED[self.policy_id]}, "volume_value": expected_volume}
            expected.update(clock_eligible=eligible, entry_at=entry, cap_exit_at=cap,
                selection=verified_decision, auxiliary_decisions=auxiliary, interaction_cell=cell,
                feature_evidence=evidence)
        else:
            _need(prepared is None and decision is None and candidate is None and volume_value is None,
                  "nonsignal computation retained")
            _equal(auxiliary_decisions, {}, "nonsignal.auxiliary_decisions")
        _equal(compact_row, expected, "selection_row")
        self.records[key].append({"setup": setup_id, "state": original["signal_state"],
            "eligible": expected["clock_eligible"], "selection": verified_decision,
            "candidate": plan, "cell": cell})
        self.row_digests[timing_policy].append(_digest(expected))
        if cell is not None:
            self.cell_counts[timing_policy][cell] += 1
        return {"verified": True, "policy_id": self.policy_id, "timing_policy": timing_policy,
                "setup_id": setup_id, "original_row_index": original["original_row_index"]}

    def _plans_and_coverage(self, timing, session, arm):
        rows = self.records[timing, session]
        _need(len(rows) == len(self.rows[timing, session]), "selection rows incomplete before account replay")
        counts, plans = {}, []
        for setup in self.setups:
            own = [row for row in rows if row["setup"] == setup]
            counts[setup] = {"targets": len(own), "signals": sum(row["state"] == "signal" for row in own),
                "known_no_signal": sum(row["state"] == "known_no_signal" for row in own),
                "unknown_signals": sum(row["state"] == "unknown" for row in own),
                "clock_ineligible": sum(row["eligible"] is False for row in own),
                "feature_unknown": sum(row["selection"] is not None and row["selection"]["state"] == "unknown" for row in own),
                "support_observed": sum(row["selection"] is not None and row["selection"]["state"] in ("selected", "rejected") for row in own)}
        control_count = 0
        for row in rows:
            choice = row["selection"]
            if not row["eligible"] or choice["state"] not in ("selected", "rejected"):
                continue
            control_count += 1
            admit = (arm == "control" or arm == "selected" and choice["state"] == "selected"
                     or arm == "volume_only" and row["cell"] in ("volume", "both")
                     or arm == "market_only" and row["cell"] in ("market", "both"))
            if admit:
                plans.append(row["candidate"])
        plans.sort(key=lambda row: (row["decision_at"], row["symbol"], row["strategy"]))
        coverage = {"by_setup": counts,
            "unknown_signal_units": sum(row["unknown_signals"] for row in counts.values()),
            "clock_ineligible_units": sum(row["clock_ineligible"] for row in counts.values()),
            "recorded_signal_units": sum(row["signals"] for row in counts.values()),
            "unknown_feature_units": sum(row["feature_unknown"] for row in counts.values()),
            "eligible_control_candidates": control_count, "eligible_arm_candidates": len(plans)}
        return plans, coverage

    def verify_day(self, *, arm, day_input, candidates, initial_state, result, compact, scenario):
        self._bind_day(day_input)
        _need(type(arm) is str and arm in self.oracles, "unregistered account arm")
        _need(isinstance(scenario, Mapping) and set(scenario) == {
            "timing_policy", "cost_bps", "mode", "risk_scope", "compress_idle_minutes"}, "scenario fields changed")
        _need(scenario["timing_policy"] in POLICIES and type(scenario["cost_bps"]) is int
              and scenario["cost_bps"] in (33, 43) and scenario["mode"] == self.mode
              and scenario["risk_scope"] in account_oracle.SCOPES
              and scenario["compress_idle_minutes"] is True, "unregistered account scenario")
        plans, coverage = self._plans_and_coverage(scenario["timing_policy"], day_input["session"], arm)
        _equal(candidates, plans, "day.original_selected_candidates")
        _equal(compact["coverage"], coverage, "day.independent_coverage")
        return self.oracles[arm].verify_day(day_input=day_input, candidates=plans,
            initial_state=initial_state, result=result, compact=compact, scenario=scenario)

    def finish_verify(self, report):
        """Verify all plans before recomputing summaries with existing oracles."""
        _need(not self._closed, "verifier already closed")
        _equal(self._visited, self.reference, "complete_original_calendar")
        self._rows_complete(self.reference[-1])
        for field, expected in {"mode": "ec-p04-p08-support-matched-account-comparison",
            "policy_id": self.policy_id, "reference_sessions": self.reference, "reference_weeks": self.weeks,
            "initial_capital": account_oracle.CAPITAL, "consumed_reference_sessions": len(self.reference),
            "consumed_target_chunks": len(self.sources), "consumed_price_rows": sum(row["input_rows"] for row in self.sources),
            "original_union_complete": True, "day_replay_count": len(self.reference) * len(self.arms) * 8,
            "idle_minutes_compressed": True, "performance_computed": True,
            "strategy_pass": False, "promotion_allowed": False, "historical_brokerage_account_claim": False,
            "proxy_id": "EC-common-observed-peer-min5", "authentication_reference": self.authentication_reference,
            "historical_timing_certified": False, "corporate_action_units_certified": False,
            "volume_availability_reauthenticated": False, "policy_results_not_pooled": True,
            "actual_execution_authorized": False, "interaction_cell_counts": self.cell_counts,
            "account_path_scope": "recorded_detectable_candidates_conditioned_on_archive_availability",
            "continuous_scope": "identified_prefix_freezes_at_first_unknown_without_next_day_reset",
            "daily_scope": "fresh_initial_capital_day_local_gate_only_no_continuous_account_claim",
            "gross_scope": "net_plus_assumed_cost_on_same_fills_no_zero_cost_rerun",
            "original_requests": {setup: value["request_metadata"] for setup, value in self.originals.items()},
            **{name: self.account[name] for name in ("source_target_matrix", "upstream_calendar", "sources", "volume_sources")},
        }.items():
            _equal(report[field], expected, field)
        _equal(set(report["selection_rows"]), set(POLICIES), "selection_rows.timing_keys")
        for timing in POLICIES:
            _equal([_digest(row) for row in report["selection_rows"][timing]], self.row_digests[timing],
                   f"selection_rows.{timing}.verified_original_rows")
        keys = {(timing, cost, self.mode, scope) for timing in POLICIES
                for cost in (33, 43) for scope in account_oracle.SCOPES}
        _equal(set(report["scenarios"]), {f"{timing}|{cost}|{arm}" for timing in POLICIES
               for cost in (33, 43) for arm in self.arms}, "scenario_keys")
        for arm, oracle in self.oracles.items():
            _equal(set(oracle._days), keys, f"{arm}.verified_scenarios")
            _equal(list(oracle._sources), self.reference, f"{arm}.verified_source_days")
            for index, session in enumerate(self.reference):
                sources = self.source_by_day[session]
                retained = {row["symbol"] for original in self.originals.values() for timing in POLICIES
                    for row in original["target_rows"][timing]
                    if row["session"] == session and row["signal_state"] == "signal"}
                expected_receipt = {"sources": [sources[symbol] for symbol in sorted(sources)],
                    "retained_price_symbols": sorted(retained), "consumed_target_chunks": len(sources),
                    "consumed_price_rows": sum(row["input_rows"] for row in sources.values()),
                    "original_union_complete": index == len(self.reference) - 1}
                _equal(oracle._sources[session], expected_receipt, f"{arm}.original_source_receipt")
                _equal(oracle._clocks[session], (cc.session_open(session), cc.session_close(session)),
                       f"{arm}.original_session_clocks")
            for key, days in oracle._days.items():
                timing, cost, _, scope = key
                _equal([day["session"] for day in days], self.reference, "scenario.full_calendar")
                _equal(oracle._compression[key], {day: True for day in self.reference}, "scenario.compression")
                result = report["scenarios"][f"{timing}|{cost}|{arm}"]
                _equal(set(result), {"timing_policy", "cost_bps", "mode", "arm", *account_oracle.SCOPES}, "scenario.fields")
                for field, expected in {"timing_policy": timing, "cost_bps": cost, "mode": self.mode, "arm": arm}.items():
                    _equal(result[field], expected, f"scenario.{field}")
                _equal(result[scope]["days"], days, "scenario.days")
                _equal(result[scope]["own_sizing"], {label: account_oracle._sizing(days, self.reference, label)
                       for label in LABELS}, "scenario.own_sizing")
                _equal(set(result[scope]), {"days", "own_sizing", "summary"}, "scenario.scope_fields")
                for day in days:
                    plans, coverage = self._plans_and_coverage(timing, day["session"], arm)
                    _equal(oracle._plans[key][day["session"]], plans, "scenario.retained_candidates")
                    _equal(day["coverage"], coverage, "scenario.retained_coverage")
        paired = [("selected", "control")]
        if self.policy_id == "P06":
            paired += [("volume_only", "control"), ("market_only", "control"),
                       ("selected", "volume_only"), ("selected", "market_only")]
        comparisons, comparison_days = {}, {}
        for timing in POLICIES:
            for cost in (33, 43):
                all_days = {arm: oracle._days[timing, cost, self.mode, "day_local_counterfactual"]
                            for arm, oracle in self.oracles.items()}
                identified = [{row["session"] for row in days if row["status"] == "identified"}
                              for days in all_days.values()]
                common = [day for day in self.reference if all(day in dates for dates in identified)]
                selected = {arm: [row for row in days if row["session"] in common] for arm, days in all_days.items()}
                own = {arm: {label: account_oracle._sizing(days, self.reference, label) for label in LABELS}
                       for arm, days in selected.items()}
                plans = {f"{left}_minus_{right}": account_oracle._sizing(selected[left], self.reference, peer=selected[right])
                         for left, right in paired}
                comparison_days[f"{timing}|{cost}"] = selected
                comparisons[f"{timing}|{cost}"] = {"timing_policy": timing, "cost_bps": cost,
                    "scope": "support_matched_common_identified_fresh_daily_accounts",
                    "reference_sessions": self.reference, "reference_weeks": self.weeks,
                    "common_sessions": common, "excluded_sessions": [day for day in self.reference if day not in common],
                    "own_sizing": own, "paired_sizing": plans,
                    "status": "conditional_descriptive_only" if common else "no_common_identified_days"}
        # Complete every scenario/common/paired own-label plan before any
        # independent public mean, including plans for the later timing/costs.
        for key, expected in comparisons.items():
            summaries = {arm: account_oracle._summary(days, self.weeks, self.reference)
                         for arm, days in comparison_days[key].items()}
            expected["accounts"] = summaries
            expected["paired_mean_daily_net_per_initial_capital"] = {
                f"{left}_minus_{right}": summaries[left]["mean_daily_net_per_initial_capital"]
                - summaries[right]["mean_daily_net_per_initial_capital"] if expected["common_sessions"] else None
                for left, right in paired}
        _equal(report["comparisons"], comparisons, "comparisons")
        for arm, oracle in self.oracles.items():
            for (timing, cost, _, scope), days in oracle._days.items():
                _equal(report["scenarios"][f"{timing}|{cost}|{arm}"][scope]["summary"],
                       account_oracle._summary(days, self.weeks, self.reference), "scenario.summary")
        self._closed = True
        self._day_identity = None
        self._current_session = None
        return {"status": "verified", "verified": True, "policy_id": self.policy_id,
            "day_replays": len(self.reference) * len(self.arms) * 8,
            "unit_work": {"policy_families": 1, "account_mode": self.mode,
                "arms": list(self.arms), "timing_policies": list(POLICIES),
                "assumed_cost_bps": sorted({key[1] for key in keys}), "scopes": list(account_oracle.SCOPES),
                "scenario_scopes": len(self.arms) * 8,
                "own_label_planning_count": len(self.arms) * 24 + len(paired) * 4,
                "model_fits": 0, "threshold_searches": 0},
            "verified_components": ["all_original_selection_rows", "raw_causal_feature_reconstruction",
                "original_volume_values", "fixed_independent_selection", "original_candidate_clocks",
                "support_matched_arm_admission", "source_inventory_and_full_calendar", "independent_coverage",
                "complete_events", "initial_and_terminal_state", "minute_NAV", "current_risk_gates",
                "prefix_drawdown", "exposure", "compact_PnL_and_lots", "all_own_and_paired_sizing",
                "aggregate_statistics", "common_date_comparisons", "P06_input_cells"],
            "statistical_float_tolerance": {"relative": 1e-12, "absolute": 1e-15},
            "unverified": ["source_byte_authentication", "original_report_to_frozen_projection_authentication",
                "raw_archive_to_Session_and_OPEN_parsing", "historical_market_availability",
                "stored_volume_historical_availability", "corporate_action_price_unit_certification"]}
