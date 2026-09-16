"""Tests for `data.krx_quote_sampler`.

The sampler exists to replace rd-q's single closing snapshot, so the
properties that matter are the ones that would quietly make a pooled
median wrong:

- **Off-hours samples must not enter the pool.** There is no continuous
  book outside 09:00–15:20 KST, and KIS still answers with the last one —
  so a cron tick at 16:00 returns plausible numbers from a different
  market state.
- **Primitives, not the spread.** A stored spread cannot answer how much
  size was there, which is the whole gap this closes.
- **A half-book is not a book.** A price with no size yields neither a
  spread nor a depth figure.
- **The futures field map is asymmetric** — `futs_`-prefixed prices,
  unprefixed quantities — and the symmetric guess returns `None`.
"""

from __future__ import annotations

import datetime as dt
import types

import pytest

from data.kis_futures import FuturesMaster
from data.krx_quote_sampler import (
    FUTURES_BLOCK,
    FUTURES_PREFIX,
    SPOT_BLOCK,
    KST,
    METRIC_PREFIX,
    PERIOD,
    SPOT_PREFIX,
    QuoteSamplerError,
    _positive,
    front_month,
    in_session,
    rows_for,
    sample_book,
    sample_once,
)

_SESSION = types.SimpleNamespace(host="https://example.invalid", headers=lambda tr: {})


def _serving(monkeypatch, payload):
    monkeypatch.setattr(
        "data.krx_quote_sampler._get_with_retry", lambda url, headers: payload
    )


def _spot_block(ask="253500", bid="253000", ask_q="50157", bid_q="41329"):
    return {"askp1": ask, "bidp1": bid, "askp_rsqn1": ask_q, "bidp_rsqn1": bid_q}


def _futures_block(ask="250500", bid="250000", ask_q="5274", bid_q="269"):
    # The real shape, measured 2026-09-16: prices carry `futs_`, the
    # quantities beside them do not.
    return {
        "futs_askp1": ask, "futs_bidp1": bid,
        "askp_rsqn1": ask_q, "bidp_rsqn1": bid_q,
    }


def _spot(monkeypatch, block=None):
    _serving(monkeypatch, {"rt_cd": "0", "output1": block or _spot_block()})
    return sample_book(_SESSION, "/p", "TR", "J", "005930", "", "", SPOT_BLOCK)


def _futures(monkeypatch, block=None):
    _serving(monkeypatch, {"rt_cd": "0", "output2": block or _futures_block()})
    return sample_book(_SESSION, "/p", "TR", "JF", "A11610", "futs_", "", FUTURES_BLOCK)


# ------------------------------------------------------ the session gate


@pytest.mark.parametrize(
    "when,open_",
    [
        ((2026, 9, 14, 9, 0), True),
        ((2026, 9, 14, 12, 0), True),
        ((2026, 9, 14, 15, 20), False),
        ((2026, 9, 14, 8, 59), False),
        ((2026, 9, 14, 15, 21), False),
        ((2026, 9, 14, 16, 0), False),
        ((2026, 9, 12, 12, 0), False),  # Saturday
        ((2026, 9, 13, 12, 0), False),  # Sunday
    ],
)
def test_only_the_continuous_session_counts(when, open_):
    """**KIS answers outside the session with the last book**, so the gate
    is what keeps a 16:00 sample — plausible numbers from a different
    market state — out of a pooled median. Verified directly: a real call
    at 23:00 KST returned 삼성전자 at 253,500/253,000."""
    assert in_session(dt.datetime(*when, tzinfo=KST)) is open_


def test_the_close_boundary_is_exclusive():
    """15:20:00 is the closing call auction's FIRST instant, not the
    continuous session's last. `KrxMarketCalendar.isOpen` uses the same
    exclusive boundary, and a sampler disagreeing with the venue calendar
    by one second is a disagreement nobody would look for."""
    assert in_session(dt.datetime(2026, 9, 14, 15, 19, 59, tzinfo=KST))
    assert not in_session(dt.datetime(2026, 9, 14, 15, 20, tzinfo=KST))
    assert not in_session(dt.datetime(2026, 9, 14, 15, 25, tzinfo=KST))


# ------------------------------------------------------- the field map


def test_the_spot_book_reads_prices_and_sizes(monkeypatch):
    assert _spot(monkeypatch) == {
        "ask1": 253500.0, "bid1": 253000.0,
        "ask1_qty": 50157.0, "bid1_qty": 41329.0,
    }


