"""Invented completed DK wire reports only; no ancestor imports or market IO."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import inspect
import json

import pytest

from research import intraday_breakout_loss_concentration as loss


DAYS = ("2025-09-04", "2025-09-11", "2025-10-02", "2026-01-02")
SYMBOLS = tuple(f"KRX:{index:06d}" for index in range(1, 21))
_KST = timezone(timedelta(hours=9))


def wire(value):
    return json.loads(loss._canonical(value))


def _full_days():
    day, end = date.fromisoformat(loss.WINDOW["start"]), date.fromisoformat(loss.WINDOW["end"])
    weekdays = []
    while day <= end:
        if day.weekday() < 5:
            weekdays.append(day.isoformat())
        day += timedelta(days=1)
    # Preserve every observed ISO week; actual exchange calendar is not read.
    mandatory = {next(day for day in weekdays if loss._week(day) == week)
        for week in sorted({loss._week(day) for day in weekdays})}
    mandatory.add(weekdays[-1])
    for day in weekdays:
        if len(mandatory) == 257:
            break
        mandatory.add(day)
    result = sorted(mandatory)
    assert len(result) == 257 and len({loss._week(day) for day in result}) == 56
    return result


def _set_return(row, gross, *, entry_price="100"):
    gross, entry = Fraction(gross), Fraction(entry_price)
    exit_price = entry * (1 + gross)
    # All fixture denominators are terminating decimals; this is exact here.
    with localcontext() as context:
        context.prec = 100
        exit_text = str(Decimal(exit_price.numerator) / Decimal(exit_price.denominator))
    label = row["arms"]["basic"]["label"]
    label.update(entry_open=entry_price, exit_open=exit_text, price_change=wire(gross))
    pair = row["breakout_exit_policy"]
    for arm in ("time_only", "managed"):
        pair[arm].update(entry_open=wire(entry), exit_open=wire(exit_price), price_change=wire(gross))
    pair["common"].update(anchor_open=wire(exit_price), anchor_price_change=wire(gross))


def build_synthetic_fixture(days=None, *, full=False, metadata_padding=0):
    """Build a DK wire skeleton directly; never run or import any old producer.

    full=True supplies 10280 target rows, 257 sessions, 56 weeks and the original
    1169/1167 event geometry. Optional padding is synthetic source metadata on
    every row, allowing the caller's separate process to exercise full-byte
    decoding/hashing memory without constructing a million-row raw archive.
    """
    reference = list(_full_days() if full and days is None else DAYS if days is None else days)
    symbols = SYMBOLS if full else SYMBOLS[:4]
    source = {"study_id": loss.SOURCE_STUDY_ID, "mode": loss.SOURCE_MODE,
        "specification_sha256": loss.UPSTREAM_SPECIFICATION_SHA256, "window": dict(loss.WINDOW),
        "parameters": {"entry_delay_minutes": 3, "time_cap_minutes": 30,
            "assumed_cost": wire(loss.COST),
            "cohort": "unchanged_DJ_inherited_DH_common_outcome_complete_basic_signals_no_new_exclusion",
            "anchors": "original_entry_OPEN_and_original_entry_plus30minute_OPEN_exact_raw_checks"},
        "assumptions": {"source": "invented_completed_DK_fixture", "availability": "assumed_not_certified"},
        "source_provenance": {"study_id": "invented_ancestor_metadata_not_replayed"},
        "reference_sessions": reference, "reference_weeks": sorted({loss._week(day) for day in reference}),
        "upstream_calendar": {"rows": [{"date": day, "state": "synthetic"} for day in reference]},
        "source_target_matrix": {}, "variants": {}, "comparison_not_run_reasons": [],
        "pin_authentication": "caller_obligation_not_proven_by_pure_builder",
        **{name: False for name in (
            "historical_eligibility_certified", "liquidity_certified", "historical_revision_status_certified",
            "calendar_completeness_certified", "historical_api_availability_proven", "price_basis_certified",
            "performance_certified", "promotion_allowed", "full_family_certified", "family_adopted",
            "underpower_exception_applied", "historical_costs_certified", "actual_fills_certified",
            "actual_portfolio_pnl", "account_maximum_drawdown_calculated", "orders_simulated", "calibration_only",
            "diagnostic_only", "new_signals_calculated", "upstream_statistics_recalculated",
            "requires_operator_choice", "requires_operator_choice_before_comparison")},
        "exploratory_only": True, "new_labels_calculated": True, "raw_replay_performed": True,
        "comparison_performed": True, "exit_policy_compared": True,
        "new_performance_families": 1, "source_rows_overwritten": 0}
    for policy in loss.POLICIES:
        rows, memberships = [], []
        for day_index, day in enumerate(reference):
            targets = [{"symbol": symbol, "rank": index + 1, "stored_row_count": 195,
                "state": "stored_rows_completeness_unverified"} for index, symbol in enumerate(symbols)]
            membership = {"session": day, "selected_at": day + "T08:00:00+09:00",
                "observation_date": (date.fromisoformat(day) - timedelta(days=2)).isoformat(),
                "available_at": day + "T07:00:00+09:00", "retrieved_at": {"synthetic": "2026-10-20T00:00:00+00:00"},
                "source": "invented_monthly_source", "is_final": None,
                "data_vintage": {"synthetic": "a" * 64}, "evidence_level": "assumed"}
            memberships.append({**deepcopy(membership), "targets": deepcopy(targets)})
            signal_count = ((5 if day_index < 145 else 4) if day_index < 256 else 0) if full else (3 if day_index < 2 else 0)
            if full and policy == "D+2" and day_index < 2:
                signal_count -= 1
            for index, target in enumerate(targets):
                symbol = target["symbol"]
                metadata = {"observation_date": day, "available_at": None,
                    "retrieved_at": "2026-10-20T00:00:00+00:00", "source": "collector klines",
                    "is_final": None, "evidence_level": "assumed"}
                row = {"session": day, "symbol": symbol, "data_vintage": f"{day_index * 20 + index:064x}",
                    "raw_chunk_metadata": metadata, "input_rows": 195,
                    "source_fetched_at": ["2026-10-20T00:00:00+00:00"],
                    "membership_target": deepcopy(target), "membership_source": deepcopy(membership),
                    "arms": {arm: {"status": "known_no_signal", "event": None, "label": None}
                        for arm in ("basic", "retest")},
                    "signal_observable": True, "outcome_complete": True,
                    "exclusion_reasons": [], "breakout_exit_policy": None}
                if metadata_padding:
                    row["synthetic_metadata_padding"] = "x" * metadata_padding
                if index < signal_count:
                    opening = datetime.combine(date.fromisoformat(day), datetime.min.time(), _KST).replace(
                        hour=10 if day in {"2025-11-13", "2026-01-02"} else 9)
                    signal_end = opening + timedelta(minutes=20)
                    decision, entry = signal_end + timedelta(minutes=2), signal_end + timedelta(minutes=5)
                    exit_at = entry + timedelta(minutes=30)
                    label = {"status": "known", "entry_at": entry.isoformat(), "exit_at": exit_at.isoformat(),
                        "time_cap_decided_at": decision.isoformat(), "entry_price_available_at": (entry + timedelta(minutes=3)).isoformat(),
                        "label_available_at": (exit_at + timedelta(minutes=3)).isoformat(),
                        "actual_fill_certified": False, "evidence_level": "assumed", "required_minute_buckets": 31,
                        "unknown_minute_buckets": 0, "unknown_reasons": [],
                        "unknown_bucket_reason_counts": {"absent": 0, "invalid": 0, "conflicting": 0}}
                    row["arms"]["basic"] = {"status": "signal", "event": {"decision_at": decision.isoformat(),
                        "available_at": decision.isoformat(), "signal_bar_start": (signal_end - timedelta(minutes=5)).isoformat(),
                        "signal_bar_end": signal_end.isoformat()}, "label": label}
                    # B is retained source evidence only; DL must never measure it.
                    row["arms"]["retest"]["path"] = {"breakout_bar": {"start_at": (signal_end - timedelta(minutes=5)).isoformat(),
                        "end_at": signal_end.isoformat(), "available_at": decision.isoformat(),
                        "open": "100", "high": "104", "low": "99", "close": "103"}}
                    timed = {"status": "known", "exit_at": exit_at.isoformat(),
                        "label_available_at": (exit_at + timedelta(minutes=3)).isoformat(),
                        "unknown_reason": None, "actual_fill_certified": False, "holding_minutes": 30}
                    row["breakout_exit_policy"] = {"common": {"decision_at": decision.isoformat(), "entry_at": entry.isoformat(),
                        "entry_available_at": (entry + timedelta(minutes=3)).isoformat(), "time_cap_at": exit_at.isoformat(),
                        "time_cap_decided_at": decision.isoformat(), "data_vintage": row["data_vintage"],
                        "raw_chunk_metadata": deepcopy(metadata)}, "time_only": deepcopy(timed), "managed": deepcopy(timed)}
                    gross = ((Fraction(-1, 50), Fraction(-1, 100), Fraction(1, 500), Fraction(1, 100),
                        Fraction(), Fraction(-1, 200))[(day_index * 3 + index) % 6])
                    _set_return(row, gross, entry_price=str(1000000 + day_index * 29 + index * 137) if full else "100")
                elif not full and day_index == 1 and index == 3:
                    row["arms"]["basic"]["status"] = "unknown"
                    row.update(signal_observable=False, outcome_complete=False, exclusion_reasons=["basic:unknown_signal"])
                rows.append(row)
        source["source_target_matrix"][policy] = {"days": memberships, "summary": {"synthetic": True}}
        selected = sum(row["breakout_exit_policy"] is not None for row in rows)
        receipt = {"own_label_dispersion": {"events": selected, "known_labels": selected, "unknown_labels": 0,
            "reference_sessions": len(reference), "mde_bps": 10.0},
            "sizing_passed": True, "comparison_choice_reasons": [], "frequency": {"synthetic_prior_receipt": True}}
        source["variants"][policy] = {"days": rows, "source_variant_provenance": {"synthetic": True},
            "by_arm": {arm: deepcopy(receipt) for arm in ("time_only", "managed")},
            "comparison": {"by_arm": {"time_only": {"gross_mean": wire(Fraction(1, 1000))}},
                "provenance_only_not_recomputed": True}}
    return source


def fixture():
    return build_synthetic_fixture()


def _first(source):
    return next(row for row in source["variants"]["D+1"]["days"] if row["breakout_exit_policy"] is not None)


def test_exact_registration_fixed_resources_and_source_only_closure():
    loss.validate_spec(json.loads(loss.SPECIFICATION_PATH.read_text()))
    assert [loss.RESOURCES[key] for key in ("cpu_seconds", "address_space_mib", "wall_seconds", "nice")] == [300, 512, 1200, 10]
    assert loss.RESOURCES["maximum_market_gets"] == loss.RESOURCES["maximum_token_posts"] == 0
    text = inspect.getsource(loss)
    assert "from research" not in text and "import research" not in text


@pytest.mark.parametrize("change", [
    lambda spec: spec["parameters"]["big_loss_gross_lte"].update(numerator="-2"),
    lambda spec: spec["parameters"]["assumed_cost"].update(numerator="34"),
    lambda spec: spec["parameters"]["next_hypothesis"].update(id="another"),
    lambda spec: spec["resources"].update(cpu_seconds=301),
    lambda spec: spec.update(upstream_sha256="a" * 64),
    lambda spec: spec.update(extra=True),
])
def test_registration_refuses_before_source(change):
    spec = json.loads(loss.SPECIFICATION_PATH.read_text())
    change(spec)
    with pytest.raises(loss.BreakoutLossConcentrationRefusal, match="specification_scope"):
        loss.build_diagnostics({}, specification=spec)


def test_original_labels_denominators_metadata_hashes_and_no_mutation():
    source = fixture()
    before = deepcopy(source)
    result = loss.build_diagnostics(source)
    assert source == before
    assert result["source_identity"]["canonical_source_sha256"] == hashlib.sha256(loss._canonical(source) + b"\n").hexdigest()
    assert result["reference_sessions"] == list(DAYS)
    for policy, variant in result["variants"].items():
        assert variant["population"]["targets"] == 16
        assert variant["population"]["selected_events"] == variant["whole"]["events"] == 6
        assert variant["population"]["basic_status_counts"] == {"signal": 6, "known_no_signal": 9, "unknown": 1}
        assert variant["population"]["exclusion_reason_counts"] == {"basic:unknown_signal": 1}
        assert variant["source_calibration_receipts"] == source["variants"][policy]["by_arm"]
        expected = [row for row in source["variants"][policy]["days"] if row["breakout_exit_policy"] is not None]
        assert len(variant["selected_events"]) == len(expected)
        for event, row in zip(variant["selected_events"], expected, strict=True):
            assert event["source_row_sha256"] == loss._digest(row)
            assert event["gross"] == loss._ratio(row["arms"]["basic"]["label"]["price_change"])
            assert event["net"] == event["gross"] - loss.COST
            for key in ("data_vintage", "raw_chunk_metadata", "source_fetched_at", "membership_target", "membership_source"):
                assert event[key] == row[key]
        assert variant["reconciliation"]["all_selected_events_retained"] is True
    assert result["diagnostic_only"] and result["new_performance_families"] == 0
    assert result["new_labels_calculated"] is result["candidate_measured"] is False


def test_exact_loss_tail_mass_share_lift_and_exposure_accounting():
    variant = loss.build_diagnostics(fixture())["variants"]["D+1"]
    whole = variant["whole"]
    assert whole["gross_sum"] == Fraction(-23, 1000)
    assert whole["net_sum"] == Fraction(-107, 2500)
    assert whole["gross_positive_mass"] == Fraction(3, 250)
    assert whole["gross_loss_mass"] == Fraction(7, 200)
    assert whole["net_loss_mass"] == Fraction(99, 2000)
    assert whole["tail_events"] == 2 and whole["tail_loss_mass"] == Fraction(3, 100)
    first = variant["panels"]["sessions"][DAYS[0]]
    assert first["event_share"] == Fraction(1, 2)
    assert first["gross_loss_share"] == Fraction(6, 7)
    assert first["gross_loss_lift"] == Fraction(12, 7)
    assert first["tail_event_share"] == first["tail_loss_share"] == 1
    assert first["tail_event_lift"] == first["tail_loss_lift"] == 2
    assert whole["gross_positive_events"] == 2 and whole["gross_negative_events"] == 3
    assert whole["gross_zero_events"] == 1 and whole["net_negative_events"] == 5


def test_big_loss_includes_exact_minus1percent_before_costs():
    source = fixture()
    row = _first(source)
    _set_return(row, Fraction(-1, 100))
    equality = loss.build_diagnostics(source)["variants"]["D+1"]["selected_events"][0]
    assert equality["big_loss"] is True and equality["tail_loss_mass"] == Fraction(1, 100)
    _set_return(row, Fraction(-999999999999999999, 100000000000000000000))
    above = loss.build_diagnostics(source)["variants"]["D+1"]["selected_events"][0]
    assert above["big_loss"] is False and above["net"] < Fraction(-1, 100)


def test_every_original_session_month_quarter_and_target_symbol_even_zero_events():
    variant = loss.build_diagnostics(fixture())["variants"]["D+1"]
    assert set(variant["panels"]["sessions"]) == set(DAYS)
    assert set(variant["panels"]["months"]) == {"2025-09", "2025-10", "2026-01"}
    assert set(variant["panels"]["quarters"]) == {"2025-Q3", "2025-Q4", "2026-Q1"}
    assert set(variant["panels"]["symbols"]) == set(SYMBOLS[:4])
    empty = variant["panels"]["symbols"][SYMBOLS[3]]
    assert empty["events"] == 0 and empty["event_share"] == empty["gross_loss_share"] == 0
    assert empty["gross_loss_lift"] is None and empty["tail_event_lift"] is None
    assert empty["target_accounting"]["targets"] == 4
    assert all(variant["panels"]["sessions"][day]["events"] == 0 for day in DAYS[2:])


def test_rankings_are_mass_specific_lexicographic_and_keep_event_exposure():
    source = fixture()
    for row in source["variants"]["D+1"]["days"]:
        if row["breakout_exit_policy"] is not None:
            _set_return(row, Fraction(-1, 100))
    variant = loss.build_diagnostics(source)["variants"]["D+1"]
    ranks = variant["rankings"]["symbols"]
    assert set(ranks) == {"gross_loss_mass", "net_loss_mass", "tail_loss_mass"}
    for basis in ranks.values():
        assert basis["top1"]["keys"] == [SYMBOLS[0]]
        assert basis["top3"]["keys"] == list(SYMBOLS[:3])
        assert basis["top5"]["keys"] == list(SYMBOLS[:4])
        assert basis["top1"]["event_share"] == basis["top1"]["gross_loss_share"] == Fraction(1, 3)
        assert basis["top3"]["gross_loss_share"] == 1


def test_zero_loss_and_zero_event_denominators_are_null_not_zero():
    source = fixture()
    for policy in loss.POLICIES:
        for row in source["variants"][policy]["days"]:
            if row["breakout_exit_policy"] is not None:
                _set_return(row, Fraction(1, 100))
    result = loss.build_diagnostics(source)
    whole = result["variants"]["D+1"]["whole"]
    assert whole["gross_loss_share"] is whole["net_loss_share"] is whole["tail_event_share"] is None
    # A new synthetic empty report has prior structural receipts but no events.
    for policy in loss.POLICIES:
        for row in source["variants"][policy]["days"]:
            row["arms"] = {arm: {"status": "known_no_signal", "event": None, "label": None} for arm in ("basic", "retest")}
            row.update(signal_observable=True, outcome_complete=True, exclusion_reasons=[], breakout_exit_policy=None)
        for receipt in source["variants"][policy]["by_arm"].values():
            receipt["own_label_dispersion"].update(events=0, known_labels=0)
    empty = loss.build_diagnostics(source)["variants"]["D+1"]["whole"]
    assert empty["events"] == 0 and empty["event_share"] is empty["gross_loss_share"] is None


@pytest.mark.parametrize("change", [
    lambda s: s.update(specification_sha256="a" * 64),
    lambda s: s.update(promotion_allowed=True),
    lambda s: s.update(new_performance_families=True),
    lambda s: s.update(comparison_performed=False),
    lambda s: s["reference_weeks"].append("2026-W53"),
    lambda s: s["variants"]["D+1"]["by_arm"]["time_only"].update(sizing_passed=False),
    lambda s: s["variants"]["D+1"]["by_arm"]["managed"]["own_label_dispersion"].update(mde_bps=float("nan")),
    lambda s: _first(s).update(outcome_complete=False),
    lambda s: _first(s)["breakout_exit_policy"]["time_only"].update(price_change=wire(Fraction(1, 2))),
    lambda s: _first(s)["breakout_exit_policy"]["common"].update(anchor_open="99"),
    lambda s: _first(s)["arms"]["basic"]["event"].update(available_at=DAYS[0] + "T09:21:00+09:00"),
    lambda s: _first(s)["arms"]["basic"]["label"].update(entry_price_available_at=DAYS[0] + "T09:25:00+09:00"),
    lambda s: _first(s)["raw_chunk_metadata"].update(is_final=True),
    lambda s: s["variants"]["D+1"]["days"].pop(),
    lambda s: s["variants"]["D+1"]["days"][-1].update(exclusion_reasons=["fabricated"]),
])
def test_source_identity_scope_flags_mask_receipt_anchor_and_unknown_tampering_refuse(change):
    source = fixture()
    change(source)
    with pytest.raises(loss.BreakoutLossConcentrationRefusal):
        loss.build_diagnostics(source)


def test_unselected_row_metadata_affects_full_identity_without_selected_drop():
    source = fixture()
    before = loss.build_diagnostics(source)
    source["variants"]["D+1"]["days"][-1]["extra_retained_metadata"] = {"retrieval_only": True}
    after = loss.build_diagnostics(source)
    assert before["source_identity"]["canonical_source_sha256"] != after["source_identity"]["canonical_source_sha256"]
    assert before["variants"]["D+1"]["source_rows_sha256"] != after["variants"]["D+1"]["source_rows_sha256"]
    assert before["variants"]["D+1"]["selected_events"] == after["variants"]["D+1"]["selected_events"]


def test_fixed_next_hypothesis_does_not_read_clv_or_entry_price_as_selection():
    source = fixture()
    before = loss.build_diagnostics(source)
    for policy in loss.POLICIES:
        for row in source["variants"][policy]["days"]:
            if row["breakout_exit_policy"] is not None:
                row["arms"]["retest"]["path"]["breakout_bar"] = None
    after = loss.build_diagnostics(source)
    assert after["next_hypothesis"] == before["next_hypothesis"]
    descriptor = after["next_hypothesis"]
    assert descriptor["id"] == "breakout_clv_top_quarter_v1" and descriptor["issuer_or_date_bans"] is False
    assert descriptor["status"] == "fixed_hypothesis_only_not_measured_in_DL"
    assert "entry_open_available_Eplus3" in descriptor["forbidden_selection_input"]
    assert "no_retry" in descriptor["first_event_rule"] and "own_label_sizing" in descriptor["future_gate"]
    assert "CLV_relationship" in descriptor["basis"]["scope"]
    for variant in after["variants"].values():
        assert set(variant["whole"]) == set(loss.RAW_METRICS) | {"event_share", "gross_loss_share", "net_loss_share",
            "tail_event_share", "tail_loss_share", "gross_loss_lift", "net_loss_lift", "tail_event_lift", "tail_loss_lift"}
    assert not after["candidate_measured"] and not after["subgroup_return_means_calculated"]


def test_full_10280_row_fixture_geometry_and_stream_hashing():
    source = build_synthetic_fixture(full=True)
    result = loss.build_diagnostics(source)
    assert len(result["reference_sessions"]) == 257 and len(result["reference_weeks"]) == 56
    assert [result["variants"][policy]["population"]["targets"] for policy in loss.POLICIES] == [5140, 5140]
    assert [result["variants"][policy]["whole"]["events"] for policy in loss.POLICIES] == [1169, 1167]
    assert all(result["variants"][policy]["panels"]["sessions"][result["reference_sessions"][-1]]["events"] == 0
        for policy in loss.POLICIES)
    assert all(all(v["reconciliation"]["panels_match_whole"].values()) for v in result["variants"].values())
