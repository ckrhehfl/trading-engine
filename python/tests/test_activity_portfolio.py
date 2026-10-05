"""Execution timing, inaccessible holdings and reserved-panel refusal."""

from array import array
from datetime import date, timedelta
import hashlib
from pathlib import Path
import sqlite3
import subprocess

import pytest

from research.activity_portfolio import Series, eligible, load_panel, simulate, block_standard_error, calibration
from research.activity_portfolio import EXECUTION_SOURCES, committed_specification
from research.activity_portfolio import audit_holdings, validate_audit_specification


def fixture_book():
    """Build a single surge with a known next-open entry and due sale."""
    dates = [date(2026, 9, 8) + timedelta(days=i) for i in range(13)]
    series = Series(array("d", [100] * 13), array("d", [100] * 13),
                    array("d", [10, 10, 30] + [10] * 10), bytearray(13), bytearray(13))
    params = dict(lookback=2, holding_sessions=3, slots=1, threshold=3.0,
                  seed=1, end="2026-09-18", commission_bps_per_side=0,
                  slippage_bps_per_side=0)
    return dates, series, params


def test_formation_does_not_read_future_and_excludes_frozen_baselines():
    """Future activity cannot alter an earlier decision; halts and limits differ."""
    _, series, _ = fixture_book()
    assert eligible(series, 2, 2, 3)
    series.turnover[3:] = array("d", [1e10] * 10)
    assert eligible(series, 2, 2, 3)
    series.frozen[1] = 1
    assert not eligible(series, 2, 2, 3)  # only one preceding usable observation
    series.frozen[1] = 0
    series.frozen[2] = 1
    assert not eligible(series, 2, 2, 3)
    series.frozen[2] = 0
    series.locked[2] = 1
    assert eligible(series, 2, 2, 3)  # a limit-locked trading day is not a halt


def test_next_open_and_sale_tax_are_real_cash_flows():
    """A price jump after the signal changes share count, not entry information."""
    dates, series, params = fixture_book()
    series.opens[3] = 200  # known only after the formation close
    series.closes[3:] = array("d", [200] * 10)
    series.opens[6] = 240
    book = simulate(dates, {"A": series}, params)
    assert book["navs"][0] == pytest.approx(1)
    assert book["navs"][-1] == pytest.approx(1.2 * 0.998)
    assert book["closed_trades"] == 1
    assert book["unresolved_positions"] == 0


def test_halt_defers_exit_and_keeps_last_mark_until_resumption():
    """A frozen quote cannot create cash or replace the last observable mark."""
    dates, series, params = fixture_book()
    for i in (6, 7):
        series.frozen[i] = 1
        series.opens[i] = series.closes[i] = 999  # cannot change the held mark
    series.opens[8] = 80
    book = simulate(dates, {"A": series}, params)
    assert book["navs"][3:5] == pytest.approx([1, 1])
    assert book["navs"][-1] == pytest.approx(0.8 * 0.998)
    assert book["diagnostics"]["delayed_exits"] == 1
    assert book["diagnostics"]["pending_exit_sessions"] == 2


def test_disappearing_name_is_never_sold_at_its_retrospective_last_bar():
    """Missing terminal prices leave unresolved inventory instead of fake recovery."""
    dates, series, params = fixture_book()
    series.opens[6:] = array("d", [0] * 7)
    series.closes[6:] = array("d", [0] * 7)
    book = simulate(dates, {"A": series}, params)
    assert book["closed_trades"] == 0
    assert book["unresolved_positions"] == 1
    assert book["unresolved_marked_value"] == 1
    assert book["diagnostics"]["entries"] == 1
    row = book["terminal_holdings"][0]
    assert row == {
        "code": "A", "entry_date": "2026-09-11", "due_date": "2026-09-14",
        "due_within_window": True, "last_observed_bar_date": "2026-09-13",
        "last_mark_date": "2026-09-13", "last_mark_split_adjusted": 100,
        "terminal_bar_state": "missing", "missing_sessions_while_held": 5,
        "frozen_sessions_while_held": 0, "pending_sale_sessions": 5,
        "recovery_value": None,
    }


