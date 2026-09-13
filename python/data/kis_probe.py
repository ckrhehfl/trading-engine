"""Task MS-B Phase 0 probes: what does KIS actually serve for history?

Read-only, exploratory, and deliberately **not** a data pipeline. It
answers the six unknowns in `.planning/ms-a-multi-asset-universe-discuss.md`
§5 and writes nothing to the kline store. `kis_klines.py` (Task MS-C) is
the pipeline, and it gets written only after this reports.

**This module cannot place an order and must stay that way.** It knows
only quotation TR ids, sends no account number, and imports nothing from
the trading path. That is the constraint under which running a
credentialed client against KIS at all was proposed (§7 decision 2), so
adding an order path here would silently void it.

Credentials come from the environment (`KIS_APP_KEY` / `KIS_APP_SECRET`)
and are never logged, echoed, or written to the report -- presence and
length only, following this project's own handling after the CRLF
credential incident (CLAUDE.md, "A real credential-handling incident").

Run:

    python -m data.kis_probe --host paper
    python -m data.kis_probe --host real     # only if paper fails; see §7
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# Both hosts are named here because probe 2 is *specifically* the question
# of which one serves quotations. Unlike the Java trading path -- where
# KIS_PAPER_BASE_URL is a hardcoded constant with no configuration surface
# on purpose -- a read-only probe has to be able to ask both. It still
# cannot trade against either.
PAPER_HOST = "https://openapivts.koreainvestment.com:29443"
REAL_HOST = "https://openapi.koreainvestment.com:9443"

TOKEN_PATH = "/oauth2/tokenP"
DAILY_ITEM_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
DAILY_INDEX_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"

TR_DAILY_ITEM = "FHKST03010100"
TR_DAILY_INDEX = "FHKUP03500100"

# 0 = 수정주가 (split-adjusted), 1 = 원주가 (raw). KIS's own published
# Python sample defaults to "1", which is the trap in §5 item 6: copying
# the official example silently yields unadjusted prices. Probe 4 calls
# both across a known split and compares, rather than trusting either.
ADJUSTED = "0"
RAW = "1"

SAMSUNG = "005930"  # 삼성전자 -- 50:1 split effective 2018-05-04
SK_HYNIX = "000660"  # SK하이닉스
# KOSPI200 and KOSDAQ150 are KR-10 members (MS-A section 4.2), so their
# data is *required*, not nice to have. KOSPI/KOSDAQ are context only. The
# probe distinguishes the two: a required index failing is a no-go.
REQUIRED_INDEX_CODES = {"2001": "KOSPI200", "2003": "KOSDAQ150"}
CONTEXT_INDEX_CODES = {"0001": "KOSPI", "1001": "KOSDAQ"}

TIMEOUT_S = 20.0  # KIS paper latency is a real 7-10s (CLAUDE.md)


class ProbeError(RuntimeError):
    """A probe could not complete. Carries no response body -- a real KIS
    response embeds account numbers and balances, and this project already
    has a rule against putting those in an exception message."""


@dataclass
class Finding:
    name: str
    ok: bool
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


def _post_json(url: str, body: dict[str, str], headers: dict[str, str]) -> dict[str, Any]:
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=raw, method="POST")
    req.add_header("content-type", "application/json; charset=utf-8")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str, params: dict[str, str], headers: dict[str, str]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{query}", method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


TOKEN_CACHE = pathlib.Path("/tmp/kis_probe_token.json")
TOKEN_REUSE_S = 3000.0  # KIS access tokens live far longer; this is just prudence


def issue_token(host: str, app_key: str, app_secret: str, *, use_cache: bool = True) -> str:
    """`POST /oauth2/tokenP`, cached on disk between runs.

    The cache is not a convenience. The endpoint rate-limits hard --
    `EGW00133`, observed as an `HTTP 403` after three issuances within a
    few minutes on 2026-09-13 -- and the allowance is **per app key**,
    shared with the live `kis-paper` JVM. A research script that burns it
    can stop a running loop from renewing its own token. Reuse one token
    per session, and per backfill.
    """
    if use_cache and TOKEN_CACHE.exists():
        try:
            blob = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            if time.time() - float(blob["at"]) < TOKEN_REUSE_S and blob.get("host") == host:
                return str(blob["token"])
        except (ValueError, KeyError, OSError):
            pass  # a corrupt cache is not a reason to fail; just re-issue
    payload = _post_json(
        host + TOKEN_PATH,
        {"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret},
        {},
    )
    token = payload.get("access_token")
    if not token:
        # Report the error *code*, never the body.
        raise ProbeError(f"no access_token in token response (code={payload.get('error_code')})")
    if use_cache:
        try:
            TOKEN_CACHE.write_text(
                json.dumps({"token": str(token), "at": time.time(), "host": host}),
                encoding="utf-8",
            )
            TOKEN_CACHE.chmod(0o600)
        except OSError:
            pass  # an uncacheable token still works for this run
    return str(token)


def _quote_headers(token: str, app_key: str, app_secret: str, tr_id: str) -> dict[str, str]:
    return {
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": tr_id,
        "custtype": "P",
    }


def fetch_daily(
    host: str,
    token: str,
    app_key: str,
    app_secret: str,
    *,
    code: str,
    start: str,
    end: str,
    adjusted: str = ADJUSTED,
    is_index: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One `inquire-daily-*chartprice` call. Returns `(envelope, output2)`.

    Documented cap is 100 rows per call, so the caller pages by date. That
    cap is itself probe 3's subject -- BingX's `limit` silently capped
    rather than erroring, and nothing says KIS behaves differently.
    """
    path = DAILY_INDEX_PATH if is_index else DAILY_ITEM_PATH
    tr = TR_DAILY_INDEX if is_index else TR_DAILY_ITEM
    params = {
        "FID_COND_MRKT_DIV_CODE": "U" if is_index else "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": "D",
    }
    if not is_index:
        params["FID_ORG_ADJ_PRC"] = adjusted
    payload = _get_json(
        host + path, params, _quote_headers(token, app_key, app_secret, tr)
    )
    rows = payload.get("output2") or []
    if not isinstance(rows, list):
        rows = []
    return payload, [r for r in rows if isinstance(r, dict) and r.get("stck_bsop_date")]


