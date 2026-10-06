"""Synthetic complete-lot replay, noninterference and discovery audit guards."""

from array import array
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from research import activity_portfolio as ap, experiment_log
from research.overfitting_check import check_project_combination_count


LOT_FIELDS = {
    "lot_id", "code", "formation_date", "entry_date", "due_date", "exit_date",
    "audit_end_date", "status",
}
ACCOUNTING_FIELDS = ("diagnostics", "closed_trades", "unresolved_positions")


def synthetic_case(scenario="closed"):
    """Use two known formation opportunities without opening a price database."""
    dates = [date(2026, 9, 8) + timedelta(days=i) for i in range(13)]
    series = ap.Series(
        array("d", [100] * 13), array("d", [100] * 13),
        array("d", [10, 10, 30] + [10] * 10), bytearray(13), bytearray(13),
    )
    params = dict(lookback=2, holding_sessions=3, slots=1, threshold=3.0,
                  seed=1, end="2026-09-18", commission_bps_per_side=2,
                  slippage_bps_per_side=5)
    if scenario == "halt_delayed":
        for i in (6, 7):
            series.frozen[i] = 1
            series.opens[i] = series.closes[i] = 999
        series.opens[8] = 80
    elif scenario == "missing_delayed":
        for i in (6, 7):
            series.opens[i] = series.closes[i] = 0
        series.opens[8] = 80
    elif scenario == "disappeared":
        series.opens[6:] = array("d", [0] * 7)
        series.closes[6:] = array("d", [0] * 7)
    elif scenario == "terminal_frozen":
        for i in range(6, 13):
            series.frozen[i] = 1
            series.opens[i] = series.closes[i] = 999
    elif scenario == "reentry":
        series.turnover[5] = 30
    elif scenario == "unfilled_frozen":
        series.frozen[3] = 1
    elif scenario == "unfilled_missing":
        series.opens[3] = series.closes[3] = 0
    elif scenario == "limit_locked":
        series.locked[3] = series.locked[6] = 1
    return dates, {"A": series}, params


@pytest.mark.parametrize("scenario", [
    "closed", "halt_delayed", "missing_delayed", "disappeared", "terminal_frozen",
    "reentry", "unfilled_frozen", "unfilled_missing", "limit_locked",
])
def test_tracing_preserves_every_original_result_and_input(scenario):
    """Observation cannot alter cash, NAV, diagnostics or any terminal mark."""
    dates, panel, params = synthetic_case(scenario)
    untouched = deepcopy((dates, panel, params))
    original = ap.simulate(dates, panel, params)
    traced = ap.simulate(dates, panel, params, trace_lots=True)
    lots = traced.pop("lot_intervals")
    assert "lot_intervals" not in original
    assert traced == original
    assert (dates, panel, params) == untouched
    assert len(lots) == original["diagnostics"].get("entries", 0)
    assert len({lot["lot_id"] for lot in lots}) == len(lots)
    assert all(set(lot) == LOT_FIELDS for lot in lots)
    assert ap.simulate(dates, panel, params, trace_lots=True)["lot_intervals"] == lots


@pytest.mark.parametrize("scenario,exit_day", [
    ("closed", "2026-09-14"), ("halt_delayed", "2026-09-16"),
    ("missing_delayed", "2026-09-16"), ("limit_locked", "2026-09-14"),
])
def test_closed_lot_records_actual_sale_and_original_due_date(scenario, exit_day):
    """A delayed liquidation extends the audit obligation through the real exit."""
    dates, panel, params = synthetic_case(scenario)
    book = ap.simulate(dates, panel, params, trace_lots=True)
    assert len(book["lot_intervals"]) == 1
    lot = book["lot_intervals"][0]
    assert {key: value for key, value in lot.items() if key != "lot_id"} == {
        "code": "A", "formation_date": "2026-09-10", "entry_date": "2026-09-11",
        "due_date": "2026-09-14", "exit_date": exit_day,
        "audit_end_date": "2026-09-18", "status": "closed",
    }
    assert book["closed_trades"] == 1
    assert book["unresolved_positions"] == 0