def test_residual_frozen_quote_is_not_a_new_mark_or_recovery():
    """Frozen terminal bars count as observations but cannot realize their quote."""
    dates, series, params = fixture_book()
    for i in range(5, 11):
        series.frozen[i] = 1
        series.opens[i] = series.closes[i] = 999
    book = simulate(dates, {"A": series}, params)
    row = book["terminal_holdings"][0]
    assert book["navs"] == [1] * 8
    assert row["last_observed_bar_date"] == "2026-09-18"
    assert row["last_mark_date"] == "2026-09-12"
    assert row["last_mark_split_adjusted"] == 100
    assert row["frozen_sessions_while_held"] == 6
    assert row["missing_sessions_while_held"] == 0
    assert row["terminal_bar_state"] == "frozen"
    assert row["recovery_value"] is None
    reference = {key: book[key] for key in ("diagnostics", "closed_trades", "unresolved_positions")}
    report = audit_holdings(book, reference)
    assert report["terminal_holdings"] == [row]
    assert not {"returns", "navs", "sharpe", "unresolved_marked_value"} & report.keys()
    with pytest.raises(ValueError, match="accounting differs"):
        audit_holdings(book, {**reference, "closed_trades": 99})


def test_audit_requires_original_specification_hash_and_parameters(tmp_path, monkeypatch):
    """A changed replay parameter cannot masquerade as the original inventory."""
    from research import activity_portfolio as ap

    raw = b'{"mode":"discovery_calibration","parameters":{"slots":20},"window":{}}'
    monkeypatch.setattr(ap, "committed_specification", lambda *args: raw)
    spec = dict(reference_specification="pilot.json", parameters={"slots": 20}, window={},
                reference_specification_sha256=hashlib.sha256(raw).hexdigest())
    validate_audit_specification(tmp_path, spec)
    with pytest.raises(ValueError, match="parameters and window exactly"):
        validate_audit_specification(tmp_path, {**spec, "parameters": {"slots": 10}})
    with pytest.raises(ValueError, match="hash differs"):
        validate_audit_specification(tmp_path, {**spec, "reference_specification_sha256": "0" * 64})


def test_changed_audit_dataset_is_logged_as_failed_before_simulation(tmp_path, monkeypatch):
    """The CLI writes start/failure records but cannot simulate a different panel."""
    from research import activity_portfolio as ap, experiment_log

    root = Path(ap.__file__).resolve().parents[2]
    spec_path = root / "configs/research/discovery/activity-holdings-audit-v1.json"
    raw = spec_path.read_bytes()
    monkeypatch.chdir(root)
    monkeypatch.setattr(ap, "committed_specification", lambda *args: raw)
    monkeypatch.setattr(ap, "validate_audit_specification", lambda *args: None)
    monkeypatch.setattr(ap, "load_panel", lambda *args: ([], {}, "changed", {}))
    monkeypatch.setattr(ap, "simulate", lambda *args: pytest.fail("must not simulate"))
    log = tmp_path / "audit.jsonl"
    with pytest.raises(ValueError, match="dataset differs"):
        ap.main(["--spec", str(spec_path), "--scan-db", "unused", "--calendar-db", "unused",
                 "--runs-path", str(log)])
    records = list(experiment_log.read_records(log))
    assert [r["status"] for r in records] == ["started", "failed"]
    assert records[0]["specification_sha256"] == hashlib.sha256(raw).hexdigest()


def test_unfilled_entry_remains_cash():
    """An unavailable next-open fill does not trigger hindsight replacement."""
    dates, series, params = fixture_book()
    series.frozen[3] = 1
    book = simulate(dates, {"A": series}, params)
    assert book["navs"] == [1] * 8
    assert book["diagnostics"]["unfilled_entries"] == 1
    assert book["closed_trades"] == 0


