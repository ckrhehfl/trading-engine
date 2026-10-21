"""Hindsight minute-path descriptions on the frozen CH selected-complete mask.

Extrema and minute-bucket touches are descriptive labels, never executable
entries, exits, fills, policy returns or evidence for strategy promotion.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
from statistics import median
import sys

from research import experiment_log
from research import intraday_entry_strength_comparison as ch


cg, horizons, cd, cc = ch.cg, ch.horizons, ch.cd, ch.cc
ROOT = ch.ROOT
SPECIFICATION_ID = "intraday-signal-path-diagnostics-v1"
MODE = "fixed-cohort-hindsight-signal-path-diagnostics"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-signal-path-diagnostics-v1.json"
WINDOW = dict(ch.WINDOW)
FAMILIES = ch.FAMILIES
QUALIFIED_HORIZONS = ch.QUALIFIED_HORIZONS
EXCLUDED_HORIZONS = ch.EXCLUDED_HORIZONS
COST = Fraction(33, 10000)
PARAMETERS = {
    "response_horizons_minutes": {family: list(values) for family, values in QUALIFIED_HORIZONS.items()},
    "cost_floor_bps": "33", "cost_touch": "strict_greater_up_strict_less_down_equality_not_exceed",
    "cohort": "exact_ch_selected_complete_mask_no_reselection_or_short_horizon_rescue",
    "signal_anchor": "close_of_minute_ending_at_original_signal_bar_end_information_only_available_at_decision",
    "decision": "original_signal_bar_end_plus_two_minutes_assumed",
    "entry": "original_next_five_minute_boundary_strictly_after_decision",
    "post_path": "all_valid_minutes_entry_inclusive_to_entry_plus_horizon_exclusive",
    "pre_path": "signal_end_inclusive_to_entry_exclusive_missing_preserved_as_unknown",
    "phases": ["unavailable", "postdecision_wait", "post_entry"],
    "touch_timing": "minute_buckets_not_exact_instants_or_fills_same_minute_order_unknown",
    "output_scope": "hindsight_extrema_cost_touch_and_timing_descriptions_no_policy_or_performance_family"}
UPSTREAM_SHA256 = "f758e48e7aa0431f9dc2ea1cb50f8925bfbd8588669fb99a50c87ca334fbf028"
INPUT_COMPRESSED_SHA256 = cg.INPUT_COMPRESSED_SHA256
INPUT_STREAM_SHA256 = cg.INPUT_STREAM_SHA256
REGISTERED_PATHS = {
    "upstream_path": "/home/minjun4897/research-evidence/intraday-ch-entry-strength-comparison-20261021-v1/report.json",
    "input_archive_path": cg.REGISTERED_PATHS["input_archive_path"],
    "output_directory": "/home/minjun4897/research-evidence/intraday-ci-signal-paths-20261021-v1",
    "canonical_runs_path": cg.REGISTERED_PATHS["canonical_runs_path"]}
RESOURCES = dict(cg.RESOURCES)
EXPECTED_REFERENCE_SESSIONS = cg.EXPECTED_REFERENCE_SESSIONS
EXPECTED_SELECTED_COMPLETE = ch.EXPECTED_SELECTED_COMPLETE
EXPECTED_INPUT_COUNTS = cg.EXPECTED_INPUT_COUNTS
FLAGS = {**cc.FLAGS, "exploratory_only": True, "diagnostic_only": True,
    "hindsight_extrema": True, "actual_portfolio_pnl": False, "family_adopted": False,
    "horizon_selected": False, "earlier_entry_policy_tested": False, "exit_policy_tested": False,
    "underpower_exception_applied": False, "comparison_performed": False,
    "historical_costs_certified": False}
ASSUMPTIONS = {
    "measurement": "hindsight minute OHLC extrema and strict assumed33bp touches on the exact CH selected-complete events; no new performance family or policy",
    "cohort": "conditional original120minute completeness and causal CG selection; all original CH target rows, unknowns and peers retained without replacement",
    "timing": "signal anchor close becomes information only at original decision S+2m; [S,D) highs are not missed tradable opportunities; [D,E) is postdecision wait",
    "availability": "path available_at=end+2m is an assumed future-label boundary, never a selection feature; raw retrieval is not historical publication",
    "touches": "strict high/low crossing is a minute bucket, not exact time, fill, take-profit or stop; simultaneous up/down bucket ordering is unknown",
    "anchors": "pre-entry touches use signal-close anchor; post-entry touches use entry-open anchor; their difference is not earlier-entry policy profit",
    "missingness": "missing pre-entry buckets remain unknown in the same event denominator; missing post-entry or signal-anchor prices refuse as an upstream contradiction",
    "cost": "33bp planning assumption only; no historical cost or fill certification and no mean of extrema minus costs",
    "source": "source, eligibility, liquidity, revision, calendar, historical availability and price-basis qualifications remain uncertified",
    "interpretation": "no threshold or horizon optimization, p-values, edge, policy adoption, holdout access, direction closure or promotion"}
ORDER_CLASSES = ("up_only", "down_only", "up_before_down", "down_before_up", "same_minute_order_unknown", "neither")
PHASES = ("unavailable", "postdecision_wait")
FIRST_PHASES = (*PHASES, "post_entry", "none", "unknown_pre_entry")
ORIGINAL_FIELDS = (*ch.ORIGINAL_FIELDS, *ch.SELECTION_FIELDS)
_canonical, _sha = cc._canonical, cc._sha


class PathRefusal(RuntimeError):
    """Stable reason codes omit saved evidence and credential values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "resources": RESOURCES, **REGISTERED_PATHS, "upstream_sha256": UPSTREAM_SHA256,
        "input_compressed_sha256": INPUT_COMPRESSED_SHA256, "input_stream_sha256": INPUT_STREAM_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise PathRefusal("specification_scope_mismatch")


