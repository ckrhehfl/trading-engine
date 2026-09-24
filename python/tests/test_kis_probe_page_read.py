"""`kis_probe.fetch_daily` fails closed, but never before it can report.

Two properties that pull in opposite directions, which is why both are
pinned here.

**It must fail closed on a successful response.** The previous form turned a
non-list `output2` into `[]`, making a shape change indistinguishable from a
real empty series -- and telling those apart is the entire job of a probe
against an endpoint that answers `rt_cd=0` with zero rows for a nonsense
code, a dead name, and an out-of-range window alike.

**And it must not raise on an application error**, because a probe's whole
output is a `Finding`: `probe_endpoint_exists` and `probe_row_cap` read a
real `rt_cd != "0"` out of the envelope and report it. Validating the row
shape ahead of that turned the very case these probes exist to
characterise -- a rejection, which may omit `output2` entirely -- into an
exception, losing the finding instead of returning it. Found on review of
PR #201.

`fetch_daily` is also **deliberately not routed through
`kis_klines.validated_output2`**. A probe that imports the implementation it
checks confirms that implementation's assumptions instead of testing them,
which this repo has paid for three times (`test_conftest_isolation.py`).
What the two share is the reasoning, not the code.
"""

from __future__ import annotations

import pytest

from data import kis_probe as P


@pytest.fixture
def answer(monkeypatch):
    """Make `_get_json` return one scripted envelope."""

    def install(payload):
        monkeypatch.setattr(P, "_get_json", lambda url, params, headers: payload)

    return install


def _fetch():
    return P.fetch_daily(
        "https://fake", "tok", "key", "sec",
        code="005930", start="20240101", end="20240131",
    )


# ------------------------------------- the application error comes first


@pytest.mark.parametrize(
    "payload, why",
    [
        ({"rt_cd": "1", "msg_cd": "EGW00205", "msg1": "서비스 오류"},
         "a rejection that omits output2 entirely"),
        ({"rt_cd": "1", "msg_cd": "OPSQ0002", "output2": None},
         "a rejection carrying an explicit null"),
        ({"rt_cd": "1", "output2": "not a list"},
         "a rejection whose output2 is the wrong type"),
        ({"msg_cd": "OPSQ2001"},
         "no rt_cd at all, which is not success either"),
    ],
)
def test_a_rejection_is_RETURNED_so_the_probe_can_report_it(answer, payload, why):
    envelope, rows = (answer(payload), _fetch())[1]
    assert envelope is payload, why
    assert rows == [], "a rejection must not be presented as having rows"


def test_a_rejection_is_distinguishable_from_a_real_empty_series(answer):
    """Both return `[]`, so the envelope is what separates them -- which is
    why it is returned alongside rather than folded away."""
    answer({"rt_cd": "1", "msg_cd": "EGW00205"})
    bad_envelope, bad_rows = _fetch()
    answer({"rt_cd": "0", "output2": []})
    ok_envelope, ok_rows = _fetch()

    assert bad_rows == ok_rows == []
    assert bad_envelope["rt_cd"] != ok_envelope["rt_cd"], (
        "the caller has nothing left to tell a rejection from an empty series"
    )


# --------------------------------- and a SUCCESSFUL response fails closed


def test_a_successful_response_with_no_output2_key_raises(answer):
    """An out-of-range date returns `rt_cd=0` with `output2` present and
    equal to `[]` -- measured. The key is always there, so its absence on a
    success is a real shape change rather than "no bars"."""
    answer({"rt_cd": "0"})
    with pytest.raises(P.ProbeError, match="no output2"):
        _fetch()


@pytest.mark.parametrize("value", [{"a": 1}, "rows", 0, 3.5])
def test_a_successful_non_list_output2_is_refused_not_normalised(answer, value):
    answer({"rt_cd": "0", "output2": value})
    with pytest.raises(P.ProbeError, match="not a list"):
        _fetch()


def test_a_successful_response_with_a_malformed_row_raises(answer):
    answer({"rt_cd": "0", "output2": [{"stck_bsop_date": "20240102"}, {}]})
    with pytest.raises(P.ProbeError, match="stck_bsop_date"):
        _fetch()


def test_a_successful_response_with_a_non_dict_row_raises(answer):
    answer({"rt_cd": "0", "output2": [["20240102"]]})
    with pytest.raises(P.ProbeError, match="not an object"):
        _fetch()


def test_a_clean_page_comes_back_whole(answer):
    rows_in = [{"stck_bsop_date": f"2024010{i}"} for i in range(1, 4)]
    answer({"rt_cd": "0", "output2": rows_in})
    envelope, rows = _fetch()
    assert rows == rows_in
    assert envelope["rt_cd"] == "0"
