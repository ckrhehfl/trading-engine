"""`python -m live.dashboard` -- read-only paper-trading status dashboard.

Summarizes both paper-trading loops (`simulated`, `bingx-vst`) purely from
data that already exists: each loop's own `tmux` pane output, the
`DailyReport` JSON files `DailyReportGenerator` (Java) writes to
`var/live/reports/{daily,vst}/`, `var/live/watchdog.log`/`cron.log`, and the
standing signal file (if any). Places no order and makes no exchange call of
its own -- the "real BingX VST balance" shown for the VST loop is
`VstPreflight`'s own already-logged startup-time query, read back out of
that session's `tmux` scrollback, not a fresh authenticated call made by
this script. That means it reflects the balance as of the VST session's
last start, not a live-refreshed figure, and disappears once enough ticks
have scrolled it out of `tmux`'s history buffer between restarts -- both
disclosed inline in the output rather than silently omitted.

See `docs/paper-trading-runbook.md` for how the loops themselves are
operated.

Usage:
    python -m live.dashboard            # human-readable
    python -m live.dashboard --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

# Matches engine.runtime.TradingLoop's own INITIAL_EQUITY constant -- both
# loops' internal equity bookkeeping starts here, so it's the right
# baseline for a return% comparable across sim and VST.
INITIAL_EQUITY = Decimal("100000")

WATCHDOG_LOG = REPO_ROOT / "var" / "live" / "watchdog.log"
CRON_LOG = REPO_ROOT / "var" / "live" / "cron.log"
SIGNAL_FILE = REPO_ROOT / "var" / "live" / "signals" / "BTC-USDT" / "daily-tsmom-ensemble" / "latest.json"

TICK_OK_RE = re.compile(r"tick complete: lastTickAt=(?P<ts>\S+) equity=(?P<equity>\S+)")
TICK_ERROR_RE = re.compile(
    r"tick completed with an error: lastTickAt=(?P<ts>\S+) equity=(?P<equity>\S+) error=(?P<error>.+)"
)
VST_BALANCE_RE = re.compile(
    r"VstPreflight: real VST balance=(?P<balance>\S+) equity=(?P<equity>\S+) "
    r"availableMargin=(?P<available_margin>\S+) usedMargin=(?P<used_margin>\S+) "
    r"unrealizedProfit=(?P<unrealized_profit>\S+)"
)
KILL_SWITCH_RE = re.compile(r"kill switch already TRIPPED|tripping kill switch", re.IGNORECASE)
CRON_NO_SIGNAL_RE = re.compile(r"^(?P<ts>[\d-]+ [\d:,]+) \S+ no signal today")
CRON_WROTE_SIGNAL_RE = re.compile(r"^(?P<ts>[\d-]+ [\d:,]+) \S+ wrote signal: (?P<detail>.+)")


@dataclass(frozen=True)
class LoopConfig:
    """Identifies one paper-trading loop to report on.

    Both `main()` and `live.web_dashboard` iterate `LOOPS` below rather than
    naming `simulated`/`vst` individually -- adding a future loop (a
    different symbol, asset class, or venue -- e.g. a KR/US equities loop)
    is one more `LoopConfig` entry, not a change to any rendering code.
    `symbol`/`asset_class` are descriptive only today (nothing here branches
    on them yet); they exist so a future multi-asset entry has somewhere to
    say what it is without another schema change.
    """

    key: str
    display_name: str
    session: str
    reports_dir: Path
    check_real_balance: bool
    symbol: str = "BTC-USDT"
    asset_class: str = "crypto-perp"


LOOPS: list[LoopConfig] = [
    LoopConfig(
        key="simulated",
        display_name="SIMULATED LOOP",
        session="paper-trading",
        reports_dir=REPO_ROOT / "var" / "live" / "reports" / "daily",
        check_real_balance=False,
    ),
    LoopConfig(
        key="vst",
        display_name="BINGX VST LOOP",
        session="paper-trading-vst",
        reports_dir=REPO_ROOT / "var" / "live" / "reports" / "vst",
        check_real_balance=True,
    ),
]


def _to_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


@dataclass
class TickStatus:
    last_tick_at: str
    equity: Decimal | None
    ok: bool
    error: str | None = None


def parse_latest_tick(pane_text: str) -> TickStatus | None:
    """Returns the most recent tick's outcome visible in `pane_text`.

    A successful tick and a failed tick are two different log lines --
    scanning line by line and keeping whichever comes last (rather than
    matching each pattern independently against the whole text) avoids
    reporting a stale successful tick's equity/timestamp when the true
    latest tick actually failed.
    """
    latest: TickStatus | None = None
    for line in pane_text.splitlines():
        m = TICK_ERROR_RE.search(line)
        if m:
            latest = TickStatus(m.group("ts"), _to_decimal(m.group("equity")), False, m.group("error").strip())
            continue
        m = TICK_OK_RE.search(line)
        if m:
            latest = TickStatus(m.group("ts"), _to_decimal(m.group("equity")), True, None)
    return latest


def parse_latest_vst_balance(pane_text: str) -> dict[str, Decimal | None] | None:
    """Returns `VstPreflight`'s most recent real-balance query visible in
    `pane_text` -- the last match, not the first, since a restarted
    session's scrollback can contain more than one (one per start)."""
    matches = list(VST_BALANCE_RE.finditer(pane_text))
    if not matches:
        return None
    m = matches[-1]
    return {
        "balance": _to_decimal(m.group("balance")),
        "equity": _to_decimal(m.group("equity")),
        "available_margin": _to_decimal(m.group("available_margin")),
        "used_margin": _to_decimal(m.group("used_margin")),
        "unrealized_profit": _to_decimal(m.group("unrealized_profit")),
    }


