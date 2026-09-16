"""Tests for `data.kis_futures`.

The properties that matter are the ones that stop a *missing* measurement
from being read as a *low* one. This module feeds a liquidity ranking, so
every silent-zero path removes a name from a universe:

- **A constructed contract code is verified against the master's own**,
  because a wrong code returns `rt_cd=0` with zero rows, which this
  module's contract says means "not served". Guessing `A11710` for
  `000660` did exactly that while this was being written — the real code
  is `A50610`.
- **Zero rows is a real answer**, not an error. It is how KIS reports a
  contract it has dropped, and the dropping is the decaying-window fact
  the whole module exists to work around.
- **The 100-row cap is silent**, so a truncated series must raise rather
  than be read as complete.
- **Volume and turnover are required**, never coalesced to zero.
"""

from __future__ import annotations

import types

import pytest

from data.kis_futures import (
    CONTRACT_SHARES,
    ROW_CAP,
    FuturesMaster,
    daily_bars,
    last_trading_day,
    parse_master,
    parse_row,
)
from data.kis_klines import KisKlinesError

# A real excerpt's shape: pipe-delimited, field 1 the code, 3 the
# description, 7 the underlying. Options and spreads share the file.
_MASTER_LINES = [
    "x|A11610|y|삼성전자   F 202610 (  10)|a|b|c|005930|z",
    "x|A11611|y|삼성전자   F 202611 (  10)|a|b|c|005930|z",
    "x|A11703|y|삼성전자   F 202703 (  10)|a|b|c|005930|z",
    "x|A50610|y|SK하이닉스 F 202610 (  10)|a|b|c|000660|z",
    "x|B11610083|y|삼성전자   C 202610   160,000(  10)|a|b|c|005930|z",
    "x|C11610083|y|삼성전자   P 202610   160,000(  10)|a|b|c|005930|z",
    "x|D1161001|y|삼성전자   SP 2610-2611 (  10)|a|b|c|005930|z",
]


def _master(lines=None) -> FuturesMaster:
    return parse_master("\n".join(lines or _MASTER_LINES).encode("cp949"))


def _bar(date="20260916", vol="100", value="1000", price="250000"):
    return {
        "stck_bsop_date": date, "futs_oprc": price, "futs_hgpr": price,
        "futs_lwpr": price, "futs_prpr": price, "acml_vol": vol,
        "acml_tr_pbmn": value,
    }


def _session(payload):
    """A session whose every call returns `payload`. `daily_bars` only
    touches `host` and `headers`, so nothing real is needed."""
    return types.SimpleNamespace(host="https://example.invalid", headers=lambda tr: {})


# ------------------------------------------------------------ the master


def test_only_futures_lines_are_parsed():
    """The file is mostly options. An option's turnover is not this
    instrument's, and sweeping them in would rank the wrong thing."""
    master = _master()
    assert master.underlyings == ["000660", "005930"]
    assert set(master.contracts["005930"]) == {"202610", "202611", "202703"}
    assert "B11610083" not in master.contracts["005930"].values()


def test_a_master_that_parses_to_nothing_is_refused():
    """An empty universe reads as 'Korea has no single-stock futures',
    which is a claim, not a missing measurement."""
    with pytest.raises(KisKlinesError, match="no single-stock futures line"):
        parse_master(b"x|A11610|y|nothing that matches|a|b|c|005930|z")


def test_a_second_contract_size_is_refused_rather_than_averaged():
    """Every Korean notional in this project assumes 10 shares. A file
    with two sizes has to be handled deliberately, not silently."""
    lines = _MASTER_LINES + ["x|A99610|y|somename   F 202610 (   1)|a|b|c|999999|z"]
    with pytest.raises(KisKlinesError, match="contract sizes"):
        _master(lines)


def test_the_contract_size_is_ten_shares():
    assert CONTRACT_SHARES == 10


# ------------------------------------------------------- the code encoding


def test_a_historical_code_is_constructed_from_the_underlying_s_own_prefix():
    """The ranking window needs expired contracts and the master lists
    only live ones, so the code has to be built — `A11610` -> `A11601`."""
    master = _master()
    assert master.contract_code("005930", "202601") == "A11601"
    assert master.contract_code("005930", "202512") == "A11512"
    assert master.contract_code("000660", "202601") == "A50601"


def test_the_encoding_is_not_arithmetic_on_the_underlying_code():
    """`005930` -> `A11610` and `000660` -> `A50610`. There is no relation
    between the two halves; the issue id is KIS's own. Asserted because
    guessing one is what returned zero rows during development."""
    master = _master()
    assert master.contract_code("005930", "202610")[:3] != master.contract_code(
        "000660", "202610"
    )[:3]


def test_a_code_rule_the_master_contradicts_is_refused():
    """**The guard that matters.** A wrong rule produces a plausible code
    that returns zero rows, which this module reports as "not served" —
    so the name silently drops out of the ranking for looking illiquid.
    Reproducing every listed expiry is what rules that out."""
    # The prefixes agree, so the first guard passes; it is the suffix rule
    # that the master contradicts. A case where the prefixes DISAGREE is
    # caught one guard earlier, which is why it is a separate test.
    lines = [
        "x|A11610|y|삼성전자   F 202610 (  10)|a|b|c|005930|z",
        "x|A11699|y|삼성전자   F 202611 (  10)|a|b|c|005930|z",
    ]
    with pytest.raises(KisKlinesError, match="does not reproduce"):
        _master(lines).contract_code("005930", "202601")