def _matrix(report: Mapping) -> tuple[dict, list[str], dict]:
    if (report.get("study_id") != ch.SPECIFICATION_ID or report.get("mode") != ch.MODE
            or report.get("parameters") != ch.PARAMETERS or report.get("window") != WINDOW
            or any(report.get(key) != value for key, value in ch.FLAGS.items())
            or report.get("qualified_cells_power_prerequisite_met") is not True
            or type(report.get("upstream_calibration_requires_operator_choice")) is not bool
            or report.get("excluded_horizons_minutes") != {family: list(values) for family, values in EXCLUDED_HORIZONS.items()}
            or set(report.get("variants", {})) != {"D+1", "D+2"}):
        raise PathRefusal("wrong_upstream_study_or_scope")
    matrix, union, reference = {}, {}, None
    for key, variant in report["variants"].items():
        grouped = {}
        for row in variant["days"]:
            day, symbol = row["session"], row["symbol"]
            if (not WINDOW["start"] <= day <= WINDOW["end"] or not re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol)
                    or not re.fullmatch(r"[0-9a-f]{64}", row["data_vintage"])):
                raise PathRefusal("target_outside_registered_population")
            grouped.setdefault(day, []).append(row)
        dates = list(grouped)
        if not dates or dates != sorted(set(dates)) or (reference is not None and dates != reference):
            raise PathRefusal("target_matrix_lost_sessions")
        reference = dates
        for day, rows in grouped.items():
            names = {row["symbol"] for row in rows}
            if len(rows) != 20 or len(names) != 20:
                raise PathRefusal("target_population_changed")
            union.setdefault(day, set()).update(names)
            for row in rows:
                if set(row.get("families", {})) != set(FAMILIES):
                    raise PathRefusal("upstream_family_population_mismatch")
                for family, finding in row["families"].items():
                    if any(name not in finding for name in ORIGINAL_FIELDS):
                        raise PathRefusal("upstream_projection_incomplete")
                    status, state = finding["status"], finding["selection_state"]
                    reasons = finding["selection_unknown_reasons"]
                    if (status not in cd.STATES or state not in cg.SELECTION_STATES
                            or not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons)
                            or (state == "unknown") != bool(reasons)
                            or (status != "signal") != (state == "non_signal")):
                        raise PathRefusal("invalid_upstream_selection_state")
                    cohort = ("non_signal" if status != "signal" else "outside_original_complete_cohort"
                        if finding["original_pair_status"] != "complete" else "selected_complete" if state == "pass" else "not_selected")
                    if finding["label_cohort_state"] != cohort:
                        raise PathRefusal("upstream_selection_cohort_mismatch")
                    peers = finding["selected_controls"]
                    peer_names = [item["symbol"] for item in peers]
                    if (peer_names != sorted(set(peer_names)) or row["symbol"] in peer_names
                            or not set(peer_names) <= names or any(set(item) != {"symbol"} for item in peers)):
                        raise PathRefusal("invalid_upstream_peer_population")
                    if status != "signal":
                        if finding["event"] is not None or finding["responses"] or finding["original_pair_status"] is not None:
                            raise PathRefusal("upstream_nonsignal_has_event_or_labels")
                        continue
                    if (finding["original_pair_status"] not in {"complete", "unknown"}
                            or (finding["original_pair_status"] == "complete") != (bool(peers) and not finding["original_pair_unknown_reasons"])
                            or set(finding["event"]) != {"signal_bar_end", "decision_at"}
                            or set(finding["responses"]) != {str(h) for h in QUALIFIED_HORIZONS[family]}):
                        raise PathRefusal("invalid_upstream_event_or_labels")
                    for response in finding["responses"].values():
                        selected = cohort == "selected_complete"
                        if response.get("status") != ("measured" if selected else f"not_measured_{cohort}"):
                            raise PathRefusal("upstream_label_cohort_mismatch")
                        values = [response.get(name) for name in ("event_price_change", "control_mean_price_change", "excess_price_change")]
                        if selected:
                            event, peer, excess = map(horizons._ratio, values)
                            if event - peer != excess:
                                raise PathRefusal("upstream_paired_label_arithmetic_mismatch")
                        elif any(value is not None for value in values):
                            raise PathRefusal("upstream_unselected_has_labels")
        for family in FAMILIES:
            counts = cg._counts([row["families"][family] for row in variant["days"]])
            if any(variant["families"][family].get(name) != value for name, value in counts.items()):
                raise PathRefusal("upstream_counts_mismatch")
        matrix[key] = grouped
    if report.get("reference_sessions") != reference or sum(map(len, union.values())) > RESOURCES["maximum_chunks"]:
        raise PathRefusal("upstream_reference_population_mismatch")
    return matrix, reference, union