def kill_switch_mentioned(pane_text: str) -> bool:
    """Best-effort only -- checks only what's still in tmux's scrollback,
    not a full log history."""
    return bool(KILL_SWITCH_RE.search(pane_text))


def _tmux(*args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def session_alive(session: str) -> bool:
    # `=name` forces an exact match -- tmux's default target matching is
    # prefix-based, which would make "paper-trading" false-positive match
    # "paper-trading-vst" (same bug the watchdog script itself guards
    # against -- see its header comment).
    result = _tmux("has-session", "-t", f"={session}")
    return result is not None and result.returncode == 0


def capture_pane(session: str, history_lines: int = 2000) -> str | None:
    """Best-effort: returns None if tmux isn't installed, the session isn't
    running, or the capture otherwise fails -- never raises, since a dead
    session is an expected, common state for this dashboard to report on,
    not a script error. `-J` rejoins wrapped lines so a log line split
    across the pane's 80-column width doesn't break mid-token."""
    if not session_alive(session):
        return None
    # Unlike `has-session`, `capture-pane` needs a pane target, not just a
    # session -- a bare `=session` fails with "can't find pane" (confirmed
    # empirically); the trailing `:` targets that session's default
    # window/pane while keeping the exact-match `=` prefix.
    result = _tmux("capture-pane", "-t", f"={session}:", "-p", "-J", "-S", f"-{history_lines}")
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def _looks_like_daily_report(parsed: Any) -> bool:
    """Rejects JSON that parses fine but doesn't have the shape every
    downstream reader in this module assumes (`report.get(...)`, trade
    sorting by `filled_at`, `errors[-1].get("message")`). `DailyReport`
    files are always written by Java's `DailyReportGenerator` in normal
    operation, but a corrupted/truncated/hand-edited file should be
    skipped, not crash the whole dashboard on `AttributeError`/`TypeError`
    partway through rendering."""
    if not isinstance(parsed, dict):
        return False
    trades = parsed.get("trades")
    if trades is not None and (not isinstance(trades, list) or not all(isinstance(t, dict) for t in trades)):
        return False
    errors = parsed.get("errors")
    if errors is not None and (not isinstance(errors, list) or not all(isinstance(e, dict) for e in errors)):
        return False
    return True


def load_daily_reports(reports_dir: Path) -> list[dict[str, Any]]:
    """Reads every `DailyReport` JSON file in `reports_dir`, sorted by
    filename (which is the report's own date, `<date>.json`). Skips a file
    that fails to parse, or that parses but doesn't look like a real
    `DailyReport` (see `_looks_like_daily_report`), rather than aborting
    the whole dashboard over one bad report."""
    if not reports_dir.is_dir():
        return []
    reports = []
    for path in sorted(reports_dir.glob("*.json")):
        try:
            parsed = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # UnicodeDecodeError isn't an OSError subclass -- a file with
            # invalid UTF-8 bytes would otherwise propagate uncaught and
            # crash the whole dashboard over one corrupted report.
            continue
        if _looks_like_daily_report(parsed):
            reports.append(parsed)
    return reports


def return_pct(equity: Decimal, baseline: Decimal = INITIAL_EQUITY) -> Decimal | None:
    if baseline == 0:
        return None
    return (equity - baseline) / baseline * Decimal(100)


@dataclass
class LoopStatus:
    key: str
    display_name: str
    session: str
    alive: bool
    tick: TickStatus | None
    kill_switch_mentioned: bool
    daily_reports: list[dict[str, Any]]
    real_balance: dict[str, Decimal | None] | None = None


def gather_loop_status(config: LoopConfig) -> LoopStatus:
    pane = capture_pane(config.session)
    alive = session_alive(config.session)
    tick = parse_latest_tick(pane) if pane else None
    ks = kill_switch_mentioned(pane) if pane else False
    real_balance = parse_latest_vst_balance(pane) if (config.check_real_balance and pane) else None
    reports = load_daily_reports(config.reports_dir)
    return LoopStatus(config.key, config.display_name, config.session, alive, tick, ks, reports, real_balance)


def current_equity(status: LoopStatus) -> Decimal | None:
    """Prefers the live tmux-observed tick equity (most current); falls
    back to the last completed daily report's ending equity if the loop
    isn't currently observable (e.g. session died); None if neither is
    available."""
    if status.tick is not None and status.tick.equity is not None:
        return status.tick.equity
    if status.daily_reports:
        return _to_decimal(str(status.daily_reports[-1].get("ending_equity")))
    return None


def recent_trades(status: LoopStatus, limit: int = 10) -> list[dict[str, Any]]:
    """Flattens every completed day's `trades` list (each a serialized
    `Fill`) across `status.daily_reports`, newest-first by `filled_at`
    (safe as a plain string sort -- Jackson serializes `Instant` as
    fixed-width ISO-8601 UTC, which sorts lexicographically in
    chronological order), capped at `limit`.

    `Fill` (`engine.execution.Fill`) has no side/direction field in this
    codebase -- `side` lives on `Order` and is never copied onto the
    `Fill` a broker returns (confirmed by reading `PaperBroker.fill` and
    `ExchangeOrderExecutor`) -- so an entry here is price/quantity/
    notional/fee/time only, never "long" or "short". That is a real gap
    in what this codebase records per fill, not something this function
    can paper over.
    """
    trades: list[dict[str, Any]] = []
    for report in status.daily_reports:
        trades.extend(report.get("trades") or [])
    trades.sort(key=lambda t: t.get("filled_at") or "", reverse=True)
    return trades[:limit]


def latest_signal_decision(cron_log: Path) -> dict[str, str] | None:
    """Returns the most recent daily-signal-generation decision logged in
    `cron_log` -- either a HOLD ("no signal today") or a real emitted
    signal -- whichever line comes last in the file, win/lose independent
    of which pattern it is."""
    if not cron_log.is_file():
        return None
    try:
        text = cron_log.read_text(errors="replace")
    except OSError:
        # TOCTOU: log rotation/deletion between the is_file() check above
        # and this read. This is optional status info, not something worth
        # failing the whole dashboard over.
        return None
    latest = None
    for line in text.splitlines():
        m = CRON_WROTE_SIGNAL_RE.match(line)
        if m:
            latest = {"timestamp": m.group("ts"), "decision": "signal", "detail": m.group("detail")}
            continue
        m = CRON_NO_SIGNAL_RE.match(line)
        if m:
            latest = {"timestamp": m.group("ts"), "decision": "hold", "detail": "no sign-category change"}
    return latest


def tail_lines(path: Path, n: int = 8) -> list[str]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        # Same TOCTOU rationale as latest_signal_decision above.
        return []
    return text.splitlines()[-n:]


def _fmt_money(value: Decimal | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:,.4f}"


def _fmt_pct(value: Decimal | None) -> str:
    if value is None:
        return "unknown"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.4f}%"


