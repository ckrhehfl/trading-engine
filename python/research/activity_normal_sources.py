"""Bounded saved KRX inputs for BL; no acquisition, database or KIS fallback.

The caller has already logged the actual trial start and validated the pinned
calendar and complete BI potential partition. Receipts are verified first, then
the exact raw-read scope is persisted before any response body is opened.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from research import activity_preflight as saved
from research import activity_price_parity as cache
from research.activity_screen import PublishedValue, ScreenObservation
from research.activity_timing import AvailabilityMetadata, SessionLagPolicy


POTENTIAL_WINDOWS = 36
NEGATIVE_TYPES = {
    "005387": ("preferred_share", "KOSPI", "주권", "신형우선주", ""),
    "005935": ("preferred_share", "KOSPI", "주권", "구형우선주", ""),
    "088980": ("investment_company", "KOSPI", "사회간접자본투융자회사", "보통주", ""),
    "950160": ("foreign_depositary_receipt", "KOSDAQ", "주식예탁증권", "보통주", "외국기업(소속부없음)"),
}
REFERENCE = ".planning/rd-bl-large-liquid-normal-input-assembly.md"


def _date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _labels(basic: dict, trade: dict) -> dict:
    return {"market": basic["MKT_TP_NM"], "security_group": basic["SECUGRP_NM"],
            "share_class": basic["KIND_STKCERT_TP_NM"],
            "basic_section": basic["SECT_TP_NM"], "trade_section": trade["SECT_TP_NM"]}


def _negative_type(code: str, labels: dict) -> str | None:
    """Only the four recorded exact source types become negative controls.

    An ordinary source label remains a label, not business-period evidence.
    Unknown or conflicting labels never become convenient exclusions.
    """
    declared = NEGATIVE_TYPES.get(code)
    if declared is not None:
        kind, market, group, share, section = declared
        saved.require(labels == {"market": market, "security_group": group,
                                "share_class": share, "basic_section": section,
                                "trade_section": section}, "conflicting negative-type source labels")
        return kind
    saved.require(labels["security_group"] == "주권" and labels["share_class"] == "보통주"
                  and not labels["basic_section"].startswith("외국")
                  and not labels["trade_section"].startswith("외국"),
                  "unknown or conflicting capitalization-pass source type")
    return None


def _coordinate(role: str, number: int, item: dict, observation: dict, declared: dict,
                *, aq: bool) -> dict:
    service, day = item["service"], item["bas_dd"]
    saved.require(observation.get("service") == service
                  and observation.get("requested_bas_dd") == day
                  and observation.get("schema_valid") is True,
                  "saved observation coordinate mismatch")
    saved._pin(observation.get("response_sha256"))
    width = 2 if aq else 4
    return {"role": role, "logical_number": number, "service": service, "date": day,
            "path": str(Path(declared[role]["root"]) /
                        f"response-{number:0{width}d}-{service}-{day}.json"),
            "sha256": observation["response_sha256"]}


def _read_plan(declared: dict, receipts: dict, history_windows: dict,
               potential_keys: set[tuple[str, str]]) -> dict:
    aq = [_coordinate("aq", index + 1, item, receipts["aq"]["observations"][index],
                      declared, aq=True) for index, item in enumerate(receipts["aq_matrix"])]
    dates = sorted({day for formation, _ in potential_keys for day in history_windows[formation]})
    ar = []
    for day in dates:
        for service in saved.probe.SERVICES[:2]:
            role, number, item, observation, is_aq = cache.raw_coordinate(day, service, receipts)
            saved.require(not is_aq, "liquidity date unexpectedly overlaps formation cache")
            ar.append(_coordinate(role, number, item, observation, declared, aq=False))
    return {"scope": "all_AQ_formation_envelopes_and_potential_prior_60_AR_only",
            "all_source_formation_dates": sorted({item["date"] for item in aq}),
            "potential_formation_dates": sorted({formation for formation, _ in potential_keys}),
            "potential_keys": [{"formation": formation, "code": code}
                               for formation, code in sorted(potential_keys)],
            "liquidity_sessions_by_formation": {
                formation: history_windows[formation] for formation in sorted({f for f, _ in potential_keys})},
            "aq_raw_responses": aq, "ar_raw_responses": ar,
            "raw_response_count": len(aq) + len(ar),
            "holding_prices_read": False, "database_access": False, "api_access": False}


def _metadata(day: str, service: str, observation: dict, calendar: tuple[date, ...],
              field: str) -> AvailabilityMetadata:
    observed = _date(day)
    recorded = observation.get("at")
    retrieved = "unknown" if recorded is None else datetime.fromisoformat(recorded.replace("Z", "+00:00"))
    return AvailabilityMetadata(
        observation_date=observed,
        available_at=SessionLagPolicy(1).selection_at(calendar, calendar.index(observed)),
        retrieved_at=retrieved, source=f"KRX OpenAPI {service}:{field}", is_final=None,
        data_vintage="retrieved_snapshot_sha256:" + observation["response_sha256"],
        evidence_level="assumed", source_public_available_at=None,
        availability_policy="same_D_inputs_assumed_next_market_session_08:30_Asia_Seoul_both_arms",
        evidence_reference=REFERENCE,
    )


def _availability_json(value: AvailabilityMetadata) -> dict:
    return {key: item.isoformat() if isinstance(item, (date, datetime)) else item
            for key, item in asdict(value).items()}


def _formation_rows(day: str, rows: dict, observations: dict, observation_positions: dict,
                    calendar: tuple[date, ...]) -> list[dict]:
    """Preserve every dated trade/basic join, including size failures."""
    result = []
    for trade_service, basic_service in zip(saved.probe.SERVICES[:2], saved.probe.SERVICES[2:], strict=True):
        cap_metadata = _metadata(day, trade_service, observations[trade_service], calendar, "MKTCAP")
        basic_metadata = _metadata(day, basic_service, observations[basic_service], calendar,
                                   "ISU_CD,LIST_DD,SECUGRP_NM,KIND_STKCERT_TP_NM,SECT_TP_NM")
        section_metadata = _metadata(day, trade_service, observations[trade_service], calendar, "SECT_TP_NM")
        for lag in (1, 2):
            cutoff = SessionLagPolicy(lag).selection_at(calendar, calendar.index(_date(day)))
            for metadata in (cap_metadata, basic_metadata, section_metadata):
                metadata.require_available(cutoff, observation_date=_date(day))
        cap_availability, basic_availability, section_availability = (
            _availability_json(value) for value in (cap_metadata, basic_metadata, section_metadata))
        basics = {row["ISU_SRT_CD"]: (position, row)
                  for position, row in enumerate(rows[basic_service])}
        for position, trade in enumerate(rows[trade_service]):
            code = trade["ISU_CD"]
            basic_position, basic = basics[code]
            cap = saved.formation._number(trade, "MKTCAP")
            saved.require(cap == cap.to_integral_value(), "nonintegral source capitalization")
            cap_pass = cap >= saved.formation.MARKET_CAP_MIN_KRW
            labels = _labels(basic, trade)
            negative = _negative_type(code, labels) if cap_pass else None
            result.append({
                "formation": day, "code": code, "isin": basic["ISU_CD"],
                "listing_date": _date(basic["LIST_DD"]).isoformat(),
                "reported_listing_date": basic["LIST_DD"],
                "capitalization": str(cap), "capitalization_raw": trade["MKTCAP"],
                "cap_pass": cap_pass, "negative_type": negative, "literal_source_labels": labels,
                "capitalization_availability": cap_availability,
                "classification_availability": basic_availability,
                "trade_section_availability": section_availability,
                "actual_classification_known_on": None,
                "historical_version_unknown": True,
                "dated_aliases": {"trade_name": trade["ISU_NM"], "basic_name": basic["ISU_NM"],
                                  "abbreviation": basic["ISU_ABBRV"], "english_name": basic["ISU_ENG_NM"]},
                "krx_metadata_provenance": {
                    "formation": day, "code": code,
                    "source_observation_position": observation_positions[basic_service],
                    "source_response_sha256": observations[basic_service]["response_sha256"],
                    "source_row_position": basic_position, "source_service": basic_service},
                "krx_capitalization_provenance": {
                    "source_observation_position": observation_positions[trade_service],
                    "source_response_sha256": observations[trade_service]["response_sha256"],
                    "source_row_position": position, "source_service": trade_service},
            })
    return result


def load_sources(spec_inputs: dict, inputs: saved.PinnedInputs, schedule: list[dict],
                 calendar: tuple[date, ...], potential_keys: set[tuple[str, str]], output: Path) -> dict:
    """Read only pinned saved source data after the caller's durable start.

    The input manifest belongs to the caller's shared PinnedInputs instance.
    Numerical values use exact source decimals. Source publication and original
    historical vintage remain unknown even when the retrieval receipt is dated.
    """
    SessionLagPolicy._calendar(calendar)
    formations = [row["formation"] for row in schedule]
    saved.require(formations == list(saved.formation.DATES)
                  and len(potential_keys) == POTENTIAL_WINDOWS
                  and all(formation in formations and saved.probe.id_kind(code) == "short6"
                          for formation, code in potential_keys), "invalid fixed source population geometry")
    positions = {day.strftime("%Y%m%d"): index for index, day in enumerate(calendar)}
    saved.require(all(day in positions and positions[day] >= saved.liquidity.WINDOW_SESSIONS
                      and positions[day] + 2 < len(calendar) for day in formations),
                  "calendar cannot supply exact source windows")
    history_windows = {day: [item.strftime("%Y%m%d") for item in calendar[
        positions[day] - saved.liquidity.WINDOW_SESSIONS:positions[day]]] for day in formations}
    receipts = cache.cache_receipts(inputs, spec_inputs, {"history_windows": history_windows})
    plan = _read_plan(spec_inputs, receipts, history_windows, potential_keys)
    raw_scope = saved.encoded(plan)
    saved.write_exclusive(output / "raw-source-read-scope.json", raw_scope)

    population, audited_days = [], []
    potential_capitalization = {}
    for day in formations:
        rows, observations, observation_positions = {}, {}, {}
        for service in saved.probe.SERVICES:
            item = {"bas_dd": day, "service": service}
            index = receipts["aq_matrix"].index(item)
            observation = receipts["aq"]["observations"][index]
            rows[service] = saved._rows(inputs, Path(spec_inputs["aq"]["root"]), index + 1,
                                        item, observation, "aq", aq=True)
            observations[service] = observation
            observation_positions[service] = index
        audited_days.append(saved.formation.audit_day(day, rows))
        population.extend(_formation_rows(day, rows, observations, observation_positions, calendar))
        for trade_service in saved.probe.SERVICES[:2]:
            for trade in rows[trade_service]:
                key = day, trade["ISU_CD"]
                if key in potential_keys:
                    metadata = _metadata(day, trade_service, observations[trade_service], calendar, "MKTCAP")
                    potential_capitalization[key] = PublishedValue(saved.formation._number(trade, "MKTCAP"),
                                                                   None, metadata)
    totals = {field: sum(row[field] for row in audited_days) for field in saved.AQ_TOTALS}
    source_keys = {(row["formation"], row["code"]) for row in population}
    saved.require(totals == saved.AQ_TOTALS and len(source_keys) == len(population) == totals["trade_rows"]
                  and receipts["aq"].get("days") == audited_days
                  and all(receipts["aq"].get(field) == value for field, value in totals.items()
                          if field != "trade_rows"), "AQ complete formation population mismatch")
    keyed = {(row["formation"], row["code"]): row for row in population}
    saved.require(potential_keys <= source_keys and set(potential_capitalization) == potential_keys
                  and all(keyed[key]["cap_pass"] and keyed[key]["negative_type"] is None
                          for key in potential_keys), "potential windows conflict with source population")

    wanted = {(code, day) for formation, code in potential_keys for day in history_windows[formation]}
    liquidity_rows = {}
    for day in sorted({day for _, day in wanted}):
        rows_by_service = {}
        for service in saved.probe.SERVICES[:2]:
            role, number, item, observation, is_aq = cache.raw_coordinate(day, service, receipts)
            rows = saved._rows(inputs, Path(spec_inputs[role]["root"]), number, item, observation, role, aq=is_aq)
            rows_by_service[service] = rows
            for position, row in enumerate(rows):
                key = row["ISU_CD"], day
                if key not in wanted:
                    continue
                saved.require(key not in liquidity_rows, "duplicate requested source liquidity code/date")
                metrics = saved.liquidity._bar_metrics(row)
                metadata = _metadata(day, service, observation, calendar, "ACC_TRDVAL")
                liquidity_rows[key] = {
                    "observation": ScreenObservation("frozen" if metrics["equal_ohlc_zero_turnover"] else "observed",
                                                     metrics["turnover"], None, metadata),
                    "raw_acc_trdval": row["ACC_TRDVAL"], "source_service": service,
                    "source_response_sha256": observation["response_sha256"],
                    "source_row_position": position, "logical_number": number, "source_role": role,
                    "historical_version_unknown": True,
                }
        saved.formation._check_relations(rows_by_service)
    missing = sorted(wanted - liquidity_rows.keys())
    saved.require(not missing, "missing potential source liquidity rows: " + ",".join(
        code + ":" + day for code, day in missing))
    potential_sources = {}
    for key in sorted(potential_keys):
        formation, code = key
        source = keyed[key]
        window = history_windows[formation]
        potential_sources[key] = {
            "isin": source["isin"], "listing_date": date.fromisoformat(source["listing_date"]),
            "capitalization": potential_capitalization[key],
            "liquidity": {_date(day): liquidity_rows[code, day]["observation"] for day in window},
            "krx_metadata_provenance": source["krx_metadata_provenance"],
            "source_provenance": {
                "capitalization": source["krx_capitalization_provenance"],
                "literal_source_labels": source["literal_source_labels"],
                "dated_aliases": source["dated_aliases"], "capitalization_raw": source["capitalization_raw"],
                "liquidity_rows": [{"date": _date(day).isoformat(), **{
                    field: value for field, value in liquidity_rows[code, day].items() if field != "observation"}}
                    for day in window], "historical_version_unknown": True,
                "source_public_available_at": None},
        }
    return {"population": population, "formation_totals": totals,
            "negative_type_controls": [row for row in population if row["cap_pass"] and row["negative_type"]],
            "potential_sources": potential_sources, "raw_source_read_scope": plan,
            "raw_source_read_scope_sha256": saved.digest(raw_scope)}