def _thresholds(price: Decimal) -> tuple[Decimal, Decimal]:
    # Multiplication is exact even when a valid source string has >28 digits.
    with localcontext() as context:
        context.prec = len(price.as_tuple().digits) + 8
        return price * Decimal("1.0033"), price * Decimal("0.9967")


def _diagnostic(minutes: Mapping, day: str, event: Mapping, horizon: int) -> dict:
    opening = cc.session_open(day)
    signal, decision = (horizons._instant(event[name]) for name in ("signal_bar_end", "decision_at"))
    elapsed = (signal - opening) / cc.MINUTE
    entry = opening + ((decision - opening) // cc.FIVE + 1) * cc.FIVE
    end = entry + horizon * cc.MINUTE
    if (not elapsed.is_integer() or signal <= opening or decision != signal + 2 * cc.MINUTE
            or end > cc.session_close(day)):
        raise PathRefusal("invalid_upstream_signal_decision_or_entry_boundary")
    s, d, e = (int((instant - opening) / cc.MINUTE) for instant in (signal, decision, entry))
    anchor = minutes.get(s - 1)
    if anchor is None:
        raise PathRefusal("upstream_signal_anchor_invalid")
    post = [(index, minutes.get(index)) for index in range(e, e + horizon)]
    if any(prices is None for _, prices in post):
        raise PathRefusal("upstream_complete_post_path_invalid")
    signal_price, entry_price = anchor[3], post[0][1][0]
    own_up, own_down = _thresholds(entry_price)
    signal_up, _ = _thresholds(signal_price)
    maximum = max(post, key=lambda item: item[1][1])
    minimum = min(post, key=lambda item: item[1][2])
    first_up = next((index for index, prices in post if prices[1] > own_up), None)
    first_down = next((index for index, prices in post if prices[2] < own_down), None)
    order = ("neither" if first_up is None and first_down is None else "down_only" if first_up is None
        else "up_only" if first_down is None else "same_minute_order_unknown" if first_up == first_down
        else "up_before_down" if first_up < first_down else "down_before_up")
    stamp = lambda index: opening + index * cc.MINUTE if index is not None else None
    mfe = Fraction(maximum[1][1]) / Fraction(entry_price) - 1
    mae = Fraction(minimum[1][2]) / Fraction(entry_price) - 1
    terminal = Fraction(post[-1][1][3]) / Fraction(entry_price) - 1
    pre = [(index, minutes.get(index)) for index in range(s, e)]
    missing = sum(prices is None for _, prices in pre)
    phases = {}
    for phase, start, stop in ((PHASES[0], s, d), (PHASES[1], d, e)):
        values = [(index, minutes.get(index)) for index in range(start, stop)]
        gaps = sum(prices is None for _, prices in values)
        touch = next((index for index, prices in values if prices is not None and prices[1] > signal_up), None)
        phases[phase] = {"status": "unknown" if gaps else "known", "missing_minute_buckets": gaps,
            "signal_anchor_cost_exceeded": None if gaps else touch is not None,
            "first_signal_anchor_cost_up_minute": None if gaps else stamp(touch)}
    global_touch = (next((index for index, prices in (*pre, *post) if prices[1] > signal_up), None)
        if not missing else None)
    first_phase = ("unknown_pre_entry" if missing else "none" if global_touch is None
        else "unavailable" if global_touch < d else "postdecision_wait" if global_touch < e else "post_entry")
    all_prices = [prices for _, prices in (*pre, *post)] if not missing else None
    return {"entry_label_start": entry, "end_label": end,
        "signal_anchor_available_at": decision, "path_available_at": end + 2 * cc.MINUTE,
        "signal_anchor_close": signal_price, "entry_open": entry_price,
        "post_entry": {"maximum_high": maximum[1][1], "minimum_low": minimum[1][2],
            "maximum_high_minute": stamp(maximum[0]), "minimum_low_minute": stamp(minimum[0]),
            "mfe": mfe, "mae": mae, "terminal_price_change": terminal, "giveback": mfe - terminal,
            "first_cost_up_minute": stamp(first_up), "first_cost_down_minute": stamp(first_down),
            "cost_touch_order": order, "mfe_exceeds_cost": mfe > COST,
            "exceeded_but_terminal_not_exceed": mfe > COST and terminal <= COST},
        "timing": {"pre_entry_status": "unknown" if missing else "known",
            "pre_entry_missing_minute_buckets": missing, "phases": phases,
            "first_signal_anchor_cost_up_phase": first_phase,
            "first_signal_anchor_cost_up_minute": stamp(global_touch),
            "entry_price_change_from_signal": Fraction(entry_price) / Fraction(signal_price) - 1,
            "signal_to_entry_minutes": e - s, "decision_to_entry_minutes": e - d,
            "pre_signal_anchor_exceeded_without_post_own_anchor_exceed": None if missing else
                any(item["signal_anchor_cost_exceeded"] for item in phases.values()) and first_up is None,
            "pre_exceed_without_post_by_phase": {phase: None if item["status"] == "unknown" else
                item["signal_anchor_cost_exceeded"] and first_up is None for phase, item in phases.items()},
            "full_signal_anchor_mfe": None if missing else Fraction(max(prices[1] for prices in all_prices)) / Fraction(signal_price) - 1,
            "full_signal_anchor_mae": None if missing else Fraction(min(prices[2] for prices in all_prices)) / Fraction(signal_price) - 1}}


def _frequency(values: list) -> dict:
    known = [value for value in values if value is not None]
    count = sum(known)
    return {"known": len(known), "unknown": len(values) - len(known), "count": count,
        "fraction": Fraction(count, len(known)) if known else None}


def _describe(values: list) -> dict:
    known = [value for value in values if value is not None]
    return {"known": len(known), "unknown": len(values) - len(known),
        "mean": math.fsum(map(float, known)) / len(known) if known else None,
        "median": median(known) if known else None, "min": min(known) if known else None,
        "max": max(known) if known else None}


def _summary(rows: list[dict]) -> dict:
    diagnostics = [row["diagnostic"] for row in rows]
    n = len(diagnostics)
    orders = Counter(item["post_entry"]["cost_touch_order"] for item in diagnostics)
    phases = Counter(item["timing"]["first_signal_anchor_cost_up_phase"] for item in diagnostics)
    def counts(values, names):
        return {name: {"count": values[name], "fraction": Fraction(values[name], n) if n else None} for name in names}
    result = {"events": n, "pre_entry_known": sum(item["timing"]["pre_entry_status"] == "known" for item in diagnostics),
        "pre_entry_unknown": sum(item["timing"]["pre_entry_status"] == "unknown" for item in diagnostics),
        "cost_touch_orders": counts(orders, ORDER_CLASSES), "first_signal_anchor_cost_up_phases": counts(phases, FIRST_PHASES),
        "post_entry": {name: _describe([item["post_entry"][name] for item in diagnostics])
            for name in ("mfe", "mae", "terminal_price_change", "giveback")},
        "timing": {name: _describe([item["timing"][name] for item in diagnostics]) for name in
            ("entry_price_change_from_signal", "signal_to_entry_minutes", "decision_to_entry_minutes", "full_signal_anchor_mfe", "full_signal_anchor_mae")},
        "pre_phase": {phase: {"known": sum(item["timing"]["phases"][phase]["status"] == "known" for item in diagnostics),
            "unknown": sum(item["timing"]["phases"][phase]["status"] == "unknown" for item in diagnostics),
            "signal_anchor_cost_exceeded": _frequency([item["timing"]["phases"][phase]["signal_anchor_cost_exceeded"] for item in diagnostics]),
            "exceeded_without_post_own_anchor_exceed": _frequency([item["timing"]["pre_exceed_without_post_by_phase"][phase] for item in diagnostics])}
            for phase in PHASES}}
    for name in ("mfe_exceeds_cost", "exceeded_but_terminal_not_exceed"):
        result[name] = _frequency([item["post_entry"][name] for item in diagnostics])
    result["pre_signal_anchor_exceeded_without_post_own_anchor_exceed"] = _frequency([
        item["timing"]["pre_signal_anchor_exceeded_without_post_own_anchor_exceed"] for item in diagnostics])
    result["first_touch_conditional_minutes_from_entry"] = {direction: _describe([
        (horizons._instant(item["post_entry"][f"first_cost_{direction}_minute"]) - horizons._instant(item["entry_label_start"])) / cc.MINUTE
        for item in diagnostics if item["post_entry"][f"first_cost_{direction}_minute"] is not None])
        for direction in ("up", "down")}
    return result


def build_paths(ch_report: Mapping, raw_chunks: Mapping | Iterable[Mapping], *,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Describe supplied paths without rerunning selection, events or peers."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    matrix, reference, union = _matrix(ch_report)
    expected = [(day, symbol) for day in reference for symbol in sorted(union[day])]
    if isinstance(raw_chunks, Mapping):
        if set(raw_chunks) != set(expected):
            raise PathRefusal("input_chunk_population_mismatch")
        chunks = iter(raw_chunks[key] for key in expected)
    else:
        chunks = iter(raw_chunks)
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "reference_sessions": reference, "assumptions": ASSUMPTIONS, **FLAGS,
        "excluded_horizons_minutes": {family: list(values) for family, values in EXCLUDED_HORIZONS.items()},
        "upstream_calibration_requires_operator_choice": ch_report["upstream_calibration_requires_operator_choice"],
        "variants": {key: {"days": [], "families": {}} for key in matrix}}
    chunk_count, row_count = 0, 0
    for day in reference:
        checkpoint()
        sessions, vintages, metadata, cache = {}, {}, {}, {}
        for symbol in sorted(union[day]):
            checkpoint()
            try:
                chunk = next(chunks)
            except StopIteration as exc:
                raise PathRefusal("missing_input_chunk") from exc
            if not isinstance(chunk, Mapping) or chunk.get("symbol") != symbol or chunk.get("session") != day:
                raise PathRefusal("input_chunk_order_or_identity_mismatch")
            if (chunk.get("observation_date") != day or chunk.get("available_at") is not None
                    or chunk.get("source") != "collector klines" or chunk.get("is_final") is not None
                    or chunk.get("evidence_level") != "assumed"):
                raise PathRefusal("input_chunk_metadata_mismatch")
            horizons._instant(chunk.get("retrieved_at"))
            raw = chunk.get("rows")
            if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_session_rows"]:
                raise PathRefusal("input_session_row_budget_exceeded")
            row_count += len(raw)
            chunk_count += 1
            if row_count > RESOURCES["maximum_rows"]:
                raise PathRefusal("input_row_budget_exceeded")
            vintages[symbol] = hashlib.sha256(_canonical(chunk) + b"\n").hexdigest()
            metadata[symbol] = {name: chunk[name] for name in ("observation_date", "retrieved_at", "available_at", "source", "is_final", "evidence_level")}
            sessions[symbol] = horizons._session_minutes(raw, day, symbol, checkpoint)
        for key, grouped in matrix.items():
            for row in grouped[day]:
                if row["data_vintage"] != vintages[row["symbol"]]:
                    raise PathRefusal("upstream_chunk_vintage_mismatch")
                output = {name: row[name] for name in ("symbol", "session", "data_vintage")}
                output.update(raw_chunk_metadata=metadata[row["symbol"]], families={})
                for family, saved in row["families"].items():
                    finding = {name: saved[name] for name in ORIGINAL_FIELDS}
                    finding["diagnostics"] = None
                    if saved["label_cohort_state"] == "selected_complete":
                        finding["diagnostics"] = {}
                        event = saved["event"]
                        opening = cc.session_open(day)
                        decision = horizons._instant(event["decision_at"])
                        entry = opening + ((decision - opening) // cc.FIVE + 1) * cc.FIVE
                        for horizon in QUALIFIED_HORIZONS[family]:
                            checkpoint()
                            cache_key = (row["symbol"], event["signal_bar_end"], event["decision_at"], entry, horizon)
                            if cache_key not in cache:
                                cache[cache_key] = _diagnostic(sessions[row["symbol"]], day, event, horizon)
                            diagnostic = cache[cache_key]
                            if diagnostic["post_entry"]["terminal_price_change"] != horizons._ratio(saved["responses"][str(horizon)]["event_price_change"]):
                                raise PathRefusal("upstream_terminal_label_mismatch")
                            finding["diagnostics"][str(horizon)] = diagnostic
                    output["families"][family] = finding
                result["variants"][key]["days"].append(output)
    try:
        next(chunks)
    except StopIteration:
        pass
    else:
        raise PathRefusal("extra_input_chunk")
    result["input_evidence"] = {"upstream_report_sha256": UPSTREAM_SHA256,
        "upstream_report_path": REGISTERED_PATHS["upstream_path"], "archive_path": REGISTERED_PATHS["input_archive_path"],
        "compressed_sha256": INPUT_COMPRESSED_SHA256, "stream_sha256": INPUT_STREAM_SHA256,
        "unique_target_days": chunk_count, "queried_rows": row_count}
    for key, variant in result["variants"].items():
        for family in FAMILIES:
            counts = cg._counts([row["families"][family] for row in variant["days"]])
            summaries = {}
            for horizon in QUALIFIED_HORIZONS[family]:
                checkpoint()
                rows = [{"session": row["session"], "symbol": row["symbol"],
                    "diagnostic": row["families"][family]["diagnostics"][str(horizon)]}
                    for row in variant["days"] if row["families"][family]["diagnostics"] is not None]
                ids = [(row["session"], row["symbol"]) for row in rows]
                cd.conclusion_check.require_no_blockers([cd.conclusion_check.check_same_population({"selected": ids, "diagnosed": ids})])
                summaries[str(horizon)] = {**_summary(rows),
                    "by_month": {month: _summary([row for row in rows if row["session"][:7] == month]) for month in sorted({day[:7] for day in reference})},
                    "by_symbol": {symbol: _summary([row for row in rows if row["symbol"] == symbol]) for symbol in sorted({row["symbol"] for row in variant["days"]})}}
            variant["families"][family] = {**counts, "by_horizon": summaries}
    checkpoint()
    return result


def run_paths(*, specification_path: str | Path, upstream_path: str | Path,
        input_archive_path: str | Path, output_directory: str | Path, runs_path: str | Path,
        budget=None, before_completion: Callable[[], None] | None = None) -> dict:
    """One durable start and exclusive artifact under the caller's shared budget."""
    if budget is None:
        from research.research_budget import ResearchBudget
        budget = ResearchBudget()
    checkpoint = lambda: budget.checkpoint("signal_paths_pipeline")
    checkpoint()
    spec_path, upstream, archive, output, log = map(Path,
        (specification_path, upstream_path, input_archive_path, output_directory, runs_path))
    if spec_path != SPECIFICATION_PATH:
        raise PathRefusal("unregistered_specification_path")
    spec_bytes = spec_path.read_bytes()
    spec, spec_sha = json.loads(spec_bytes), hashlib.sha256(spec_bytes).hexdigest()
    validate_spec(spec)
    paths = (spec_path, upstream, archive, output, log)
    for path, key in zip((upstream, archive, output, log), REGISTERED_PATHS, strict=True):
        if not path.is_absolute() or path != Path(spec[key]):
            raise PathRefusal("unregistered_path")
    for index, path in enumerate(paths):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise PathRefusal("symlinked_path")
        if any(path.resolve() == other.resolve() or (path.exists() and other.exists() and path.samefile(other)) for other in paths[:index]):
            raise PathRefusal("aliased_paths")
    sources = (spec_path, Path(__file__), Path(ch.__file__), Path(cg.__file__),
        ROOT / "python/research/research_budget.py", Path(horizons.__file__), Path(cd.__file__),
        Path(cc.__file__), Path(cc.core.__file__), Path(experiment_log.__file__), Path(cd.conclusion_check.__file__),
        ROOT / "python/research/__init__.py", ROOT / "python/pyproject.toml", ROOT / "python/uv.lock")
    pins = {str(path): _sha(path) for path in sources}
    executable = Path(sys.executable).resolve(strict=True)
    runtime = {"python": sys.version, "implementation": sys.implementation.name,
        "executable": str(executable), "executable_sha256": _sha(executable)}
    if pins[str(spec_path)] != spec_sha:
        raise PathRefusal("specification_changed")

    def evaluate() -> dict:
        checkpoint()
        if not output.parent.is_dir() or os.path.lexists(output):
            raise PathRefusal("unsafe_or_existing_output")
        if shutil.disk_usage(output.parent).free < RESOURCES["minimum_free_bytes"]:
            raise PathRefusal("insufficient_free_disk")
        output.mkdir(mode=0o700)
        cc._sync_directory(output.parent)
        if output.stat().st_mode & 0o777 != 0o700:
            raise PathRefusal("unsafe_output_permissions")
        with upstream.open("rb") as stream:
            upstream_bytes = stream.read(RESOURCES["maximum_upstream_bytes"] + 1)
        checkpoint()
        if len(upstream_bytes) > RESOURCES["maximum_upstream_bytes"] or hashlib.sha256(upstream_bytes).hexdigest() != UPSTREAM_SHA256:
            raise PathRefusal("upstream_size_or_hash_mismatch")
        if archive.stat().st_size > RESOURCES["maximum_compressed_bytes"] or _sha(archive) != INPUT_COMPRESSED_SHA256:
            raise PathRefusal("compressed_input_size_or_hash_mismatch")
        upstream_report = json.loads(upstream_bytes)
        del upstream_bytes
        report = build_paths(upstream_report, horizons._archive_chunks(archive, checkpoint), checkpoint=checkpoint)
        del upstream_report
        if len(report["reference_sessions"]) != EXPECTED_REFERENCE_SESSIONS:
            raise PathRefusal("registered_reference_population_changed")
        for key, variant in report["variants"].items():
            for family in FAMILIES:
                if variant["families"][family]["selected_complete"] != EXPECTED_SELECTED_COMPLETE[key][family]:
                    raise PathRefusal("registered_selected_population_changed")
        if any(report["input_evidence"][key] != value for key, value in EXPECTED_INPUT_COUNTS.items()):
            raise PathRefusal("registered_raw_population_changed")
        report.update(specification_sha256=spec_sha, source_sha256=pins, runtime=runtime)
        report_path = output / "report.json"
        with cc._private_file(report_path) as stream:
            encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False, default=lambda value: json.loads(_canonical(value)))
            for index, chunk in enumerate(encoder.iterencode(report)):
                if index % 1024 == 0:
                    checkpoint()
                stream.write(chunk.encode())
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        cc._sync_directory(output)
        report_sha = _sha(report_path)
        if before_completion is not None:
            before_completion()
        checkpoint()
        if (any(_sha(path) != pins[str(path)] for path in sources)
                or _sha(upstream) != UPSTREAM_SHA256 or _sha(archive) != INPUT_COMPRESSED_SHA256):
            raise PathRefusal("source_specification_or_input_changed")
        if _sha(executable) != runtime["executable_sha256"] or _sha(report_path) != report_sha:
            raise PathRefusal("runtime_or_output_changed")
        checkpoint()
        return {"output_path": str(report_path), "output_sha256": report_sha,
            "input_evidence": report["input_evidence"], **FLAGS}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
            "upstream_sha256": UPSTREAM_SHA256, "input_compressed_sha256": INPUT_COMPRESSED_SHA256,
            "input_stream_sha256": INPUT_STREAM_SHA256, "resources": RESOURCES},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log)
