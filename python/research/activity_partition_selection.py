"""Pure selection contract for an independently verified, pinned BL package.

The caller verifies input bytes, source lineage and proof arithmetic before this
boundary. These checks establish internal agreement, not historical source truth,
price coverage, readiness or performance. The complete dated source population
is separate from a replay's consequential holding-price panel. No IO or producer
import occurs here, and selection never consults an execution-session quote.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from research.activity_timing import AvailabilityMetadata, KST, SessionLagPolicy


def _require(condition: bool, message: str) -> None:
    """Reject a package contradiction before exposing eligible codes."""
    if not condition:
        raise ValueError(message)


def _text(value: object) -> bool:
    """Recognize explicit, unpadded identity or provenance text."""
    return isinstance(value, str) and bool(value) and value == value.strip()


def _pin(value: object) -> bool:
    """Recognize a declared SHA-256 without claiming to verify its source bytes."""
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _day(value: object, *, compact: bool = False) -> date:
    """Parse the canonical ISO or compact dates emitted by BL."""
    _require(isinstance(value, str), "explicit date text is required")
    try:
        parsed = datetime.strptime(value, "%Y%m%d").date() if compact else date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("invalid date text") from error
    _require(value == (parsed.strftime("%Y%m%d") if compact else parsed.isoformat()), "noncanonical date")
    return parsed


def _timestamp(value: object) -> datetime:
    """Require a timezone-aware timestamp rather than infer a local timezone."""
    _require(isinstance(value, str), "explicit timestamp text is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("invalid timestamp") from error
    _require(parsed.utcoffset() is not None, "timestamp must be aware")
    return parsed


def _amount(value: object) -> Decimal:
    """Preserve exact nonnegative decimal text without float conversion."""
    _require(isinstance(value, str), "exact decimal text is required")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("invalid decimal text") from error
    _require(number.is_finite() and number >= 0, "amount must be finite and nonnegative")
    return number


@dataclass(frozen=True)
class PartitionSelection:
    """Immutable eligible signals from the complete source partition, not fills."""

    formation_on: date
    decision_on: date
    selection_at: datetime
    eligible_codes: tuple[str, ...]
    source_population_count: int
    source_partition_sha256: str

    def __post_init__(self) -> None:
        """Keep the declaration immutable and reject ambiguous schedule/count inputs."""
        _require(type(self.formation_on) is date and type(self.decision_on) is date
                 and self.decision_on > self.formation_on, "selection needs explicit ordered dates")
        _require(isinstance(self.selection_at, datetime) and self.selection_at.utcoffset() is not None
                 and self.selection_at.astimezone(KST).date() == self.decision_on,
                 "selection cutoff must be aware and belong to decision session")
        _require(type(self.eligible_codes) is tuple and all(_text(code) for code in self.eligible_codes)
                 and len(set(self.eligible_codes)) == len(self.eligible_codes),
                 "eligible codes must be immutable, unique unpadded text")
        _require(type(self.source_population_count) is int
                 and self.source_population_count >= len(self.eligible_codes)
                 and _pin(self.source_partition_sha256), "complete population count and fingerprint required")


def select_partition(
    selection: PartitionSelection, *, formation_on: date, decision_on: date,
    held_codes: set[str], seed: int, free_slots: int, selection_at: datetime,
) -> tuple[str, ...]:
    """Apply v1 hash order, held exclusion and slots to eligible signals only.

    The source pool need not equal the price panel. A later failed fill gets no
    replacement: this function cannot inspect prices or execution outcomes.
    """
    _require(isinstance(selection, PartitionSelection)
             and selection.formation_on == formation_on and selection.decision_on == decision_on
             and selection.selection_at == selection_at, "partition selection schedule mismatch")
    _require(type(held_codes) is set and all(_text(code) for code in held_codes)
             and type(seed) is int and type(free_slots) is int and free_slots >= 0,
             "held codes, integer seed and nonnegative slots are required")
    pool = [code for code in selection.eligible_codes if code not in held_codes]
    pool.sort(key=lambda code: hashlib.sha256(f"{seed}:{formation_on}:{code}".encode()).digest())
    return tuple(pool[:free_slots])


def _availability(value: object, observed: date, cutoffs: tuple[datetime, datetime]) -> None:
    """Check every field's declared availability against both policy cutoffs."""
    _require(isinstance(value, dict), "field availability metadata is required")
    required = {"observation_date", "available_at", "retrieved_at", "source", "is_final",
                "data_vintage", "evidence_level", "source_public_available_at",
                "availability_policy", "evidence_reference"}
    _require(required <= value.keys(), "incomplete availability metadata")
    metadata = AvailabilityMetadata(
        observation_date=_day(value["observation_date"]), available_at=_timestamp(value["available_at"]),
        retrieved_at=("unknown" if value["retrieved_at"] == "unknown" else _timestamp(value["retrieved_at"])),
        source=value["source"], is_final=value["is_final"], data_vintage=value["data_vintage"],
        evidence_level=value["evidence_level"],
        source_public_available_at=(None if value["source_public_available_at"] is None
                                    else _timestamp(value["source_public_available_at"])),
        availability_policy=value["availability_policy"], evidence_reference=value["evidence_reference"],
    )
    for cutoff in cutoffs:
        metadata.require_available(cutoff, observation_date=observed)


