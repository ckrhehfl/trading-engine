"""Synthetic only: causal coverage, labels, clustered dispersion and bounded IO."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from research import intraday_pattern_calibration as calibration


OPEN = calibration.session_open("2025-09-04")


def rows(count=390, opening=OPEN, symbol="KRX:000001"):
    return [{"symbol": symbol, "interval": "1m", "open_time_ms": int((opening + index * timedelta(minutes=1)).timestamp()) * 1000,
             "open": "100", "high": "100", "low": "100", "close": "100", "fetched_at": "2026-10-20T00:00:00Z"}
            for index in range(count)]


def breakout_rows():
    result = rows()
    result[34].update(high="102", close="102")
    return result


def findings(rawrows):
    return calibration.calibrate_session(calibration.aggregate_session(rawrows, session_open=OPEN))


def test_decimal_aggregation_and_assumed_offset_availability():
    raw = rows(5)
    raw[0]["open_time_ms"] += 12_345
    raw[1].update(high="103", low="98")
    raw[4].update(high="102", close="101.0000000000000000000000000001")
    with localcontext() as context:
        context.prec = 3
        session = calibration.aggregate_session(raw, OPEN)
    bar = session.prefix[0]
    assert (bar.open, bar.high, bar.low, bar.close) == tuple(map(Decimal, ("100", "103", "98", raw[4]["close"])))
    assert bar.start_at == OPEN and bar.end_at == OPEN + timedelta(minutes=5)
    assert bar.available_at == OPEN + timedelta(minutes=7)
    assert session.source_offsets_ms == (12345, 0, 0, 0, 0)
    assert session.gaps[0] == {"start": OPEN + timedelta(minutes=5), "end": OPEN.replace(hour=15, minute=30), "state": "missing"}


@pytest.mark.parametrize("replacement", [None, "", "NaN", "Infinity", "0", "-1", 100.0, "garbage"])
def test_invalid_price_is_gap_never_zero_fill(replacement):
    raw = rows(12)
    raw[6]["close"] = replacement
    session = calibration.aggregate_session(raw, OPEN)
    assert len(session.prefix) == 1
    assert session.minutes[OPEN + timedelta(minutes=6)] is None
    assert any(gap["state"] == "invalid_price" for gap in session.gaps)
    assert all(result["status"] == "unknown" for result in calibration.calibrate_session(session).values())


def test_duplicate_second_offsets_conflict_even_with_equal_prices():
    raw = rows(15)
    raw.append({**raw[6], "open_time_ms": raw[6]["open_time_ms"] + 1000})
    session = calibration.aggregate_session(raw, OPEN)
    assert len(session.prefix) == 1
    assert any(gap["state"] == "conflict" for gap in session.gaps)


def test_bad_ohlc_and_missing_minute_preserve_longest_prefix():
    raw = rows(20)
    raw[11]["low"] = "101"
    del raw[17]
    session = calibration.aggregate_session(raw, OPEN)
    assert len(session.prefix) == 2
    assert [gap["state"] for gap in session.gaps][:2] == ["invalid_price", "missing"]


def test_known_no_signal_requires_exact_full_decision_prefix():
    complete = findings(rows(235))
    assert {value["status"] for value in complete.values()} == {"known_no_signal"}
    assert {value["required_bars"] for value in complete.values()} == {47}
    assert {value["status"] for value in findings(rows(234)).values()} == {"unknown"}
    assert {value["status"] for value in findings([]).values()} == {"unknown"}
    raw = rows()
    raw[235]["close"] = "NaN"  # This bar cannot be available by the decision cutoff.
    assert {value["status"] for value in findings(raw).values()} == {"known_no_signal"}


def test_first_signal_survives_later_gap_and_unknown_label():
    raw = breakout_rows()
    del raw[42]
    result = findings(raw)["opening_range_breakout"]
    assert result["status"] == "signal"
    assert result["event"]["decision_at"] == OPEN + timedelta(minutes=37)
    assert result["label"]["status"] == "unknown"
    assert result["label"]["price_change"] is None


def test_label_uses_strictly_later_boundary_exact_open_and_terminal_close():
    raw = breakout_rows()
    raw[40].update(open="125", high="125")
    raw[159].update(close="150", high="150")
    result = findings(raw)["opening_range_breakout"]
    label = result["label"]
    assert label["entry_label_start"] == OPEN + timedelta(minutes=40)
    assert label["end_label"] == OPEN + timedelta(minutes=160)
    assert label["price_change"] == Fraction(1, 5)
    session = calibration.aggregate_session(raw, OPEN)
    assert calibration.price_label(session, OPEN + timedelta(minutes=40))["entry_label_start"] == OPEN + timedelta(minutes=45)
    assert calibration.price_label(session, OPEN.replace(hour=14))["status"] == "unknown"


def test_middle_label_gap_cannot_be_hidden_by_observed_endpoints():
    raw = breakout_rows()
    del raw[100]
    result = findings(raw)["opening_range_breakout"]
    assert result["status"] == "signal"
    assert result["label"]["status"] == "unknown"


def test_recovery_needs_subsequent_bar_and_new_low_resets_confirmation():
    raw = rows()
    raw[30].update(low="98")
    for index in range(35, 40):
        raw[index].update(open="98", low="97", close="99")
    raw[44].update(low="97", close="99")
    result = findings(raw)["post_drop_recovery"]
    assert result["status"] == "signal"
    assert result["event"]["decision_at"] == OPEN + timedelta(minutes=47)
    assert result["event"]["trough_bar"]["start_at"] == OPEN + timedelta(minutes=35)


@pytest.mark.parametrize("day,hour", [("2025-11-13", 10), ("2026-01-02", 10), ("2026-01-05", 9)])
def test_explicit_assumed_open_schedule(day, hour):
    opening = calibration.session_open(day)
    assert opening.hour == hour
    session = calibration.aggregate_session(rows(235, opening), opening)
    assert all(value["status"] == "known_no_signal" for value in calibration.calibrate_session(session).values())


def test_exceptional_closes_bound_both_aggregation_and_labels():
    november = calibration.session_open("2025-11-13")
    january = calibration.session_open("2026-01-02")
    november_session = calibration.aggregate_session(rows(390, november), november)
    january_session = calibration.aggregate_session(rows(330, january), january)
    assert calibration.session_close("2025-11-13").hour == 16
    assert calibration.session_close("2026-01-02").hour == 15
    assert len(november_session.minutes) == 390
    assert len(january_session.minutes) == 330
    assert calibration.price_label(november_session, november + timedelta(hours=4))["status"] == "known"
    assert calibration.price_label(january_session, january + timedelta(hours=4))["status"] == "unknown"
    with pytest.raises(calibration.CalibrationRefusal, match="outside_session"):
        calibration.aggregate_session(rows(331, january), january)


def test_mixed_symbols_cannot_form_a_synthetic_complete_bar():
    raw = rows(5)
    raw[4]["symbol"] = "KRX:999999"
    with pytest.raises(calibration.CalibrationRefusal, match="mixed_session_symbols"):
        calibration.aggregate_session(raw, OPEN)


@pytest.mark.parametrize("mutate", [lambda raw: raw[0].update(interval="1d"),
                                    lambda raw: raw[0].update(open_time_ms=True),
                                    lambda raw: raw[0].update(open_time_ms=0)])
def test_wrong_session_metadata_refuses(mutate):
    raw = rows(1)
    mutate(raw)
    with pytest.raises(calibration.CalibrationRefusal):
        calibration.aggregate_session(raw, OPEN)


def test_cluster_formula_includes_zero_event_sessions_and_no_mean_claim():
    days = [f"2025-09-{index:02d}" for index in range(1, 11)]
    records = [{"session": days[0], "label": Fraction(1, 100)},
               {"session": days[0], "label": Fraction(1, 100)},
               {"session": days[1], "label": Fraction(-1, 100)},
               {"session": days[1], "label": Fraction(-1, 100)},
               {"session": days[2], "label": None}]
    result = calibration.dispersion(records, days)
    expected = (10 / 9 * (.02 ** 2 + (-.02) ** 2)) ** .5 / 4
    assert result["session_cluster_se"] == pytest.approx(expected)
    assert result["events"] == 5 and result["known_labels"] == 4 and result["unknown_labels"] == 1
    assert result["reference_sessions"] == 10
    assert result["design_se"] == max(result[name] for name in ("naive_se", "session_cluster_se", "bootstrap_se"))
    assert result["corrected_to_naive_se_ratio"] == pytest.approx(result["design_se"] / result["naive_se"])
    assert result["mde_bps"] == pytest.approx((1.959963984540054 + .8416212335729143) * result["design_se"] * 10000)
    assert result == calibration.dispersion(records, days)
    assert "mean" not in result and "p_value" not in result
    assert result["full_family_certified"] is result["family_adopted"] is False
    assert result["would_require_operator_underpower_choice"] is True
    assert result["planning_constraint"] == "own_label_mde_exceeds_assumed_cost_floor"


def test_same_date_cross_stock_labels_cannot_supply_date_replication():
    days = [f"2025-09-{index:02d}" for index in range(1, 11)]
    result = calibration.dispersion([
        {"session": days[0], "label": Fraction(1, 100)},
        {"session": days[0], "label": Fraction(-1, 100)},
        {"session": days[1], "label": None},
    ], days)
    assert result["known_labels"] == 2 and result["known_label_sessions"] == 1
    assert result["event_sd"] > 0 and result["naive_se"] > 0
    assert all(result[key] is None for key in (
        "session_cluster_se", "bootstrap_se", "design_se", "mde_bps"))
    assert result["planning_constraint"] == "insufficient_known_label_sessions"
    assert result["would_require_operator_underpower_choice"] is False
    assert result["full_family_certified"] is result["family_adopted"] is False


def test_bootstrap_resamples_fixed_circular_five_session_blocks():
    days = [f"2025-09-{index:02d}" for index in range(1, 6)]
    records = [{"session": day, "label": Fraction(index, 100)} for index, day in enumerate(days)]
    result = calibration.dispersion(records, days)
    assert result["bootstrap_se"] == pytest.approx(0, abs=1e-16)
    assert result["naive_se"] > 0  # Every five-session circular block has the same full set.
    assert calibration.dispersion([], days)["mde_bps"] is None
    assert calibration.dispersion(records[:1], days)["event_sd"] is None


@pytest.mark.parametrize("days,records", [(["b", "a"], []), (["a", "a"], []),
    (["a"], [{"session": "b", "label": Fraction(1)}]),
    (["a"], [{"session": "a", "label": .01}])])
def test_dispersion_input_contract(days, records):
    with pytest.raises(calibration.CalibrationRefusal):
        calibration.dispersion(records, days)


def specification():
    return {"id": calibration.SPECIFICATION_ID, "mode": calibration.MODE, "window": deepcopy(calibration.WINDOW),
            "parameters": deepcopy(calibration.PARAMETERS), "allowed_columns": list(calibration.COLUMNS),
            "resources": deepcopy(calibration.RESOURCES), "upstream_sha256": "a" * 64,
            **calibration.REGISTERED_PATHS}


@pytest.mark.parametrize("mutate", [lambda spec: spec.update(extra=True), lambda spec: spec.update(upstream_sha256="bad"),
    lambda spec: spec["parameters"].update(drop_bps="100"), lambda spec: spec["resources"].update(maximum_rows=True),
    lambda spec: spec["allowed_columns"].append("volume"), lambda spec: spec["window"].update(end="2026-10-20")])
def test_specification_is_exact_and_rejects_scope_drift(mutate):
    spec = specification()
    calibration.validate_spec(spec)
    mutate(spec)
    with pytest.raises(calibration.CalibrationRefusal):
        calibration.validate_spec(spec)


def upstream_fixture():
    days = ("2025-09-04", "2026-09-23")
    targets = [{"symbol": f"KRX:{index:06d}", "rank": index, "stored_row_count": None,
                "state": "no_stored_rows_unknown_reason"} for index in range(1, 21)]
    return {"study_id": "intraday-universe-sources-v1", **calibration.FLAGS,
        "calendar": {"rows": [{"date": day} for day in days]}, "variants": {
            key: {"days": [{"session": day, "observation_date": day[:8] + "01", "available_at": day + "T08:30:00+09:00",
                "selected_at": day + "T08:30:00+09:00", "retrieved_at": {"synthetic": "later"}, "source": "synthetic",
                "is_final": None, "data_vintage": {"synthetic": "a" * 64}, "evidence_level": "assumed", "targets": deepcopy(targets)}
                for day in days]} for key in ("D+1", "D+2")}}


@pytest.mark.parametrize("mutate", [lambda source: source["variants"]["D+1"]["days"].pop(),
    lambda source: source["variants"]["D+2"]["days"][0]["targets"].pop(),
    lambda source: source.update(liquidity_certified=True),
    lambda source: source["variants"]["D+1"]["days"][0].update(available_at="2025-09-04T09:30:00+09:00")])
def test_target_matrix_preserves_every_date_target_and_uncertainty(mutate):
    source = upstream_fixture()
    variants, union = calibration._target_matrix(source)
    assert len(variants) == 2 and sum(map(len, union.values())) == 40
    mutate(source)
    with pytest.raises(calibration.CalibrationRefusal):
        calibration._target_matrix(source)


@pytest.fixture
def runner(tmp_path, monkeypatch):
    paths = {"source_database": str(tmp_path / "synthetic.sqlite3"), "upstream_path": str(tmp_path / "upstream.json"),
             "output_directory": str(tmp_path / "output"), "canonical_runs_path": str(tmp_path / "runs.jsonl")}
    spec_path = tmp_path / "spec.json"
    monkeypatch.setattr(calibration, "REGISTERED_PATHS", paths)
    monkeypatch.setattr(calibration, "SPECIFICATION_PATH", spec_path)
    with sqlite3.connect(paths["source_database"]) as connection:
        connection.execute("CREATE TABLE klines (symbol TEXT, interval TEXT, open_time_ms INTEGER, open TEXT, high TEXT, low TEXT, close TEXT, fetched_at TEXT, volume TEXT)")
        connection.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)", [tuple(row[column] for column in calibration.COLUMNS) + ("UNREAD",) for row in breakout_rows()])
        connection.execute("CREATE INDEX synthetic_lookup ON klines(symbol,interval,open_time_ms)")
    upstream_path = Path(paths["upstream_path"])
    upstream_path.write_bytes(calibration._canonical(upstream_fixture()))
    spec = specification()
    spec["upstream_sha256"] = hashlib.sha256(upstream_path.read_bytes()).hexdigest()
    spec_path.write_bytes(calibration._canonical(spec))
    args = {"db_path": paths["source_database"], "specification_path": spec_path,
            "upstream_path": upstream_path, "output_directory": paths["output_directory"], "runs_path": paths["canonical_runs_path"]}
    return args, paths


def test_runner_logs_before_read_queries_union_once_preserves_unknowns_and_pins(runner, monkeypatch):
    args, paths = runner
    original, queries = calibration._read_session, []
    def checked(connection, symbol, day):
        assert json.loads(Path(paths["canonical_runs_path"]).read_text().splitlines()[0])["status"] == "started"
        queries.append((symbol, day))
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("SELECT volume FROM klines").fetchall()
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("DELETE FROM klines")
        return original(connection, symbol, day)
    monkeypatch.setattr(calibration, "_read_session", checked)
    result = calibration.run_calibration(**args)
    assert len(queries) == len(set(queries)) == 40
    output = Path(paths["output_directory"])
    report = json.loads((output / "report.json").read_text())
    evidence = report["input_evidence"]
    archive = (output / evidence["file"]).read_bytes()
    raw = gzip.decompress(archive)
    assert hashlib.sha256(archive).hexdigest() == evidence["compressed_sha256"]
    assert hashlib.sha256(raw).hexdigest() == evidence["stream_sha256"]
    assert evidence["queried_rows"] == 390 and len(raw.splitlines()) == 40
    for variant in report["variants"].values():
        assert len(variant["days"]) == 40
        summary = variant["families"]["opening_range_breakout"]
        assert summary["counts"] == {"signal": 1, "known_no_signal": 0, "unknown": 39}
        assert summary["dispersion"]["known_labels"] == 1
        assert sum(month["unknown"] for month in summary["by_month"].values()) == 39
    record = report["variants"]["D+1"]["days"][0]
    chunk = next(line + b"\n" for line in raw.splitlines() if json.loads(line)["symbol"] == "KRX:000001")
    assert record["data_vintage"] == hashlib.sha256(chunk).hexdigest()
    assert record["retrieved_at"] != record["source_fetched_at"][0]
    assert record["observation_date"] == record["session"]
    assert record["available_at"] is None
    assert record["membership_observation_date"] != record["observation_date"]
    assert record["membership_available_at"].endswith("08:30:00+09:00")
    event = record["families"]["opening_range_breakout"]["event"]
    assert event["observation_date"] == record["session"]
    assert event["available_at"] == event["decision_at"]
    assert event["data_vintage"] == record["data_vintage"]
    assert event["retrieved_at"] == record["retrieved_at"]
    assert event["parameters_sha256"] == hashlib.sha256(calibration._canonical(calibration.PARAMETERS)).hexdigest()
    assert result["status"] == "completed"
    assert [json.loads(line)["status"] for line in Path(paths["canonical_runs_path"]).read_text().splitlines()] == ["started", "completed"]
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output / "inputs.jsonl.gz").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("what", ["upstream", "report", "archive", "callback"])
def test_completion_tampering_or_callback_failure_cannot_complete(runner, what):
    args, paths = runner
    def change():
        output = Path(paths["output_directory"])
        target = {"upstream": Path(paths["upstream_path"]), "report": output / "report.json", "archive": output / "inputs.jsonl.gz"}
        if what == "callback":
            raise RuntimeError("synthetic_failure")
        with target[what].open("ab") as stream:
            stream.write(b" ")
    with pytest.raises((calibration.CalibrationRefusal, RuntimeError)):
        calibration.run_calibration(**args, before_completion=change)
    records = [json.loads(line) for line in Path(paths["canonical_runs_path"]).read_text().splitlines()]
    assert [record["status"] for record in records] == ["started", "failed"]


def test_query_failure_retains_durable_partial_input(runner, monkeypatch):
    args, paths = runner
    original, calls = calibration._read_session, 0
    def fail(connection, symbol, day):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic_query_failure")
        return original(connection, symbol, day)
    monkeypatch.setattr(calibration, "_read_session", fail)
    with pytest.raises(RuntimeError):
        calibration.run_calibration(**args)
    archive = Path(paths["output_directory"]) / "inputs.jsonl.gz"
    assert len(gzip.decompress(archive.read_bytes()).splitlines()) == 1
    assert not archive.with_name("report.json").exists()
    assert json.loads(Path(paths["canonical_runs_path"]).read_text().splitlines()[-1])["status"] == "failed"


def test_oversized_session_query_refuses_without_reading_other_symbols(runner):
    args, paths = runner
    with sqlite3.connect(paths["source_database"]) as connection:
        extra = rows(1)[0]
        connection.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)", [tuple(extra[column] for column in calibration.COLUMNS) + ("UNREAD",)] * 211)
    with pytest.raises(calibration.CalibrationRefusal, match="session_row_budget"):
        calibration.run_calibration(**args)


def test_exceptional_query_uses_the_same_registered_close(runner):
    args, paths = runner
    opening = calibration.session_open("2025-11-13")
    raw = rows(390, opening)
    with sqlite3.connect(paths["source_database"]) as connection:
        connection.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?)", [tuple(row[column] for column in calibration.COLUMNS) + ("UNREAD",) for row in raw])
    connection = sqlite3.connect(Path(paths["source_database"]).as_uri() + "?mode=ro", uri=True, isolation_level=None)
    try:
        connection.set_authorizer(calibration._authorize)
        queried, _ = calibration._read_session(connection, "KRX:000001", "2025-11-13")
    finally:
        connection.close()
    assert len(queried) == 390
    assert queried[-1]["open_time_ms"] == raw[-1]["open_time_ms"]


def test_upstream_hash_mismatch_is_logged_before_database_price_read(runner, monkeypatch):
    args, paths = runner
    Path(paths["upstream_path"]).write_text("{}")
    monkeypatch.setattr(calibration, "_read_session", lambda *args: pytest.fail("must not read prices"))
    with pytest.raises(calibration.CalibrationRefusal, match="hash_mismatch"):
        calibration.run_calibration(**args)
    assert [json.loads(line)["status"] for line in Path(paths["canonical_runs_path"]).read_text().splitlines()] == ["started", "failed"]


def test_query_budget_interrupts_a_scan_even_with_no_matching_rows(runner):
    args, paths = runner
    real = sqlite3.connect(Path(paths["source_database"]).as_uri() + "?mode=ro", uri=True, isolation_level=None)
    class ScanBudget:
        def set_progress_handler(self, handler, steps):
            if handler is not None:
                # Exercise the actual callback's VM budget, not a copied formula.
                assert [handler() for _ in range(4999)][-1] == 0
                assert handler() == 1
            real.set_progress_handler((lambda: 1) if handler is not None else None, 1)
        def execute(self, sql, parameters):
            return real.execute(sql, parameters)
    try:
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            calibration._read_session(ScanBudget(), "KRX:000001", "2025-09-04")
    finally:
        real.close()
