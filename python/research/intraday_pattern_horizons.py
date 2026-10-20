"""Fixed response horizons on the original CD 120-minute complete-pair cohort.

The pinned, separately verified CD events and causal peer selections are reused.
Original unknown pairs stay unknown at every horizon; shorter labels cannot
rescue them. These are conditional price responses, never fills or portfolio PnL.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
from statistics import NormalDist, stdev
import sys

from research import experiment_log
from research import intraday_pattern_comparison as cd


cc = cd.cc
ROOT = cd.ROOT
SPECIFICATION_ID = "intraday-pattern-horizons-v1"
MODE = "limited-exploratory-fixed-horizon-price-label-comparison"
SPECIFICATION_PATH = ROOT / "configs/research/discovery/intraday-pattern-horizons-v1.json"
HORIZONS = (5, 15, 30, 60, 120)
WINDOW = dict(cd.WINDOW)
PARAMETERS = {**cd.PARAMETERS, "response_horizons_minutes": list(HORIZONS),
    "cohort": "original_cd_120minute_complete_pairs_only",
    "upstream_reuse": "pinned_verified_cd_events_and_control_selection_no_detector_rerun"}
REGISTERED_PATHS = {
    "upstream_path": "/home/minjun4897/research-evidence/intraday-cd-comparison-20261020-v1/report.json",
    "input_archive_path": cd.REGISTERED_PATHS["input_archive_path"],
    "output_directory": "/home/minjun4897/research-evidence/intraday-ce-horizons-20261020-v1",
    "canonical_runs_path": cd.REGISTERED_PATHS["canonical_runs_path"]}
UPSTREAM_SHA256 = "830b69ddbf94fe3ee9297912df381e72c55463fc92ddbe5b4c296ea24743846a"
INPUT_COMPRESSED_SHA256 = cd.INPUT_COMPRESSED_SHA256
INPUT_STREAM_SHA256 = cd.INPUT_STREAM_SHA256
RESOURCES = dict(cd.RESOURCES)
FLAGS = {**cc.FLAGS, "exploratory_only": True, "actual_portfolio_pnl": False,
    "family_adopted": False, "horizon_selected": False,
    "underpower_exception": "conditional_descriptive_extension_only_no_family_adoption"}
ASSUMPTIONS = {**cd.ASSUMPTIONS,
    "measurement": "fixed 5/15/30/60/120-minute same-CD-entry price labels on original CD 120-minute complete pairs only",
    "cohort": "original unknown pairs stay unknown at every horizon; no horizon-specific population or winner selection",
    "upstream": "reuse pinned separately verified CD events and control selections; no detector or CC calibration rerun",
    "power": "each horizon reports own-label sigma/MDE and constraints; descriptive extension does not adopt a family or silently waive a new shortfall"}
FAMILIES = cd.FAMILIES
EXPECTED_REFERENCE_SESSIONS = cd.EXPECTED_REFERENCE_SESSIONS
EXPECTED_COMPLETE_PAIRS = {"D+1": dict(zip(FAMILIES, (884, 1109))),
    "D+2": dict(zip(FAMILIES, (881, 1106)))}
_canonical = cc._canonical
_sha = cc._sha


class HorizonRefusal(RuntimeError):
    """Stable reason codes deliberately omit raw evidence values."""


def validate_spec(spec: Mapping) -> None:
    expected = {"id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW,
        "parameters": PARAMETERS, "resources": RESOURCES, **REGISTERED_PATHS,
        "upstream_sha256": UPSTREAM_SHA256, "input_compressed_sha256": INPUT_COMPRESSED_SHA256,
        "input_stream_sha256": INPUT_STREAM_SHA256}
    if _canonical(spec) != _canonical(expected):
        raise HorizonRefusal("specification_scope_mismatch")


def _instant(value: object) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.utcoffset() is None:
        raise HorizonRefusal("invalid_upstream_clock")
    return result


def _ratio(value: object) -> Fraction:
    if isinstance(value, Fraction):
        return value
    if isinstance(value, Mapping) and set(value) == {"numerator", "denominator"}:
        return Fraction(int(value["numerator"]), int(value["denominator"]))
    raise HorizonRefusal("upstream_label_not_exact")


def _matrix(report: Mapping) -> tuple[dict, list[str], dict]:
    if (report.get("study_id") != cd.SPECIFICATION_ID or report.get("mode") != cd.MODE
            or report.get("parameters") != cd.PARAMETERS or report.get("window") != WINDOW
            or any(report.get(key) != value for key, value in cd.FLAGS.items())):
        raise HorizonRefusal("wrong_upstream_study_or_scope")
    # Reuse only CD's membership/target validator, never its detector or replay.
    projection = {"study_id": cc.SPECIFICATION_ID, "parameters": cc.PARAMETERS,
        **cc.FLAGS, "variants": report["variants"]}
    matrix, reference, union = cd._matrix(projection)
    if report.get("reference_sessions") != reference:
        raise HorizonRefusal("upstream_reference_sessions_mismatch")
    for grouped in matrix.values():
        for rows in grouped.values():
            for row in rows:
                if set(row.get("families", {})) != set(FAMILIES):
                    raise HorizonRefusal("upstream_family_population_mismatch")
                for finding in row["families"].values():
                    if finding.get("status") not in cd.STATES:
                        raise HorizonRefusal("invalid_upstream_state")
                    comparison = finding.get("comparison")
                    if finding["status"] != "signal":
                        if comparison is not None or finding.get("event") is not None:
                            raise HorizonRefusal("upstream_nonsignal_has_comparison")
                        continue
                    if not isinstance(comparison, Mapping) or comparison.get("pair_status") not in {"complete", "unknown"}:
                        raise HorizonRefusal("invalid_upstream_pair")
                    selected = comparison["selected_controls"]
                    names = [item["symbol"] for item in selected]
                    if names != sorted(set(names)) or row["symbol"] in names or not set(names) <= {item["symbol"] for item in rows}:
                        raise HorizonRefusal("invalid_upstream_control_population")
                    if (comparison["pair_status"] == "complete") != (bool(selected) and not comparison["pair_unknown_reasons"]):
                        raise HorizonRefusal("invalid_upstream_pair_state")
    return matrix, reference, union


def _session_minutes(raw: list, day: str, symbol: str, checkpoint: Callable[[], None]) -> dict:
    """Parse integer minute buckets once; no bar aggregation or event detector."""
    opening = int(cc.session_open(day).timestamp()) * 1000
    closing = int(cc.session_close(day).timestamp()) * 1000
    minutes = {}
    seen = set()
    for index, row in enumerate(raw):
        if index % 64 == 0:
            checkpoint()
        if not isinstance(row, Mapping) or set(row) != set(cc.COLUMNS):
            raise HorizonRefusal("input_raw_columns_mismatch")
        stamp = row["open_time_ms"]
        if type(stamp) is not int or row["symbol"] != symbol or row["interval"] != "1m":
            raise HorizonRefusal("input_raw_identity_or_timestamp_mismatch")
        bucket = stamp // 60_000 * 60_000
        if not opening <= bucket < closing:
            raise HorizonRefusal("input_raw_row_outside_session")
        minute = (bucket - opening) // 60_000
        prices = None
        if minute not in seen:
            try:
                values = [row[key] for key in cc.COLUMNS[3:7]]
                if not all(isinstance(value, str) for value in values):
                    raise ValueError
                parsed = tuple(Decimal(value) for value in values)
                op, hi, lo, cl = parsed
                if (not all(value.is_finite() and value > 0 for value in parsed)
                        or not lo <= min(op, cl) <= max(op, cl) <= hi):
                    raise ValueError
                prices = parsed
            except (ValueError, InvalidOperation):
                pass  # Invalid/zero prices remain unknown, never a zero return.
        seen.add(minute)
        minutes[minute] = prices  # A duplicate permanently invalidates its bucket.
    return minutes


def _label(minutes: Mapping, opening: datetime, entry: datetime, horizon: int) -> dict:
    elapsed = (entry - opening) / cc.MINUTE
    if not elapsed.is_integer() or entry < opening:
        raise HorizonRefusal("invalid_upstream_label_boundary")
    start = int(elapsed)
    prices = [minutes.get(start + index) for index in range(horizon)]
    missing = sum(item is None for item in prices)
    label = {"entry_label_start": entry, "end_label": entry + horizon * cc.MINUTE,
        "status": "unknown", "price_change": None, "missing_minute_buckets": missing,
        "unknown_reason": f"incomplete_{horizon}minute_path" if missing else None}
    if not missing:
        label.update(status="known", entry_open=prices[0][0], terminal_close=prices[-1][3],
            price_change=Fraction(prices[-1][3]) / Fraction(prices[0][0]) - 1)
    return label


def _anchor(actual: Mapping, saved: Mapping) -> None:
    """The unchanged 120-minute price label must reproduce CD exactly."""
    for key in ("entry_label_start", "end_label"):
        if actual[key] != _instant(saved[key]):
            raise HorizonRefusal("upstream_120minute_label_boundary_mismatch")
    if (saved["status"] != "known" or actual["status"] != "known"
            or actual["price_change"] != _ratio(saved["price_change"])
            or actual["entry_open"] != Decimal(saved["entry_open"])
            or actual["terminal_close"] != Decimal(saved["terminal_close"])
            or saved["missing_minute_buckets"] != 0 or saved["unknown_reason"] is not None):
        raise HorizonRefusal("upstream_120minute_label_value_mismatch")


def _statistics(records: list[dict], reference: list[str], checkpoint: Callable[[], None]) -> dict:
    """Same CD circular block design, with budget checkpoints during draws."""
    totals = {day: [0.0, 0] for day in reference}
    values = []
    for row in records:
        value = float(row["label"])
        values.append(value)
        totals[row["session"]][0] += value
        totals[row["session"]][1] += 1
    n, groups = len(values), len(totals)
    known = sum(item[1] > 0 for item in totals.values())
    result = {"reference_sessions": groups, "known_label_sessions": known, "event_sd": None,
        "naive_se": None, "session_cluster_se": None, "bootstrap_se": None, "design_se": None,
        "corrected_to_naive_se_ratio": None, "bootstrap_zero_label_draws": 0}
    if n < 2 or groups < 2:
        return result
    sd, mean = stdev(values), math.fsum(values) / n
    naive = sd / math.sqrt(n)
    result.update(event_sd=sd, naive_se=naive)
    if known < 2:
        return result
    cluster = math.sqrt(groups / (groups - 1) * math.fsum(
        (total - count * mean) ** 2 for total, count in totals.values())) / n
    daily = list(totals.values())
    blocks = {size: [(math.fsum(daily[(index + offset) % groups][0] for offset in range(size)),
        sum(daily[(index + offset) % groups][1] for offset in range(size))) for index in range(groups)]
        for size in {5, groups % 5} if size}
    rng, draws = random.Random(0), []
    for draw in range(2000):
        if draw % 32 == 0:
            checkpoint()
        sampled = [blocks[5][rng.randrange(groups)] for _ in range(groups // 5)]
        if groups % 5:
            sampled.append(blocks[groups % 5][rng.randrange(groups)])
        count = sum(item[1] for item in sampled)
        if count:
            draws.append(math.fsum(item[0] for item in sampled) / count)
    bootstrap = stdev(draws) if len(draws) >= 2 else None
    design = max(naive, cluster, bootstrap) if bootstrap is not None else None
    result.update(session_cluster_se=cluster, bootstrap_se=bootstrap, design_se=design,
        corrected_to_naive_se_ratio=design / naive if naive and design is not None else None,
        bootstrap_zero_label_draws=2000 - len(draws))
    checkpoint()
    return result


def _summary(rows: list[dict], reference: list[str], *, uncertainty: bool,
        checkpoint: Callable[[], None]) -> dict:
    pairs = [row for row in rows if row["response"] is not None and row["response"]["status"] == "measured"]
    ids = [(row["session"], row["symbol"]) for row in pairs]
    cd.conclusion_check.require_no_blockers([
        cd.conclusion_check.check_same_population({"event": ids, "control": ids}),
        cd.conclusion_check.check_clustered_observations([row["session"] for row in pairs],
            reported_n=len({row["session"] for row in pairs}))])
    # Overall own-label sizing precedes the event/control mean comparison.
    # Month/symbol descriptions are produced only after that overall stage.
    own_dispersion, paired_uncertainty = None, None
    if uncertainty:
        own = _statistics([{"session": row["session"], "label": row["response"]["event_price_change"]}
            for row in pairs], reference, checkpoint)
        design = own["design_se"]
        mde = (NormalDist().inv_cdf(.975) + NormalDist().inv_cdf(.8)) * design * 10000 if design is not None else None
        shortfall = mde is not None and mde > 33
        constraint = ("insufficient_known_labels_or_reference_sessions" if len(pairs) < 2 or len(reference) < 2
            else "insufficient_known_label_sessions" if own["known_label_sessions"] < 2
            else "insufficient_valid_bootstrap_draws" if design is None
            else "own_label_mde_exceeds_assumed_cost_floor" if shortfall else "no_underpower_choice_inferred")
        own_dispersion = {**own, "events": sum(row["status"] == "signal" for row in rows),
            "measured_labels": len(pairs),
            "not_measured_outside_fixed_cohort": sum(row["status"] == "signal" for row in rows) - len(pairs),
            "mde_bps": mde, "cost_floor_bps_assumed": "33",
            "would_require_operator_underpower_choice": shortfall, "planning_constraint": constraint,
            "planning_constraint_scope": "explicit CE conditional descriptive extension; failed prerequisite retained; no family certification",
            "underpower_exception_applied": shortfall, "descriptive_extension_authorized": True,
            "descriptive_extension_only": True,
            "family_adopted": False, "full_family_certified": False}
        paired = _statistics([{"session": row["session"], "label": row["response"]["excess_price_change"]}
            for row in pairs], reference, checkpoint)
        paired_uncertainty = {("known_pair_sessions" if key == "known_label_sessions" else
            "bootstrap_zero_pair_draws" if key == "bootstrap_zero_label_draws" else key): value
            for key, value in paired.items() if key != "event_sd"}
    summary = {"measured_pairs": len(pairs),
        "event": cd._distribution([row["response"]["event_price_change"] for row in pairs]),
        "control": cd._distribution([row["response"]["control_mean_price_change"] for row in pairs]),
        "excess_mean": math.fsum(float(row["response"]["excess_price_change"]) for row in pairs) / len(pairs) if pairs else None,
        "paired_mean_uncertainty": paired_uncertainty}
    if own_dispersion is not None:
        summary["own_label_dispersion"] = own_dispersion
    return summary


def _check_summary_anchor(summary: Mapping, saved: Mapping) -> None:
    # Retain the exact CD cohort, statistics, monthly and symbol contributions.
    def check(actual, original):
        if isinstance(original, Mapping):
            if set(original) == {"numerator", "denominator"}:
                if actual != _ratio(original):
                    raise HorizonRefusal("upstream_120minute_summary_mismatch")
            else:
                for key, value in original.items():
                    if key == "upstream_calibration_sizing":
                        continue
                    if key not in actual:
                        raise HorizonRefusal("upstream_120minute_summary_mismatch")
                    check(actual[key], value)
        elif isinstance(original, float):
            if actual is None or not math.isclose(actual, original, rel_tol=1e-12, abs_tol=1e-15):
                raise HorizonRefusal("upstream_120minute_summary_mismatch")
        elif actual != original:
            raise HorizonRefusal("upstream_120minute_summary_mismatch")
    check(summary, {("measured_pairs" if key == "events" else key): value
        for key, value in saved["paired_complete_conditional"].items()})
    for grouping in ("by_month", "by_symbol"):
        if grouping in saved:
            if set(summary[grouping]) != set(saved[grouping]):
                raise HorizonRefusal("upstream_120minute_summary_mismatch")
            for key, value in saved[grouping].items():
                _check_summary_anchor(summary[grouping][key], value)


def build_horizons(cd_report: Mapping, raw_chunks: Mapping | Iterable[Mapping], *,
        checkpoint: Callable[[], None] | None = None) -> dict:
    """Pure supplied-byte replay; smaller synthetic date grids still need 20 names."""
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    matrix, reference, union = _matrix(cd_report)
    expected = [(day, symbol) for day in reference for symbol in sorted(union[day])]
    if isinstance(raw_chunks, Mapping):
        if set(raw_chunks) != set(expected):
            raise HorizonRefusal("input_chunk_population_mismatch")
        chunks = iter(raw_chunks[key] for key in expected)
    else:
        chunks = iter(raw_chunks)
    result = {"study_id": SPECIFICATION_ID, "mode": MODE, "window": WINDOW, "parameters": PARAMETERS,
        "reference_sessions": reference, "assumptions": ASSUMPTIONS, **FLAGS,
        "variants": {key: {"days": [], "families": {}} for key in matrix}}
    chunk_count, row_count = 0, 0
    for day in reference:
        checkpoint()
        sessions, vintages, labels = {}, {}, {}
        opening = cc.session_open(day)
        for symbol in sorted(union[day]):
            checkpoint()
            try:
                chunk = next(chunks)
            except StopIteration as exc:
                raise HorizonRefusal("missing_input_chunk") from exc
            if not isinstance(chunk, Mapping) or chunk.get("symbol") != symbol or chunk.get("session") != day:
                raise HorizonRefusal("input_chunk_order_or_identity_mismatch")
            if (chunk.get("observation_date") != day or chunk.get("available_at") is not None
                    or chunk.get("source") != "collector klines" or chunk.get("is_final") is not None
                    or chunk.get("evidence_level") != "assumed"):
                raise HorizonRefusal("input_chunk_metadata_mismatch")
            raw = chunk.get("rows")
            if not isinstance(raw, list) or len(raw) > RESOURCES["maximum_session_rows"]:
                raise HorizonRefusal("input_session_row_budget_exceeded")
            row_count += len(raw)
            chunk_count += 1
            if row_count > RESOURCES["maximum_rows"]:
                raise HorizonRefusal("input_row_budget_exceeded")
            vintages[symbol] = hashlib.sha256(_canonical(chunk) + b"\n").hexdigest()
            sessions[symbol] = _session_minutes(raw, day, symbol, checkpoint)
        for key, grouped in matrix.items():
            for row in grouped[day]:
                if row["data_vintage"] != vintages[row["symbol"]]:
                    raise HorizonRefusal("upstream_chunk_vintage_mismatch")
                output = {name: row[name] for name in ("symbol", "session", "data_vintage", "observation_date",
                    "membership_observation_date", "membership_available_at", "membership_source")}
                output["families"] = {}
                for family in FAMILIES:
                    saved = row["families"][family]
                    finding = {"status": saved["status"], "event": saved["event"],
                        "original_pair_status": None, "original_pair_unknown_reasons": [],
                        "original_unknown_control_symbols": [], "original_event_label_state": None,
                        "selected_controls": [], "excluded_controls": [], "event_prices": None,
                        "responses": {}}
                    output["families"][family] = finding
                    if saved["status"] != "signal":
                        continue
                    comparison = saved["comparison"]
                    entry = _instant(comparison["event_label"]["entry_label_start"])
                    decision = _instant(saved["event"]["decision_at"])
                    if entry != opening + ((decision - opening) // cc.FIVE + 1) * cc.FIVE:
                        raise HorizonRefusal("upstream_entry_boundary_changed")
                    complete = comparison["pair_status"] == "complete"
                    controls = [{name: value for name, value in item.items() if name != "label"}
                        for item in comparison["selected_controls"]]
                    for control, original in zip(controls, comparison["selected_controls"], strict=True):
                        control["original_label_state"] = {name: original["label"][name]
                            for name in ("status", "missing_minute_buckets", "unknown_reason")}
                    finding.update(original_pair_status=comparison["pair_status"],
                        original_pair_unknown_reasons=comparison["pair_unknown_reasons"],
                        original_unknown_control_symbols=comparison["unknown_control_symbols"],
                        original_event_label_state={name: comparison["event_label"][name]
                            for name in ("status", "missing_minute_buckets", "unknown_reason")},
                        selected_controls=controls, excluded_controls=comparison["excluded_controls"])
                    def label(symbol, horizon):
                        cache_key = (symbol, entry, horizon)
                        if cache_key not in labels:
                            labels[cache_key] = _label(sessions[symbol], opening, entry, horizon)
                        return labels[cache_key]
                    if complete:
                        _anchor(label(row["symbol"], 120), comparison["event_label"])
                        for item in comparison["selected_controls"]:
                            _anchor(label(item["symbol"], 120), item["label"])
                        finding["event_prices"] = {"entry_label_start": entry,
                            "entry_open": label(row["symbol"], 120)["entry_open"],
                            "terminal_closes": {str(h): label(row["symbol"], h)["terminal_close"] for h in HORIZONS}}
                        for item in controls:
                            item.update(entry_open=label(item["symbol"], 120)["entry_open"],
                                terminal_closes={str(h): label(item["symbol"], h)["terminal_close"] for h in HORIZONS})
                    for horizon in HORIZONS:
                        checkpoint()
                        if complete:
                            event_label = label(row["symbol"], horizon)
                            control_labels = [label(item["symbol"], horizon) for item in controls]
                            if event_label["status"] != "known" or any(item["status"] != "known" for item in control_labels):
                                raise HorizonRefusal("complete_120minute_cohort_lost_shorter_label")
                            control_mean = sum((item["price_change"] for item in control_labels), Fraction()) / len(controls)
                            excess = event_label["price_change"] - control_mean
                        else:
                            event_label = None
                            control_mean, excess = None, None
                        projected = {"status": "measured" if complete else "not_measured_outside_fixed_cohort",
                            "event_price_change": event_label["price_change"] if complete else None,
                            "control_mean_price_change": control_mean, "excess_price_change": excess}
                        if complete and horizon == 120 and (control_mean != _ratio(comparison["control_mean_price_change"])
                                or excess != _ratio(comparison["excess_price_change"])):
                            raise HorizonRefusal("upstream_120minute_pair_value_mismatch")
                        finding["responses"][str(horizon)] = projected
                result["variants"][key]["days"].append(output)
    try:
        next(chunks)
    except StopIteration:
        pass
    else:
        raise HorizonRefusal("extra_input_chunk")
    evidence = cd_report["input_evidence"]
    if evidence["unique_target_days"] != chunk_count or evidence["queried_rows"] != row_count:
        raise HorizonRefusal("upstream_input_count_mismatch")
    result["input_evidence"] = {"upstream_report_sha256": UPSTREAM_SHA256,
        "archive_path": REGISTERED_PATHS["input_archive_path"],
        "compressed_sha256": evidence["compressed_sha256"], "stream_sha256": evidence["stream_sha256"],
        "unique_target_days": chunk_count, "queried_rows": row_count}
    for key, variant in result["variants"].items():
        for family in FAMILIES:
            horizons = {}
            for horizon in HORIZONS:
                checkpoint()
                rows = [{"session": row["session"], "symbol": row["symbol"],
                    "status": row["families"][family]["status"],
                    "response": row["families"][family]["responses"].get(str(horizon))} for row in variant["days"]]
                summary = _summary(rows, reference, uncertainty=True, checkpoint=checkpoint)
                summary["by_month"] = {month: _summary([row for row in rows if row["session"][:7] == month],
                    [day for day in reference if day[:7] == month], uncertainty=False, checkpoint=checkpoint)
                    for month in sorted({day[:7] for day in reference})}
                summary["by_symbol"] = {symbol: _summary([row for row in rows if row["symbol"] == symbol],
                    reference, uncertainty=False, checkpoint=checkpoint) for symbol in sorted({row["symbol"] for row in rows})}
                if horizon == 120:
                    _check_summary_anchor(summary, cd_report["variants"][key]["families"][family])
                horizons[str(horizon)] = summary
            variant["families"][family] = {"by_horizon": horizons,
                "original_cd_summary": cd_report["variants"][key]["families"][family],
                "cohort": PARAMETERS["cohort"]}
    checkpoint()
    return result


def _archive_chunks(path: Path, checkpoint: Callable[[], None]) -> Iterable[dict]:
    digest, size, chunks = hashlib.sha256(), 0, 0
    with gzip.open(path, "rb") as stream:
        while True:
            checkpoint()
            line = stream.readline(RESOURCES["maximum_chunk_bytes"] + 1)
            if not line:
                break
            size += len(line)
            chunks += 1
            if (len(line) > RESOURCES["maximum_chunk_bytes"] or not line.endswith(b"\n")
                    or size > RESOURCES["maximum_uncompressed_bytes"] or chunks > RESOURCES["maximum_chunks"]):
                raise HorizonRefusal("input_stream_budget_or_framing_mismatch")
            digest.update(line)
            yield json.loads(line)
    if digest.hexdigest() != INPUT_STREAM_SHA256:
        raise HorizonRefusal("input_stream_hash_mismatch")


def run_horizons(*, specification_path: str | Path, upstream_path: str | Path,
        input_archive_path: str | Path, output_directory: str | Path, runs_path: str | Path,
        budget=None, before_completion: Callable[[], None] | None = None) -> dict:
    """Durable discovery start precedes any read of saved market evidence."""
    if budget is None:
        from research.research_budget import ResearchBudget
        budget = ResearchBudget()
    checkpoint = lambda: budget.checkpoint("horizon_pipeline")
    checkpoint()
    spec_path, upstream, archive, output, log = map(Path,
        (specification_path, upstream_path, input_archive_path, output_directory, runs_path))
    if spec_path.resolve() != SPECIFICATION_PATH.resolve():
        raise HorizonRefusal("unregistered_specification_path")
    spec_bytes = spec_path.read_bytes()
    spec, spec_sha = json.loads(spec_bytes), hashlib.sha256(spec_bytes).hexdigest()
    validate_spec(spec)
    paths = (spec_path, upstream, archive, output, log)
    for path, key in zip((upstream, archive, output, log), REGISTERED_PATHS, strict=True):
        if not path.is_absolute() or path.resolve() != Path(spec[key]).resolve():
            raise HorizonRefusal("unregistered_path")
    for index, path in enumerate(paths):
        if any(item.is_symlink() for item in (path, *path.parents)):
            raise HorizonRefusal("symlinked_path")
        if any(path.resolve() == other.resolve() or (path.exists() and other.exists() and path.samefile(other)) for other in paths[:index]):
            raise HorizonRefusal("aliased_paths")
    sources = (Path(__file__), Path(cd.__file__), Path(cc.__file__), Path(cc.core.__file__),
        Path(experiment_log.__file__), Path(cd.conclusion_check.__file__),
        ROOT / "python/research/research_budget.py", ROOT / "python/research/__init__.py",
        ROOT / "python/pyproject.toml", ROOT / "python/uv.lock", spec_path)
    pins = {str(path): _sha(path) for path in sources}
    executable = Path(sys.executable).resolve(strict=True)
    runtime = {"python": sys.version, "implementation": sys.implementation.name,
        "executable": str(executable), "executable_sha256": _sha(executable)}
    if pins[str(spec_path)] != spec_sha:
        raise HorizonRefusal("specification_changed")

    def evaluate() -> dict:
        checkpoint()
        if not output.parent.is_dir() or os.path.lexists(output):
            raise HorizonRefusal("unsafe_or_existing_output")
        if shutil.disk_usage(output.parent).free < RESOURCES["minimum_free_bytes"]:
            raise HorizonRefusal("insufficient_free_disk")
        output.mkdir(mode=0o700)
        cc._sync_directory(output.parent)
        if output.stat().st_mode & 0o777 != 0o700:
            raise HorizonRefusal("unsafe_output_permissions")
        with upstream.open("rb") as stream:
            upstream_bytes = stream.read(RESOURCES["maximum_upstream_bytes"] + 1)
        checkpoint()
        if len(upstream_bytes) > RESOURCES["maximum_upstream_bytes"] or hashlib.sha256(upstream_bytes).hexdigest() != UPSTREAM_SHA256:
            raise HorizonRefusal("upstream_size_or_hash_mismatch")
        if archive.stat().st_size > RESOURCES["maximum_compressed_bytes"] or _sha(archive) != INPUT_COMPRESSED_SHA256:
            raise HorizonRefusal("compressed_input_size_or_hash_mismatch")
        checkpoint()
        upstream_report = json.loads(upstream_bytes)
        del upstream_bytes
        if (upstream_report["input_evidence"]["compressed_sha256"] != INPUT_COMPRESSED_SHA256
                or upstream_report["input_evidence"]["stream_sha256"] != INPUT_STREAM_SHA256):
            raise HorizonRefusal("upstream_input_pin_mismatch")
        report = build_horizons(upstream_report, _archive_chunks(archive, checkpoint), checkpoint=checkpoint)
        del upstream_report
        if len(report["reference_sessions"]) != EXPECTED_REFERENCE_SESSIONS:
            raise HorizonRefusal("registered_reference_population_changed")
        for key, variant in report["variants"].items():
            for family in FAMILIES:
                if variant["families"][family]["by_horizon"]["120"]["measured_pairs"] != EXPECTED_COMPLETE_PAIRS[key][family]:
                    raise HorizonRefusal("registered_complete_pair_population_changed")
        report.update(specification_sha256=spec_sha, source_sha256=pins, runtime=runtime)
        checkpoint()
        report_path = output / "report.json"
        with cc._private_file(report_path) as stream:
            # Streaming avoids a second full report-sized UTF-8 allocation.
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
        if (any(_sha(path) != pins[str(path)] for path in sources) or _sha(upstream) != UPSTREAM_SHA256
                or _sha(archive) != INPUT_COMPRESSED_SHA256):
            raise HorizonRefusal("source_specification_or_input_changed")
        if _sha(executable) != runtime["executable_sha256"] or _sha(report_path) != report_sha:
            raise HorizonRefusal("runtime_or_output_changed")
        checkpoint()
        return {"output_path": str(report_path), "output_sha256": report_sha,
            "input_evidence": report["input_evidence"], **FLAGS}

    return experiment_log.run_discovery_trial(study_id=SPECIFICATION_ID, specification_sha256=spec_sha,
        parameters={"mode": MODE, "parameters": PARAMETERS, "source_sha256": pins, "runtime": runtime,
            "upstream_sha256": UPSTREAM_SHA256, "input_compressed_sha256": INPUT_COMPRESSED_SHA256,
            "input_stream_sha256": INPUT_STREAM_SHA256, "resources": RESOURCES},
        window=dict(WINDOW), evaluate=evaluate, runs_path=log)