def format_loop_section(status: LoopStatus) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"{status.display_name}  (tmux session: {status.session})")
    lines.append("=" * 78)
    lines.append(f"Status:      {'RUNNING' if status.alive else 'NOT RUNNING'}")

    if status.tick is not None:
        outcome = "ok" if status.tick.ok else f"ERROR: {status.tick.error}"
        lines.append(
            f"Last tick:   {status.tick.last_tick_at}  equity={_fmt_money(status.tick.equity)}  ({outcome})"
        )
    else:
        lines.append("Last tick:   no tick line found in current tmux scrollback")

    eq = current_equity(status)
    if eq is not None:
        lines.append(f"Return:      {_fmt_pct(return_pct(eq))}  ({_fmt_money(eq)} vs {_fmt_money(INITIAL_EQUITY)} baseline)")
    else:
        lines.append("Return:      unknown (no tick data or daily report available yet)")

    lines.append(
        "Kill switch: "
        + (
            "MENTIONED IN VISIBLE SCROLLBACK -- check logs"
            if status.kill_switch_mentioned
            else "no trip mentioned in visible scrollback"
        )
    )
    lines.append(f"Daily reports on record: {len(status.daily_reports)}")

    if status.real_balance is not None:
        rb = status.real_balance
        lines.append("")
        lines.append(
            "Real BingX VST balance (as of that session's own startup query -- NOT live-refreshed by this "
            "dashboard, see module docstring):"
        )
        lines.append(
            f"  balance={_fmt_money(rb['balance'])}  equity={_fmt_money(rb['equity'])}  "
            f"availableMargin={_fmt_money(rb['available_margin'])}  usedMargin={_fmt_money(rb['used_margin'])}  "
            f"unrealizedProfit={_fmt_money(rb['unrealized_profit'])}"
        )
        if eq is not None and rb["equity"] is not None:
            diff = rb["equity"] - eq
            diff_pct = ((rb["equity"] - eq) / eq * 100) if eq != 0 else None
            lines.append(
                f"  internal tracked equity ({_fmt_money(eq)}) vs real exchange equity "
                f"({_fmt_money(rb['equity'])}): {_fmt_money(diff)} ({_fmt_pct(diff_pct)} relative)"
            )
    elif status.key == "vst":
        lines.append("")
        lines.append(
            "Real BingX VST balance: not found in current tmux scrollback (VstPreflight only logs this once, "
            "at process startup -- it scrolls out of tmux's history buffer over time between restarts)."
        )

    if status.daily_reports:
        lines.append("")
        lines.append("-" * 78)
        lines.append("Daily equity trend")
        lines.append("-" * 78)
        lines.append(f"{'Date':<12}{'Start':>14}{'End':>14}{'Return':>12}{'Trades':>8}{'Ticks(ok/att)':>16}{'KillSw':>8}")
        for report in status.daily_reports:
            date = str(report.get("date", "?"))
            start = _to_decimal(str(report.get("starting_equity")))
            end = _to_decimal(str(report.get("ending_equity")))
            day_return = return_pct(end, start) if (start is not None and end is not None and start != 0) else None
            trades = len(report.get("trades") or [])
            ticks = f"{report.get('ticks_succeeded', '?')}/{report.get('ticks_attempted', '?')}"
            ks_flag = "yes" if report.get("kill_switch_tripped") else "no"
            lines.append(
                f"{date:<12}{_fmt_money(start):>14}{_fmt_money(end):>14}{_fmt_pct(day_return):>12}"
                f"{trades:>8}{ticks:>16}{ks_flag:>8}"
            )
        first_start = _to_decimal(str(status.daily_reports[0].get("starting_equity")))
        last_end = _to_decimal(str(status.daily_reports[-1].get("ending_equity")))
        if first_start is not None and last_end is not None and first_start != 0:
            lines.append(
                f"Cumulative ({status.daily_reports[0].get('date')} -> {status.daily_reports[-1].get('date')}): "
                f"{_fmt_pct(return_pct(last_end, first_start))}"
            )

        error_days = [(r.get("date"), r.get("errors")) for r in status.daily_reports if r.get("errors")]
        if error_days:
            lines.append("")
            lines.append("Tick errors (the 'Ticks(ok/att)' column above already reflects these as failed ticks):")
            for date, errors in error_days:
                lines.append(f"  {date}: {len(errors)} error(s) -- most recent: {errors[-1].get('message', '?')}")

        trades = recent_trades(status)
        lines.append("")
        lines.append("-" * 78)
        lines.append("Recent trades (newest first; Fill has no side/direction field -- see recent_trades() docstring)")
        lines.append("-" * 78)
        if trades:
            lines.append(f"{'filled_at':<32}{'price':>14}{'quantity':>12}{'notional':>14}{'fee':>10}")
            for t in trades:
                price = _to_decimal(str(t.get("price")))
                quantity = _to_decimal(str(t.get("quantity")))
                notional = _to_decimal(str(t.get("notional")))
                fee = _to_decimal(str(t.get("fee")))
                lines.append(
                    f"{str(t.get('filled_at', '?')):<32}{_fmt_money(price):>14}{_fmt_money(quantity):>12}"
                    f"{_fmt_money(notional):>14}{_fmt_money(fee):>10}"
                )
        else:
            lines.append("none in the completed daily reports on record.")
    else:
        lines.append("")
        lines.append(
            "No completed daily reports yet -- the loop hasn't crossed a UTC day boundary since it last "
            "started. Any trades from the still-in-progress day aren't visible here until that happens "
            "(DailyReportGenerator only finalizes/reports a day once it ends)."
        )

    return "\n".join(lines)


