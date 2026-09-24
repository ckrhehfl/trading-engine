"""A session's token has to obey the cache's freshness bound (audit F-2).

**The defect.** `KisSession.__init__` issued a token once and every later
`headers()` call reused that string for the life of the object. The
lifetimes here are long — the full-universe scan ran a single session for
about **50 hours**, and every backfill runs for hours — while the token
cache next to it enforces `TOKEN_REUSE_S` and would have refused to hand
the same string out. The session was the one place that bound did not
apply.

**Latent rather than live, checked rather than assumed.** The scan's 199
failures are spread evenly across every hour of its run (1–22 per hour,
2026-09-21 … 09-23) rather than bursting at a token boundary, and bars
accumulated through the final symbol. So no collected data is in question;
what was wrong is that nothing would have caught it.

The fix gives the session **no second notion of freshness**: it re-resolves
through `issue_token`, which returns the cached token while fresh and
issues once when not. One definition of "still good", and a concurrent
process — the `kis-paper` JVM shares this app key's `EGW00133` allowance —
benefits from the same single issuance.
"""

from __future__ import annotations

import pytest

from data import kis_klines as K


@pytest.fixture
def issuances(monkeypatch):
    """Count real issuances and control what the cache reports."""
    state = {"issued": 0, "cached": None}

    def fake_urlopen(*a, **k):  # pragma: no cover - never reached
        raise AssertionError("a real token request escaped the fixture")

    monkeypatch.setattr(K.urllib.request, "urlopen", fake_urlopen)

    def fake_issue(host, app_key, app_secret, *, use_cache=True):
        if use_cache and state["cached"] is not None:
            return state["cached"]
        state["issued"] += 1
        token = f"token-{state['issued']}"
        state["cached"] = token
        return token

    monkeypatch.setattr(K, "issue_token", fake_issue)
    return state


def test_a_session_re_resolves_its_token_on_every_request(issuances):
    """Not "issues on every request" — the cache absorbs that. What must be
    true is that the *cache* is consulted, so a token it has replaced is
    picked up instead of the session's own copy."""
    session = K.KisSession("key", "secret")
    first = session.headers("TR")["authorization"]
    assert issuances["issued"] == 1

    # The cache moves on, as it does when it expires or another process
    # renews it.
    issuances["cached"] = None
    second = session.headers("TR")["authorization"]

    assert issuances["issued"] == 2, "the session kept its own stale token"
    assert second != first
    assert second == "Bearer token-2"


def test_a_fresh_cache_costs_no_extra_issuance(issuances):
    """The other direction, so the fix cannot be "re-issue constantly" —
    that would exhaust `EGW00133` within a minute, which is the exact
    failure `issue_token`'s cache exists to prevent."""
    session = K.KisSession("key", "secret")
    for _ in range(50):
        session.headers("TR")
    assert issuances["issued"] == 1


def test_construction_still_fails_fast_on_a_bad_key(monkeypatch):
    """Resolving per request must not defer the first failure to the first
    real call — a backfill that authenticates only on page one of symbol
    one has already logged a start it cannot honour."""
    def boom(*a, **k):
        raise K.KisKlinesError("token issuance failed with HTTP 403")

    monkeypatch.setattr(K, "issue_token", boom)
    with pytest.raises(K.KisKlinesError, match="403"):
        K.KisSession("key", "secret")


def test_empty_credentials_are_still_refused_before_any_request(issuances):
    for key, secret in (("", "s"), ("k", ""), ("", "")):
        with pytest.raises(K.KisKlinesError, match="both required"):
            K.KisSession(key, secret)
    assert issuances["issued"] == 0


def test_the_headers_still_carry_what_KIS_requires(issuances):
    headers = K.KisSession("key", "secret").headers("FHKST03010100")
    assert headers["tr_id"] == "FHKST03010100"
    assert headers["custtype"] == "P"
    assert headers["authorization"].startswith("Bearer ")
    assert headers["appkey"] == "key"


def test_the_token_cache_bound_is_shorter_than_KIS_own_validity():
    """`TOKEN_REUSE_S` is this project's conservative reuse bound, not the
    venue's expiry. Stated as an assertion so a future edit that raises it
    past a day has to argue with a test rather than with a comment."""
    assert 0 < K.TOKEN_REUSE_S < 86_400
