"""`streamlit run live/web_dashboard.py` -- graphical, auto-refreshing
paper-trading dashboard.

A visual companion to `python -m live.dashboard` (the plain-text/JSON
snapshot tool), not a replacement -- this reuses `live.dashboard`'s own
data-gathering functions (`gather_loop_status`, `current_equity`,
`recent_trades`, `latest_signal_decision`, `tail_lines`, ...) rather than
re-parsing anything itself, so there is exactly one place that knows how to
read a `tmux` pane or a `DailyReport` JSON file. Same read-only guarantee as
that module: makes no exchange call and no order-placing call of its own:
this dashboard, like the CLI one, is a pure spectator.

Iterates `live.dashboard.LOOPS` (a list of `LoopConfig`) rather than naming
`simulated`/`vst` individually -- a future loop (a different symbol, asset
class, or venue, e.g. a KR/US equities loop per CLAUDE.md's long-term
multi-asset target) is one more `LoopConfig` entry in `dashboard.py`, not a
change to this file.

Run (from repo root):
    cd python && .venv/bin/streamlit run live/web_dashboard.py

Binds to 127.0.0.1 only (see `python/.streamlit/config.toml`) -- never
reachable over the network. Auto-refreshes the whole page every
`REFRESH_SECONDS` via a plain HTML meta-refresh tag, deliberately not a
`time.sleep()` + `st.rerun()` loop: this is a personal, single-user local
tool, and a meta-refresh can't leave a runaway background loop behind if a
browser tab is left open. The tradeoff (a full page reload each cycle, a
small flicker) is fine at this scale.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import streamlit as st

from live import dashboard

REFRESH_SECONDS = 30


def _fmt_pct(value: Decimal | None) -> str:
    if value is None:
        return "unknown"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def day_over_day(status: dashboard.LoopStatus) -> tuple[Decimal | None, Decimal | None]:
    """Returns (current_equity, day_over_day_pct).

    "Day over day" here means versus the last *completed* daily report's
    ending equity, not versus `INITIAL_EQUITY` (that comparison is the
    separate "cumulative return" figure, from `dashboard.return_pct`).
    Before any daily report exists yet (a freshly (re)started loop, still
    mid-way through its first day), there is no real "yesterday" to compare
    against -- falls back to `INITIAL_EQUITY` in that case, which makes the
    delta trivially match the cumulative-return figure until the first
    report lands, rather than showing a misleading `None`.
    """
    current = dashboard.current_equity(status)
    if current is None:
        return None, None
    if status.daily_reports:
        reference = dashboard._to_decimal(str(status.daily_reports[-1].get("ending_equity")))
    else:
        reference = dashboard.INITIAL_EQUITY
    if reference is None or reference == 0:
        return current, None
    return current, (current - reference) / reference * Decimal(100)


def equity_history(status: dashboard.LoopStatus) -> pd.DataFrame | None:
    """One row per completed daily report, indexed by date -- the same
    `ending_equity` series `format_loop_section`'s "Daily equity trend"
    table already renders as text, here as a chart instead. `None` (not an
    empty DataFrame) when there's nothing to plot yet, so callers can tell
    "no data" from "empty chart" without inspecting `.empty`."""
    if not status.daily_reports:
        return None
    rows = []
    for report in status.daily_reports:
        equity = dashboard._to_decimal(str(report.get("ending_equity")))
        if equity is None:
            continue
        rows.append({"date": report.get("date"), "equity": float(equity)})
    if not rows:
        return None
    return pd.DataFrame(rows).set_index("date")


def render_loop(config: dashboard.LoopConfig, status: dashboard.LoopStatus) -> None:
    header = f"{config.display_name}  ({config.symbol})"
    st.subheader(f"🟢 {header} -- RUNNING" if status.alive else f"🔴 {header} -- NOT RUNNING")

    current, dod_pct = day_over_day(status)
    cum_pct = dashboard.return_pct(current) if current is not None else None

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(
        "Equity",
        f"{current:,.2f}" if current is not None else "unknown",
        delta=(
            f"{_fmt_pct(dod_pct)} ({'전일 대비' if status.daily_reports else 'baseline 대비'})"
            if dod_pct is not None
            else None
        ),
    )
    col2.metric("누적 수익률 (baseline 대비)", _fmt_pct(cum_pct))
    # `kill_switch_mentioned=False` means "not seen in the visible tmux
    # scrollback" -- that's also what a dead/unreadable session pane looks
    # like (capture_pane returns None), not proof the kill switch is
    # actually fine. Showing a green "정상" for that case would overclaim
    # certainty this dashboard doesn't have -- real CodeRabbit review finding.
    col3.metric(
        "Kill Switch",
        "🔴 로그 언급 감지 / 확인 필요" if status.kill_switch_mentioned else "⚪ 로그에서 언급 없음",
    )
    col4.metric("완료된 일별 리포트", str(len(status.daily_reports)))

    if status.real_balance is not None:
        rb = status.real_balance
        st.caption(
            f"실 BingX VST 잔고 (세션 시작 시점 기준, 실시간 갱신 아님): balance={rb['balance']} "
            f"equity={rb['equity']} availableMargin={rb['available_margin']} usedMargin={rb['used_margin']}"
        )

    history = equity_history(status)
    if history is not None:
        st.line_chart(history)
    else:
        st.caption("아직 완료된 일별 리포트가 없어 차트를 그릴 데이터가 없습니다 (UTC 하루가 아직 안 지남).")

    trades = dashboard.recent_trades(status)
    if trades:
        st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)
    else:
        st.caption("기록된 최근 체결 없음.")


def main() -> None:
    st.set_page_config(page_title="Paper Trading Dashboard", layout="wide")
    st.markdown(f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">', unsafe_allow_html=True)

    st.title("Paper Trading Dashboard")
    st.caption(
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} -- auto-refreshes every "
        f"{REFRESH_SECONDS}s. Read-only, makes no exchange call of its own -- reads the same already-existing "
        "data files as `python -m live.dashboard` (see docs/paper-trading-runbook.md)."
    )

    for config in dashboard.LOOPS:
        status = dashboard.gather_loop_status(config)
        render_loop(config, status)
        st.divider()

    st.subheader("일별 시그널 생성 (cron: live.generate_daily_signal)")
    decision = dashboard.latest_signal_decision(dashboard.CRON_LOG)
    if decision is None:
        st.caption("var/live/cron.log 에서 아직 결정 내역을 찾지 못했습니다.")
    elif decision["decision"] == "hold":
        st.caption(f"최근 결정: HOLD ({decision['timestamp']}, {decision['detail']})")
    else:
        st.caption(f"최근 결정: SIGNAL ({decision['timestamp']}) -- {decision['detail']}")

    st.subheader("최근 watchdog 재시작 내역")
    wd_tail = dashboard.tail_lines(dashboard.WATCHDOG_LOG, 8)
    if wd_tail:
        st.code("\n".join(wd_tail), language=None)
    else:
        st.caption("기록 없음 (계속 정상이었거나, watchdog 자체가 안 돌고 있을 수 있음 -- runbook 5절 참고).")


if __name__ == "__main__":
    # Streamlit's own script runner does set __name__ to "__main__" for the
    # entrypoint script given to `streamlit run` (confirmed against
    # Streamlit's own source during review of this PR) -- this guard is
    # what keeps a plain `from live.web_dashboard import day_over_day`
    # (as tests/test_web_dashboard.py does) from also triggering a full
    # dashboard render -- including real tmux subprocess calls for both
    # loops -- as an import side effect. Real CodeRabbit review finding.
    main()