def _normal(row: dict, source: dict, calendar: tuple[date, ...], cutoffs: tuple[datetime, datetime]) -> None:
    """Check joins, field cutoffs and screen dispositions; do not re-prove BL math."""
    formation = _day(row["formation"])
    _require(row.get("isin") == source["isin"] and row.get("code") == source["code"],
             "normal/source issue identity mismatch")
    period, listing, accepted = row.get("identity_period"), row.get("listing"), row.get("accepted_evidence")
    _require(all(isinstance(value, dict) for value in (period, listing, accepted)),
             "normal period, listing and accepted evidence are required")
    _require(period.get("code") == row["code"] and period.get("common_stock") is True
             and _day(period.get("start")) <= formation < _day(period.get("end"))
             and _day(period.get("known_on")) <= formation,
             "normal operating period disagrees with formation")
    _require(listing.get("listed_on") == source["listing_date"]
             and _day(listing.get("known_on")) <= formation,
             "normal listing identity or availability mismatch")
    _require(accepted.get("code") == row["code"] and accepted.get("decision") == "accepted"
             and accepted.get("continuity_evidence_level") == "inferred"
             and _day(accepted.get("known_on_conservative_candidate")) <= formation,
             "normal accepted decision mismatch")
    windows = accepted.get("required_windows")
    _require(isinstance(windows, list) and all(isinstance(window, dict) for window in windows),
             "accepted windows must be records")
    matched = [window for window in windows if window.get("formation") == row["formation"]]
    _require(len(matched) == 1 and matched[0].get("window_id") == f"{formation:%Y%m%d}/{row['code']}"
             and matched[0].get("isin") == row["isin"]
             and matched[0].get("known_before_formation") is True
             and matched[0].get("krx_metadata_provenance") == source["krx_metadata_provenance"],
             "normal accepted/source window provenance mismatch")
    _require(isinstance(matched[0].get("required_dates"), list), "accepted activity dates required")
    inputs = row.get("inputs")
    _require(isinstance(inputs, dict), "normal field inputs are required")
    for field in ("activity", "official_liquidity"):
        observations = inputs.get(field)
        _require(isinstance(observations, dict) and bool(observations), "normal observation map required")
        for raw_day, observation in observations.items():
            observed = _day(raw_day)
            _require(observed in calendar and observed <= formation and isinstance(observation, dict),
                     "normal observation outside historical calendar")
            _require(observation.get("state") in {"observed", "frozen"}, "normal observation state is unresolved")
            amount = _amount(observation.get("turnover"))
            _require(observation["state"] != "frozen" or amount == 0, "frozen normal observation has positive turnover")
            _availability(observation.get("availability"), observed, cutoffs)
            public = observation.get("public_available_at")
            _require(public is None or all(_timestamp(public) <= cutoff for cutoff in cutoffs),
                     "normal actual publication exceeds selection")
    cap = inputs.get("official_capitalization")
    _require(isinstance(cap, dict) and _amount(cap.get("value")) == _amount(source["capitalization"]),
             "normal/source capitalization mismatch")
    _availability(cap.get("availability"), formation, cutoffs)
    public = cap.get("public_available_at")
    _require(public is None or all(_timestamp(public) <= cutoff for cutoff in cutoffs),
             "capitalization actual publication exceeds selection")
    _require(set(inputs["activity"]) == set(matched[0].get("required_dates", [])),
             "normal consumed activity dates disagree with accepted window")
    _require(row["formation"] in inputs["activity"], "normal activity must include formation observation")
    index = calendar.index(formation)
    _require(index >= 60 and set(inputs["official_liquidity"]) == {
        day.isoformat() for day in calendar[index - 60:index]}, "normal liquidity dates differ from preceding 60 sessions")
    arms = row.get("arms")
    _require(isinstance(arms, dict) and set(arms) == {"D1", "D2"}, "both normal arms required")
    for lag, cutoff in enumerate(cutoffs, 1):
        current = arms[f"D{lag}"]
        _require(isinstance(current, dict) and type(current.get("session_lag")) is int
                 and current["session_lag"] == lag and current.get("decision_on") == calendar[index + lag].isoformat()
                 and _timestamp(current.get("selection_at")) == cutoff
                 and current.get("every_consumed_input_available_by_selection") is True,
                 "normal arm schedule/availability mismatch")
        candidate = current.get("candidate")
        _require((candidate is None) == (inputs["activity"][row["formation"]]["state"] == "frozen"),
                 "normal formation state disagrees with candidate presence")
        if candidate is None:
            reasons = ["formation_frozen"]
        else:
            _require(isinstance(candidate, dict) and candidate.get("code") == row["code"]
                     and candidate.get("classification") == "operating_common"
                     and _day(candidate.get("classification_known_on")) <= formation
                     and type(candidate.get("size_pass")) is bool
                     and type(candidate.get("activity_pass")) is bool
                     and all(candidate.get(key) in {"pass", "fail", "insufficient"}
                             for key in ("absolute_liquidity", "operating_baseline")),
                     "normal candidate is unresolved or has conflicting identity")
            _require(candidate["size_pass"] == source["cap_pass"], "normal/source cap disposition mismatch")
            reasons = []
            if not candidate["size_pass"]:
                reasons.append("capitalization_below_floor")
            for key in ("absolute_liquidity", "operating_baseline"):
                if candidate[key] != "pass":
                    reasons.append(f"{key}_{candidate[key]}")
            if not candidate["activity_pass"]:
                reasons.append("activity_predicate_failed")
        _require(type(current.get("selected")) is bool and current["selected"] == (not reasons)
                 and current.get("exclusion_reasons") == reasons,
                 "normal selected/exclusion disposition mismatch")
    comparable = set(arms["D1"]) - {"session_lag", "decision_on", "selection_at"}
    _require(set(arms["D1"]) == set(arms["D2"])
             and all(arms["D1"][key] == arms["D2"][key] for key in comparable)
             and row.get("paired_eligibility_and_dates_equal") is True,
             "paired normal eligibility changed")


