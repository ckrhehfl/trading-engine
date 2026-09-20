"""Tests for `data.krx_instrument`.

Every fixture here is a **real** code observed in KIS's master files or
KRX's finder on 2026-09-20, because the failure this module exists to
prevent was believing a plausible encoding. The cases that matter are the
ones where the obvious alternative rule gets it wrong:

- a name-based test calls 다우기술 preferred
- a last-digit test calls 대덕1우 (`00806K`) common
- a digit-only test has to invent a meaning for `K`
- either test calls a Hong Kong ISIN something
"""

from __future__ import annotations

import pytest

from data.krx_instrument import IssueType, classify, is_common_stock

# (isin, short code, name) — real rows
COMMON = [
    ("KR7005930003", "005930", "삼성전자"),
    ("KR7000660001", "000660", "SK하이닉스"),
    ("KR7005380001", "005380", "현대차"),
    ("KR7051910008", "051910", "LG화학"),
    ("KR7117930008", "117930", "한진해운 (delisted 2017)"),
]
PREFERRED = [
    ("KR7005931001", "005935", "삼성전자우"),
    ("KR7005381009", "005385", "현대차우"),
    ("KR7051911006", "051915", "LG화학우"),
    ("KR7002791002", "002795", "아모레퍼시픽홀딩스우"),
]
#: The 24 live rows whose issue-type position is a LETTER. All preferred.
LETTER_CODED_PREFERRED = [
    ("KR700088K015", "00088K", "한화3우B"),
    ("KR703473K016", "03473K", "SK우"),
    ("KR702826K016", "02826K", "삼성물산우B"),
    ("KR718064K016", "18064K", "한진칼우"),
    ("KR700806K010", "00806K", "대덕1우"),
    ("KR700279K010", "00279K", "아모레퍼시픽홀딩스3우C"),
]
#: Foreign-domiciled KOSDAQ listings — their ISIN is not Korean at all.
FOREIGN = [
    ("HK0000307485", "900290", "GRT"),
    ("KYG3931T1076", "900070", "글로벌에스엠"),
    ("HK0000057197", "900110", "딥커머스"),
    ("KYG887121070", "900010", "3노드디지탈"),
]
#: Names a 우-in-the-name test misclassifies. All genuinely common stock.
NAME_TEST_FALSE_POSITIVES = [
    ("KR7023590003", "023590", "다우기술"),
    ("KR7108670001", "108670", "LX하우시스"),
    ("KR7054050000", "054050", "NH농우바이오"),
    ("KR7293580007", "293580", "나우IB"),
    ("KR7015670003", "015670", "AP우주통신"),
    ("KR7013200001", "013200", "C&우방"),
]


@pytest.mark.parametrize("isin,code,name", COMMON)
def test_common_stock_is_identified(isin, code, name):
    assert classify(isin) is IssueType.COMMON, f"{code} {name}"
    assert is_common_stock(isin)


@pytest.mark.parametrize("isin,code,name", PREFERRED)
def test_a_preferred_line_is_not_common(isin, code, name):
    """**The whole point.** 삼성전자 and 삼성전자우 both carry KIS's `ST`
    group code, so anything ranking an `ST` pool by turnover ranks the two
    against each other."""
    assert classify(isin) is IssueType.NOT_COMMON, f"{code} {name}"
    assert not is_common_stock(isin)


@pytest.mark.parametrize("isin,code,name", LETTER_CODED_PREFERRED)
def test_a_LETTER_at_the_issue_position_is_not_common(isin, code, name):
    """24 real live rows do this, and every one is preferred. Stating the
    rule as "not `0`" rather than "a digit other than `0`" is what makes
    them fall out correctly without having to know what `K` means."""
    assert classify(isin) is IssueType.NOT_COMMON, f"{code} {name}"


@pytest.mark.parametrize("isin,code,name", FOREIGN)
def test_a_non_korean_isin_is_UNKNOWN_not_a_verdict(isin, code, name):
    """**Neither answer would be honest.** Position 8 of a Hong Kong or
    Cayman ISIN is not an issue-type field, so reading one is reading
    noise. It is also why the `KR` prefix is part of the rule: without it
    `HK0000307485`[8] is `'4'` and 글로벌에스엠 would be 'preferred'."""
    assert classify(isin) is IssueType.UNKNOWN, f"{code} {name}"
    assert not is_common_stock(isin), "fail closed for a universe filter"


@pytest.mark.parametrize("isin,code,name", NAME_TEST_FALSE_POSITIVES)
def test_the_name_heuristic_is_wrong_and_the_isin_is_right(isin, code, name):
    """112 real issues contain 우 as an ordinary syllable. A name-based
    classifier drops every one of them from a common-stock universe —
    which is a survivorship-shaped error, just not the usual one."""
    assert "우" in name
    assert classify(isin) is IssueType.COMMON, f"{code} {name}"


@pytest.mark.parametrize("bad", [None, "", "   ", "KR70059300", "KR70059300035", "x"])
def test_an_unreadable_isin_is_UNKNOWN(bad):
    assert classify(bad) is IssueType.UNKNOWN
    assert not is_common_stock(bad)


def test_whitespace_is_tolerated():
    """The master file's fields are fixed-width and arrive padded."""
    assert classify("  KR7005930003  ") is IssueType.COMMON


def test_UNKNOWN_is_not_silently_either_side():
    """`is_common_stock` collapses UNKNOWN into False, which is right for
    a filter and wrong for a report — so the three-valued `classify` has
    to stay reachable, and this pins that it is."""
    assert is_common_stock("HK0000307485") == is_common_stock("KR7005931001") is False
    assert classify("HK0000307485") is not classify("KR7005931001")