def test_the_futures_quantity_fields_are_not_prefixed(monkeypatch):
    """**The wire fact this module was first wrong about.** `futs_askp1` is
    the price and `askp_rsqn1` is its size — `futs_askp_rsqn1` does not
    exist and returns `None` for every level."""
    assert _futures(monkeypatch) == {
        "ask1": 250500.0, "bid1": 250000.0,
        "ask1_qty": 5274.0, "bid1_qty": 269.0,
    }


def test_the_symmetric_guess_yields_no_book(monkeypatch):
    """The negative control for the test above: asking for the prefixed
    quantity name finds nothing, and the result must be `None` rather than
    a book with prices and no size."""
    _serving(monkeypatch, {"rt_cd": "0", "output2": _futures_block()})
    assert sample_book(
        _SESSION, "/p", "TR", "JF", "A11610", "futs_", "futs_", FUTURES_BLOCK
    ) is None


def test_only_the_endpoint_s_own_block_is_read(monkeypatch):
    """**Searching both blocks would let a request read one it did not ask
    for**, and the two are not distinguishable by shape: the quantity
    fields are unprefixed on BOTH endpoints, so a futures `output2`
    satisfies three quarters of a spot field map and differs only in the
    price key. The block is a measured property of each endpoint, so it is
    pinned rather than searched."""
    _serving(monkeypatch, {"rt_cd": "0", "output2": _spot_block()})
    assert sample_book(_SESSION, "/p", "TR", "J", "005930", "", "", SPOT_BLOCK) is None
    _serving(monkeypatch, {"rt_cd": "0", "output1": _futures_block()})
    assert sample_book(
        _SESSION, "/p", "TR", "JF", "A11610", "futs_", "", FUTURES_BLOCK
    ) is None


def test_the_blocks_are_the_measured_ones():
    assert SPOT_BLOCK == "output1" and FUTURES_BLOCK == "output2"


# --------------------------------------------------- refusals and gaps


def test_an_empty_book_is_none_rather_than_an_error(monkeypatch):
    """How KIS answers for an expired or unlisted contract."""
    _serving(monkeypatch, {"rt_cd": "0", "output1": {}})
    assert sample_book(_SESSION, "/p", "TR", "J", "005930", "", "") is None


@pytest.mark.parametrize("missing", ["askp1", "askp_rsqn1", "bidp_rsqn1"])
def test_a_half_book_is_not_stored(monkeypatch, missing):
    """A price with no size yields neither a spread nor a depth figure,
    and storing the half would make the series look denser than it is."""
    block = _spot_block()
    del block[missing]
    assert _spot(monkeypatch, block) is None


def test_a_crossed_book_is_refused(monkeypatch):
    """Ask below bid is bad data, and would read as a negative cost."""
    with pytest.raises(QuoteSamplerError, match="crossed book"):
        _spot(monkeypatch, _spot_block(ask="253000", bid="253500"))


def test_a_rejected_response_raises(monkeypatch):
    _serving(monkeypatch, {"rt_cd": "1", "msg_cd": "EGW00123"})
    with pytest.raises(QuoteSamplerError, match="rejected"):
        sample_book(_SESSION, "/p", "TR", "J", "005930", "", "")


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "-1", "abc", True])
def test_an_unusable_value_raises_rather_than_being_skipped(bad):
    """A *present but unusable* value is a failed measurement; an absent
    one is a fact about the instrument. `krx_instrument_cost.py` conflated
    the two twice before they were split, so they are separated here from
    the start."""
    with pytest.raises(QuoteSamplerError):
        _positive(bad, "askp1", "005930")


@pytest.mark.parametrize("absent", [None, "", "0"])
def test_an_absent_value_is_none(absent):
    assert _positive(absent, "askp1", "005930") is None


# -------------------------------------------------------- what is stored


def test_primitives_are_stored_and_the_spread_is_not():
    """A stored spread cannot answer how much size was there, cannot be
    recomputed against a different convention, and cannot be checked for a
    crossed book afterwards."""
    rows = rows_for(
        {"ask1": 1.0, "bid1": 2.0, "ask1_qty": 3.0, "bid1_qty": 4.0}, "", 1000
    )
    names = {r.metric for r in rows}
    assert names == {
        f"{METRIC_PREFIX}.{n}" for n in ("ask1", "bid1", "ask1_qty", "bid1_qty")
    }
    assert not any("spread" in n for n in names)