def selections_from_bl(
    result: dict, population: dict, normal: list, calendar: tuple[date, ...],
    params: Mapping, arm: str,
) -> tuple[PartitionSelection, ...]:
    """Validate BL package agreement and retain every formation, including empty signals.

    Package hashes must already have been checked by the separately logged
    caller. A fingerprint here covers all formation rows and referenced metadata;
    it does not turn a self-consistent declaration into verified source evidence.
    """
    _require(isinstance(arm, str) and arm in {"D1", "D2"} and type(calendar) is tuple and isinstance(params, Mapping),
             "explicit paired arm, immutable calendar and parameters required")
    SessionLagPolicy._calendar(calendar)
    end = _day(params.get("end"))
    _require(end in calendar, "evaluation end missing from calendar")
    indices = SessionLagPolicy(1, paired=True).formation_indices(
        calendar, lookback=params.get("lookback"), holding_sessions=params.get("holding_sessions"),
        end_index=calendar.index(end))
    formations = {calendar[index].strftime("%Y%m%d"): index for index in indices}
    _require(bool(formations), "no paired formation horizon")
    _require(isinstance(result, dict) and result.get("schema") == "activity-normal-input-assembly-v1"
             and result.get("status") == "normal_inputs_assembled"
             and result.get("returns_computed") is False and result.get("holding_selection_executed") is False
             and result.get("promotion_allowed") is False
             and _pin(result.get("source_manifest_sha256")) and _pin(result.get("specification_sha256")),
             "completed pinned BL input package required")
    audits = result.get("audit_sha256")
    _require(isinstance(audits, dict) and all(_pin(audits.get(name)) for name in (
        "population-audit.json", "normal-input-audit.json", "restored-activity-audit.json",
        "read-scope.json", "raw-source-read-scope.json")), "BL audit lineage pins required")
    expected_schedule = [{"formation": calendar[index].strftime("%Y%m%d"), "arms": [
        {"session_lag": lag, "execution_on": calendar[index + lag].isoformat(),
         "selection_at": SessionLagPolicy(lag).selection_at(calendar, index).isoformat(),
         "due_on": calendar[index + lag + params["holding_sessions"]].isoformat(),
         "status": "input_coverage_only_not_executed"} for lag in (1, 2)]} for index in indices]
    _require(result.get("paired_schedule") == expected_schedule, "incomplete or changed paired schedule")
    _require(isinstance(population, dict) and population.get("schema") == "activity-normal-population-audit-v1"
             and isinstance(population.get("rows"), list)
             and isinstance(population.get("availability_metadata"), dict), "complete BL population audit required")
    rows, metadata = population["rows"], population["availability_metadata"]
    keyed, grouped, referenced, issues = {}, {formation: [] for formation in formations}, set(), set()
    dispositions = {"cap_below", "negative_type", "bh_failure", "bi_failure", "normal_window"}
    for row in rows:
        _require(isinstance(row, dict), "population rows must be records")
        key = row.get("formation"), row.get("code")
        _require(key[0] in formations and isinstance(key[1], str)
                 and re.fullmatch(r"[0-9A-Z]{6}", key[1]) is not None and key not in keyed
                 and _text(row.get("isin")) and isinstance(row.get("krx_metadata_provenance"), dict),
                 "duplicate, missing or invalid population identity")
        issue = key[0], row["isin"]
        provenance = row["krx_metadata_provenance"]
        _require(issue not in issues and provenance.get("formation") == key[0]
                 and provenance.get("code") == key[1] and _pin(provenance.get("source_response_sha256")),
                 "duplicate issue or conflicting source identity provenance")
        issues.add(issue)
        _require(_day(row.get("listing_date")) <= calendar[formations[key[0]]], "invalid source listing date")
        index = formations[key[0]]
        cutoffs = tuple(SessionLagPolicy(lag).selection_at(calendar, index) for lag in (1, 2))
        for field in ("capitalization_availability", "classification_availability", "trade_section_availability"):
            reference = row.get(field)
            _require(isinstance(reference, str) and reference in metadata, "missing population metadata reference")
            _availability(metadata[reference], calendar[index], cutoffs)
            referenced.add(reference)
        cap = _amount(row.get("capitalization"))
        _require(cap == cap.to_integral_value() and type(row.get("cap_pass")) is bool
                 and row["cap_pass"] == (cap >= Decimal("5000000000000")), "invalid capitalization disposition")
        disposition, proof = row.get("disposition"), row.get("proof")
        _require(disposition in dispositions and isinstance(proof, dict), "explicit partition proof required")
        if disposition == "cap_below":
            _require(not row["cap_pass"] and row.get("negative_type") is None
                     and proof == {"capitalization": row["capitalization"]}, "false cap exclusion")
        elif disposition == "negative_type":
            _require(row["cap_pass"] and row.get("negative_type") in {
                "preferred_share", "investment_company", "foreign_depositary_receipt"}
                and proof == {"literal_type": row["negative_type"]}, "false negative-type exclusion")
            labels = row.get("literal_source_labels")
            _require(isinstance(labels, dict) and (
                (row["negative_type"] == "preferred_share" and labels.get("share_class") in {"신형우선주", "구형우선주"})
                or (row["negative_type"] == "investment_company" and labels.get("security_group") == "사회간접자본투융자회사")
                or (row["negative_type"] == "foreign_depositary_receipt" and labels.get("security_group") == "주식예탁증권")),
                "negative disposition contradicts literal source labels")
        else:
            _require(row["cap_pass"] and row.get("negative_type") is None, "downstream proof contradicts source disposition")
            labels = row.get("literal_source_labels")
            _require(isinstance(labels, dict) and labels.get("security_group") == "주권"
                     and labels.get("share_class") == "보통주"
                     and all(isinstance(labels.get(field), str) and not labels[field].startswith("외국")
                             for field in ("basic_section", "trade_section")),
                     "downstream proof contradicts ordinary domestic source labels")
            if disposition == "bh_failure":
                failures = proof.get("proven_numeric_failures")
                _require(proof.get("formation") == key[0] and proof.get("code") == key[1]
                         and isinstance(failures, list) and bool(failures)
                         and all(value in {"exact_preceding_60_session_median_below_threshold",
                                           "BB_verified_observed_history_less_than_60"} for value in failures)
                         and failures == sorted(set(failures))
                         and proof.get("potentially_influential_unresolved") is False, "false BH failure proof")
            elif disposition == "bi_failure":
                _require(proof.get("formation") == key[0] and proof.get("code") == key[1]
                         and proof.get("proven_failure") is True and proof.get("unresolved") == []
                         and proof.get("reason") in {"formation_frozen", "zero_baseline", "activity_below_threshold"}
                         and proof.get("historical_eligibility_certified") is False
                         and all(isinstance(proof.get(field), list) for field in (
                             "consumed_dates", "normal_dates", "frozen_dates"))
                         and bool(proof["consumed_dates"])
                         and proof["consumed_dates"][-1] == calendar[index].isoformat()
                         and all(field in proof and (proof[field] is None or isinstance(proof[field], str))
                                 for field in ("baseline", "formation_turnover", "required_turnover"))
                         and isinstance(proof.get("selection_at"), dict)
                         and set(proof["selection_at"]) == {"D1", "D2"}
                         and all(_timestamp(proof["selection_at"].get(f"D{lag}")) == cutoff
                                 for lag, cutoff in enumerate(cutoffs, 1)), "false BI failure proof")
            else:
                _require(proof == {"reason": "numeric_pass_unresolved"}, "false normal-window proof")
        keyed[key] = row
        grouped[key[0]].append(row)
    _require(all(grouped.values()) and referenced == set(metadata), "missing formation population or unreferenced metadata")
    _require(all(isinstance(row.get("literal_source_labels"), dict)
                 and _text(row["literal_source_labels"].get("share_class")) for row in rows),
             "literal source share labels required")
    _require(result.get("formation_totals") == {
        "trade_rows": len(rows), "cap_pass_rows": sum(row["cap_pass"] for row in rows),
        "cap_below_rows": sum(not row["cap_pass"] for row in rows),
        "cap_pass_common_label_rows": sum(row["cap_pass"] and row["literal_source_labels"]["share_class"] == "보통주"
                                          for row in rows)}, "formation totals disagree with complete source pool")
    controls = []
    for row in rows:
        if row["disposition"] == "negative_type":
            control = {key: value for key, value in row.items() if key not in {"disposition", "proof"}}
            for field in ("capitalization_availability", "classification_availability", "trade_section_availability"):
                control[field] = metadata[row[field]]
            controls.append(control)
    _require(result.get("negative_type_controls") == controls, "negative controls disagree with source partition")
    _require(isinstance(normal, list), "normal audit must be records")
    normals = {}
    for row in normal:
        _require(isinstance(row, dict), "normal audit rows must be records")
        key = _day(row.get("formation")).strftime("%Y%m%d"), row.get("code")
        _require(key in keyed and key not in normals and keyed[key]["disposition"] == "normal_window",
                 "duplicate or unjoined normal window")
        index = formations[key[0]]
        _normal(row, keyed[key], calendar,
                tuple(SessionLagPolicy(lag).selection_at(calendar, index) for lag in (1, 2)))
        normals[key] = row
    _require(set(normals) == {key for key, row in keyed.items() if row["disposition"] == "normal_window"},
             "normal audit does not cover exactly the normal partition")
    signals = {name: [{"formation": row["formation"], "code": row["code"], "isin": row["isin"]}
                     for _, row in sorted(normals.items()) if row["arms"][name]["selected"]]
               for name in ("D1", "D2")}
    _require(isinstance(result.get("arms"), dict) and set(result["arms"]) == {"D1", "D2"}, "paired result arms required")
    for name in signals:
        _require(result["arms"][name] == {"screen_windows": len(normals), "signal_windows": len(signals[name]),
                 "signals": signals[name], "performance_completed": False}, "signal list disagrees with normal windows")
    counts = dict(sorted(Counter(row["disposition"] for row in rows).items()))
    a, b = ({(row["formation"], row["code"]) for row in signals[name]} for name in ("D1", "D2"))
    _require(result.get("summary") == {"source_rows": len(rows), "disposition_counts": counts,
        "replayed_BI_windows": counts.get("bi_failure", 0) + len(normals), "normal_windows": len(normals),
        "normal_codes": len({code for _, code in normals}),
        "signal_windows": {name: len(values) for name, values in signals.items()},
        "paired_signal_symmetric_difference": len(a ^ b), "paired_signal_union": len(a | b),
        "unresolved_input_windows": 0}, "BL summary disagrees with complete partition")
    selections = []
    for formation, index in formations.items():
        formation_rows = sorted(grouped[formation], key=lambda row: row["code"])
        references = {row[field] for row in formation_rows for field in (
            "capitalization_availability", "classification_availability", "trade_section_availability")}
        raw = json.dumps({"rows": formation_rows, "availability_metadata": {
            reference: metadata[reference] for reference in sorted(references)}}, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        lag = int(arm[-1])
        eligible = tuple(row["code"] for row in signals[arm]
                         if _day(row["formation"]).strftime("%Y%m%d") == formation)
        selections.append(PartitionSelection(calendar[index], calendar[index + lag],
            SessionLagPolicy(lag).selection_at(calendar, index), eligible, len(formation_rows),
            hashlib.sha256(raw).hexdigest()))
    return tuple(selections)
