"""보통주 or not, from the ISIN — the one field that actually says.

**KIS's 증권그룹구분코드 `ST` does NOT mean common stock.** Measured
2026-09-20 against the real master files: of its 2,718 `ST` rows, **114
are preferred shares** — 삼성전자 `005930` and 삼성전자우 `005935` both
carry `ST`. Every relative-volume or turnover ranking over an `ST` pool
therefore ranks a name's preferred line beside the name itself.

The 12-character 표준코드 (ISIN) is already in both sources and was being
parsed over and discarded by both:

```
삼성전자   005930  KR7 00593 0 00 3
삼성전자우 005935  KR7 00593 1 00 1      <- position 8 is the issue type
현대차     005380  KR7 00538 0 00 1
현대차우   005385  KR7 00538 1 00 9
```

**The rule, and it was validated rather than assumed.** Checked against an
independent label (the Korean name) across every live and delisted issue,
with **every disagreement explained**:

- **56 live + 56 delisted "ISIN says common, name contains 우"** are all
  false positives *of the name test*: 다우기술, 나우IB, LX하우시스,
  NH농우바이오, AP우주통신, C&우방 — 우 as an ordinary syllable. The ISIN
  is right.
- **22 "ISIN says non-common, name has no 우"** are all **foreign-domiciled
  listings** whose ISIN is Hong Kong or Cayman, not Korean:
  GRT `HK0000307485`, 글로벌에스엠 `KYG3931T1076`. Position 8 means
  nothing there, which is why the `KR` prefix is part of the rule and not
  a tidiness check.
- **24 live rows carry a letter at position 8** (`KR700088K015`
  한화3우B, `KR703473K016` SK우, `KR702826K016` 삼성물산우B). All 24 are
  preferred. A digit test alone would have to decide what a letter means;
  "not `0`" classifies them correctly without needing to know.

**This separates 보통주 from 우선주, and NOT stock from ETF.** KODEX 200
`069500` is `KR7069500007` -- position 8 is `0`, so this module calls it
common, and it is an ETF. That distinction lives in KIS's
증권그룹구분코드 (`ST` vs `EF`/`EN`). **The two filters are orthogonal and
a stock universe needs both**: the group code drops the instrument
classes, the ISIN drops the preferred lines within 주식.

The delisted finder publishes **no group code at all**, so on that side
only the second filter is available -- `krx_delisted.common_stock` says
so, and a delisted ETF or SPAC with a `0` issue type will pass it.

**`UNKNOWN` is a real outcome and must not collapse into either side.** A
malformed or foreign ISIN is not evidence of common stock and not evidence
against it. A universe scan excludes it *and reports the count*, because
silently dropping a name is how survivorship bias gets back in —
`.planning/rd-w-the-delisted-universe.md`.
"""

from __future__ import annotations

import re
from enum import Enum

ISIN_LENGTH = 12
#: Korean ISINs only. A foreign-domiciled KOSDAQ listing carries its home
#: market's ISIN (`HK…`, `KYG…`), where this position means something else
#: entirely.
KOREAN_ISIN_PREFIX = "KR"
#: Position 8 (0-indexed) is the issue-type character: `0` 보통주,
#: `1`/`2`/`3` 우선주 classes, a letter for the newer preferred codes.
ISSUE_TYPE_INDEX = 8
COMMON_STOCK_ISSUE_TYPE = "0"


class IssueType(Enum):
    """What an ISIN says an issue is.

    Three values, not two, on purpose: `UNKNOWN` is what an unparseable or
    non-Korean ISIN resolves to, and a caller has to decide what to do with
    it rather than being handed a silent `False`.
    """

    COMMON = "common"
    NOT_COMMON = "not_common"
    UNKNOWN = "unknown"


def classify(isin: str | None) -> IssueType:
    """`IssueType` for one 표준코드.

    Returns `UNKNOWN` — never `NOT_COMMON` — for anything this rule cannot
    read, because "I cannot tell" and "I can tell, and it is preferred" are
    different facts and only one of them is evidence.
    """
    if not isin:
        return IssueType.UNKNOWN
    isin = isin.strip()
    if len(isin) != ISIN_LENGTH or not isin.startswith(KOREAN_ISIN_PREFIX):
        return IssueType.UNKNOWN
    return (
        IssueType.COMMON
        if isin[ISSUE_TYPE_INDEX] == COMMON_STOCK_ISSUE_TYPE
        else IssueType.NOT_COMMON
    )


def is_common_stock(isin: str | None) -> bool:
    """`True` only for a positively-identified 보통주.

    **`UNKNOWN` is `False` here, which is the fail-closed direction for a
    universe filter** — an unreadable ISIN must not be ranked as common
    stock. Callers that need to *act* on the difference (report it, chase
    the missing code) should use `classify` instead, since this function
    cannot tell the two rejections apart.
    """
    return classify(isin) is IssueType.COMMON


class InstrumentClass(Enum):
    """What KIND of instrument an ISIN describes, from its 3rd character.

    Separate from `IssueType` and answering a different question:
    `classify` says 보통주 or not *within* 주식, this says whether the code
    is 주식 at all.
    """

    #: 주식 -- and ETFs and REITs, which share `KR7` with common stock.
    STOCK_LIKE = "stock_like"
    ETN = "etn"
    FUND = "fund"
    DEPOSITARY_RECEIPT = "dr"
    WARRANT = "warrant"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"