def test_contracts_that_share_no_prefix_are_refused_before_that():
    """The earlier of the two guards. Separated because a single test
    passing through whichever fires first would not show that both do."""
    lines = [
        "x|A11610|y|삼성전자   F 202610 (  10)|a|b|c|005930|z",
        "x|AZZ611|y|삼성전자   F 202611 (  10)|a|b|c|005930|z",
    ]
    with pytest.raises(KisKlinesError, match="do not share one prefix"):
        _master(lines).contract_code("005930", "202601")


def test_an_underlying_with_no_future_is_refused():
    with pytest.raises(KisKlinesError, match="no listed single-stock future"):
        _master().contract_code("999999", "202601")


@pytest.mark.parametrize("expiry", ["2026", "20261", "2026aa", "", "2026100"])
def test_a_malformed_expiry_is_refused(expiry):
    with pytest.raises(KisKlinesError, match="not a YYYYMM expiry"):
        _master().contract_code("005930", expiry)


# ---------------------------------------------------------------- the bar


def test_a_bar_parses_with_turnover_in_won():
    bar = parse_row(_bar(vol="1526025", value="35812341021450"))
    assert bar.volume == 1_526_025
    assert bar.value == 35_812_341_021_450


@pytest.mark.parametrize("field", ["acml_vol", "acml_tr_pbmn"])
def test_a_missing_liquidity_field_is_refused_not_zeroed(field):
    """The whole point of this module is ranking names by liquidity, so a
    missing figure recorded as 0 is the one error that silently removes a
    name from a universe."""
    row = _bar()
    del row[field]
    with pytest.raises(KisKlinesError, match="unparseable futures bar"):
        parse_row(row)


def test_a_genuinely_untraded_day_is_kept():
    """A listed contract can print a bar with no volume — the 2026-10
    삼성전자 contract's own first bar did. That is an observation, and
    conflating it with a missing field is how the bug above starts."""
    bar = parse_row(_bar(vol="0", value="0"))
    assert bar.volume == 0 and bar.value == 0


@pytest.mark.parametrize("price", ["nan", "inf", "-inf", "0", "-1"])
def test_a_non_finite_or_non_positive_price_is_refused(price):
    """`float()` accepts `nan` and `inf`, and neither is caught by a
    positivity test — every comparison against `nan` is False, so it
    propagates through a ranking without ever looking wrong."""
    with pytest.raises(KisKlinesError, match="non-finite or non-positive"):
        parse_row(_bar(price=price))


@pytest.mark.parametrize("field", ["futs_prpr", "acml_vol", "acml_tr_pbmn"])
def test_a_boolean_is_refused_rather_than_converted(field):
    """`float(True)` is 1.0 and `int(True)` is 1 — the only types that
    convert silently into a plausible measurement."""
    row = _bar()
    row[field] = True
    with pytest.raises(KisKlinesError, match="boolean"):
        parse_row(row)


@pytest.mark.parametrize("date", ["2026091", "20261332", "", "abcdefgh"])
def test_a_malformed_date_is_refused(date):
    """Shape is not validity: 20261332 is eight digits and not a date."""
    with pytest.raises(KisKlinesError):
        parse_row(_bar(date=date))


# --------------------------------------------------------------- fetching


def test_zero_rows_is_an_empty_series_rather_than_an_error(monkeypatch):
    """How KIS reports a contract it has dropped — `rt_cd=0`, nothing in
    it. The decaying window is the fact, not a failure."""
    monkeypatch.setattr(
        "data.kis_futures._get_with_retry",
        lambda url, headers: {"rt_cd": "0", "output2": []},
    )
    assert daily_bars(_session(None), "A11512", "20250101", "20251231") == []


def test_a_rejected_request_still_raises(monkeypatch):
    """The negative control for the test above: `rt_cd != 0` is a real
    failure and must not be flattened into 'this contract is gone'."""
    monkeypatch.setattr(
        "data.kis_futures._get_with_retry",
        lambda url, headers: {"rt_cd": "1", "msg_cd": "EGW00123"},
    )
    with pytest.raises(KisKlinesError, match="rejected"):
        daily_bars(_session(None), "A11610", "20260101", "20260916")


def test_hitting_the_silent_row_cap_raises(monkeypatch):
    """KIS keeps the NEWEST rows and drops the oldest with `rt_cd=0`, so a
    truncated series is indistinguishable from a short-lived contract
    unless the count is checked."""
    rows = [_bar(date=f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}") for i in range(ROW_CAP)]
    monkeypatch.setattr(
        "data.kis_futures._get_with_retry",
        lambda url, headers: {"rt_cd": "0", "output2": rows},
    )
    with pytest.raises(KisKlinesError, match="row cap"):
        daily_bars(_session(None), "A11603", "20250101", "20260916")


def test_bars_come_back_oldest_first(monkeypatch):
    """KIS returns newest-first. Every consumer here walks forward."""
    monkeypatch.setattr(
        "data.kis_futures._get_with_retry",
        lambda url, headers: {
            "rt_cd": "0",
            "output2": [_bar(date="20260916"), _bar(date="20260915")],
        },
    )
    bars = daily_bars(_session(None), "A11610", "20260901", "20260916")
    assert [b.date for b in bars] == ["20260915", "20260916"]


def test_the_last_trading_day_is_read_from_the_series(monkeypatch):
    """Never computed. The expiry is the second Thursday, but a holiday
    moves it and KRX's moving lunar holidays are an unresolved gap."""
    monkeypatch.setattr(
        "data.kis_futures._get_with_retry",
        lambda url, headers: {
            "rt_cd": "0",
            "output2": [_bar(date="20260910"), _bar(date="20260909")],
        },
    )
    assert last_trading_day(daily_bars(_session(None), "A11609", "x", "y")) == "20260910"
    assert last_trading_day([]) is None
