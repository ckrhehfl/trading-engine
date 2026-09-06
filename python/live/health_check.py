"""Decide whether either paper-trading loop needs a human, and record it.

    cd python && .venv/bin/python -m live.health_check          # human-readable
    cd python && .venv/bin/python -m live.health_check --json
    cd python && .venv/bin/python -m live.health_check --quiet  # cron: print only on breach

Exits **1** when anything needs attention, **0** otherwise.

## Why this exists when there are already three dashboards

`live.dashboard`, `scripts/paper-trading-monitor.sh` and
`live/web_dashboard.py` are all **pull**, and all three show the same
thing: the state *right now*. None of them answers "what happened
overnight", which is the question that actually matters for a system
running unattended.

The gap is not theoretical. On 2026-09-05, day 1 of Gate A, the
simulated loop recorded `uptime_fraction: 0.989691` -- **below Gate A's
own 99% floor** -- and nothing surfaced it. A 15-day gate can be failed
on day 1 and discovered on day 15.

## Detection and history, deliberately without a notification channel

**Human-decided 2026-09-06**: no outbound alerting channel yet. Every
one (email, Telegram, a webhook) needs an account and a credential on
the VPS, and the operator's judgment is that the right time to add one
is when there is P&L worth pushing -- not now, for infrastructure
warnings.

So this is not "unfinished alerting". It is a deliberate scope: **judge
every 15 minutes and write the verdict down**, so that whoever looks
next -- an operator, or an AI session with SSH access -- reads the
*history* rather than a snapshot. Today there is no history at all, and
that is the actual thing being fixed here.

`Notifier` stays as the seam a channel plugs into when that day comes.
`FileNotifier` needs no account and no credential, so it ships now.

**What this therefore does NOT do**: reach anyone who is not looking. A
loop that wedges on Friday night is detected at 20 minutes and read on
Monday. Stated plainly rather than implied, because a monitor believed
to alert and not alerting is worse than none.

## It reads the dashboard's own view of the world, not the world

`live.dashboard.to_json_dict` already gathers sessions, ticks, kill
switch, daily reports and signal freshness. Re-reading any of that here
would create a second source of truth that drifts from the first -- the
same reasoning that made `s14_eligibility.py` delegate DSR to
`retrospective.py` rather than keep its own copy. Disk space is the one
exception, because no dashboard reads it.

## Every threshold is named, with the reason it has that value

A threshold with no stated reason gets tuned until nothing fires.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from live._locking import exclusive_lock

CRITICAL = "critical"
WARNING = "warning"

# The watchdog restarts a dead session within 5 minutes, so a single
# "not alive" reading is the normal appearance of a restart in progress,
# not an incident. Two consecutive failures across this check's own
# cadence means the watchdog is not recovering it, which is the
# condition worth a human's attention.
DEAD_LOOP_CONSECUTIVE_CHECKS = 2

# A loop ticks every 5 minutes. 20 minutes is four missed ticks -- long
# enough that a slow BingX response or one restart cannot explain it.
#
# This is the highest-value check here, because it is the one failure
# the watchdog is structurally blind to: a wedged JVM still holds its
# tmux session, so `session_alive` stays true forever while nothing
# happens.
TICK_STALE_AFTER = timedelta(minutes=20)

# Gate A: "uptime >= 99% measured from the daily reports' own
# ticks_succeeded / ticks_attempted". Not a round number picked here --
# it is the gate's own figure, so a breach here is a Gate A breach.
GATE_A_UPTIME_FLOOR = 0.99

# The e2-micro's boot disk is 30 GB. Everything -- both loops, the kline
# cache, reports, logs -- stops in unpredictable ways when it fills, and
# the failure looks like something else entirely. 85% leaves room to
# notice before that.
DISK_USED_FRACTION_CEILING = 0.85

# Once a condition has been recorded, do not record it again for this
# long. A 15-minute cron writing the same line 96 times a day buries the
# transitions -- which are the whole point of keeping a history -- under
# repetition of a state that has not changed.
REPEAT_AFTER = timedelta(hours=6)

STATE_PATH = Path("var/live/health-state.json")
ALERT_LOG_PATH = Path("var/live/health-alerts.jsonl")


@dataclass(frozen=True)
class Alert:
    """One thing that is wrong, named so it can be suppressed by key."""

    key: str
    """Stable across runs -- this is what repeat suppression matches on,
    so it must not embed a changing value like a timestamp or an age."""

    severity: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.key}: {self.detail}"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def check_loops(
    status: dict[str, Any], previous: dict[str, Any], now: datetime
) -> list[Alert]:
    """Liveness, tick freshness and the kill switch, per loop."""
    alerts: list[Alert] = []
    for key, loop in (status.get("loops") or {}).items():
        name = loop.get("display_name", key)

        if not loop.get("alive"):
            # Consecutive, not instantaneous: see
            # DEAD_LOOP_CONSECUTIVE_CHECKS.
            seen = int(previous.get("consecutive_dead", {}).get(key, 0)) + 1
            if seen >= DEAD_LOOP_CONSECUTIVE_CHECKS:
                alerts.append(
                    Alert(
                        f"loop_dead:{key}",
                        CRITICAL,
                        f"{name} has not been running for {seen} consecutive "
                        f"checks. The watchdog restarts a dead session within "
                        f"5 minutes, so it is not recovering this one.",
                    )
                )
            # A dead loop is not also reported as stale: two entries for
            # one fact is how a history becomes unreadable.
            continue

        tick = loop.get("last_tick") or {}
        last_at = _parse_iso(tick.get("last_tick_at"))
        if last_at is None:
            # Not an alert on its own. A freshly restarted loop has no
            # tick in its scrollback yet, and neither does one whose pane
            # could not be read; guessing here would fire on every
            # ordinary restart. Liveness and the daily reports cover the
            # cases that matter.
            pass
        elif now - last_at > TICK_STALE_AFTER:
            age = now - last_at
            alerts.append(
                Alert(
                    f"ticks_stale:{key}",
                    CRITICAL,
                    f"{name} is running but its last tick was "
                    f"{int(age.total_seconds() // 60)} minutes ago. A wedged "
                    f"process keeps its tmux session, so the watchdog cannot "
                    f"see this.",
                )
            )

        # `None` means "the pane could not be read", which the dashboard
        # documents as distinct from `False`. Only a positive True is
        # treated as tripped.
        if loop.get("kill_switch_mentioned_in_scrollback") is True:
            alerts.append(
                Alert(
                    f"kill_switch:{key}",
                    CRITICAL,
                    f"{name}'s log mentions the kill switch. Resetting it is "
                    f"always a deliberate human decision -- find out why it "
                    f"tripped before doing anything.",
                )
            )
    return alerts


def check_daily_reports(status: dict[str, Any], today: date) -> list[Alert]:
    """Gate A's "no missing daily reports" and its 99% uptime floor.

    Yesterday, not today: today's report is written at the end of the
    day, so its absence at 09:00 means nothing and flagging it would fire
    every single morning.
    """
    alerts: list[Alert] = []
    yesterday = (today - timedelta(days=1)).isoformat()

    for key, loop in (status.get("loops") or {}).items():
        name = loop.get("display_name", key)
        reports = loop.get("daily_reports") or []
        by_date = {r.get("date"): r for r in reports if isinstance(r, dict)}

        if yesterday not in by_date:
            alerts.append(
                Alert(
                    f"missing_daily_report:{key}",
                    WARNING,
                    f"{name} has no daily report for {yesterday}. Gate A "
                    f"requires no missing daily reports across its 15 days.",
                )
            )
            continue

        report = by_date[yesterday]
        uptime = report.get("uptime_fraction")
        if uptime is None:
            # Reports written before `uptime_fraction` existed still
            # carry the two counts it is derived from.
            attempted = report.get("ticks_attempted") or 0
            succeeded = report.get("ticks_succeeded") or 0
            uptime = (succeeded / attempted) if attempted else None
        if uptime is not None and float(uptime) < GATE_A_UPTIME_FLOOR:
            errors = [e.get("message", "")[:80] for e in (report.get("errors") or [])]
            alerts.append(
                Alert(
                    f"uptime_below_gate_a:{key}",
                    WARNING,
                    f"{name} recorded {float(uptime):.4%} uptime on "
                    f"{yesterday}, below Gate A's {GATE_A_UPTIME_FLOOR:.0%} "
                    f"floor. Errors: {errors[:3]}",
                )
            )
    return alerts


def check_signal_freshness(status: dict[str, Any]) -> list[Alert]:
    """The daily signal runner is what makes the loops do anything.

    `signal_stale` is `None` when the age cannot be determined, and the
    dashboard's own docstring says a consumer must not read that as
    fresh -- so it is reported rather than ignored.
    """
    stale = status.get("signal_stale")
    if stale is True:
        age = status.get("signal_decision_age_seconds")
        hours = "unknown" if age is None else f"{age / 3600:.1f}"
        return [
            Alert(
                "signal_stale",
                WARNING,
                f"the last daily signal decision is {hours} hours old. The "
                f"cron job retries every 5 minutes, so this means it is "
                f"failing, not merely waiting.",
            )
        ]
    if stale is None:
        return [
            Alert(
                "signal_age_unknown",
                WARNING,
                "the age of the last signal decision could not be determined. "
                "That is not the same as fresh -- check var/live/cron.log.",
            )
        ]
    return []


def check_disk(path: Path | str = ".") -> list[Alert]:
    """The one thing no dashboard reads.

    A full disk stops the loops, the reports and the kline cache at once,
    and every one of those failures looks like something else.
    """
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return [Alert("disk_unreadable", WARNING, f"could not stat {path}: {exc}")]
    used = 1 - (usage.free / usage.total)
    if used >= DISK_USED_FRACTION_CEILING:
        return [
            Alert(
                "disk_nearly_full",
                CRITICAL,
                f"{used:.1%} of the disk is used ({usage.free / 2**30:.1f} GiB "
                f"free). Everything fails in confusing ways once this fills.",
            )
        ]
    return []


def evaluate(
    status: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    now: datetime | None = None,
    disk_path: Path | str = ".",
) -> list[Alert]:
    """Every check, against one already-gathered view of the world.

    Pure apart from `check_disk`, so the whole decision surface is
    testable from a fixture dict.
    """
    now = now or datetime.now(timezone.utc)
    previous = previous or {}
    return [
        *check_loops(status, previous, now),
        *check_daily_reports(status, now.date()),
        *check_signal_freshness(status),
        *check_disk(disk_path),
    ]


# --------------------------------------------------------------------
# State: repeat suppression and recovery
# --------------------------------------------------------------------


def load_state(path: Path | str = STATE_PATH) -> dict[str, Any]:
    """A missing, corrupt or structurally wrong state file means "no
    history", never a crash.

    Fail-*open* here specifically, and deliberately: losing history
    re-records an alert, which is noise. Refusing to run produces
    silence, which is the thing this module exists to prevent. Every
    other fail-open decision in this project's operational code is a bug;
    this one is reasoned, which is why the reasoning is written down.

    **Structure is checked to the leaves, not just JSON syntax, and not
    just the containers.** This validator has been wrong twice, each
    time by being one level too shallow:

    - a first version verified only that the top level was a `dict`, so
      `{"open": []}` passed and `decide` died on `open_before.get(...)`;
    - a second verified the containers, so
      `{"consecutive_dead": {"simulated": []}}` passed and `check_loops`
      died on `int([])`, and `{"open": {"k": {"last_notified": []}}}`
      passed and `_parse_iso` died on `.replace`.

    Both are the exact silence this module exists to prevent, arriving
    through the guard written to prevent it. The leaf types are now a
    declared table (`_is_well_formed_state`) rather than an inline
    check, so "is this field covered?" is answerable by reading it.

    Anything not shaped like state we wrote is discarded whole rather
    than partially trusted; a half-valid history is worse than none,
    because suppression would then key off nonsense.
    """
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if _is_well_formed_state(loaded) else {}


# Every leaf `decide` and `check_loops` actually read, with the types
# they must have. Declared as a table rather than checked inline,
# because this validator has now been wrong twice by being one level too
# shallow -- containers checked, leaves not -- and a table makes "did we
# cover this field?" answerable by reading it.
#
# `_parse_iso` calls `.replace` on the timestamps and `check_loops` calls
# `int()` on the counters, so a list or a dict in either place raises
# from inside a cron job. That is silence, which is the one outcome this
# module exists to prevent.
_OPEN_ENTRY_STRING_FIELDS = ("severity", "first_seen", "last_notified", "detail")


def _is_well_formed_state(loaded: object) -> bool:
    """True only for a structure this module could itself have written.

    Whole-or-nothing: a partially valid history is worse than none,
    because repeat suppression would key off nonsense.
    """
    if not isinstance(loaded, dict):
        return False

    for key in ("open", "consecutive_dead"):
        value = loaded.get(key)
        if value is not None and not isinstance(value, dict):
            return False

    for entry in (loaded.get("open") or {}).values():
        if not isinstance(entry, dict):
            return False
        for field_name in _OPEN_ENTRY_STRING_FIELDS:
            field_value = entry.get(field_name)
            if field_value is not None and not isinstance(field_value, str):
                return False

    for count in (loaded.get("consecutive_dead") or {}).values():
        # `bool` is an `int` subclass and would survive `int()`, but it
        # is not something this module writes -- so it is a sign the file
        # came from somewhere else, and the safe read is to discard.
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return False

    return True


def save_state(state: dict[str, Any], path: Path | str = STATE_PATH) -> None:
    """Atomic, and safe to call from more than one process at once.

    Per-process temp name: a shared `.tmp` is a collision independent of
    any lock, and one that outlives someone changing the locking later.
    Two instances on a fixed name can race such that one `replace`s a
    file the other already moved, and the loser raises FileNotFoundError
    from inside a cron job -- silence, again.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