# ---------------------------------------------------------------- probes


def probe_endpoint_exists(host: str, tok: str, key: str, sec: str) -> Finding:
    """§5 item 1 -- does the stock daily endpoint answer at all?"""
    try:
        env, rows = fetch_daily(
            host, tok, key, sec, code=SAMSUNG, start="20240502", end="20240605"
        )
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        return Finding("endpoint_exists", False, f"transport failure: {type(exc).__name__}")
    rc, msg = env.get("rt_cd"), env.get("msg1", "")
    if rc != "0":
        return Finding("endpoint_exists", False, f"rt_cd={rc} msg_cd={env.get('msg_cd')} {msg}")
    return Finding(
        "endpoint_exists",
        bool(rows),
        f"rt_cd=0, {len(rows)} rows, fields={sorted(rows[0])[:8] if rows else '[]'}",
        {"row_count": len(rows), "sample_fields": sorted(rows[0]) if rows else []},
    )


def probe_row_cap(host: str, tok: str, key: str, sec: str) -> Finding:
    """§5 item 1 -- is the 100-row cap real, and does it cap silently?

    BingX silently capped an over-limit `limit`; Binance futures returned a
    real HTTP 400 for the same thing. Two venues, two behaviours, so this
    is measured rather than assumed.
    """
    try:
        env, rows = fetch_daily(
            host, tok, key, sec, code=SAMSUNG, start="20200101", end="20241231"
        )
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        return Finding("row_cap", False, f"transport failure: {type(exc).__name__}")
    # A KIS application error arrives as HTTP 200 with rt_cd != "0" and an
    # empty output2. Reporting that as "silently capped" would turn a wrong
    # TR or parameter into a finding about pagination.
    if env.get("rt_cd") != "0":
        return Finding(
            "row_cap", False,
            f"application error, not a cap: rt_cd={env.get('rt_cd')} msg_cd={env.get('msg_cd')}",
        )
    if not rows:
        return Finding("row_cap", False, "rt_cd=0 but zero rows -- cannot infer a cap")
    return Finding(
        "row_cap",
        True,
        f"a ~5-year request returned {len(rows)} rows "
        f"({'silently capped' if len(rows) <= 100 else 'more than 100 -- cap is not 100'})",
        {"rows_for_5y_request": len(rows)},
    )