def test_costs_are_charged_on_both_actual_notionals():
    """Entry fees reduce shares and sale fees and tax reduce recovered cash."""
    dates, series, params = fixture_book()
    params.update(commission_bps_per_side=2, slippage_bps_per_side=5)
    book = simulate(dates, {"A": series}, params)
    assert book["navs"][-1] == pytest.approx((1 - 27 / 10000) / (1 + 7 / 10000))


def test_reserved_panel_refused_before_reading_any_prices(tmp_path, monkeypatch):
    """A wrong panel is rejected using metadata before any price query executes."""
    scan, calendar = tmp_path / "reserved.db", tmp_path / "calendar.db"
    with sqlite3.connect(scan) as con:
        con.execute("CREATE TABLE scan_panel(id INTEGER, start TEXT, end TEXT)")
        con.execute("INSERT INTO scan_panel VALUES(1,'19910828','20181231')")
    sqlite3.connect(calendar).close()
    statements = []
    original = sqlite3.connect

    def trace(*args, **kwargs):
        """Capture real SQLite statements without replacing the database behavior."""
        con = original(*args, **kwargs)
        con.set_trace_callback(statements.append)
        return con

    monkeypatch.setattr(sqlite3, "connect", trace)
    with pytest.raises(ValueError, match="only the registered spent"):
        load_panel(scan, calendar)
    assert not any("scan_bars" in sql for sql in statements)


def test_session_blocks_capture_serial_dependence_and_seed_reproduces():
    """Long same-sign runs require greater uncertainty than independent days."""
    values = [0.01] * 100 + [-0.01] * 100
    block = block_standard_error(values, 50, 99, 1000)
    assert block > 3 * block_standard_error(values, 1, 99, 1000)
    assert block == block_standard_error(values, 50, 99, 1000)


def test_calibration_only_reports_dispersion_and_stops_an_unpowered_family():
    """An unresolvable cost-sized effect cannot authorize the comparison family."""
    book = {"returns": [0.01] * 300 + [-0.01] * 300,
            "diagnostics": {}, "closed_trades": 40, "unresolved_positions": 0}
    params = dict(bootstrap_blocks=[63, 126, 252], bootstrap_repetitions=200,
                  seed=1, alpha=0.05, power=0.8, holding_sessions=126,
                  sizing_round_trip_bps=43.54)
    result = calibration(book, params)
    assert result["cost_floor_resolvable"] is False
    assert not {"mean", "sharpe", "total_return", "pass"} & result.keys()
    assert result["corrected_to_naive_se"] > 1


def test_all_cash_is_not_perfect_power():
    """No measurable event dispersion is an error, not zero required effect."""
    book = {"returns": [0.0] * 600, "diagnostics": {}, "closed_trades": 0,
            "unresolved_positions": 0}
    params = dict(bootstrap_blocks=[63, 126, 252], bootstrap_repetitions=200,
                  seed=1, alpha=0.05, power=0.8, holding_sessions=126,
                  sizing_round_trip_bps=43.54)
    with pytest.raises(ValueError, match="event-arm dispersion"):
        calibration(book, params)


@pytest.mark.parametrize("source", EXECUTION_SOURCES)
@pytest.mark.parametrize("staged", [False, True])
def test_dirty_execution_dependency_refused_but_unrelated_edits_allowed(tmp_path, source, staged):
    """Use a real temporary Git repo to verify staged and unstaged dependency edits."""
    def git(*args):
        """Run fixture-only Git commands without touching the project's index."""
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], stderr=subprocess.DEVNULL)

    git("init")
    for relative in EXECUTION_SOURCES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# original\n")
    spec = tmp_path / "spec.json"
    spec.write_text("{}\n")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
    (tmp_path / "unrelated.txt").write_text("allowed")
    assert committed_specification(tmp_path, spec) == b"{}\n"
    (tmp_path / source).write_text("# changed\n")
    if staged:
        git("add", source)
    with pytest.raises(ValueError, match="execution sources"):
        committed_specification(tmp_path, spec)
