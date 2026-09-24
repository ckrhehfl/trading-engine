"""One validated page-read contract, and what each half of it refuses.

**The defect these exist for** (external audit F-5, 2026-09-23, re-verified
here). Four readers in `python/data/` each turned `output2` into rows and
each checked the endpoint's silent row cap. Two of them filtered malformed
rows out **first** and compared the surviving count against the cap, so a
genuinely truncated 100-row page carrying one unparseable row presented as
an uncapped 99-row page and was read as complete. `kis_futures` happened to
have the order right; `kis_klines.fetch_daily_page` and `krx_scan._page`
did not.

**Why the fix drops the filter entirely rather than moving it.** Measured
2026-09-24 against the live paper host, five real pages -- both endpoints
at their caps (equity 100, index 50), a narrow window, and 한진해운's final
page as a delisted name: every one returned the raw array unchanged, with
zero non-dict entries, zero dicts missing `stck_bsop_date` and zero empty
dicts. **KIS does not pad `output2`.** The filter was defending against a
shape the venue never produces while defeating `_parse_row`'s fail-closed
contract and miscounting the cap.

Each test below is written so that deleting the guard it covers makes it
fail -- this project's own rule, after three isolation fixtures in a row
were inert and read fine (`test_conftest_isolation.py`).
"""

from __future__ import annotations

import pytest

from data.kis_klines import (
    EQUITY_ROWS_PER_CALL_CAP,
    INDEX_ROWS_PER_CALL_CAP,
    KisKlinesError,
    validated_output2,
)


def _rows(n: int, *, start: int = 1) -> list[dict[str, str]]:
    return [{"stck_bsop_date": f"2026{i:04d}"} for i in range(start, start + n)]


# ------------------------------------------------- the ordering, the defect


def test_a_capped_page_with_one_malformed_row_is_still_capped():
    """**The audit finding itself.** 100 rows, one of them unparseable. The
    old order filtered to 99 and compared 99 against 100, so the page
    passed as complete -- while KIS had silently dropped every session
    older than the newest 100."""
    raw = _rows(99) + [{"stck_bsop_date": ""}]
    assert len(raw) == EQUITY_ROWS_PER_CALL_CAP
    with pytest.raises(KisKlinesError, match="at or over this endpoint"):
        validated_output2({"output2": raw}, cap=EQUITY_ROWS_PER_CALL_CAP, what="X")


def test_the_cap_refusal_wins_over_the_row_refusal():
    """Both faults present. The message must be the cap's, because the cap
    is a statement about the response and a row's shape says nothing about
    whether sessions are missing from it."""
    raw = _rows(99) + ["not a dict"]
    with pytest.raises(KisKlinesError) as exc:
        validated_output2({"output2": raw}, cap=EQUITY_ROWS_PER_CALL_CAP, what="X")
    assert "at or over this endpoint" in str(exc.value)


def test_a_page_one_row_under_the_cap_is_accepted():
    rows = validated_output2(
        {"output2": _rows(EQUITY_ROWS_PER_CALL_CAP - 1)},
        cap=EQUITY_ROWS_PER_CALL_CAP,
        what="X",
    )
    assert len(rows) == EQUITY_ROWS_PER_CALL_CAP - 1


def test_the_index_cap_is_a_property_of_the_endpoint_not_the_venue():
    """50, not 100. A pipeline whose guard assumed one shared cap let a
    120-day KOSPI request through carrying only the newest 73 days."""
    raw = _rows(INDEX_ROWS_PER_CALL_CAP)
    validated_output2({"output2": raw}, cap=EQUITY_ROWS_PER_CALL_CAP, what="equity")
    with pytest.raises(KisKlinesError, match="at or over this endpoint"):
        validated_output2({"output2": raw}, cap=INDEX_ROWS_PER_CALL_CAP, what="index")


# ------------------------------------------------------ nothing is dropped


@pytest.mark.parametrize(
    "bad, why",
    [
        ({"stck_bsop_date": ""}, "an empty date"),
        ({"stck_bsop_date": "   "}, "a whitespace date"),
        ({"stck_bsop_date": None}, "an explicit null date"),
        ({"stck_clpr": "1000"}, "no date key at all"),
        ({}, "an empty object"),
    ],
)
def test_a_malformed_row_raises_rather_than_shortening_the_page(bad, why):
    """Silently dropping it produces a page that is short by one session
    and indistinguishable from a real one. Measured: KIS never sends such
    a row, so this cannot fire spuriously -- and if that ever changes it
    fires on the first page instead of quietly truncating a series."""
    with pytest.raises(KisKlinesError, match="stck_bsop_date"):
        validated_output2(
            {"output2": _rows(3) + [bad]}, cap=EQUITY_ROWS_PER_CALL_CAP, what=why
        )