def probe_history_depth(host: str, tok: str, key: str, sec: str) -> Finding:
    """§5 item 3 -- the highest-leverage unknown. Walks backwards a year at
    a time until a request comes back empty, then reports the earliest bar
    seen and the detection floor that span implies."""
    import datetime as dt
    import math

    earliest: str | None = None
    year = dt.date.today().year
    misses = 0
    while year > 1990 and misses < 2:
        start, end = f"{year}0101", f"{year}1231"
        try:
            _, rows = fetch_daily(host, tok, key, sec, code=SAMSUNG, start=start, end=end)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            break
        dates = sorted(r["stck_bsop_date"] for r in rows)
        if dates:
            earliest, misses = dates[0], 0
        else:
            misses += 1
        year -= 1
        time.sleep(0.2)  # be polite; KIS rate limits are real
    if not earliest:
        return Finding("history_depth", False, "no dated rows returned for any probed year")
    years = (dt.date.today() - dt.date(int(earliest[:4]), int(earliest[4:6]), int(earliest[6:]))).days / 365.25
    floor = 1.6449 / math.sqrt(years) if years > 0 else float("inf")
    return Finding(
        "history_depth",
        True,
        f"earliest bar {earliest} -> {years:.2f} years -> detection floor {floor:.3f} "
        f"(BTC 1d holdout was 0.96; this project's best is 0.62)",
        {"earliest": earliest, "years": round(years, 2), "detection_floor": round(floor, 3)},
    )


def probe_adjustment(host: str, tok: str, key: str, sec: str) -> Finding:
    """§5 item 6 -- the one most likely to produce a wrong answer that looks
    right. Samsung's 50:1 split took effect 2018-05-04. Adjusted and raw
    must differ across it by roughly 50x, and the *raw* series is the one
    that shows a ~-98% single-day move."""
    out: dict[str, Any] = {}
    for label, flag in (("adjusted", ADJUSTED), ("raw", RAW)):
        try:
            _, rows = fetch_daily(
                host, tok, key, sec,
                code=SAMSUNG, start="20180425", end="20180515", adjusted=flag,
            )
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            return Finding("adjustment", False, f"{label}: transport failure: {type(exc).__name__}")
        closes = {r["stck_bsop_date"]: r.get("stck_clpr") for r in rows}
        out[label] = closes
    pre = [d for d in sorted(out["adjusted"]) if d < "20180504"]
    post = [d for d in sorted(out["adjusted"]) if d >= "20180504"]
    if not pre or not post:
        return Finding("adjustment", False, "the split window returned no rows on one side")

    def _ratio(series: dict[str, Any]) -> float | None:
        try:
            return float(series[pre[-1]]) / float(series[post[0]])
        except (TypeError, ValueError, ZeroDivisionError, KeyError):
            return None

    adj_r, raw_r = _ratio(out["adjusted"]), _ratio(out["raw"])
    # BOTH legs are required. Checking only the adjusted one would pass even
    # if KIS ignored FID_ORG_ADJ_PRC entirely and served the adjusted series
    # for both requests -- in which case the parameter is not doing what the
    # pipeline will assume it does, which is the whole point of asking.
    adj_ok = adj_r is not None and 0.5 < adj_r < 2.0
    raw_ok = raw_r is not None and 25.0 < raw_r < 100.0
    ok = adj_ok and raw_ok
    if adj_ok and not raw_ok:
        verdict = ("adjusted behaves, but the RAW leg does not show the 50:1 step -- "
                   "FID_ORG_ADJ_PRC may be ignored, so the flag cannot be relied on")
    elif ok:
        verdict = "FID_ORG_ADJ_PRC distinguishes the two as documented"
    else:
        verdict = "DOES NOT MATCH -- do not build on this until resolved"
    return Finding(
        "adjustment",
        ok,
        f"close ratio across 2018-05-04 -- adjusted={adj_r}, raw={raw_r}. "
        f"Adjusted should be near 1.0 and raw near 50.0. {verdict}",
        {"adjusted_ratio": adj_r, "raw_ratio": raw_r, "adjusted_ok": adj_ok, "raw_ok": raw_ok},
    )