@pytest.mark.parametrize("scenario", ["disappeared", "terminal_frozen"])
def test_unresolved_lot_covers_window_end_without_fabricated_exit(scenario):
    """Missing or frozen prices leave the whole remaining holding interval open."""
    dates, panel, params = synthetic_case(scenario)
    book = ap.simulate(dates, panel, params, trace_lots=True)
    assert len(book["lot_intervals"]) == 1
    lot = book["lot_intervals"][0]
    assert {key: value for key, value in lot.items() if key != "lot_id"} == {
        "code": "A", "formation_date": "2026-09-10", "entry_date": "2026-09-11",
        "due_date": "2026-09-14", "exit_date": None,
        "audit_end_date": "2026-09-18", "status": "unresolved",
    }
    assert book["closed_trades"] == 0
    assert book["unresolved_positions"] == 1


def test_same_code_can_exit_and_reenter_as_two_distinct_lots():
    """A code-indexed terminal book must not erase its previous closed interval."""
    dates, panel, params = synthetic_case("reentry")
    book = ap.simulate(dates, panel, params, trace_lots=True)
    first, second = book["lot_intervals"]
    assert first["code"] == second["code"] == "A"
    assert first["lot_id"] != second["lot_id"]
    assert first["exit_date"] == second["entry_date"] == "2026-09-14"
    assert second["formation_date"] == "2026-09-13"
    assert second["due_date"] == second["exit_date"] == "2026-09-17"
    assert book["diagnostics"]["entries"] == book["closed_trades"] == 2


@pytest.mark.parametrize("scenario", ["unfilled_frozen", "unfilled_missing"])
def test_unfilled_attempt_creates_no_lot(scenario):
    """The trace represents successful entries, not intended allocations."""
    dates, panel, params = synthetic_case(scenario)
    book = ap.simulate(dates, panel, params, trace_lots=True)
    assert book["lot_intervals"] == []
    assert book["diagnostics"]["unfilled_entries"] == 1
    assert book["navs"] == [1] * 8


def synthetic_audit():
    """Return one closed and one residual lot with original accounting intact."""
    dates, panel, params = synthetic_case("reentry")
    panel["A"].opens[9:] = array("d", [0] * 4)
    panel["A"].closes[9:] = array("d", [0] * 4)
    book = ap.simulate(dates, panel, params, trace_lots=True)
    reference = deepcopy({key: book[key] for key in ACCOUNTING_FIELDS})
    return book, reference


def test_audit_returns_only_original_accounting_and_complete_date_obligations():
    """Prices, performance arrays and terminal marked inventory stay internal."""
    book, reference = synthetic_audit()
    original = deepcopy(book)
    report = ap.audit_lot_coverage(book, reference)
    assert set(report) == {*ACCOUNTING_FIELDS, "lot_intervals", "interpretation"}
    assert {key: report[key] for key in ACCOUNTING_FIELDS} == reference
    assert report["lot_intervals"] == book["lot_intervals"]
    assert all(set(lot) == LOT_FIELDS for lot in report["lot_intervals"])
    assert [lot["status"] for lot in report["lot_intervals"]] == ["closed", "unresolved"]
    assert book == original


@pytest.mark.parametrize("field", [
    "entries", "limit_locked_fills", "invested_sessions", "delayed_exits",
    "unpriced_or_frozen_position_sessions", "pending_exit_sessions", "unfilled_entries",
    "closed_trades", "unresolved_positions",
])
def test_audit_refuses_any_changed_original_accounting_counter(field):
    """Even a diagnostic unrelated to lot counts must match the recorded pilot."""
    book, reference = synthetic_audit()
    container = reference if field in ACCOUNTING_FIELDS else reference["diagnostics"]
    container[field] = container.get(field, 0) + 1
    with pytest.raises(ValueError, match="accounting differs"):
        ap.audit_lot_coverage(book, reference)