def format_dashboard(statuses: list[LoopStatus]) -> str:
    parts = [f"Paper trading dashboard -- generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}", ""]

    for status in statuses:
        parts.append(format_loop_section(status))
        parts.append("")

    parts.append("-" * 78)
    parts.append("Daily signal generation (cron: live.generate_daily_signal)")
    parts.append("-" * 78)
    decision = latest_signal_decision(CRON_LOG)
    if decision is None:
        parts.append("No decision found in var/live/cron.log yet.")
    elif decision["decision"] == "hold":
        parts.append(f"Latest decision: HOLD at {decision['timestamp']} ({decision['detail']})")
    else:
        parts.append(f"Latest decision: SIGNAL at {decision['timestamp']} -- {decision['detail']}")
    if SIGNAL_FILE.is_file():
        parts.append(f"Standing signal file present: {SIGNAL_FILE.relative_to(REPO_ROOT)}")
        try:
            parts.append(f"  {SIGNAL_FILE.read_text().strip()}")
        except OSError:
            pass
    else:
        # Deliberately states only the one thing actually observed --
        # no file at this path -- and nothing about order-intent state
        # more broadly. A missing file doesn't prove nothing is pending:
        # it could have already been consumed by the Java FileSignalSource
        # and be in flight through OMS/RiskGateway/Execution, which this
        # script has no visibility into (real CodeRabbit review finding).
        parts.append(f"Standing signal file: none present at {SIGNAL_FILE}")
    parts.append("")

    parts.append("-" * 78)
    parts.append("Recent watchdog restarts (var/live/watchdog.log, last 8)")
    parts.append("-" * 78)
    wd_tail = tail_lines(WATCHDOG_LOG, 8)
    no_entries_note = (
        "(no entries -- either nothing has ever needed restarting, or the watchdog isn't running; "
        "see docs/paper-trading-runbook.md section 5)"
    )
    parts.extend(wd_tail if wd_tail else [no_entries_note])

    return "\n".join(parts)


