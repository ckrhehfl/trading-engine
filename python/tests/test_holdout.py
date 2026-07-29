"""Tests for `python/research/holdout.py`.

See CLAUDE.md's "Strategy Research Operational Design" section,
"Holdout-split mechanics" subsection, for the full design this module
implements.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from data.bingx_funding import FundingRow
from data.bingx_klines import KlineRow
from data.store import connect, upsert_funding_rates, upsert_klines
from research.experiment_log import DEFAULT_RUNS_PATH as _UNUSED  # sanity import
from research.holdout import (
    HoldoutAlreadyClaimedError,
    _clamp_research_range,
    load_holdout_config,
    load_holdout_klines,
    load_research_funding,
    load_research_klines,
)

STEP = 900_000
BASE = (1_700_000_000_000 // STEP) * STEP
CUTOFF = BASE + 10 * STEP  # bars 0..9 are "research", 10+ are "holdout"


def _row(offset: int, price: str = "100") -> KlineRow:
    return KlineRow(
        open_time_ms=BASE + offset * STEP,
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=Decimal("1"),
    )


def _funding_row(offset: int, rate: str = "0.0001") -> FundingRow:
    return FundingRow(
        funding_time_ms=BASE + offset * STEP,
        funding_rate=Decimal(rate),
        mark_price=Decimal("50000"),
    )


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "klines.sqlite3"
    conn = connect(path)
    upsert_klines(conn, "BTC-USDT", "15m", [_row(i) for i in range(20)])  # bars 0..19
    conn.close()
    return path


@pytest.fixture
def funding_db_path(tmp_path):
    path = tmp_path / "funding.sqlite3"
    conn = connect(path)
    upsert_funding_rates(conn, "BTC-USDT", [_funding_row(i) for i in range(20)])  # settlements 0..19
    conn.close()
    return path


@pytest.fixture
def holdout_config_path(tmp_path):
    path = tmp_path / "holdout.json"
    path.write_text(
        json.dumps(
            {
                "symbol": "BTC-USDT",
                "interval": "15m",
                "holdout_cutoff_ms": CUTOFF,
                "set_on": "2026-07-26",
                "rationale": "test fixture cutoff",
            }
        )
    )
    return path


@pytest.fixture
def runs_path(tmp_path):
    return tmp_path / "experiments.jsonl"


# ---------------------------------------------------------------------------
# load_research_klines
# ---------------------------------------------------------------------------


def test_load_research_klines_returns_klines_within_the_requested_range(db_path, holdout_config_path):
    klines = load_research_klines(BASE, BASE + 5 * STEP, db_path=db_path, holdout_config_path=holdout_config_path)

    assert len(klines) == 5


def test_load_research_klines_clamps_end_ms_to_the_holdout_cutoff(db_path, holdout_config_path):
    # Requesting through bar 19, well past the cutoff at bar 10.
    klines = load_research_klines(
        BASE, BASE + 20 * STEP, db_path=db_path, holdout_config_path=holdout_config_path
    )

    # Only bars 0..9 (i.e. up to but not including CUTOFF) are research data.
    assert len(klines) == 10
    assert klines[-1].open_time.timestamp() * 1000 == CUTOFF - STEP


def test_load_research_klines_logs_a_warning_when_it_actually_clamps(db_path, holdout_config_path, caplog):
    with caplog.at_level("WARNING"):
        load_research_klines(BASE, BASE + 20 * STEP, db_path=db_path, holdout_config_path=holdout_config_path)

    assert any("clamp" in record.message.lower() for record in caplog.records)


def test_load_research_klines_does_not_warn_when_end_ms_is_already_before_cutoff(
    db_path, holdout_config_path, caplog
):
    with caplog.at_level("WARNING"):
        load_research_klines(BASE, BASE + 5 * STEP, db_path=db_path, holdout_config_path=holdout_config_path)

    assert not any("clamp" in record.message.lower() for record in caplog.records)


def test_load_research_klines_never_returns_data_at_or_after_the_cutoff(db_path, holdout_config_path):
    klines = load_research_klines(
        BASE, BASE + 20 * STEP, db_path=db_path, holdout_config_path=holdout_config_path
    )

    assert all(k.open_time.timestamp() * 1000 < CUTOFF for k in klines)


# ---------------------------------------------------------------------------
# load_research_funding
# ---------------------------------------------------------------------------


def test_load_research_funding_returns_funding_rates_within_the_requested_range(
    funding_db_path, holdout_config_path
):
    rates = load_research_funding(
        BASE, BASE + 5 * STEP, db_path=funding_db_path, holdout_config_path=holdout_config_path
    )

    assert len(rates) == 5


def test_load_research_funding_clamps_end_ms_to_the_holdout_cutoff(funding_db_path, holdout_config_path):
    rates = load_research_funding(
        BASE, BASE + 20 * STEP, db_path=funding_db_path, holdout_config_path=holdout_config_path
    )

    # Only settlements 0..9 (up to but not including CUTOFF) are research data.
    assert len(rates) == 10
    assert rates[-1].funding_time.timestamp() * 1000 == CUTOFF - STEP


def test_load_research_funding_logs_a_warning_when_it_actually_clamps(funding_db_path, holdout_config_path, caplog):
    with caplog.at_level("WARNING"):
        load_research_funding(BASE, BASE + 20 * STEP, db_path=funding_db_path, holdout_config_path=holdout_config_path)

    assert any("clamp" in record.message.lower() for record in caplog.records)


def test_load_research_funding_does_not_warn_when_end_ms_is_already_before_cutoff(
    funding_db_path, holdout_config_path, caplog
):
    with caplog.at_level("WARNING"):
        load_research_funding(BASE, BASE + 5 * STEP, db_path=funding_db_path, holdout_config_path=holdout_config_path)

    assert not any("clamp" in record.message.lower() for record in caplog.records)


def test_load_research_funding_never_returns_data_at_or_after_the_cutoff(funding_db_path, holdout_config_path):
    rates = load_research_funding(
        BASE, BASE + 20 * STEP, db_path=funding_db_path, holdout_config_path=holdout_config_path
    )

    assert all(r.funding_time.timestamp() * 1000 < CUTOFF for r in rates)


def test_load_research_funding_defaults_to_the_holdout_configs_own_symbol(funding_db_path, holdout_config_path):
    # holdout_config_path's own fixture symbol is "BTC-USDT" -- this
    # proves the default is genuinely read from the config (not a
    # hardcoded literal) via the dedicated mismatched-symbol test below,
    # which uses a config with a DIFFERENT symbol.
    rates = load_research_funding(
        BASE, BASE + 5 * STEP, db_path=funding_db_path, holdout_config_path=holdout_config_path
    )

    assert len(rates) == 5  # would be 0 if the wrong default symbol were queried


def test_load_research_funding_respects_an_explicit_symbol(funding_db_path, holdout_config_path):
    rates = load_research_funding(
        BASE, BASE + 5 * STEP, symbol="ETH-USDT", db_path=funding_db_path, holdout_config_path=holdout_config_path
    )

    assert rates == []


def test_load_research_funding_default_symbol_tracks_a_non_btc_holdout_config(tmp_path, funding_db_path):
    # Real CodeRabbit review finding on this task's PR: a hardcoded
    # "BTC-USDT" default would silently query the wrong symbol's funding
    # data for a caller using a holdout config for a different symbol and
    # forgetting to also pass `symbol` explicitly. Proves the default is
    # genuinely config-derived, not a hardcoded literal that happens to
    # match the other fixtures' "BTC-USDT" config.
    eth_config_path = tmp_path / "holdout_eth.json"
    eth_config_path.write_text(
        json.dumps(
            {
                "symbol": "ETH-USDT",
                "interval": "15m",
                "holdout_cutoff_ms": CUTOFF,
                "set_on": "2026-07-26",
                "rationale": "test fixture cutoff for a non-BTC symbol",
            }
        )
    )

    rates = load_research_funding(BASE, BASE + 5 * STEP, db_path=funding_db_path, holdout_config_path=eth_config_path)

    # funding_db_path only has BTC-USDT rows stored -- querying with the
    # config's own ETH-USDT symbol (the correct, non-hardcoded default)
    # legitimately returns nothing, proving no silent BTC-USDT fallback.
    assert rates == []


def test_load_research_funding_returns_funding_rate_typed_objects_with_expected_fields(
    funding_db_path, holdout_config_path
):
    rates = load_research_funding(
        BASE, BASE + 1 * STEP, db_path=funding_db_path, holdout_config_path=holdout_config_path
    )

    assert len(rates) == 1
    assert rates[0].funding_rate == Decimal("0.0001")
    assert rates[0].mark_price == Decimal("50000")
    assert rates[0].funding_time.timestamp() * 1000 == BASE


# ---------------------------------------------------------------------------
# load_holdout_klines
# ---------------------------------------------------------------------------


def test_load_holdout_klines_raises_without_the_explicit_keyword(db_path, holdout_config_path, runs_path):
    with pytest.raises(TypeError):
        load_holdout_klines(  # noqa
            CUTOFF,
            BASE + 20 * STEP,
            strategy_id="s1",
            db_path=db_path,
            holdout_config_path=holdout_config_path,
            runs_path=runs_path,
        )


def test_load_holdout_klines_raises_when_keyword_is_explicitly_false(db_path, holdout_config_path, runs_path):
    with pytest.raises(ValueError):
        load_holdout_klines(
            CUTOFF,
            BASE + 20 * STEP,
            strategy_id="s1",
            i_understand_this_is_holdout_data=False,
            db_path=db_path,
            holdout_config_path=holdout_config_path,
            runs_path=runs_path,
        )


def test_load_holdout_klines_returns_only_data_at_or_after_the_cutoff(db_path, holdout_config_path, runs_path):
    klines = load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    assert len(klines) == 10  # bars 10..19
    assert all(k.open_time.timestamp() * 1000 >= CUTOFF for k in klines)


def test_load_holdout_klines_rejects_a_start_ms_before_the_cutoff(db_path, holdout_config_path, runs_path):
    with pytest.raises(ValueError):
        load_holdout_klines(
            BASE,  # before CUTOFF
            BASE + 20 * STEP,
            strategy_id="s1",
            i_understand_this_is_holdout_data=True,
            db_path=db_path,
            holdout_config_path=holdout_config_path,
            runs_path=runs_path,
        )


def test_load_holdout_klines_logs_a_holdout_access_record(db_path, holdout_config_path, runs_path):
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    lines = runs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["record_type"] == "holdout_access"
    assert record["strategy_id"] == "s1"
    assert record["force_reclaim_reason"] is None


def test_load_holdout_klines_second_call_for_same_strategy_id_raises(db_path, holdout_config_path, runs_path):
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    with pytest.raises(HoldoutAlreadyClaimedError):
        load_holdout_klines(
            CUTOFF,
            BASE + 20 * STEP,
            strategy_id="s1",
            i_understand_this_is_holdout_data=True,
            db_path=db_path,
            holdout_config_path=holdout_config_path,
            runs_path=runs_path,
        )

    # The rejected second call must not have appended a second record.
    lines = runs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_load_holdout_klines_different_strategy_ids_do_not_collide(db_path, holdout_config_path, runs_path):
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    # A different strategy_id must succeed -- the claim is per-strategy_id.
    klines = load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s2",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )
    assert len(klines) == 10


def test_load_holdout_klines_second_call_with_blank_force_reclaim_reason_still_raises(
    db_path, holdout_config_path, runs_path
):
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    for blank in ("", "   "):
        with pytest.raises(HoldoutAlreadyClaimedError):
            load_holdout_klines(
                CUTOFF,
                BASE + 20 * STEP,
                strategy_id="s1",
                i_understand_this_is_holdout_data=True,
                force_reclaim_reason=blank,
                db_path=db_path,
                holdout_config_path=holdout_config_path,
                runs_path=runs_path,
            )


def test_load_holdout_klines_force_reclaim_reason_allows_a_second_access(
    db_path, holdout_config_path, runs_path
):
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    klines = load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        force_reclaim_reason="fixed a metrics bug found after the first holdout run, re-confirming",
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    assert len(klines) == 10
    lines = runs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    second_record = json.loads(lines[1])
    assert second_record["force_reclaim_reason"] == (
        "fixed a metrics bug found after the first holdout run, re-confirming"
    )


def test_load_holdout_klines_a_third_access_without_a_new_reason_still_raises(
    db_path, holdout_config_path, runs_path
):
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )
    load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        force_reclaim_reason="legitimate re-run after a metrics bugfix",
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )

    with pytest.raises(HoldoutAlreadyClaimedError):
        load_holdout_klines(
            CUTOFF,
            BASE + 20 * STEP,
            strategy_id="s1",
            i_understand_this_is_holdout_data=True,
            db_path=db_path,
            holdout_config_path=holdout_config_path,
            runs_path=runs_path,
        )


# ---------------------------------------------------------------------------
# holdout_side (Strategy Research Task T)
#
# `holdout_side` says which side of `holdout_cutoff_ms` the HOLDOUT
# occupies -- research data is always the other side:
#
#   "after"  (default): holdout = [cutoff, inf), research = (-inf, cutoff)
#   "before":           holdout = (-inf, cutoff), research = [cutoff, inf)
#
# "before" looks backwards next to the usual trailing-holdout convention.
# It isn't: a holdout is data whose contents have informed no decision,
# and for this project's 1d dataset that is the EARLY window (see
# `configs/research/holdout_1d.json` and
# `.planning/sr-t-daily-data-path.md`). The tests below pin both
# directions, and -- most importantly -- pin that omitting the key
# reproduces today's behavior exactly.
# ---------------------------------------------------------------------------


def _repo_path(relative: str) -> Path:
    """Resolve a repo-root-relative path (e.g. `configs/research/...`)
    from this test file's own location, so these tests don't depend on
    pytest's cwd the way the loaders' own runtime defaults do.
    """
    return Path(__file__).resolve().parents[2] / relative


def _write_config(path, **overrides):
    config = {
        "symbol": "BTC-USDT",
        "interval": "15m",
        "holdout_cutoff_ms": CUTOFF,
        "set_on": "2026-07-28",
        "rationale": "test fixture cutoff",
    }
    config.update(overrides)
    path.write_text(json.dumps(config))
    return path


@pytest.fixture
def before_config_path(tmp_path):
    return _write_config(tmp_path / "holdout_before.json", holdout_side="before")


@pytest.fixture
def explicit_after_config_path(tmp_path):
    return _write_config(tmp_path / "holdout_after.json", holdout_side="after")


# --- the default: omitting the key must change nothing -----------------------


def test_omitting_holdout_side_is_identical_to_an_explicit_after(
    db_path, holdout_config_path, explicit_after_config_path
):
    implicit = load_research_klines(
        BASE, BASE + 20 * STEP, db_path=db_path, holdout_config_path=holdout_config_path
    )
    explicit = load_research_klines(
        BASE, BASE + 20 * STEP, db_path=db_path, holdout_config_path=explicit_after_config_path
    )

    assert [k.open_time for k in implicit] == [k.open_time for k in explicit]
    assert len(implicit) == 10  # bars 0..9, exactly as before this feature existed


def test_omitting_holdout_side_is_identical_to_an_explicit_after_for_funding(
    funding_db_path, holdout_config_path, explicit_after_config_path
):
    implicit = load_research_funding(
        BASE, BASE + 20 * STEP, db_path=funding_db_path, holdout_config_path=holdout_config_path
    )
    explicit = load_research_funding(
        BASE, BASE + 20 * STEP, db_path=funding_db_path, holdout_config_path=explicit_after_config_path
    )

    assert [r.funding_time for r in implicit] == [r.funding_time for r in explicit]
    assert len(implicit) == 10


def test_omitting_holdout_side_keeps_load_holdout_klines_serving_the_trailing_slice(
    db_path, holdout_config_path, explicit_after_config_path, runs_path
):
    implicit = load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s-implicit",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=holdout_config_path,
        runs_path=runs_path,
    )
    explicit = load_holdout_klines(
        CUTOFF,
        BASE + 20 * STEP,
        strategy_id="s-explicit",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=explicit_after_config_path,
        runs_path=runs_path,
    )

    assert [k.open_time for k in implicit] == [k.open_time for k in explicit]
    assert len(implicit) == 10  # bars 10..19


# --- holdout_side="before" ---------------------------------------------------


def test_research_klines_with_side_before_clamps_start_up_to_the_cutoff(db_path, before_config_path):
    klines = load_research_klines(
        BASE, BASE + 20 * STEP, db_path=db_path, holdout_config_path=before_config_path
    )

    # Research data is now everything AT/AFTER the cutoff: bars 10..19.
    assert len(klines) == 10
    assert all(k.open_time.timestamp() * 1000 >= CUTOFF for k in klines)
    assert klines[0].open_time.timestamp() * 1000 == CUTOFF


def test_research_klines_with_side_before_refuses_an_entirely_pre_cutoff_request(db_path, before_config_path):
    # The whole requested range is holdout data under "before". Clamping
    # it leaves an empty range, which `require_valid_range` rejects --
    # the mirror of the pre-existing "after"-side behavior pinned by
    # test_research_klines_with_side_after_refuses_an_entirely_post_cutoff_request
    # below. Loud failure, never a silent empty list.
    with pytest.raises(ValueError):
        load_research_klines(BASE, CUTOFF, db_path=db_path, holdout_config_path=before_config_path)


def test_research_klines_with_side_after_refuses_an_entirely_post_cutoff_request(db_path, holdout_config_path):
    # Pre-existing behavior, never previously pinned by a test: under the
    # default "after" side, a request wholly at/after the cutoff clamps to
    # an empty range and raises. Locked in here so the "before" side's
    # mirror image above can be shown to be symmetric rather than new.
    with pytest.raises(ValueError):
        load_research_klines(
            CUTOFF, BASE + 20 * STEP, db_path=db_path, holdout_config_path=holdout_config_path
        )


def test_research_klines_with_side_before_logs_a_warning_when_it_actually_clamps(
    db_path, before_config_path, caplog
):
    with caplog.at_level("WARNING"):
        load_research_klines(BASE, BASE + 20 * STEP, db_path=db_path, holdout_config_path=before_config_path)

    assert any("clamp" in record.message.lower() for record in caplog.records)


def test_research_klines_with_side_before_does_not_warn_when_start_is_already_at_the_cutoff(
    db_path, before_config_path, caplog
):
    with caplog.at_level("WARNING"):
        load_research_klines(CUTOFF, BASE + 20 * STEP, db_path=db_path, holdout_config_path=before_config_path)

    assert not any("clamp" in record.message.lower() for record in caplog.records)


def test_research_funding_with_side_before_clamps_start_up_to_the_cutoff(funding_db_path, before_config_path):
    rates = load_research_funding(
        BASE, BASE + 20 * STEP, db_path=funding_db_path, holdout_config_path=before_config_path
    )

    assert len(rates) == 10
    assert all(r.funding_time.timestamp() * 1000 >= CUTOFF for r in rates)


def test_research_funding_with_side_before_logs_a_warning_when_it_actually_clamps(
    funding_db_path, before_config_path, caplog
):
    with caplog.at_level("WARNING"):
        load_research_funding(
            BASE, BASE + 20 * STEP, db_path=funding_db_path, holdout_config_path=before_config_path
        )

    assert any("clamp" in record.message.lower() for record in caplog.records)


def test_holdout_klines_with_side_before_returns_only_data_before_the_cutoff(
    db_path, before_config_path, runs_path
):
    klines = load_holdout_klines(
        BASE,
        CUTOFF,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=before_config_path,
        runs_path=runs_path,
    )

    assert len(klines) == 10  # bars 0..9
    assert all(k.open_time.timestamp() * 1000 < CUTOFF for k in klines)


def test_holdout_klines_with_side_before_rejects_an_end_ms_after_the_cutoff(
    db_path, before_config_path, runs_path
):
    # Mirror image of the "after" side's start_ms guard: under "before"
    # the holdout is the EARLY slice, so a request reaching past the
    # cutoff is asking for research data through the holdout door.
    with pytest.raises(ValueError):
        load_holdout_klines(
            BASE,
            BASE + 20 * STEP,
            strategy_id="s1",
            i_understand_this_is_holdout_data=True,
            db_path=db_path,
            holdout_config_path=before_config_path,
            runs_path=runs_path,
        )


def test_holdout_klines_with_side_before_does_not_burn_the_claim_on_a_rejected_range(
    db_path, before_config_path, runs_path
):
    with pytest.raises(ValueError):
        load_holdout_klines(
            BASE,
            BASE + 20 * STEP,
            strategy_id="s1",
            i_understand_this_is_holdout_data=True,
            db_path=db_path,
            holdout_config_path=before_config_path,
            runs_path=runs_path,
        )

    assert not runs_path.exists() or runs_path.read_text(encoding="utf-8").strip() == ""


def test_holdout_klines_with_side_before_still_enforces_single_access(db_path, before_config_path, runs_path):
    load_holdout_klines(
        BASE,
        CUTOFF,
        strategy_id="s1",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=before_config_path,
        runs_path=runs_path,
    )

    with pytest.raises(HoldoutAlreadyClaimedError):
        load_holdout_klines(
            BASE,
            CUTOFF,
            strategy_id="s1",
            i_understand_this_is_holdout_data=True,
            db_path=db_path,
            holdout_config_path=before_config_path,
            runs_path=runs_path,
        )


def test_holdout_klines_with_side_before_still_requires_the_explicit_keyword(
    db_path, before_config_path, runs_path
):
    with pytest.raises(ValueError):
        load_holdout_klines(
            BASE,
            CUTOFF,
            strategy_id="s1",
            i_understand_this_is_holdout_data=False,
            db_path=db_path,
            holdout_config_path=before_config_path,
            runs_path=runs_path,
        )


def test_the_two_sides_partition_the_data_with_no_overlap_and_no_hole(db_path, before_config_path, runs_path):
    research = load_research_klines(
        BASE, BASE + 20 * STEP, db_path=db_path, holdout_config_path=before_config_path
    )
    holdout = load_holdout_klines(
        BASE,
        CUTOFF,
        strategy_id="s-partition",
        i_understand_this_is_holdout_data=True,
        db_path=db_path,
        holdout_config_path=before_config_path,
        runs_path=runs_path,
    )

    research_times = {k.open_time for k in research}
    holdout_times = {k.open_time for k in holdout}
    assert research_times & holdout_times == set()
    assert len(research_times | holdout_times) == 20  # every stored bar, exactly once


# --- config validation -------------------------------------------------------


def test_load_holdout_config_rejects_an_unknown_holdout_side(tmp_path):
    path = _write_config(tmp_path / "holdout_bogus.json", holdout_side="trailing")

    with pytest.raises(ValueError):
        load_holdout_config(path)


def test_load_holdout_config_returns_the_file_contents_unmodified(tmp_path):
    # No default is injected into the returned dict -- a config without
    # `holdout_side` must still read back exactly as written, so nothing
    # downstream can start depending on a key the committed 15m/1h files
    # don't actually have.
    path = _write_config(tmp_path / "holdout_plain.json")

    config = load_holdout_config(path)

    assert "holdout_side" not in config
    assert config["holdout_cutoff_ms"] == CUTOFF


def test_load_holdout_config_accepts_both_valid_sides(tmp_path):
    for side in ("before", "after"):
        path = _write_config(tmp_path / f"holdout_{side}.json", holdout_side=side)
        assert load_holdout_config(path)["holdout_side"] == side


def test_clamp_research_range_rejects_an_unknown_side_rather_than_assuming_before():
    # CodeRabbit review finding on this task's PR. Both real callers
    # pre-validate via `resolve_holdout_side`, so there is no live path
    # that reaches this -- but an `if after: ... else: ...` shape would
    # treat a typo'd side as "before" and silently invert which end of
    # the range gets clamped, i.e. serve holdout data to a research
    # caller. That is precisely the failure this module's fail-loud
    # discipline exists to prevent, so the private helper enforces it
    # itself rather than trusting every future caller to have validated.
    with pytest.raises(ValueError):
        _clamp_research_range("test", BASE, BASE + 20 * STEP, CUTOFF, "trailing")


def test_clamp_research_range_clamps_the_opposite_end_for_each_valid_side():
    after_start, after_end = _clamp_research_range("test", BASE, BASE + 20 * STEP, CUTOFF, "after")
    before_start, before_end = _clamp_research_range("test", BASE, BASE + 20 * STEP, CUTOFF, "before")

    assert (after_start, after_end) == (BASE, CUTOFF)  # end clamped down
    assert (before_start, before_end) == (CUTOFF, BASE + 20 * STEP)  # start clamped up


# --- the real, committed config files ---------------------------------------


def test_committed_15m_and_1h_configs_have_no_holdout_side_and_stay_trailing():
    # The behavioral half of "byte-for-byte unaffected": both pre-existing
    # configs are silent on `holdout_side`, so both resolve to the trailing
    # "after" semantics they had before this feature existed.
    for path in ("configs/research/holdout.json", "configs/research/holdout_1h.json"):
        config = load_holdout_config(_repo_path(path))
        assert "holdout_side" not in config, path


def test_committed_1d_config_is_a_before_side_daily_config():
    config = load_holdout_config(_repo_path("configs/research/holdout_1d.json"))

    assert config["interval"] == "1d"
    assert config["symbol"] == "BTC-USDT"
    assert config["holdout_side"] == "before"
    # UTC-midnight aligned, per data/_grid.py's 1d grid.
    assert config["holdout_cutoff_ms"] % 86_400_000 == 0
    assert config["rationale"].strip()
