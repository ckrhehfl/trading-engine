"""Logged saved-input coverage for the fixed large/liquid timing experiment.

No prices/returns are replayed, no source is acquired and no operating-company
period is invented. Run from the repository root with committed source/specs:
Use this worktree's WSL Python environment and PYTHONPATH=python while keeping
the repository root as cwd. Evidence stays in exclusive private outputs.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tomllib
from typing import Any

from data import krx_formation_audit as formation
from data import krx_liquidity_audit as liquidity
from data import krx_openapi_probe as probe
from research import experiment_log
from research.activity_timing import SessionLagPolicy


SCHEMA = "activity-timing-preflight-v1"
REFERENCE = "configs/research/discovery/activity-calibration-v1.json"
WINDOW = {"symbol": "KRX:", "interval": "1d", "start": "2019-01-02", "end": "2026-09-18"}
BB_SHA256 = "2ac0b2c81d19921d19cfea95a850574d9bef8dc7b5d1ebc900aa1f039ea0d9b4"
RECOVERY_REPORT_SHA256 = "f23e37b0b6613a4720c21949b63f8c3ba1ba0a6c97546478b9abae522ca983ad"
CANDIDATES_SHA256 = "09a942994bf2d4eec746a5cfb2337cf0bc47fb3d4f271d21339fb97f7a1a95d6"
RECOVERY_STARTED_SHA256 = "e7a3f7290abc6e7541a1ff005c39e0ec07a1e4d99bfcd64b182b7cb404141e27"
RECOVERY_LEDGER_SHA256 = "2f86c7af7db76f2fcf6410bcef477eef7b5e25973db87b4e4a8ff13f39e34518"
AQ_TOTALS = {"trade_rows": 35271, "cap_pass_rows": 944, "cap_below_rows": 34327,
             "cap_pass_common_label_rows": 926}
BB_DENOMINATORS = {"target_windows": 917, "target_codes": 136, "retained_controls": 27,
                   "population_rows": 944, "short_windows": 8,
                   "listing_bounded_unique_code_dates": 55773}
Reader = Callable[[Path], bytes]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def strict_json(raw: bytes) -> Any:
    """Offline JSON parsing without a credential or duplicate-key ambiguity."""
    def unique(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError("nonfinite JSON constant")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)


def parse_envelope(raw: bytes) -> list[dict]:
    payload = strict_json(raw)
    require(isinstance(payload, dict) and set(payload) == {"OutBlock_1"}
            and isinstance(payload["OutBlock_1"], list)
            and all(isinstance(row, dict) for row in payload["OutBlock_1"]),
            "invalid saved response envelope")
    return payload["OutBlock_1"]


def _safe_path(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    require(not any(part.is_symlink() for part in (path, *path.parents)),
            "symlink evidence path")
    return path


def private_directory(path: Path) -> Path:
    path = _safe_path(path)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700,
            "private directory must be mode 0700")
    return path


def read_private(path: Path) -> bytes:
    """Open a single regular private file, refusing links and special files."""
    path = _safe_path(path)
    private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_nlink == 1, "private input must be single-link regular mode 0600")
        # The BB inventory and diagnostics are larger than one API response.
        require(info.st_size <= 64 * 1024**2, "input exceeds fixed file size ceiling")
        raw = handle.read(64 * 1024**2 + 1)
        require(len(raw) <= 64 * 1024**2, "input exceeds fixed file size ceiling")
        return raw


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_exclusive(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(path.parent)


def _git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def freeze_sources(root: Path, spec_path: Path) -> tuple[dict, bytes, dict]:
    """Freeze ALL tracked Python, the lock/project and both committed specs.

    Source reads are engineering inputs, not research data. Untracked Python
    anywhere under python/ and staged changes are included in the dirty check.
    The runner intentionally does not inherit v1's narrow EXECUTION_SOURCES.
    """
    root = root.resolve()
    relative = spec_path.resolve().relative_to(root).as_posix()
    all_python = _git(root, "ls-files", "-z", "--", "python").decode().split("\0")
    paths = sorted({p for p in all_python if p.endswith(".py")}
                   | {"python/pyproject.toml", "python/uv.lock", relative, REFERENCE})
    require(any(p.endswith(".py") for p in paths), "tracked Python source is required")
    tracked = set(_git(root, "ls-files", "-z").decode().split("\0"))
    require(set(paths) <= tracked, "source, dependency files and specs must be tracked")

    def clean() -> None:
        require(not _git(root, "status", "--porcelain", "--untracked-files=all", "--",
                         "python", relative, REFERENCE).strip(),
                "full Python source, dependency files and relevant specs must be clean")

    clean()
    contents = {}
    for name in paths:
        path = _safe_path(root / name)
        require(stat.S_ISREG(path.lstat().st_mode), "source must be a regular file")
        raw = path.read_bytes()
        require(raw == _git(root, "show", f"HEAD:{name}"), "source differs from committed bytes")
        contents[name] = raw
    clean()
    raw = contents[relative]
    spec, reference = strict_json(raw), strict_json(contents[REFERENCE])
    validate_spec(spec, reference)
    locked = tomllib.loads(contents["python/uv.lock"].decode("utf-8"))
    installed = {}
    for package in locked.get("package", []):
        name, version = package["name"], package["version"]
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue  # Platform-specific lock entries need not be installed.
        # This runner imports no third-party libraries. Unused installed
        # packages cannot become a new gate on a stdlib/local-source audit.
        installed[name] = {"installed_version": actual, "locked_version": version,
                           "matches_lock": actual == version}
    manifest = {"code_version": _git(root, "rev-parse", "HEAD").decode().strip(),
                "files": [{"path": name, "bytes": len(body), "sha256": digest(body)}
                          for name, body in sorted(contents.items())],
                "python_version": sys.version, "python_executable": sys.executable,
                "installed_locked_distributions": installed,
                "runtime_dependencies": "stdlib_and_manifested_local_Python_only"}
    return manifest, raw, spec


def _pin(value: Any) -> None:
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            "SHA-256 pin is required")


def validate_spec(spec: dict, reference: dict) -> None:
    require(isinstance(spec, dict) and spec.get("schema") == SCHEMA
            and spec.get("mode") == "discovery_input_coverage_preflight"
            and spec.get("promotion_allowed") is False
            and isinstance(spec.get("study_id"), str) and bool(spec["study_id"].strip())
            and spec.get("window") == WINDOW and spec.get("reference_specification") == REFERENCE
            and spec.get("parameters") == reference["parameters"], "invalid fixed preflight specification")
    inputs = spec["inputs"]
    required_pins = {
        "calendar_manifest": {"sha256": formation.CALENDAR_MANIFEST_SHA256},
        "bb_result": {"sha256": BB_SHA256},
        "aq": {"report_sha256": liquidity.FORMATION_REPORT_SHA256,
               "started_sha256": liquidity.FORMATION_STARTED_SHA256,
               "ledger_sha256": liquidity.FORMATION_LEDGER_SHA256},
        "ar_original": {"report_sha256": liquidity.RECOVERY_REPORT_SHA256,
                        "started_sha256": liquidity.RECOVERY_STARTED_SHA256,
                        "ledger_sha256": liquidity.RECOVERY_LEDGER_SHA256},
        "ar_recovery": {"report_sha256": RECOVERY_REPORT_SHA256,
                        "started_sha256": RECOVERY_STARTED_SHA256,
                        "ledger_sha256": RECOVERY_LEDGER_SHA256,
                        "candidates_sha256": CANDIDATES_SHA256},
    }
    require(set(inputs) == set(required_pins), "input roles must match fixed acquisition sources")
    for role, pins in required_pins.items():
        location = "path" if role in {"calendar_manifest", "bb_result"} else "root"
        require(set(inputs[role]) == {location, *pins}, "unexpected input declaration")
        require(isinstance(inputs[role][location], str) and Path(inputs[role][location]).is_absolute(),
                "input locations must be absolute")
        require(all(inputs[role].get(field) == value for field, value in pins.items()),
                "input pin differs from recorded acquisition")


def calendar_schedule(manifest: dict, params: dict) -> tuple[list[str], dict[str, list[str]], list[dict]]:
    """Use the fixed actual calendar for both arms; never weekday arithmetic."""
    calendar = manifest.get("calendar_dates")
    require(isinstance(calendar, list) and len(calendar) == 1898
            and all(isinstance(day, str) and re.fullmatch(r"[0-9]{8}", day) for day in calendar)
            and calendar == sorted(set(calendar))
            and calendar[0] == "20190102" and calendar[-1] == "20260923"
            and digest(probe.canonical(calendar)) == formation.CALENDAR_SHA256,
            "invalid pinned calendar")
    days = [date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}") for day in calendar]
    end = calendar.index("20260918")
    policies = [SessionLagPolicy(lag, paired=True) for lag in (1, 2)]
    indices = policies[0].formation_indices(days, lookback=params["lookback"],
                                            holding_sessions=params["holding_sessions"], end_index=end)
    require(tuple(indices) == policies[1].formation_indices(
        days, lookback=params["lookback"], holding_sessions=params["holding_sessions"], end_index=end),
        "paired formation geometry differs")
    require([calendar[i] for i in indices] == list(formation.DATES)
            and manifest.get("formation_dates") == list(formation.DATES), "formation geometry drift")
    windows = {calendar[i]: calendar[i - params["lookback"]:i] for i in indices}
    schedule = [{"formation": calendar[i], "arms": [
        {"session_lag": p.session_lag, "execution_on": p.execution_on(days, i).isoformat(),
         "selection_at": p.selection_at(days, i).isoformat(),
         "due_on": days[i + p.session_lag + params["holding_sessions"]].isoformat(),
         "status": "input_coverage_only_not_executed"} for p in policies]} for i in indices]
    return calendar, windows, schedule


class PinnedInputs:
    """Small reader seam: hash each byte stream before interpreting any content."""

    def __init__(self, reader: Reader = read_private) -> None:
        self.reader = reader
        self.manifest: list[dict] = []

    def read(self, path: Path, expected: str, role: str) -> bytes:
        _pin(expected)
        entry = {"role": role, "path": str(path), "expected_sha256": expected,
                 "read_status": "started"}
        self.manifest.append(entry)
        raw = self.reader(path)
        actual = digest(raw)
        entry.update(bytes=len(raw), sha256=actual, read_status="hash_verified" if actual == expected else "hash_mismatch")
        require(actual == expected, "actual input hash mismatch")
        return raw

    def json(self, path: Path, expected: str, role: str) -> Any:
        return strict_json(self.read(path, expected, role))


def _receipt(inputs: PinnedInputs, declaration: dict, role: str) -> tuple[dict, dict, list[dict]]:
    root = Path(declaration["root"])
    report = inputs.json(root / "report.json", declaration["report_sha256"], role + ":report")
    started = inputs.json(root / "started.json", declaration["started_sha256"], role + ":started")
    ledger = [strict_json(line) for line in inputs.read(
        root / "requests.jsonl", declaration["ledger_sha256"], role + ":ledger").splitlines()]
    return report, started, ledger


def _ledger(ledger: list[dict], matrix: list[dict], *, logical_first: int = 1,
            trailing_transport_failure: bool = False, recovery: bool = False) -> None:
    require(len(ledger) == 2 * (len(matrix) + int(trailing_transport_failure)), "ledger event count mismatch")
    for offset, item in enumerate(matrix):
        for step, event in enumerate(("attempt", "response")):
            row = ledger[2 * offset + step]
            require(row.get("event") == event and row.get("number") == offset + 1
                    and all(row.get(key) == value for key, value in item.items())
                    and (not recovery or row.get("logical_number") == logical_first + offset)
                    and (event != "response" or row.get("http_status") == 200),
                    "ledger request/response mismatch")
    if trailing_transport_failure:
        attempt, response = ledger[-2:]
        next_item = {"bas_dd": "20210310", "service": probe.SERVICES[1]}
        require(all(row.get("number") == 550 and all(row.get(k) == v for k, v in next_item.items())
                    for row in (attempt, response)) and attempt.get("event") == "attempt"
                and response.get("event") == "response" and response.get("http_status") is None,
                "original transport failure evidence mismatch")


def _rows(inputs: PinnedInputs, root: Path, number: int, item: dict, observation: dict,
          role: str, *, aq: bool = False) -> list[dict]:
    service, day = item["service"], item["bas_dd"]
    require(observation.get("service") == service and observation.get("requested_bas_dd") == day
            and observation.get("schema_valid") is True, "saved observation coordinate mismatch")
    width = 2 if aq else 4
    body = inputs.read(root / f"response-{number:0{width}d}-{service}-{day}.json",
                       observation["response_sha256"], role + ":raw")
    require(len(body) <= probe.MAX_RESPONSE_BYTES, "saved API response exceeds acquisition ceiling")
    rows = parse_envelope(body)
    summary = (formation._summarize(rows, service, day) if aq
               else formation.summarize_trading_day(rows, service, day))
    require(summary["schema_valid"] and summary == {
        key: value for key, value in observation.items()
        if key not in {"at", "response_sha256", "number", "logical_number"}},
        "saved raw observation re-audit mismatch")
    return rows


def bar_state(row: dict | None, *, response_saved: bool) -> str:
    """Zero is observed data; an absent issue never becomes not-listed or halt."""
    if not response_saved:
        return "no_saved_response_day"
    if row is None:
        return "missing_raw_row_cause_unknown"
    metrics = liquidity._bar_metrics(row)
    if metrics["equal_ohlc_zero_turnover"]:
        return "observed_frozen_zero"
    if formation._number(row, "TDD_OPNPRC") == 0:
        return "observed_zero_open"
    if metrics["turnover"] == 0:
        return "observed_zero_turnover"
    return "observed_positive_turnover"


def response_metadata(day: str, service: str, observation: dict,
                      calendar: list[date], index: int, response_id: str) -> dict:
    """Shared row provenance with assumed and actual publication kept apart."""
    require(calendar[index].strftime("%Y%m%d") == day and service in probe.SERVICES[:2],
            "raw provenance date/service mismatch")
    _pin(observation["response_sha256"])
    return {
        "response_id": response_id, "observation_date": calendar[index].isoformat(),
        "available_at": SessionLagPolicy(1).selection_at(calendar, index).isoformat(),
        "retrieved_at": "unknown", "source": f"KRX OpenAPI {service}", "is_final": None,
        "data_vintage": "retrieved_snapshot_sha256:" + observation["response_sha256"],
        "evidence_level": "assumed", "evidence_level_scope": "modeled_availability_only",
        "source_public_available_at": None, "historical_version_unknown": True,
        "availability_policy": "same_D_inputs_assumed_next_market_session_08:30_Asia_Seoul_both_arms",
        "evidence_reference": ".planning/rd-bh-large-liquid-timing-sensitivity.md",
        "observation_receipt_recorded_at": observation.get("at"),
        "response_sha256": observation["response_sha256"],
    }


def _bb_windows(bb: dict, calendar: list[str]) -> tuple[list[dict], set[tuple[str, str]]]:
    require(bb.get("status") == "passed" and all(bb.get(key) == value for key, value in BB_DENOMINATORS.items()),
            "BB population mismatch")
    windows = bb["windows"]
    pairs, obligations, codes = set(), set(), set()
    short = 0
    for row in windows:
        key = row["formation"], row["code"]
        dates = row["required_observed_dates"]
        require(key not in pairs and key[0] in formation.DATES and probe.id_kind(key[1]) == "short6"
                and isinstance(dates, list) and dates == sorted(set(dates)) and bool(dates)
                and dates[-1] == key[0] and set(dates) <= set(calendar)
                and not set(dates) & set(row["prelisting_search_dates"])
                and type(row["prior_count"]) is int and 0 <= row["prior_count"] <= 60,
                "invalid BB window obligations")
        pairs.add(key)
        codes.add(key[1])
        obligations.update((key[1], day) for day in dates)
        short += row["prior_count"] < 60
    require(len(windows) == len(pairs) == BB_DENOMINATORS["target_windows"]
            and len(codes) == BB_DENOMINATORS["target_codes"] and short == BB_DENOMINATORS["short_windows"]
            and len(obligations) == BB_DENOMINATORS["listing_bounded_unique_code_dates"],
            "BB obligation denominator mismatch")
    return windows, obligations


def coverage_summary(windows: list[dict], observations: dict[tuple[str, str], dict],
                     saved_dates: set[str], numeric_failures: dict[tuple[str, str], list[str]],
                     raw_formation_proxies: dict[tuple[str, str], bool] | None = None) -> dict:
    """Reconcile actual coverage independently of unproved classification."""
    union = {(row["code"], day) for row in windows for day in row["required_observed_dates"]}
    states = {key: bar_state(observations.get(key), response_saved=key[1] in saved_dates)
              for key in sorted(union)}
    details = []
    for row in windows:
        key = row["formation"], row["code"]
        failures = list(numeric_failures.get(key, []))
        if row["prior_count"] < 60:
            failures.append("BB_verified_observed_history_less_than_60")
        counts = Counter(states[row["code"], day] for day in row["required_observed_dates"])
        details.append({"formation": key[0], "code": key[1], "required_dates": len(row["required_observed_dates"]),
                        "raw_states": dict(sorted(counts.items())), "proven_numeric_failures": sorted(set(failures)),
                        "raw_formation_tradability_proxy": (raw_formation_proxies or {}).get(key),
                        "raw_to_adjusted_formation_state_verified": False,
                        "historical_eligibility_certified": False, "normal_baseline_certified": False,
                        "potentially_influential_unresolved": not failures})
    return {"required_unique_code_dates": len(union), "raw_states": dict(sorted(Counter(states.values()).items())),
            "provenance_unknown_code_dates": sum(key in observations for key in union),
            "unresolved_potentially_influential_windows": sum(row["potentially_influential_unresolved"] for row in details),
            "windows_with_proven_numeric_failure": sum(bool(row["proven_numeric_failures"]) for row in details),
            "windows": details,
            "missing_causes": "unknown; source row absence is not prelisting/postdelisting/halt evidence",
            "provenance_unknown_meaning": "raw hash/row coordinates known; historical true publication, vintage and finality unknown"}


def numeric_liquidity_failures(candidates: list[dict]) -> dict[tuple[str, str], list[str]]:
    """Absolute source turnover can fail liquidity without mixing price bases.

    KRX raw OHL/frozen flags remain diagnostics. They cannot eliminate a KIS
    adjusted-price formation before cross-channel state verification.
    """
    return {(row["formation"], row["code"]): (
        ["exact_preceding_60_session_median_below_threshold"] if row["liquidity_pass"] is False else [])
        for row in candidates}


def validate_write_boundaries(root: Path, spec_path: Path, spec: dict, sources: dict,
                              output_dir: Path, runs_path: Path) -> tuple[Path, Path]:
    """Metadata-only checks before the durable logger can write anything.

    A misdirected log must never append its started record to immutable input
    evidence or committed source. Existing canonical log modes are retained.
    """
    output, runs = _safe_path(output_dir), _safe_path(runs_path)
    protected_roots = []
    for declaration in spec["inputs"].values():
        if "root" in declaration:
            protected_roots.append(_safe_path(Path(declaration["root"])))
        else:
            protected_roots.append(_safe_path(Path(declaration["path"])).parent)
    source_files = {_safe_path(root / item["path"]) for item in sources.get("files", [])}
    source_files.update(_safe_path(path) for path in (
        spec_path, root / REFERENCE, root / "python/pyproject.toml", root / "python/uv.lock"))
    require(runs not in source_files, "trial log overlaps committed source/spec/dependency file")
    require(not any(runs.is_relative_to(path) for path in protected_roots),
            "trial log overlaps immutable input evidence")
    require(not any(output.is_relative_to(path) or path.is_relative_to(output) for path in protected_roots),
            "output overlaps immutable input evidence")
    require(not output.is_relative_to(root), "private output must be outside checkout")
    require(not runs.is_relative_to(output), "trial log must be outside exclusive output root")
    if runs.exists():
        info = runs.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                "existing trial log must be a single-link regular file")
    return output, runs


def evaluate_inputs(spec: dict, inputs: PinnedInputs) -> dict:
    """Only called after durable trial start; every actual byte read is here."""
    declared = spec["inputs"]
    calendar_manifest = inputs.json(Path(declared["calendar_manifest"]["path"]),
                                    declared["calendar_manifest"]["sha256"], "calendar_manifest")
    calendar, history_windows, schedule = calendar_schedule(calendar_manifest, spec["parameters"])
    bb = inputs.json(Path(declared["bb_result"]["path"]), declared["bb_result"]["sha256"], "bb_result")
    bb_windows, wanted = _bb_windows(bb, calendar)
    observations, provenance, saved_dates = {}, {}, set()
    response_provenance = []
    calendar_dates = [date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}") for day in calendar]
    calendar_indices = {day: index for index, day in enumerate(calendar)}

    def retain(day: str, service: str, rows: list[dict], observation: dict, response_id: str) -> None:
        if service not in probe.SERVICES[:2]:
            return
        saved_dates.add(day)
        # Availability is modeled identically for both arms; only selection
        # and entry lag differ. The response receipt is not a source release
        # timestamp or proof of an original historical data vintage.
        response_provenance.append(response_metadata(day, service, observation, calendar_dates,
                                                     calendar_indices[day], response_id))
        for position, row in enumerate(rows):
            key = row["ISU_CD"], day
            if key not in wanted:
                continue
            require(key not in observations, "duplicate raw code/date across saved responses")
            # Keep only the exact fields needed for the coverage partition.
            # Whole-market raw strings remain solely in the pinned evidence.
            observations[key] = {field: row[field] for field in (
                "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC", "ACC_TRDVAL")}
            provenance[key] = {"service": service, "response_sha256": observation["response_sha256"],
                               "row_position": position, "observation_date": day,
                               "response_metadata_ref": response_id}

    aq_report, aq_started, aq_ledger = _receipt(inputs, declared["aq"], "aq")
    aq_matrix = [{"service": service, "bas_dd": day} for day in formation.DATES for service in probe.SERVICES]
    require(aq_report.get("status") == "formations_audited" and aq_report.get("report_persisted") is True
            and aq_started.get("request_matrix") == aq_matrix
            and len(aq_report["observations"]) == 56, "AQ acquisition receipt mismatch")
    _ledger(aq_ledger, aq_matrix)
    targets, audited_formations, rows_by_service = [], [], {}
    for index, item in enumerate(aq_matrix):
        obs = aq_report["observations"][index]
        rows = _rows(inputs, Path(declared["aq"]["root"]), index + 1, item, obs, "aq", aq=True)
        rows_by_service[item["service"]] = rows
        retain(item["bas_dd"], item["service"], rows, obs, f"aq:{index + 1}")
        if index % 4 != 3:
            continue
        day = item["bas_dd"]
        audited_formations.append(formation.audit_day(day, rows_by_service))
        for trade_service, basic_service in zip(probe.SERVICES[:2], probe.SERVICES[2:], strict=True):
            basics = {row["ISU_SRT_CD"]: row for row in rows_by_service[basic_service]}
            for row in rows_by_service[trade_service]:
                if formation._number(row, "MKTCAP") < formation.MARKET_CAP_MIN_KRW:
                    continue
                code = row["ISU_CD"]
                metrics = liquidity._bar_metrics(row)
                targets.append({"formation": day, "code": code,
                                "source_common_label": basics[code]["KIND_STKCERT_TP_NM"] == "보통주",
                                "reported_listing_date": basics[code]["LIST_DD"],
                                "formation_tradability_proxy": not metrics["zero_ohl"] and not metrics["equal_ohlc_zero_turnover"]})
        rows_by_service.clear()
    totals = {field: sum(day[field] for day in audited_formations)
              for field in ("trade_rows", "cap_pass_rows", "cap_below_rows", "cap_pass_common_label_rows")}
    require(totals == AQ_TOTALS
            and aq_report.get("days") == audited_formations
            and all(aq_report.get(field) == totals[field] for field in totals if field != "trade_rows"),
            "AQ full numeric denominator re-audit mismatch")

    ar_days = sorted({day for values in history_windows.values() for day in values})
    matrix = [{"bas_dd": day, "service": service} for day in ar_days for service in probe.SERVICES[:2]]
    require(len(matrix) == 1680 and not set(ar_days) & set(formation.DATES), "AR fixed matrix mismatch")
    original, original_started, original_ledger = _receipt(inputs, declared["ar_original"], "ar_original")
    recovery, recovery_started, recovery_ledger = _receipt(inputs, declared["ar_recovery"], "ar_recovery")
    require(original.get("status") == "failed" and original.get("failure") == "transport_failure"
            and original.get("requests_attempted") == 550 and len(original["observations"]) == 549
            and original_started.get("request_matrix") == matrix
            and recovery.get("status") == "liquidity_audited"
            and recovery.get("combined_valid_responses") == 1680
            and recovery.get("candidates_persisted") is True
            and recovery.get("transport_retries_used") == 0
            and len(recovery["observations"]) == 1131
            and recovery_started.get("request_matrix") == matrix
            and recovery_started.get("new_request_matrix") == matrix[549:], "AR acquisition receipt mismatch")
    _ledger(original_ledger, matrix[:549], trailing_transport_failure=True)
    _ledger(recovery_ledger, matrix[549:], logical_first=550, recovery=True)
    target_keys = {(row["formation"], row["code"]) for row in targets}
    require(len(target_keys) == AQ_TOTALS["cap_pass_rows"]
            and {(w["formation"], w["code"]) for w in bb_windows} <= target_keys,
            "BB/AQ source population mismatch")
    targets_per_day = {day: {row["code"] for row in targets if day in history_windows[row["formation"]]}
                       for day in ar_days}
    histories = defaultdict(dict)
    source_observations = original["observations"] + recovery["observations"]
    for index, item in enumerate(matrix):
        role = "ar_original" if index < 549 else "ar_recovery"
        obs = source_observations[index]
        if role == "ar_recovery":
            require(obs.get("logical_number") == index + 1 and obs.get("number") == index - 548,
                    "AR recovery observation number mismatch")
        rows = _rows(inputs, Path(declared[role]["root"]), index + 1, item, obs, role)
        rows_by_service[item["service"]] = rows
        formation._check_relations(rows_by_service)
        retain(item["bas_dd"], item["service"], rows, obs, f"{role}:{index + 1}")
        for row in rows:
            if row["ISU_CD"] in targets_per_day[item["bas_dd"]]:
                histories[row["ISU_CD"]][item["bas_dd"]] = liquidity._bar_metrics(row)
        if index % 2 == 1:
            rows_by_service.clear()
    candidates = [liquidity.summarize_history(
        row, history_windows[row["formation"]],
        {day: value for day, value in histories[row["code"]].items() if day in history_windows[row["formation"]]},
        set(ar_days)) for row in targets]
    cached_candidates = inputs.json(Path(declared["ar_recovery"]["root"]) / "candidates.json",
                                    declared["ar_recovery"]["candidates_sha256"], "ar_recovery:candidates")
    require(cached_candidates.get("candidates") == candidates
            and recovery.get("candidates_sha256") == declared["ar_recovery"]["candidates_sha256"]
            and recovery.get("totals") == liquidity._counts(candidates)
            and recovery.get("common_label_totals") == liquidity._counts(
                [row for row in candidates if row["source_common_label"]]), "AR candidate re-audit mismatch")
    coverage = coverage_summary(bb_windows, observations, saved_dates, numeric_liquidity_failures(candidates),
        {(row["formation"], row["code"]): row["formation_tradability_proxy"] for row in candidates})
    return {"schema": SCHEMA, "status": "saved_input_coverage_audited",
            "formation_totals": totals, "liquidity_totals": liquidity._counts(candidates),
            "bb_population": {key: bb[key] for key in ("population_rows", "target_windows", "target_codes", "retained_controls")},
            "paired_schedule": schedule, "coverage": coverage,
            "response_provenance": response_provenance,
            "row_provenance": [{"code": code, "date": day, **value} for (code, day), value in sorted(provenance.items())],
            "source_labels_are_classification": False, "historical_eligibility_certified": False,
            "normal_baseline_certified": False, "historical_publication_certified": False,
            "holding_lot_denominator": None, "action_lot_denominator": None,
            "D1_completed_studies": 0, "D2_completed_studies": 0,
            "holding_selection_executed": False, "returns_computed": False,
            "database_access": False, "new_source_requests": 0, "promotion_allowed": False,
            "remaining": ["dated issue membership and continuous operating-period baselines",
                          "saved adjusted-price panel integration and logged actual selection",
                          "consequential holdings/successors and price-basis/action evidence",
                          "paired descriptive performance replay"],
            "input_manifest": inputs.manifest}


def run_preflight(root: Path, spec_path: Path, output_dir: Path, runs_path: Path,
                  *, reader: Reader = read_private) -> dict:
    """One attempt; failed partial packages and durable trial records survive."""
    root = root.resolve()
    require(Path.cwd().resolve() == root, "run from repository root")
    sources, raw, spec = freeze_sources(root, spec_path)
    output, runs_path = validate_write_boundaries(root, spec_path, spec, sources, output_dir, runs_path)
    inputs = PinnedInputs(reader)

    def evaluate() -> dict:
        created = False
        try:
            private_directory(output.parent)
            output.mkdir(mode=0o700)
            created = True
            _sync_directory(output.parent)
            write_exclusive(output / "specification.json", raw)
            write_exclusive(output / "source-manifest.json", encoded(sources))
            result = evaluate_inputs(spec, inputs)
            after, after_raw, _ = freeze_sources(root, spec_path)
            # The complete source/spec/lock and actual Python runtime identity
            # remain frozen. Installed third-party packages are unused by this
            # stdlib/local-source runner, so their diagnostics are not a gate.
            identity = {key: value for key, value in sources.items() if key != "installed_locked_distributions"}
            after_identity = {key: value for key, value in after.items() if key != "installed_locked_distributions"}
            require(after_identity == identity and after_raw == raw, "source changed during actual-input preflight")
            before_packages = sources.get("installed_locked_distributions", {})
            after_packages = after.get("installed_locked_distributions", {})
            result["unused_installed_package_diagnostics"] = {
                "before": before_packages, "after": after_packages,
                "changed": before_packages != after_packages, "execution_dependency": False}
            result["source_manifest_sha256"] = digest(encoded(sources))
            result["specification_sha256"] = digest(raw)
            write_exclusive(output / "result.json", encoded(result))
            write_exclusive(output / "input-manifest.json", encoded(inputs.manifest))
            return {"status": result["status"], "output_dir": str(output),
                    "result_sha256": digest(encoded(result)), "source_manifest_sha256": digest(encoded(sources)),
                    "input_manifest_sha256": digest(encoded(inputs.manifest)),
                    "formation_totals": result["formation_totals"], "liquidity_totals": result["liquidity_totals"],
                    "coverage": {key: value for key, value in result["coverage"].items() if key != "windows"},
                    "unused_installed_package_diagnostics": result["unused_installed_package_diagnostics"],
                    "holding_lot_denominator": None, "action_lot_denominator": None,
                    "D1_completed_studies": 0, "D2_completed_studies": 0}
        except BaseException as exc:
            if created:
                write_exclusive(output / "failure.json", encoded({"status": "failed", "error_type": type(exc).__name__,
                    "partial_package_preserved": True, "actual_inputs_attempted": inputs.manifest,
                    "source_manifest_sha256": digest(encoded(sources)), "specification_sha256": digest(raw)}))
            raise

    previous_umask = os.umask(0o077)
    try:
        return experiment_log.run_discovery_trial(
            study_id=spec["study_id"], specification_sha256=digest(raw),
            parameters={**spec["parameters"], "source_manifest_sha256": digest(encoded(sources)),
                        "source_manifest": sources, "declared_inputs": spec["inputs"],
                        "output_dir": str(output), "automatic_retries": 0},
            window=spec["window"], evaluate=evaluate, runs_path=runs_path)
    finally:
        os.umask(previous_umask)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runs-path", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = run_preflight(Path(__file__).resolve().parents[2], args.spec, args.output_dir, args.runs_path)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "automatic_retries": 0}))
        return 1
    print(json.dumps(record["result"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