def test_the_contract_rides_in_the_metric_not_the_symbol():
    """A futures contract rolls monthly. Keying the symbol on the code
    would scatter one economic series across twelve symbols a year, so the
    symbol stays the underlying and the metric records which contract was
    actually sampled."""
    rows = rows_for({"ask1": 1.0}, "A11610", 1000)
    assert rows[0].metric == f"{METRIC_PREFIX}.ask1.A11610"
    assert FUTURES_PREFIX == "KRX-FUT:" and SPOT_PREFIX == "KRX:"


def test_the_period_is_an_instant_not_a_window():
    """Every other series in this table is an aggregate over a window.
    Calling this one "1m" would invite a later reader to average it as
    though it were a bar."""
    assert PERIOD == "snapshot"
    assert rows_for({"ask1": 1.0}, "", 1000)[0].period == "snapshot"


def test_values_are_stored_as_exact_strings():
    """Same reason as every other numeric column in the store: SQLite has
    no exact decimal type and a float round-trip perturbs the value."""
    row = rows_for({"ask1": 253500.0}, "", 1000)[0]
    assert isinstance(row.value, str)
    assert float(row.value) == 253500.0


# ------------------------------------------------- the expiry, per run


def _master(listed=("202610", "202611", "202703")):
    return FuturesMaster(contracts={"005930": {e: f"A11{e[3:]}" for e in listed}})


def test_the_front_month_is_the_earliest_listed_expiry():
    """The master carries only live contracts, so its minimum expiry is
    the front month by construction — which is what makes resolving it per
    run cheaper than tracking the second Thursday of every month."""
    assert front_month(_master(), "005930") == "202610"
    assert front_month(_master(("202611", "202612")), "005930") == "202611"


def test_a_name_with_no_listed_future_has_no_front_month():
    """Two of the KR-10 had none at all (rd-r §1.2). A real answer, not a
    failure."""
    assert front_month(_master(), "000000") is None


# ----------------------------------------------- one pass over the universe


class _Recorder:
    """A stand-in for the store that records instead of writing."""

    def __init__(self):
        self.written: list[tuple] = []


def _sampling(monkeypatch, responses):
    """`responses` maps an instrument code to a payload or an exception."""
    def fake(url, headers):
        for code, response in responses.items():
            if code in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("data.krx_quote_sampler._get_with_retry", fake)
    written: list[tuple] = []
    monkeypatch.setattr(
        "data.krx_quote_sampler.upsert_positioning",
        lambda conn, symbol, rows: written.append((symbol, len(rows))) or len(rows),
    )
    return written


def test_one_failure_does_not_cost_the_rest_of_the_pass(monkeypatch):
    """**The property that matters most here.** An order book cannot be
    backfilled at any price, so a single rejected call aborting the pass
    would permanently lose this tick for every symbol after it, for a
    reason unrelated to them."""
    written = _sampling(monkeypatch, {
        "005930": QuoteSamplerError("KIS rejected the book"),
        "000660": {"rt_cd": "0", "output1": _spot_block()},
    })
    books, failures = sample_once(
        _SESSION, None, ["005930", "000660"], None, pause_s=0, now_ms=1
    )
    assert list(books) == ["KRX:000660"], "the later symbol was still sampled"
    assert len(failures) == 1 and "KRX:005930" in failures[0]
    assert written == [("KRX:000660", 4)]


def test_a_failure_names_the_instrument(monkeypatch):
    """One line saying "a pass failed" cannot distinguish a dead contract
    from a rejected key."""
    _sampling(monkeypatch, {"005930": QuoteSamplerError("boom")})
    _, failures = sample_once(_SESSION, None, ["005930"], None, pause_s=0, now_ms=1)
    assert "KRX:005930" in failures[0] and "QuoteSamplerError" in failures[0]


def test_a_probe_pass_stores_nothing(monkeypatch):
    """The only way to look at an out-of-session book. There is
    deliberately no path that both bypasses the session and writes — an
    earlier `--force` did, and contaminated the series with sixteen rows
    during its own verification."""
    written = _sampling(monkeypatch, {"005930": {"rt_cd": "0", "output1": _spot_block()}})
    books, _ = sample_once(
        _SESSION, None, ["005930"], None, pause_s=0, now_ms=1, store=False
    )
    assert books, "it still fetches"
    assert written == [], "and stores nothing"


def test_an_empty_book_is_neither_stored_nor_a_failure(monkeypatch):
    """An unquoted instrument is a fact about it. Counting it as a failure
    would make a thin name look like an outage."""
    written = _sampling(monkeypatch, {"005930": {"rt_cd": "0", "output1": {}}})
    books, failures = sample_once(_SESSION, None, ["005930"], None, pause_s=0, now_ms=1)
    assert books == {} and failures == [] and written == []
