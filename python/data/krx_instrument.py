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