@pytest.mark.parametrize("damage", [
    "missing_lot", "duplicate_id", "wrong_status", "fabricated_exit",
    "missing_exit", "terminal_code", "terminal_entry", "terminal_due",
    "price_field", "invalid_status", "invalid_date", "early_exit",
    "overlap", "different_audit_end",
])
def test_audit_refuses_unreconciled_trace_even_when_accounting_matches(damage):
    """Unchanged aggregate counters cannot conceal a missing or misidentified lot."""
    book, reference = synthetic_audit()
    lots = book["lot_intervals"]
    if damage == "missing_lot":
        lots.pop()
    elif damage == "duplicate_id":
        lots[1]["lot_id"] = lots[0]["lot_id"]
    elif damage == "wrong_status":
        lots[1]["status"] = "closed"
    elif damage == "fabricated_exit":
        lots[1]["exit_date"] = "2026-09-18"
    elif damage == "missing_exit":
        lots[0]["exit_date"] = None
    elif damage == "terminal_code":
        book["terminal_holdings"][0]["code"] = "B"
    elif damage == "terminal_entry":
        book["terminal_holdings"][0]["entry_date"] = "2026-09-15"
    elif damage == "terminal_due":
        book["terminal_holdings"][0]["due_date"] = "2026-09-18"
    elif damage == "price_field":
        lots[0]["exit_price"] = 100
    elif damage == "invalid_status":
        lots[1]["status"] = "liquidated"
    elif damage == "invalid_date":
        lots[0]["formation_date"] = "2026-09-31"
    elif damage == "early_exit":
        lots[0]["exit_date"] = "2026-09-13"
    elif damage == "overlap":
        lots[1].update(lot_id="A:2026-09-13", formation_date="2026-09-12",
                       entry_date="2026-09-13")
        book["terminal_holdings"][0]["entry_date"] = "2026-09-13"
    elif damage == "different_audit_end":
        lots[0]["audit_end_date"] = "2026-09-17"
    with pytest.raises(ValueError):
        ap.audit_lot_coverage(book, reference)


def load_audit_spec():
    """Read committed-intent configuration only; this helper never reads prices."""
    root = Path(ap.__file__).resolve().parents[2]
    path = root / "configs/research/discovery/activity-lot-coverage-audit-v1.json"
    raw = path.read_bytes()
    return root, path, raw, json.loads(raw)


def test_lot_audit_spec_keeps_exact_pilot_parameters_and_provenance():
    """The complete audit must replay the same pilot as the residual audit."""
    root, _, _, spec = load_audit_spec()
    residual = json.loads((root / "configs/research/discovery/activity-holdings-audit-v1.json").read_bytes())
    pilot_raw = (root / spec["reference_specification"]).read_bytes()
    pilot = json.loads(pilot_raw)
    assert spec["mode"] == "discovery_lot_coverage_audit"
    assert spec["promotion_allowed"] is False
    assert spec["parameters"] == pilot["parameters"]
    assert spec["window"] == pilot["window"]
    assert spec["reference_specification_sha256"] == hashlib.sha256(pilot_raw).hexdigest()
    for field in ("reference_run_id", "reference_dataset_sha256", "reference_accounting"):
        assert spec[field] == residual[field]


def cli_args(spec_path, log):
    """Use non-existent database names so a missing fake loader fails loudly."""
    return ["--spec", str(spec_path), "--scan-db", "unused-lot-scan.db",
            "--calendar-db", "unused-lot-calendar.db", "--runs-path", str(log)]


