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
    """Count real issuances and control the cache independently of them.

    The two are separate on purpose. `_read_cached_token` returning `None`
    is not only "expired" -- it is also a permission problem, an
    unparseable file, or a `_write_cached_token` whose `OSError` was
    swallowed. `writable=False` reproduces that box, which is what the
    issuance ceiling exists for.
    """
    state = {"issued": 0, "cached": None, "writable": True}

    def fake_urlopen(*a, **k):  # pragma: no cover - never reached
        raise AssertionError("a real token request escaped the fixture")

    monkeypatch.setattr(K.urllib.request, "urlopen", fake_urlopen)

    def fake_issue(host, app_key, app_secret, *, use_cache=True):
        if use_cache and state["cached"] is not None:
            return state["cached"]
        state["issued"] += 1
        token = f"token-{state['issued']}"
        if state["writable"]:
            state["cached"] = token
        return token

    monkeypatch.setattr(K, "issue_token", fake_issue)
    monkeypatch.setattr(K, "_read_cached_token", lambda host, key: state["cached"])
    return state


def test_a_session_re_resolves_its_token_on_every_request(issuances, monkeypatch):
    """Not "issues on every request" -- the cache absorbs that, and the
    issuance ceiling bounds what happens when the cache cannot answer. What
    must be true is that the session does not serve a token past the bound
    the cache itself enforces, which is the F-2 defect.
    """
    clock = {"t": 0.0}
    monkeypatch.setattr(K.time, "monotonic", lambda: clock["t"])
    session = K.KisSession("key", "secret")
    first = session.headers("TR")["authorization"]
    assert issuances["issued"] == 1

    # The cache expires, exactly as it does after `TOKEN_REUSE_S`.
    issuances["cached"] = None
    clock["t"] = K.TOKEN_REUSE_S + 1
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


# ------------------------------- the ceiling, found on review of this change


def test_an_UNWRITABLE_cache_cannot_turn_into_an_issuance_per_request(
    issuances, monkeypatch
):
    """**A regression this change introduced, caught on review.**

    `_read_cached_token` returns `None` for an expired token, a key
    mismatch, a permission problem and an unparseable file alike, and
    `_write_cached_token` swallows `OSError`. So "ask the cache every
    request" means "issue every request" on a box where the cache cannot be
    written -- which exhausts `EGW00133` within a minute and takes the
    `kis-paper` JVM's own renewal down with it. Exactly the failure the
    cache exists to prevent.
    """
    issuances["writable"] = False
    session = K.KisSession("key", "secret")
    assert issuances["issued"] == 1

    for _ in range(200):
        session.headers("TR")

    assert issuances["issued"] == 1, (
        f"issued {issuances['issued']} times against a broken cache -- the "
        f"session's own clock is not bounding issuance"
    )


def test_the_ceiling_still_lets_a_token_be_renewed_once_the_bound_passes(
    issuances, monkeypatch
):
    """The other direction: the ceiling must not pin a token forever, which
    is the original F-2 defect wearing a different hat."""
    issuances["writable"] = False
    session = K.KisSession("key", "secret")
    clock = {"t": 0.0}
    monkeypatch.setattr(K.time, "monotonic", lambda: clock["t"])
    session._token_at = 0.0

    clock["t"] = K.TOKEN_REUSE_S - 1
    session.headers("TR")
    assert issuances["issued"] == 1, "renewed early"

    clock["t"] = K.TOKEN_REUSE_S
    session.headers("TR")
    assert issuances["issued"] == 2, "never renewed at all"


def test_a_token_another_process_wrote_wins_over_the_session_own(issuances):
    """The whole point of consulting the cache: the `kis-paper` JVM shares
    this app key, and whichever process renews first should serve both."""
    session = K.KisSession("key", "secret")
    issuances["cached"] = "token-from-the-JVM"
    assert session.headers("TR")["authorization"] == "Bearer token-from-the-JVM"
    assert issuances["issued"] == 1, "an external renewal caused an issuance"
