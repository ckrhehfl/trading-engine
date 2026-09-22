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


# ============================ instrument CLASS, a different question


from data.krx_instrument import (  # noqa: E402
    InstrumentClass,
    instrument_class,
    is_reit,
    is_spac,
)

#: Real 표준코드 observed 2026-09-21, one per class the live join produced.
CLASS_CASES = [
    ("KR7005930003", "005930", "삼성전자", InstrumentClass.STOCK_LIKE),
    ("KR7069500007", "069500", "KODEX 200 (an ETF)", InstrumentClass.STOCK_LIKE),
    ("KR7088260005", "088260", "이리츠코크렙 (a REIT)", InstrumentClass.STOCK_LIKE),
    ("KRG500000671", "Q500067", "신한 레버리지 ETN", InstrumentClass.ETN),
    ("KR5701000303", "F70100030", "한투한미핵심성장포커스1", InstrumentClass.FUND),
    ("HK0000057197", "900110", "딥커머스", InstrumentClass.FOREIGN),
    ("KYG5307W1015", "900140", "엘브이엠씨홀딩스", InstrumentClass.FOREIGN),
]


@pytest.mark.parametrize("isin,code,name,expected", CLASS_CASES)
def test_the_isin_third_character_gives_the_instrument_class(isin, code, name, expected):
    assert instrument_class(isin) is expected, f"{code} {name}"


def test_STOCK_LIKE_really_does_include_ETFs_and_REITs():
    """**Named `STOCK_LIKE` rather than `STOCK` because of exactly this.**
    ETFs and REITs share `KR7` with common stock, so this field cannot
    separate them and a name that implied otherwise would be a lie."""
    assert instrument_class("KR7069500007") is InstrumentClass.STOCK_LIKE
    assert instrument_class("KR7088260005") is InstrumentClass.STOCK_LIKE
    assert instrument_class("KR7005930003") is InstrumentClass.STOCK_LIKE


def test_the_class_is_what_the_delisted_side_has_instead_of_a_group_code():
    """KRX's delisted finder publishes no 증권그룹구분코드, so the ISIN is
    the only structural signal there — and it is what gets ETN, fund, DR
    and warrant out of a pool that `plain_codes` alone would keep."""
    for isin in ("KRG500000671", "KR5701000303"):
        assert instrument_class(isin) is not InstrumentClass.STOCK_LIKE


@pytest.mark.parametrize("bad", [None, "", "KR700", "KR7005930"])
def test_a_wrong_LENGTH_isin_has_no_class(bad):
    assert instrument_class(bad) is InstrumentClass.UNKNOWN


def test_a_twelve_char_non_KR_string_reads_as_FOREIGN_even_if_it_is_junk():
    """**Stated because the test caught it and the alternative is a lie.**
    A country code is two letters and so is garbage, so without a country
    table these are indistinguishable. Both are excluded from a Korean
    stock universe — which is what a caller needs — but a caller
    *counting* foreign listings would over-count."""
    assert instrument_class("HK0000057197") is InstrumentClass.FOREIGN
    assert instrument_class("x" * 12) is InstrumentClass.FOREIGN
    assert not is_common_stock("x" * 12), "excluded either way, which is the point"


# ============================ SPAC and REIT: name rules, weaker on purpose


def test_a_SPAC_is_invisible_to_every_structural_filter():
    """**The finding this exists for.** A SPAC is legally a 주식회사: its
    group code is `ST` and its ISIN is `KR7…0`, so it passes both the
    class filter and the issue-type filter. 70 live names were being
    counted as common stock by the filter shipped one day earlier."""
    spac_isin = "KR7223040007"
    assert instrument_class(spac_isin) is InstrumentClass.STOCK_LIKE
    assert is_common_stock(spac_isin)
    assert is_spac("교보5호스팩")


@pytest.mark.parametrize(
    "name",
    [
        "KB제32호스팩",
        "교보18호스팩",
        "엘에스스팩1호",
        "디비금융제14호스팩",
        # the space is real, and an anchor written without `\s*` drops it
        "미래에셋대우스팩 5호",
        "대우증권그린코리아기업인수목적",
    ],
)
def test_the_regulated_spac_name_is_matched(name):
    assert is_spac(name)


@pytest.mark.parametrize(
    "name", ["삼성전자", "다우기술", "스팩토리", "아스팩오일", "아스팩\n5호"]
)
def test_an_ordinary_name_is_not_a_spac(name):
    """**아스팩오일 is the real one**, a 코넥스 oil company the substring
    form took for a blank-cheque vehicle — 다우기술's lesson recurring one
    rule later. `스팩토리` is the constructed counterpart: the rule has to
    survive a name that merely *starts* with the token."""
    assert not is_spac(name)


def test_the_spac_rule_is_anchored_rather_than_a_substring_search():
    """Remove the anchors and this fails — the guard is the anchoring, so
    it is verified against the pattern itself rather than trusted.

    Both directions, because each has a real name behind it: a bare
    substring over-matches (아스팩오일), and an anchor without `\\s*`
    under-matches (미래에셋대우스팩 5호).
    """
    from data.krx_instrument import _SPAC_PATTERN

    assert _SPAC_PATTERN.search("미래에셋대우스팩 5호")
    assert not _SPAC_PATTERN.search("아스팩오일")
    assert not _SPAC_PATTERN.search("스팩토리")
    # `\s` would span this and drop the name; a separator inside one
    # trading name is a space or a tab.
    assert not _SPAC_PATTERN.search("아스팩\n5호")


def test_the_naive_REIT_rule_is_rejected_and_the_measurement_says_why():
    """**A bare `리츠` match returns 116 live names of which 23 are
    REITs** — 75 ETNs and 14 ETFs *named* 리츠, plus 메리츠종금, which is a
    securities firm. Anchoring takes it to 25 hits with all 23 REITs."""
    assert not is_reit("메리츠종금"), "the substring rule's worst case"
    assert not is_reit("메리츠화재")
    assert is_reit("이리츠코크렙")
    assert is_reit("대신밸류리츠")


def test_the_two_name_rules_are_kept_separate_from_the_isin_rules():
    """They are weaker evidence — a regulated naming convention rather
    than a structural field — and composing them into one function would
    hide that. 우선주 already showed what a substring match does."""
    import inspect

    from data import krx_instrument

    src = inspect.getsource(krx_instrument)
    assert "def is_spac" in src and "def is_reit" in src
    assert "def is_common_stock" in src
    # no single call that silently does all of it
    assert "def is_tradeable" not in src