def test_changed_lot_dataset_is_durably_failed_before_simulation(tmp_path, monkeypatch):
    """A matching committed spec cannot authorize a different loaded panel."""
    root, spec_path, raw, _ = load_audit_spec()
    log = tmp_path / "audit.jsonl"
    monkeypatch.chdir(root)
    monkeypatch.setattr(ap, "committed_specification", lambda root, path: path.read_bytes())

    def load(*args):
        """The durable discovery start must already exist when prices are loaded."""
        assert [row["status"] for row in experiment_log.read_records(log)] == ["started"]
        return [], {}, "changed", {}

    monkeypatch.setattr(ap, "load_panel", load)
    monkeypatch.setattr(ap, "simulate", lambda *args, **kwargs: pytest.fail("must not simulate"))
    with pytest.raises(ValueError, match="dataset differs"):
        ap.main(cli_args(spec_path, log))
    records = list(experiment_log.read_records(log))
    assert [record["status"] for record in records] == ["started", "failed"]
    assert records[0]["run_id"] == records[1]["run_id"]
    assert records[0]["specification_sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("damage", ["parameters", "window", "reference_hash", "promotion"])
def test_changed_lot_specification_refused_before_data_load(tmp_path, monkeypatch, damage):
    """The new CLI mode keeps the original registration and promotion guards."""
    root, spec_path, _, spec = load_audit_spec()
    if damage == "parameters":
        spec["parameters"]["slots"] += 1
    elif damage == "window":
        spec["window"]["start"] = "2018-01-02"
    elif damage == "reference_hash":
        spec["reference_specification_sha256"] = "0" * 64
    else:
        spec["promotion_allowed"] = True
    monkeypatch.chdir(root)
    monkeypatch.setattr(ap, "committed_specification", lambda root, path:
                        json.dumps(spec).encode() if path == spec_path else path.read_bytes())
    monkeypatch.setattr(ap, "load_panel", lambda *args: pytest.fail("must not load prices"))
    log = tmp_path / "audit.jsonl"
    with pytest.raises(ValueError):
        ap.main(cli_args(spec_path, log))
    assert not log.exists()


def pilot_sized_synthetic_book(reference):
    """Build only identifiers and dates matching the real spec's aggregate counts."""
    lots, terminal = [], []
    for i in range(reference["diagnostics"]["entries"]):
        code = f"fixture-{i:06d}"
        closed = i < reference["closed_trades"]
        lot = dict(lot_id=f"{code}:2020-01-02", code=code, formation_date="2020-01-01",
                   entry_date="2020-01-02", due_date="2020-07-01",
                   exit_date="2020-07-01" if closed else None,
                   audit_end_date="2026-09-18",
                   status="closed" if closed else "unresolved")
        lots.append(lot)
        if not closed:
            terminal.append({key: lot[key] for key in ("code", "entry_date", "due_date")})
    return {**deepcopy(reference), "lot_intervals": lots, "terminal_holdings": terminal,
            "returns": [0.42], "navs": [1.42], "unresolved_marked_value": 123.45}


def test_valid_lot_cli_logs_before_load_and_emits_only_audit(tmp_path, monkeypatch, capsys):
    """Exercise real logging and audit validation without data or dispersion work."""
    root, spec_path, raw, spec = load_audit_spec()
    log = tmp_path / "audit.jsonl"
    dates, panel = [], {}
    quality = {"done": 1, "never_served": 1}
    book = pilot_sized_synthetic_book(spec["reference_accounting"])
    monkeypatch.chdir(root)
    monkeypatch.setattr(ap, "committed_specification", lambda root, path: path.read_bytes())

    def load(*args):
        """Inspect the on-disk log from the loader, not a mocked write callback."""
        records = list(experiment_log.read_records(log))
        assert [row["status"] for row in records] == ["started"]
        assert records[0]["specification_sha256"] == hashlib.sha256(raw).hexdigest()
        return dates, panel, spec["reference_dataset_sha256"], quality

    def simulate(given_dates, given_panel, params, *, trace_lots=False):
        """Require the opt-in trace and exact registered parameters."""
        assert given_dates is dates and given_panel is panel
        assert params == spec["parameters"]
        assert trace_lots is True
        return book

    monkeypatch.setattr(ap, "load_panel", load)
    monkeypatch.setattr(ap, "simulate", simulate)
    for name in ("calibration", "block_standard_error", "audit_holdings"):
        monkeypatch.setattr(ap, name, lambda *args, **kwargs: pytest.fail("audit-only execution"))
    assert ap.main(cli_args(spec_path, log)) == 0
    records = list(experiment_log.read_records(log))
    assert [record["status"] for record in records] == ["started", "completed"]
    output = json.loads(capsys.readouterr().out)
    assert output == records[1]
    assert output["promotion_allowed"] is False
    report = output["result"]
    assert set(report) == {
        *ACCOUNTING_FIELDS, "lot_intervals", "interpretation", "reference_run_id",
        "dataset_sha256", "scan_progress",
    }
    assert report["reference_run_id"] == spec["reference_run_id"]
    assert report["dataset_sha256"] == spec["reference_dataset_sha256"]
    assert report["scan_progress"] == quality
    assert report["lot_intervals"] == book["lot_intervals"]
    assert all(set(lot) == LOT_FIELDS for lot in report["lot_intervals"])
    assert check_project_combination_count(runs_path=log).research_selection_trials == 0