def probe_indices(host: str, tok: str, key: str, sec: str) -> Finding:
    """§5 item 1 -- the index endpoint is materially less well attested than
    the stock one.

    KOSPI200 and KOSDAQ150 are KR-10 members, so **every** required index
    must answer. An `any()` here would let the probe pass with no signal
    data for a constituent the universe depends on.
    """
    results: dict[str, str] = {}
    required_ok = True
    for code, name in {**REQUIRED_INDEX_CODES, **CONTEXT_INDEX_CODES}.items():
        required = code in REQUIRED_INDEX_CODES
        try:
            env, rows = fetch_daily(
                host, tok, key, sec,
                code=code, start="20240502", end="20240605", is_index=True,
            )
            good = env.get("rt_cd") == "0" and bool(rows)
            results[name] = f"rt_cd={env.get('rt_cd')} rows={len(rows)}"
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            good = False
            results[name] = f"failed: {type(exc).__name__}"
        if required and not good:
            required_ok = False
        time.sleep(0.3)
    missing = [n for c, n in REQUIRED_INDEX_CODES.items() if "rows=0" in results.get(n, "") or "failed" in results.get(n, "")]
    detail = "; ".join(f"{k}: {v}" for k, v in results.items())
    if not required_ok:
        detail += f" -- REQUIRED index unavailable: {', '.join(missing)}"
    return Finding("indices", required_ok, detail, results)



# Real KRX closures in 2024-01-01..2024-04-30, from the published KRX
# calendar. Committed as a fixture so the probe compares against an
# independent source rather than against its own weekday arithmetic --
# comparing a series to a count derived from that same series cannot
# detect a bar KIS simply failed to return.
KRX_CLOSURES_2024_Q1 = ("20240101", "20240209", "20240212", "20240301", "20240410")


def probe_calendar(host: str, tok: str, key: str, sec: str) -> Finding:
    """§5 item 4 -- the real trading calendar, checked two independent ways.

    A five-day Chuseok closure is not a data gap and the pipeline must be
    able to tell the difference, so `verify_known_gaps` needs a true
    calendar to check against.

    Two comparisons, because each catches what the other cannot:

    1. **Against the committed KRX fixture** -- catches a bar KIS failed to
       return on a day the market was genuinely open.
    2. **Against the KOSPI index's own dates** -- the index trades whenever
       the market does, so a date present there and absent for the stock is
       a *stock-specific* halt, not a market closure. Counting those as
       closures is exactly the conflation this probe must not make.
    """
    import datetime as dt

    try:
        env, rows = fetch_daily(host, tok, key, sec, code=SAMSUNG,
                                start="20240101", end="20240430")
        ienv, irows = fetch_daily(host, tok, key, sec, code="0001",
                                  start="20240101", end="20240430", is_index=True)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        return Finding("calendar", False, f"transport failure: {type(exc).__name__}")
    if env.get("rt_cd") != "0" or ienv.get("rt_cd") != "0":
        return Finding("calendar", False,
                       f"rt_cd stock={env.get('rt_cd')} index={ienv.get('rt_cd')}")

    stock = {r["stck_bsop_date"] for r in rows}
    index = {r["stck_bsop_date"] for r in irows}
    if not stock or not index:
        return Finding("calendar", False, "one of the two series came back empty")

    lo, hi = max(min(stock), min(index)), min(max(stock), max(index))
    span = lambda s: {d for d in s if lo <= d <= hi}
    stock, index = span(stock), span(index)

    def _weekdays(a: str, b: str) -> set[str]:
        d0 = dt.date(int(a[:4]), int(a[4:6]), int(a[6:]))
        d1 = dt.date(int(b[:4]), int(b[4:6]), int(b[6:]))
        out = set()
        for i in range((d1 - d0).days + 1):
            d = d0 + dt.timedelta(days=i)
            if d.weekday() < 5:
                out.add(d.strftime("%Y%m%d"))
        return out

    weekdays = _weekdays(lo, hi)
    expected_open = weekdays - set(KRX_CLOSURES_2024_Q1)
    missing_vs_fixture = sorted(expected_open - index)
    unexpected_open = sorted(index - expected_open)
    halted = sorted(index - stock)  # market open, this stock did not print

    ok = not missing_vs_fixture and not unexpected_open
    detail = (
        f"index {len(index)} days vs {len(expected_open)} expected open "
        f"({lo}..{hi}); missing_vs_KRX_fixture={missing_vs_fixture or 'none'}; "
        f"open_but_not_in_fixture={unexpected_open or 'none'}; "
        f"stock-specific non-printing days={halted or 'none'} (informational)"
    )
    return Finding("calendar", ok, detail, {
        "index_days": len(index), "expected_open": len(expected_open),
        "missing_vs_fixture": missing_vs_fixture,
        "unexpected_open": unexpected_open,
        "stock_specific_missing": halted,
    })