@dataclass
class Decision:
    """What to record, what to stay quiet about, and what has recovered."""

    to_send: list[Alert] = field(default_factory=list)
    suppressed: list[Alert] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)


def decide(
    alerts: list[Alert], previous: dict[str, Any], now: datetime, status: dict[str, Any]
) -> Decision:
    """Apply repeat suppression, and notice what stopped being wrong.

    A recovery entry matters as much as the alert. Without one the
    history says "loop dead" and never says otherwise, so a reader
    cannot tell a five-minute blip from a three-day outage -- which is
    exactly the distinction Gate A's uptime clause turns on.
    """
    open_before = previous.get("open", {})
    open_now: dict[str, Any] = {}
    decision = Decision()

    for alert in alerts:
        was = open_before.get(alert.key)
        last_notified = _parse_iso((was or {}).get("last_notified"))
        if was is None or last_notified is None or now - last_notified >= REPEAT_AFTER:
            decision.to_send.append(alert)
            notified_at = now.isoformat(timespec="seconds")
        else:
            decision.suppressed.append(alert)
            notified_at = (was or {}).get("last_notified")
        open_now[alert.key] = {
            "severity": alert.severity,
            "first_seen": (was or {}).get(
                "first_seen", now.isoformat(timespec="seconds")
            ),
            "last_notified": notified_at,
            "detail": alert.detail,
        }

    decision.recovered = sorted(set(open_before) - set(open_now))

    # Consecutive-dead counters, the one piece of history a check needs.
    dead_now = {
        key: int(previous.get("consecutive_dead", {}).get(key, 0)) + 1
        for key, loop in (status.get("loops") or {}).items()
        if not loop.get("alive")
    }

    decision.state = {
        "checked_at": now.isoformat(timespec="seconds"),
        "open": open_now,
        "consecutive_dead": dead_now,
    }
    return decision


