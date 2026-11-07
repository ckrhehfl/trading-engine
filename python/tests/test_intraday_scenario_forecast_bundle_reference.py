"""Invented sealed bytes for the independent EH forecast bundle audit."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from research import intraday_campaign_wsl_transport as wire
from research.intraday_scenario_forecast_bundle_reference import verify_forecast_bundle


SNAPSHOT = {"features": {"schema": "eh-opening-features-v1", "sha256": "a" * 64, "size": 800, "records": 257},
            "labels": {"schema": "eh-opening-labels-v1", "sha256": "b" * 64, "size": 200, "records": 15}}
FOLD_LENGTHS = (32, 32, 31, 31, 31)
DAYS = tuple((date(2032, 1, 1) + timedelta(days=index)).isoformat() for index in range(sum(FOLD_LENGTHS)))
PREDICTIONS = "eh-opening-fold-predictions-v1"
COUNTS = ("candidates", "first_supported", "second_supported", "paired_forecasts",
          "paired_outcomes", "scored_events", "scored_dates")
HORIZON_COUNTS = ("horizon_pairs", "horizon_outcomes", "horizon_events", "horizon_dates")
GROUPS = tuple({"stage": stage, "clock": clock, "contrast": contrast} for stage in ("approach", "breakout")
               for clock in ("initial", "update") for contrast in ("chart_vs_empirical", "market_vs_matched_chart"))


def decision(day, symbol, *, available=True):
    at = f"{day}T00:22:00+00:00"
    question = [at, "100", "1", "102", "98", f"{day}T00:52:00+00:00"]
    first, second = [1] + [0] * 9, [0] * 9 + [1]
    return {"day": day, "symbol": symbol, "stage": "approach", "decision_at": at,
            "question": question if available else None, "chart_status": "ready" if available else "unsupported",
            "peers_supported": False, "probabilities": [first, second, None, None] if available else [None] * 4}


def counts(value):
    return {**dict.fromkeys(COUNTS, value), **{key: [value] * 3 for key in HORIZON_COUNTS}}


def fixture():
    header = {"schema": "eh-forecast-fold-bundle-v1", "scenario": "S2", "snapshot": deepcopy(SNAPSHOT), "folds": [],
              "date_weighted_comparisons": [{**group, **counts(0), "first_brier": None,
                                              "second_brier": None, "second_minus_first": None} for group in GROUPS]}
    parts, offset = [], 0
    for index, span in enumerate(FOLD_LENGTHS, 1):
        days = DAYS[offset:offset + span]
        offset += span
        member_header = {"schema": PREDICTIONS, "scenario": "S2", "fold": index,
                         "feature_sha256": SNAPSHOT["features"]["sha256"], "label_sha256": SNAPSHOT["labels"]["sha256"]}
        metadata = {"original_coordinates": 20 * span, "training_events": 0, "training_dates": 0,
                    "matched_training_events": 0, "matched_training_dates": 0, "artifacts": [None] * 3}
        report = {"scenario": "S2", "fold": index, "original_coordinates": 20 * span, "decisions_without_question": 0,
                  "groups": [{**group, "counts": {**counts(0), "first_brier": None, "second_brier": None,
                                                  "daily_difference": []}} for group in GROUPS]}
        rows = []
        if index <= 2:
            rows = [decision(day, "TEST-0001") for day in days[:index]]
            first, second = (0, 2) if index == 1 else (2, 0)
            report["groups"][0]["counts"] = {**counts(index), "first_brier": [first] * 3,
                                             "second_brier": [second] * 3,
                                             "daily_difference": [[day, [second - first] * 3] for day in days[:index]]}
            report["groups"][1]["counts"]["candidates"] = index
        if index == 4:
            rows = [decision(days[0], "TEST-0002", available=False)]
            report["decisions_without_question"] = 1
        body = [member_header, metadata, *rows, {"records": 1 + len(rows)}]
        parts.append(body)
        header["folds"].append({"fold": index, "test_sessions": list(days), "score": report,
                                "seal": {"schema": PREDICTIONS, "records": 1 + len(rows)}})
    combined = header["date_weighted_comparisons"]
    combined[0].update(counts(3), first_brier=[4 / 3] * 3, second_brier=[2 / 3] * 3,
                       second_minus_first=[-2 / 3] * 3)
    combined[1]["candidates"] = 3
    return header, parts


def pack(path, header, parts, *, canonical=True):
    bodies = [b"".join(wire.encode(value) for value in body) for body in parts]
    for fold, body in zip(header["folds"], bodies, strict=True):
        fold["seal"].update(size=len(body), sha256=wire.digest(body))
    raw = wire.encode(header) + b"".join(bodies)
    if not canonical:
        raw = b" " + raw
    path.write_bytes(raw)
    path.chmod(0o400)
    return {"expected_sha256": wire.digest(raw), "expected_size": len(raw)}


def check(path, expected, **changes):
    arguments = {"scenario": "S2", "expected_members": SNAPSHOT, "checkpoint": lambda _: None,
                 "expected_evaluation_sessions": DAYS, **expected}
    return verify_forecast_bundle(path, **(arguments | changes))


def test_checks_five_folds_with_unequal_scored_date_weights(tmp_path):
    header, parts = fixture()
    path = tmp_path / "forecast.bundle"
    expected = pack(path, header, parts)
    observed = check(path, expected)
    assert observed == {"schema": header["schema"], "scenario": "S2", "input_members": SNAPSHOT,
                        "folds": 5, "evaluation_sessions": 157, "original_evaluation_coordinates": 3140,
                        "fold_seals": [fold["seal"] for fold in header["folds"]], "bundle_integrity_verified": True}


@pytest.mark.parametrize("change,reason", (
    ("noncanonical", "not canonical"), ("source", "outer identity"), ("scenario", "outer identity"),
    ("overlap", "evaluation dates"), ("population", "original population"), ("weight", "combined Brier"),
    ("daily", "date-weighted differences"), ("member_header", "member header"), ("member_footer", "member seal or EOF"),
    ("record_count", "member seal or EOF"), ("out_of_fold", "outside its fold"), ("repeat_row", "not strictly ordered"),
    ("reported_support", "report support"), ("outer_digest", "outer hash"), ("training", "training population"),
    ("extra_symbol", "exceeds twenty symbols"),
))
def test_rejects_invented_corruption(tmp_path, change, reason):
    header, parts = fixture()
    path = tmp_path / "forecast.bundle"
    if change == "source":
        header["snapshot"]["features"]["sha256"] = "c" * 64
    elif change == "scenario":
        header["scenario"] = "S1"
    elif change == "overlap":
        header["folds"][2]["test_sessions"][0] = header["folds"][1]["test_sessions"][-1]
    elif change == "population":
        header["folds"][0]["score"]["original_coordinates"] -= 1
    elif change == "weight":
        header["date_weighted_comparisons"][0]["first_brier"] = [1] * 3
    elif change == "daily":
        header["folds"][0]["score"]["groups"][0]["counts"]["daily_difference"][0][1] = [1] * 3
    elif change == "member_header":
        parts[0][0]["fold"] = 2
    elif change == "member_footer":
        parts[0][-1]["records"] = 8
    elif change == "record_count":
        header["folds"][0]["seal"]["records"] = 1
    elif change == "out_of_fold":
        parts[0][2] = decision(DAYS[-1], "TEST-0001")
    elif change == "repeat_row":
        parts[1][3] = parts[1][2]
    elif change == "reported_support":
        parts[0][2]["probabilities"][1] = None
    elif change == "training":
        parts[0][1]["training_events"] = 21
        parts[0][1]["training_dates"] = 1
    elif change == "extra_symbol":
        parts[0][2:-1] = [decision(DAYS[0], f"TEST-{index:04}") for index in range(21)]
        parts[0][-1]["records"] = 22
        header["folds"][0]["seal"]["records"] = 22
    expected = pack(path, header, parts, canonical=change != "noncanonical")
    if change == "outer_digest":
        expected["expected_sha256"] = "0" * 64
    with pytest.raises(ValueError, match=reason):
        check(path, expected)


def test_rejects_rehashed_extra_bytes_and_wrong_external_calendar(tmp_path):
    header, parts = fixture()
    path = tmp_path / "forecast.bundle"
    expected = pack(path, header, parts)
    with pytest.raises(ValueError, match="evaluation dates"):
        check(path, expected, expected_evaluation_sessions=tuple(reversed(DAYS)))
    path.chmod(0o600)
    appended = path.read_bytes() + b"\n"
    path.write_bytes(appended)
    path.chmod(0o400)
    with pytest.raises(ValueError, match="member bytes"):
        check(path, {"expected_sha256": wire.digest(appended), "expected_size": len(appended)})


def test_rejects_five_minute_score_at_exact_five_minute_deadline(tmp_path):
    header, parts = fixture()
    report = header["folds"][0]["score"]["groups"]
    parts[0][2]["decision_at"] = f"{DAYS[0]}T00:27:00+00:00"
    for first, updated in ((0, 2), (1, 3)):
        report[first]["counts"], report[updated]["counts"] = report[updated]["counts"], report[first]["counts"]
    combined = header["date_weighted_comparisons"]
    combined[0].update(counts(2), first_brier=[2] * 3, second_brier=[0] * 3, second_minus_first=[-2] * 3)
    combined[1]["candidates"] = 2
    combined[2].update(counts(1), first_brier=[0] * 3, second_brier=[2] * 3, second_minus_first=[2] * 3)
    combined[3]["candidates"] = 1
    path = tmp_path / "expired-horizon.bundle"
    with pytest.raises(ValueError, match="report support"):
        check(path, pack(path, header, parts))

    expired = report[2]["counts"]
    for key in HORIZON_COUNTS:
        expired[key][0] = combined[2][key][0] = 0
    for key in ("first_brier", "second_brier"):
        expired[key][0] = combined[2][key][0] = None
    expired["daily_difference"][0][1][0] = combined[2]["second_minus_first"][0] = None
    path = tmp_path / "live-horizons.bundle"
    assert check(path, pack(path, header, parts))["bundle_integrity_verified"]


def test_prediction_rows_match_the_independently_supplied_feature_roster(tmp_path):
    header, parts = fixture()
    roster = {day: () for day in DAYS}
    for rows in parts:
        for row in rows[2:-1]:
            roster[row["day"]] += (tuple(row[key] for key in ("symbol", "stage", "decision_at", "question",
                                                            "chart_status", "peers_supported")),)
    good = tmp_path / "matching-roster.bundle"
    assert check(good, pack(good, header, parts), expected_forecast_roster=roster)["bundle_integrity_verified"]
    parts[0][2]["symbol"] = "OTHER"
    bad = tmp_path / "mismatched-roster.bundle"
    with pytest.raises(ValueError, match="roster differs from sealed features"):
        check(bad, pack(bad, header, parts), expected_forecast_roster=roster)
