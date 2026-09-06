"""The health check must fire on the real conditions, and stay quiet otherwise.

Two opposite failure modes, both fatal to the point of the thing: a check
that never fires (silence during an outage) and one that fires constantly
(a history so repetitive the transitions are unreadable). So every check
has a test in both directions.

The fixture is shaped like `live.dashboard.to_json_dict`'s real output,
including the real 2026-09-05 figures from the VPS, because the whole
design depends on consuming that dict rather than re-reading the world.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from live.health_check import (
    CRITICAL,
    DEAD_LOOP_CONSECUTIVE_CHECKS,
    GATE_A_UPTIME_FLOOR,
    REPEAT_AFTER,
    WARNING,
    Alert,
    FileNotifier,
    check_daily_reports,
    check_disk,
    check_loops,
    check_signal_freshness,
    decide,
    evaluate,
    load_state,
    main,
    save_state,
)

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def healthy_status(at=NOW, **overrides):
    """A view in which nothing is wrong. Each test breaks exactly one thing.

    `at` exists because the CLI path calls `datetime.now()` itself -- it
    has no `--now` and should not grow one just for tests. A fixture
    pinned to a fixed date is healthy only relative to that date, so the
    CLI tests build theirs relative to the real clock instead.
    """
    yesterday = (at.date() - timedelta(days=1)).isoformat()
    status = {
        "generated_at": at.isoformat(),
        "loops": {
            "simulated": {
                "display_name": "SIMULATED LOOP",
                "alive": True,
                "last_tick": {
                    "last_tick_at": (at - timedelta(minutes=2)).isoformat(),
                    "ok": True,
                    "error": None,
                },
                "kill_switch_mentioned_in_scrollback": False,
                "daily_reports": [
                    {
                        "date": yesterday,
                        "ticks_attempted": 288,
                        "ticks_succeeded": 288,
                        "uptime_fraction": 1.0,
                        "errors": [],
                    }
                ],
            }
        },
        "signal_stale": False,
        "signal_decision_age_seconds": 3600,
    }
    status.update(overrides)
    return status


def cli_args(tmp_path, status_file, *extra):
    """Every path injected, so no test depends on the host machine."""
    return [
        "--status-json", str(status_file),
        "--state-path", str(tmp_path / "state.json"),
        "--alert-log", str(tmp_path / "alerts.jsonl"),
        "--disk-path", str(tmp_path),
        *extra,
    ]


def live_status(**overrides):
    """A healthy view relative to the real clock, for the CLI tests."""
    return healthy_status(at=datetime.now(timezone.utc), **overrides)


def write_status(tmp_path, status):
    path = tmp_path / "status.json"
    path.write_text(json.dumps(status), encoding="utf-8")
    return path


class TestLoopChecks:
    def test_a_healthy_loop_produces_nothing(self):
        assert check_loops(healthy_status(), {}, NOW) == []

    def test_one_dead_reading_is_not_an_alert(self):
        """The watchdog restarts within 5 minutes, so a single dead
        reading is what a restart in progress looks like. Alerting on it
        would fire on every ordinary restart."""
        status = healthy_status()
        status["loops"]["simulated"]["alive"] = False
        assert check_loops(status, {}, NOW) == []

    def test_two_consecutive_dead_readings_is_critical(self):
        status = healthy_status()
        status["loops"]["simulated"]["alive"] = False
        previous = {"consecutive_dead": {"simulated": DEAD_LOOP_CONSECUTIVE_CHECKS - 1}}
        alerts = check_loops(status, previous, NOW)
        assert [a.key for a in alerts] == ["loop_dead:simulated"]
        assert alerts[0].severity == CRITICAL

    def test_a_wedged_loop_is_caught_even_though_it_is_alive(self):
        """The highest-value check here: a wedged JVM keeps its tmux
        session, so `session_alive` stays true forever and the watchdog
        is structurally blind to it."""
        status = healthy_status()
        status["loops"]["simulated"]["last_tick"]["last_tick_at"] = (
            NOW - timedelta(hours=3)
        ).isoformat()
        alerts = check_loops(status, {}, NOW)
        assert [a.key for a in alerts] == ["ticks_stale:simulated"]
        assert alerts[0].severity == CRITICAL
        assert "180 minutes ago" in alerts[0].detail

    def test_a_dead_loop_is_not_also_reported_as_stale(self):
        """Two entries for one fact is how a history becomes unreadable."""
        status = healthy_status()
        status["loops"]["simulated"]["alive"] = False
        status["loops"]["simulated"]["last_tick"]["last_tick_at"] = (
            NOW - timedelta(hours=3)
        ).isoformat()
        keys = [
            a.key
            for a in check_loops(status, {"consecutive_dead": {"simulated": 5}}, NOW)
        ]
        assert keys == ["loop_dead:simulated"]

    def test_a_loop_with_no_tick_yet_is_not_reported_stale(self):
        """A freshly restarted loop has nothing in its scrollback, and so
        does one whose pane could not be read. Guessing here would fire
        on every restart."""
        status = healthy_status()
        status["loops"]["simulated"]["last_tick"] = None
        assert check_loops(status, {}, NOW) == []

    def test_kill_switch_mentioned_is_critical(self):
        status = healthy_status()
        status["loops"]["simulated"]["kill_switch_mentioned_in_scrollback"] = True
        alerts = check_loops(status, {}, NOW)
        assert [a.key for a in alerts] == ["kill_switch:simulated"]
        assert alerts[0].severity == CRITICAL

    def test_an_unreadable_pane_is_not_treated_as_a_tripped_switch(self):
        """`None` means "could not check", which the dashboard documents
        as distinct from `False`. Only a positive True is reported."""
        status = healthy_status()
        status["loops"]["simulated"]["kill_switch_mentioned_in_scrollback"] = None
        assert check_loops(status, {}, NOW) == []


class TestDailyReports:
    def test_the_real_2026_09_05_reading_breaches_gate_a(self):
        """Not synthetic. This is what the VPS actually recorded on day 1
        of Gate A, and nothing surfaced it."""
        status = healthy_status()
        status["loops"]["simulated"]["daily_reports"] = [
            {
                "date": "2026-09-05",
                "ticks_attempted": 97,
                "ticks_succeeded": 96,
                "uptime_fraction": 0.989691,
                "errors": [
                    {
                        "occurred_at": "2026-09-05T20:35:40Z",
                        "message": (
                            "engine.exchange.ExchangeException: BingX price "
                            "feed request failed"
                        ),
                    }
                ],
            }
        ]
        alerts = check_daily_reports(status, NOW.date())
        assert [a.key for a in alerts] == ["uptime_below_gate_a:simulated"]
        assert alerts[0].severity == WARNING
        assert "98.9691%" in alerts[0].detail
        assert "BingX price feed" in alerts[0].detail

    def test_exactly_at_the_floor_passes(self):
        """`< floor`, not `<= floor`. Gate A says "uptime >= 99%"."""
        status = healthy_status()
        status["loops"]["simulated"]["daily_reports"] = [
            {"date": "2026-09-05", "uptime_fraction": GATE_A_UPTIME_FLOOR, "errors": []}
        ]
        assert check_daily_reports(status, NOW.date()) == []

    def test_uptime_is_derived_when_the_field_is_absent(self):
        """Older reports predate `uptime_fraction`; the two tick counts
        have always been there."""
        status = healthy_status()
        status["loops"]["simulated"]["daily_reports"] = [
            {
                "date": "2026-09-05",
                "ticks_attempted": 100,
                "ticks_succeeded": 90,
                "errors": [],
            }
        ]
        assert [a.key for a in check_daily_reports(status, NOW.date())] == [
            "uptime_below_gate_a:simulated"
        ]

    def test_a_report_with_no_ticks_at_all_is_not_divided_by_zero(self):
        status = healthy_status()
        status["loops"]["simulated"]["daily_reports"] = [
            {"date": "2026-09-05", "ticks_attempted": 0, "ticks_succeeded": 0, "errors": []}
        ]
        assert check_daily_reports(status, NOW.date()) == []

    def test_a_missing_report_for_yesterday_is_flagged(self):
        status = healthy_status()
        status["loops"]["simulated"]["daily_reports"] = []
        assert [a.key for a in check_daily_reports(status, NOW.date())] == [
            "missing_daily_report:simulated"
        ]

    def test_todays_report_being_absent_is_not_an_alert(self):
        """It is written at the end of the day. Flagging its absence at
        09:00 would fire every single morning."""
        status = healthy_status()
        status["loops"]["simulated"]["daily_reports"] = [
            {"date": "2026-09-05", "uptime_fraction": 1.0, "errors": []}
        ]
        assert check_daily_reports(status, NOW.date()) == []


class TestSignalFreshness:
    def test_stale_is_flagged(self):
        alerts = check_signal_freshness(
            {"signal_stale": True, "signal_decision_age_seconds": 90000}
        )
        assert [a.key for a in alerts] == ["signal_stale"]
        assert "25.0 hours" in alerts[0].detail

    def test_unknown_is_flagged_rather_than_assumed_fresh(self):
        """The dashboard's own docstring says a consumer must not read
        `None` as fresh."""
        assert [a.key for a in check_signal_freshness({"signal_stale": None})] == [
            "signal_age_unknown"
        ]

    def test_fresh_is_silent(self):
        assert check_signal_freshness({"signal_stale": False}) == []


class TestDisk:
    def test_a_healthy_disk_is_silent(self, tmp_path):
        assert check_disk(tmp_path) == []

    def test_a_full_disk_is_critical(self, tmp_path, monkeypatch):
        import shutil as shutil_module

        monkeypatch.setattr(
            shutil_module,
            "disk_usage",
            lambda _p: shutil_module._ntuple_diskusage(100, 95, 5),
        )
        alerts = check_disk(tmp_path)
        assert [a.key for a in alerts] == ["disk_nearly_full"]
        assert alerts[0].severity == CRITICAL

    def test_an_unreadable_path_is_reported_not_swallowed(self, tmp_path):
        assert [a.key for a in check_disk(tmp_path / "nope" / "deeper")] == [
            "disk_unreadable"
        ]


class TestRepeatSuppression:
    def test_the_first_occurrence_is_recorded(self):
        decision = decide([Alert("k", CRITICAL, "d")], {}, NOW, healthy_status())
        assert [a.key for a in decision.to_send] == ["k"]

    def test_an_immediate_repeat_is_suppressed(self):
        """96 identical lines a day buries the transitions, which are the
        whole point of keeping a history."""
        alerts = [Alert("k", CRITICAL, "d")]
        first = decide(alerts, {}, NOW, healthy_status())
        second = decide(
            alerts, first.state, NOW + timedelta(minutes=15), healthy_status()
        )
        assert second.to_send == []
        assert [a.key for a in second.suppressed] == ["k"]

    def test_it_is_recorded_again_after_the_backoff(self):
        alerts = [Alert("k", CRITICAL, "d")]
        first = decide(alerts, {}, NOW, healthy_status())
        later = decide(
            alerts,
            first.state,
            NOW + REPEAT_AFTER + timedelta(minutes=1),
            healthy_status(),
        )
        assert [a.key for a in later.to_send] == ["k"]

    def test_first_seen_survives_a_repeat_but_last_notified_moves(self):
        """`first_seen` is how a reader tells a five-minute blip from a
        three-day outage, which is the distinction Gate A turns on."""
        alerts = [Alert("k", CRITICAL, "d")]
        first = decide(alerts, {}, NOW, healthy_status())
        later = decide(
            alerts,
            first.state,
            NOW + REPEAT_AFTER + timedelta(minutes=1),
            healthy_status(),
        )
        assert (
            later.state["open"]["k"]["first_seen"]
            == first.state["open"]["k"]["first_seen"]
        )
        assert (
            later.state["open"]["k"]["last_notified"]
            != first.state["open"]["k"]["last_notified"]
        )

    def test_a_condition_that_clears_produces_a_recovery_entry(self):
        first = decide([Alert("k", CRITICAL, "d")], {}, NOW, healthy_status())
        cleared = decide([], first.state, NOW + timedelta(minutes=15), healthy_status())
        assert cleared.recovered == ["k"]
        assert cleared.state["open"] == {}

    def test_a_recovery_is_recorded_once_not_forever(self):
        first = decide([Alert("k", CRITICAL, "d")], {}, NOW, healthy_status())
        cleared = decide([], first.state, NOW + timedelta(minutes=15), healthy_status())
        again = decide([], cleared.state, NOW + timedelta(minutes=30), healthy_status())
        assert again.recovered == []


class TestState:
    def test_a_missing_state_file_is_empty_history_not_a_crash(self, tmp_path):
        assert load_state(tmp_path / "absent.json") == {}

    def test_a_corrupt_state_file_fails_OPEN_deliberately(self, tmp_path):
        """The only fail-open decision in this module, and it is
        reasoned: losing history re-records an alert (noise), while
        refusing to run produces silence (the thing this prevents)."""
        path = tmp_path / "state.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_state(path) == {}

    def test_a_state_file_holding_a_list_is_rejected(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("[1,2,3]", encoding="utf-8")
        assert load_state(path) == {}

    def test_a_round_trip_preserves_the_state(self, tmp_path):
        path = tmp_path / "nested" / "state.json"
        state = {"open": {"k": {"first_seen": "x"}}, "consecutive_dead": {"simulated": 2}}
        save_state(state, path)
        assert load_state(path) == state


class TestCli:
    def test_the_disk_path_is_injectable_so_tests_do_not_depend_on_the_host(
        self, tmp_path
    ):
        """The first version of these tests read the real filesystem and
        failed on a dev machine that happened to be 87.5% full. A test
        whose result depends on the host is not testing the code."""
        status_file = write_status(tmp_path, live_status())
        assert main(cli_args(tmp_path, status_file)) == 0

    def test_a_healthy_system_exits_zero(self, tmp_path, capsys):
        status_file = write_status(tmp_path, live_status())
        assert main(cli_args(tmp_path, status_file)) == 0
        assert "all checks pass" in capsys.readouterr().out

    def test_a_breach_exits_one_and_is_recorded(self, tmp_path):
        status = live_status()
        status["loops"]["simulated"]["kill_switch_mentioned_in_scrollback"] = True
        status_file = write_status(tmp_path, status)

        assert main(cli_args(tmp_path, status_file)) == 1
        recorded = [
            json.loads(line)
            for line in (tmp_path / "alerts.jsonl").read_text().splitlines()
        ]
        assert [r["key"] for r in recorded] == ["kill_switch:simulated"]

    def test_a_suppressed_repeat_still_exits_one(self, tmp_path):
        """The exit code reports the state of the SYSTEM, not whether
        this run chose to write a line. A cron job reading 0 while a loop
        is dead is exactly the silence this prevents."""
        status = live_status()
        status["loops"]["simulated"]["kill_switch_mentioned_in_scrollback"] = True
        status_file = write_status(tmp_path, status)
        args = cli_args(tmp_path, status_file)
        assert main(args) == 1
        assert main(args) == 1

    def test_quiet_prints_nothing_when_healthy(self, tmp_path, capsys):
        status_file = write_status(tmp_path, live_status())
        main(cli_args(tmp_path, status_file, "--quiet"))
        assert capsys.readouterr().out == ""

    def test_dry_run_writes_neither_the_state_nor_the_alert_log(self, tmp_path):
        """`check_readonly_path_is_pure`'s scar: a `--dry-run` that
        advanced persisted state changed the next real run."""
        status = live_status()
        status["loops"]["simulated"]["kill_switch_mentioned_in_scrollback"] = True
        status_file = write_status(tmp_path, status)

        assert main(cli_args(tmp_path, status_file, "--dry-run")) == 1
        assert not (tmp_path / "state.json").exists()
        assert not (tmp_path / "alerts.jsonl").exists()

    def test_dry_run_predicts_exactly_what_the_real_run_does(self, tmp_path, capsys):
        """The other half of a read-only path being useful: it has to be
        right, not merely harmless."""
        status = live_status()
        status["loops"]["simulated"]["kill_switch_mentioned_in_scrollback"] = True
        status_file = write_status(tmp_path, status)
        base = cli_args(tmp_path, status_file, "--json")

        main([*base, "--dry-run"])
        predicted = json.loads(capsys.readouterr().out)
        main(base)
        actual = json.loads(capsys.readouterr().out)
        for key in ("alerts", "sent", "suppressed", "recovered", "healthy"):
            assert predicted[key] == actual[key], key


class TestNotifier:
    def test_nothing_is_written_when_there_is_nothing_to_say(self, tmp_path):
        path = tmp_path / "alerts.jsonl"
        FileNotifier(path).send([], [], NOW)
        assert not path.exists()

    def test_recoveries_are_recorded_as_their_own_event(self, tmp_path):
        path = tmp_path / "alerts.jsonl"
        FileNotifier(path).send([], ["loop_dead:simulated"], NOW)
        record = json.loads(path.read_text().strip())
        assert record["event"] == "recovered"
        assert record["key"] == "loop_dead:simulated"


def test_evaluate_on_a_healthy_view_is_silent(tmp_path):
    assert evaluate(healthy_status(), now=NOW, disk_path=tmp_path) == []


def test_every_alert_key_is_stable_across_runs():
    """Repeat suppression matches on the key, so a key embedding a
    timestamp or an age would defeat it silently -- every run would look
    like a new condition and re-record."""
    status = healthy_status()
    status["loops"]["simulated"]["last_tick"]["last_tick_at"] = (
        NOW - timedelta(hours=3)
    ).isoformat()
    first = {a.key for a in check_loops(status, {}, NOW)}
    status["loops"]["simulated"]["last_tick"]["last_tick_at"] = (
        NOW - timedelta(hours=9)
    ).isoformat()
    assert first == {a.key for a in check_loops(status, {}, NOW)}