def probe_single_stock_futures(host: str, tok: str, key: str, sec: str) -> Finding:
    """§5 item 5 -- single-stock futures: do they answer, and how far back?

    Deliberately included even though the endpoint family is uncertain.
    Omitting it would let `main()` print "all probes ok" while the fact
    that decides whether KR-10 is *tradeable at all* went unasked -- the
    "evidence that cannot fail" pattern from CLAUDE.md's change checks.

    Reported as a real finding either way. A failure here does not mean
    single-stock futures are unavailable; it means this probe did not
    establish them, and MS-C must.
    """
    path = "/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice"
    # Samsung Electronics single-stock future, front month. KRX's own code
    # shape for these is 1AAxxx / 111xxx depending on vintage; both are
    # tried rather than guessed at once.
    for tr in ("FHKIF03020100", "FHKST03010100"):
        for code in ("101S12", "1AA000"):
            try:
                payload = _get_json(
                    host + path,
                    {
                        "FID_COND_MRKT_DIV_CODE": "F",
                        "FID_INPUT_ISCD": code,
                        "FID_INPUT_DATE_1": "20240502",
                        "FID_INPUT_DATE_2": "20240605",
                        "FID_PERIOD_DIV_CODE": "D",
                    },
                    _quote_headers(tok, key, sec, tr),
                )
                rows = payload.get("output2") or []
                if payload.get("rt_cd") == "0" and rows:
                    return Finding(
                        "single_stock_futures", True,
                        f"answered for tr={tr} code={code}, {len(rows)} rows",
                        {"tr_id": tr, "code": code, "rows": len(rows)},
                    )
            except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                pass
            time.sleep(0.3)
    return Finding(
        "single_stock_futures", False,
        "no (tr_id, code) combination tried returned rows. NOT a finding that "
        "single-stock futures are unavailable -- the endpoint family and symbol "
        "format are both unconfirmed. MS-C must establish them, and section 2.1's "
        "tradeable window depends on it.",
    )


PROBES = (
    probe_endpoint_exists,
    probe_row_cap,
    probe_history_depth,
    probe_adjustment,
    probe_indices,
    probe_calendar,
    probe_single_stock_futures,
)

# MS-A section 5 items this probe does NOT settle. Printed with the summary
# so "N/N ok" can never be read as a full go decision.
NOT_COVERED = (
    "item 5 (partial): single-stock-futures contract multiplier, and "
    "per-underlying futures liquidity -- only endpoint reachability is probed",
    "item 7: a point-in-time 2018 listing / futures-eligibility universe. "
    "Section 4.4's ranking dependency is closed by acml_tr_pbmn, but the "
    "candidate-pool dependency is not.",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("paper", "real"), default="paper")
    args = parser.parse_args(argv)

    key, sec = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not key or not sec:
        print(
            "KIS_APP_KEY / KIS_APP_SECRET are not set in this environment.\n"
            "Export them for this shell only; this script never writes or logs them.",
            file=sys.stderr,
        )
        return 2
    # Presence and length only -- never the value. See the module docstring.
    print(f"credentials: KIS_APP_KEY len={len(key)}, KIS_APP_SECRET len={len(sec)}")

    host = PAPER_HOST if args.host == "paper" else REAL_HOST
    print(f"host: {args.host} ({host})\n")

    try:
        token = issue_token(host, key, sec)
    except (ProbeError, urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        print(f"token issuance failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "\nEGW00133 means the token endpoint's own rate limit -- wait 60-90s and retry.",
            file=sys.stderr,
        )
        return 1
    print("token: ready\n")

    findings = []
    for probe in PROBES:
        finding = probe(host, token, key, sec)
        findings.append(finding)
        print(f"[{'ok ' if finding.ok else 'FAIL'}] {finding.name}: {finding.detail}\n")
        time.sleep(0.3)

    failed = [f.name for f in findings if not f.ok]
    print("=" * 70)
    print(f"{len(findings) - len(failed)}/{len(findings)} probes ok")
    if failed:
        print(f"failed: {', '.join(failed)}")
    print("\nNOT settled by this run -- a clean summary above is not a full go:")
    for item in NOT_COVERED:
        print(f"  - {item}")
    print("\nJSON:")
    print(json.dumps({f.name: {"ok": f.ok, **f.data} for f in findings}, indent=2, ensure_ascii=False))
    # Fail closed. A go/no-go script that always exits 0 lets automation read
    # a failed gate as a passed one -- change_check's `check_script_fails_closed`.
    return 0 if not failed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