def _decimal_default(obj: Any) -> str:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"not JSON serializable: {obj!r}")


def to_json_dict(statuses: list[LoopStatus]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "loops": {},
    }
    for status in statuses:
        eq = current_equity(status)
        result["loops"][status.key] = {
            "display_name": status.display_name,
            "session": status.session,
            "alive": status.alive,
            "last_tick": (
                {
                    "last_tick_at": status.tick.last_tick_at,
                    "equity": status.tick.equity,
                    "ok": status.tick.ok,
                    "error": status.tick.error,
                }
                if status.tick is not None
                else None
            ),
            "current_equity": eq,
            "return_pct": return_pct(eq) if eq is not None else None,
            "kill_switch_mentioned_in_scrollback": status.kill_switch_mentioned,
            "real_balance": status.real_balance,
            "recent_trades": recent_trades(status),
            "daily_reports": status.daily_reports,
        }
    result["latest_signal_decision"] = latest_signal_decision(CRON_LOG)
    result["watchdog_log_tail"] = tail_lines(WATCHDOG_LOG, 8)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output instead of the human-readable report")
    args = parser.parse_args(argv)

    statuses = [gather_loop_status(config) for config in LOOPS]

    if args.json:
        print(json.dumps(to_json_dict(statuses), indent=2, default=_decimal_default))
    else:
        print(format_dashboard(statuses))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