def test_a_non_dict_row_raises():
    with pytest.raises(KisKlinesError, match="not an object"):
        validated_output2(
            {"output2": _rows(2) + [["a", "list"]]},
            cap=EQUITY_ROWS_PER_CALL_CAP,
            what="X",
        )


# -------------------------------------------------- absent vs empty output2


def test_an_empty_series_is_accepted_and_an_absent_key_is_not():
    """The distinction is load-bearing and was measured before being
    relied on: an out-of-range date returns `rt_cd=0` with `output2` present
    and equal to `[]`. The key is always there, so its absence is a real
    shape change rather than "no bars"."""
    assert validated_output2({"output2": []}, cap=100, what="X") == []
    with pytest.raises(KisKlinesError, match="no output2"):
        validated_output2({"rt_cd": "0"}, cap=100, what="X")


def test_a_non_list_output2_is_refused_rather_than_normalised():
    """`kis_probe` used to coerce this to `[]`, making a shape change
    indistinguishable from a real empty series -- the one distinction a
    probe against this endpoint exists to draw."""
    for value in ({"a": 1}, "rows", 0):
        with pytest.raises(KisKlinesError, match="not a list"):
            validated_output2({"output2": value}, cap=100, what="X")


def test_the_refusal_names_the_request():
    """A bare refusal is not diagnosable in a 4,000-symbol scan."""
    with pytest.raises(KisKlinesError, match=r"005930 20240101\.\.20240501"):
        validated_output2(
            {"output2": _rows(100)}, cap=100, what="005930 20240101..20240501"
        )


def test_no_refusal_message_can_carry_a_row_value():
    """A KIS response carries account numbers and balances and these
    exceptions land in a persisted log. The rows here hold a marker value;
    none of the four messages may echo it."""
    marker = "SECRET-ACCOUNT-9999"
    for payload, cap in (
        ({"output2": [{"stck_bsop_date": "20240101", "acct": marker}] * 100}, 100),
        ({"output2": [{"acct": marker}]}, 100),
        ({"output2": [[marker]]}, 100),
        ({"output2": marker}, 100),
    ):
        with pytest.raises(KisKlinesError) as exc:
            validated_output2(payload, cap=cap, what="X")
        assert marker not in str(exc.value)


# --------------------------------------- every reader really uses the one


def test_all_four_readers_route_through_the_contract():
    """D1's actual point is the DUPLICATION. The four-filter common-stock
    rule was implemented twice and the SPAC fix reached one copy only; this
    asserts the same cannot happen to the page-read rule by checking that
    no module keeps its own copy of the filter expression.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "data"
    routed = {
        "kis_klines.py",
        "krx_scan.py",
        "kis_futures.py",
    }
    for name in routed:
        text = (root / name).read_text(encoding="utf-8")
        assert "validated_output2" in text, f"{name} does not use the contract"

    # `kis_probe.py` is deliberately NOT in that set: a probe that imports
    # the implementation it checks confirms that implementation's
    # assumptions instead of testing them. It carries its own equivalent.
    # Judged on what it CALLS and IMPORTS, not on the name appearing: the
    # module names the contract in a comment saying why it does not use it,
    # and a substring test would read that explanation as the violation.
    probe = (root / "kis_probe.py").read_text(encoding="utf-8")
    assert "validated_output2(" not in probe, "kis_probe calls the contract"
    assert "kis_klines import" not in probe and "import kis_klines" not in probe, (
        "kis_probe must stay independent of kis_klines -- it is the external "
        "observable for that module, not a client of it"
    )
    assert "not a list" in probe and "stck_bsop_date" in probe, (
        "kis_probe lost its own fail-closed check"
    )

    # And no module may reintroduce the pre-cap filter.
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert 'if isinstance(r, dict) and r.get("stck_bsop_date")' not in text, (
            f"{path.name} reintroduced the filter that defeats the cap check"
        )
