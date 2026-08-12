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

# Validated tokens from the dataviz skill's reference palette
# (references/palette.md) -- consumed as-is, not re-derived, matching
# .streamlit/config.toml's [theme] section so the badges below and
# Streamlit's own chrome (st.line_chart's line color included, via
# primaryColor) read as one palette. Status colors are fixed/never
# themed by design: `good`/`critical` map to a loop's own alive state,
# `serious` to "needs a human look" (kill switch mentioned in the
# visible log) -- deliberately NOT `critical`, since a mention is not
# proof of an actual trip, just something worth checking (see
# render_loop's own comment on this same distinction, carried over from
# the original CodeRabbit finding this dashboard already had to
# address once).
_STATUS_GOOD = "#0ca30c"
_STATUS_SERIOUS = "#ec835a"
_STATUS_CRITICAL = "#d03b3b"


def _badge(color: str, label: str) -> str:
    """A small inline pill: colored dot + label, never color alone --
    same "icon/shape + text, not hue alone" rule the skill's status
    palette requires. `color + "1a"` appends ~10% alpha for the tinted
    background (color is always a 6-hex-digit constant above, never
    user input, so this string-append is safe here).
    """
    return (
        f'<span style="display:inline-flex;align-items:center;gap:6px;'
        f'padding:3px 12px;border-radius:999px;background:{color}1a;'
        f'color:{color};font-weight:600;font-size:0.85rem;white-space:nowrap;">'
        f'<span style="width:8px;height:8px;border-radius:50%;background:{color};'
        f'flex-shrink:0;"></span>{label}</span>'
    )


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
    """One loop's card. Wrapped in `st.container(border=True)` by the
    caller (`main`) rather than in here, so the border spans everything
    `main` places inside it (including anything a future loop-specific
    addition might append) -- keeping the "where does the card start
    and end" decision at the call site.
    """
    st.subheader(config.display_name)
    st.caption(config.symbol)
    st.markdown(
        _badge(_STATUS_GOOD, "운영 중") if status.alive else _badge(_STATUS_CRITICAL, "중단됨"),
        unsafe_allow_html=True,
    )

    current, dod_pct = day_over_day(status)
    cum_pct = dashboard.return_pct(current) if current is not None else None

    col1, col2, col3 = st.columns(3)
    col1.metric(
        "자산",
        f"{current:,.2f}" if current is not None else "알 수 없음",
        delta=(
            f"{_fmt_pct(dod_pct)} ({'전일 대비' if status.daily_reports else 'baseline 대비'})"
            if dod_pct is not None
            else None
        ),
    )
    col2.metric("누적 수익률 (baseline 대비)", _fmt_pct(cum_pct))
    col3.metric("완료된 일별 리포트", str(len(status.daily_reports)))

    # `kill_switch_mentioned=False` means "not seen in the visible tmux
    # scrollback" -- that's also what a dead/unreadable session pane
    # looks like (capture_pane returns None), not proof the kill switch
    # is actually fine. `serious` (amber/orange), not `critical` (red):
    # a mention is "worth a human look," not itself proof of a trip --
    # overclaiming certainty here was a real CodeRabbit review finding
    # on the original version of this dashboard.
    if status.kill_switch_mentioned:
        st.markdown(_badge(_STATUS_SERIOUS, "킬스위치 언급 감지 -- 확인 필요"), unsafe_allow_html=True)
    else:
        st.caption("킬스위치: 로그에서 언급 없음")

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
        f"생성 시각 {datetime.now(timezone.utc).isoformat(timespec='seconds')} -- {REFRESH_SECONDS}초마다 "
        "자동 새로고침됩니다. 읽기 전용이며 거래소 호출을 직접 하지 않습니다 -- `python -m live.dashboard`와 "
        "동일한 기존 데이터 파일만 읽습니다 (docs/paper-trading-runbook.md 참고)."
    )

    # Side by side, not stacked -- `layout="wide"` already claims the
    # full browser width, and comparing the two loops at a glance is
    # the actual point of a dashboard. Generalizes to any number of
    # `dashboard.LOOPS` entries automatically (a future loop is one
    # more column, not a layout rewrite -- see dashboard.py's own
    # "one entry per loop" design note).
    columns = st.columns(len(dashboard.LOOPS))
    for column, config in zip(columns, dashboard.LOOPS):
        with column, st.container(border=True):
            status = dashboard.gather_loop_status(config)
            render_loop(config, status)

    st.divider()

    footer_left, footer_right = st.columns(2)
    with footer_left, st.container(border=True):
        st.subheader("일별 시그널 생성")
        st.caption("cron: live.generate_daily_signal")
        decision = dashboard.latest_signal_decision(dashboard.CRON_LOG)
        if decision is None:
            st.caption("var/live/cron.log 에서 아직 결정 내역을 찾지 못했습니다.")
        elif decision["decision"] == "hold":
            st.caption(f"최근 결정: HOLD ({decision['timestamp']}, {decision['detail']})")
        else:
            st.caption(f"최근 결정: SIGNAL ({decision['timestamp']}) -- {decision['detail']}")

    with footer_right, st.container(border=True):
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