#: Measured 2026-09-21 by joining KIS's 증권그룹구분코드 onto the 표준코드
#: for all 4,398 live rows. Each letter was exclusive to its group in that
#: join; `7` is the one that is NOT exclusive -- `EF` (ETF) and `RT`
#: (REIT) carry it too, which is why `STOCK_LIKE` is named that and not
#: `STOCK`.
_CLASS_BY_ISIN_PREFIX_CHAR = {
    "7": InstrumentClass.STOCK_LIKE,   # ST 2,718 + EF 1,172 + RT 23
    "G": InstrumentClass.ETN,          # EN 369
    "5": InstrumentClass.FUND,         # BC 84
    "8": InstrumentClass.DEPOSITARY_RECEIPT,  # DR 10
    "A": InstrumentClass.WARRANT,      # SW 4 + SR 1
}
_CLASS_INDEX = 2


def instrument_class(isin: str | None) -> InstrumentClass:
    """`InstrumentClass` for one 표준코드.

    **This is what the delisted side has instead of a group code.** KRX's
    delisted finder publishes no 증권그룹구분코드 at all, so the ISIN is
    the only structural signal there -- and it gets ETN, fund, DR and
    warrant out of the pool, which `plain_codes` alone does not.

    It does **not** separate 주식 from ETF or REIT. Those share `KR7`.

    **`FOREIGN` is "twelve characters that do not start `KR`", and cannot
    tell a genuine foreign ISIN from a malformed string** -- a country
    code is two letters and so is garbage, and no country table is
    consulted. Both are excluded from a Korean stock universe, which is
    what the caller needs; a caller *counting* foreign listings should not
    lean on it.
    """
    if not isin:
        return InstrumentClass.UNKNOWN
    isin = isin.strip()
    if len(isin) != ISIN_LENGTH:
        return InstrumentClass.UNKNOWN
    if not isin.startswith(KOREAN_ISIN_PREFIX):
        return InstrumentClass.FOREIGN
    return _CLASS_BY_ISIN_PREFIX_CHAR.get(isin[_CLASS_INDEX], InstrumentClass.UNKNOWN)


#: A SPAC's trading name is regulated: `…스팩`, `…스팩N호` or
#: `…기업인수목적…`. **Validated against live ground truth 2026-09-21**:
#: every hit carries 증권그룹구분코드 `ST` -- i.e. the pattern catches no
#: non-stock, and a SPAC is invisible to every structural filter because
#: legally it *is* a 주식회사 with a `KR7…0` ISIN.
#:
#: **Anchored, for the same reason 우선주's rule is** (corrected on review
#: of PR #192, then measured across both universes rather than argued).
#: A bare `스팩` substring is 다우기술 again: it takes **아스팩오일**, a
#: 코넥스 oil company, for a blank-cheque vehicle. But the obvious anchor
#: -- 스팩 at the end, or 스팩 then a digit -- drops **미래에셋대우스팩
#: 5호**, which puts a *space* before its 호수, so the whitespace is part
#: of the rule and not tidiness. Measured 2026-09-22: identical to the
#: substring form on all 4,403 live rows (72 hits either way), and on the
#: 4,185 delisted ones it releases exactly 아스팩오일 (179 -> 178).
#:
#: The separator is `[ \t]`, not `\s`, for the same reason the anchor is
#: there at all: `\s` also spans a newline, so `아스팩\n5호` would match
#: and the name would be dropped from the pool. A separator inside a
#: trading name is a space or a tab; anything else is not one name.
_SPAC_PATTERN = re.compile(r"스팩[ \t]*$|스팩[ \t]*[0-9]|기업인수목적")

#: **A naive `리츠` match is unusable and that is measured, not guessed.**
#: It returns 116 live names of which only 23 are REITs: 75 are ETNs and
#: 14 ETFs *named* 리츠, and 메리츠종금 is a securities firm. This form is
#: anchored instead -- 25 live hits, 23 of them the complete set of live
#: REITs (recall 23/23), the 2 misses being ETFs.
_REIT_PATTERN = re.compile(r"리츠$|리츠[0-9]|코크렙|위탁관리부동산|자기관리부동산")


def is_spac(name: str | None) -> bool:
    """A blank-cheque acquisition vehicle, by its regulated name.

    **Weaker evidence than the ISIN rules above, and deliberately kept
    separate from them.** Those read a structural field; this reads a
    name, and 우선주 already showed what a substring match does to
    다우기술 and 하우시스. What makes it usable here is that the naming is
    regulated rather than incidental, and that it was checked against
    every live name.
    """
    return bool(name) and bool(_SPAC_PATTERN.search(name))


def is_reit(name: str | None) -> bool:
    """A real-estate investment trust, by an anchored name match.

    Same weaker-evidence caveat as `is_spac`. On the live side the group
    code `RT` answers this properly and this function is unnecessary; it
    exists for the delisted side, which has no group code.
    """
    return bool(name) and bool(_REIT_PATTERN.search(name))