# --------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------


class Notifier:
    """The seam a real channel plugs into, when one is chosen.

    Nothing here opens a socket. Adding a channel means adding an
    account and a credential on the VPS, which the operator has
    deliberately deferred until there is P&L worth pushing -- see this
    module's docstring.
    """

    def send(self, alerts: list[Alert], recovered: list[str], now: datetime) -> None:
        raise NotImplementedError


class FileNotifier(Notifier):
    """Appends to a JSONL file. Needs no account and no credential.

    This is the durable history the whole module is for: a reader
    arriving days later gets every transition with its timestamp, rather
    than the single instant the dashboards show.
    """

    def __init__(self, path: Path | str = ALERT_LOG_PATH) -> None:
        self.path = Path(path)

    def send(self, alerts: list[Alert], recovered: list[str], now: datetime) -> None:
        if not alerts and not recovered:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for alert in alerts:
                handle.write(
                    json.dumps(
                        {
                            "at": now.isoformat(timespec="seconds"),
                            "event": "alert",
                            **asdict(alert),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            for key in recovered:
                handle.write(
                    json.dumps(
                        {
                            "at": now.isoformat(timespec="seconds"),
                            "event": "recovered",
                            "key": key,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )


def gather_status() -> dict[str, Any]:
    """The live view, via the dashboard rather than a second reader."""
    from live import dashboard  # noqa: PLC0415 -- keeps import cost off --help

    statuses = [dashboard.gather_loop_status(config) for config in dashboard.LOOPS]
    # Round-tripped through the dashboard's own Decimal encoder so this
    # module sees exactly the shape any other JSON consumer would --
    # including a fixture saved from `--json` output.
    return json.loads(
        json.dumps(dashboard.to_json_dict(statuses), default=dashboard._decimal_default)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing when everything is healthy -- for cron",
    )
    parser.add_argument(
        "--status-json",
        help="read the dashboard view from this file instead of gathering it live",
    )
    parser.add_argument("--state-path", default=str(STATE_PATH))
    parser.add_argument("--alert-log", default=str(ALERT_LOG_PATH))
    parser.add_argument(
        "--disk-path",
        default=".",
        # The filesystem holding var/live/, which is what actually fills.
        # Injectable so a test does not depend on the free space of
        # whatever machine it runs on -- the first version did, and
        # failed on a dev box that happened to be 87.5% full.
        help="filesystem to check for free space (default: the working directory)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="evaluate and report, writing neither the state file nor the alert log",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    if args.status_json:
        status = json.loads(Path(args.status_json).read_text(encoding="utf-8"))
    else:
        status = gather_status()

    # One lock across read-modify-write. Two instances -- the 15-minute
    # cron and an operator running it by hand -- would otherwise both
    # read the same previous state, both decide the condition is new,
    # and both append: a duplicate alert line, and whichever saves last
    # silently discards the other's `first_seen`. `first_seen` is how a
    # reader tells a blip from a three-day outage, so losing it defeats
    # the point of keeping a history.
    #
    # Taken even for `--dry-run`, which writes nothing: holding it means
    # a dry run reports what the real run would do rather than a torn
    # read of state another process is mid-write on.
    with exclusive_lock(Path(args.state_path)):
        previous = load_state(args.state_path)
        alerts = evaluate(status, previous=previous, now=now, disk_path=args.disk_path)
        decision = decide(alerts, previous, now, status)

        if not args.dry_run:
            FileNotifier(args.alert_log).send(
                decision.to_send, decision.recovered, now
            )
            save_state(decision.state, args.state_path)

    if args.json:
        print(
            json.dumps(
                {
                    "checked_at": now.isoformat(timespec="seconds"),
                    "healthy": not alerts,
                    "alerts": [asdict(a) for a in alerts],
                    "sent": [a.key for a in decision.to_send],
                    "suppressed": [a.key for a in decision.suppressed],
                    "recovered": decision.recovered,
                    "dry_run": args.dry_run,
                },
                indent=2,
                sort_keys=True,
            )
        )
    elif alerts or decision.recovered:
        for alert in decision.to_send:
            print(str(alert))
        for alert in decision.suppressed:
            print(f"{alert}  (already recorded, not repeated within {REPEAT_AFTER})")
        for key in decision.recovered:
            print(f"[RECOVERED] {key}")
    elif not args.quiet:
        print("all checks pass")

    # Non-zero whenever anything is open, including a suppressed repeat:
    # the exit code reports the state of the SYSTEM, not whether this
    # particular run chose to write a line about it.
    return 1 if alerts else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
