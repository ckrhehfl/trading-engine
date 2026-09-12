"""The reader must parse what the Java side actually emits.

The two halves are written in different languages and joined only by a
log-line format, so the format is the contract and nothing checks it at
compile time. The load-bearing test here is
`test_parses_the_exact_line_java_emits`, which uses a line copied from
`CostDivergence.toLogLine`'s own construction rather than one invented to
suit the parser.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from live.cost_divergence import (
    DEFAULT_REPO_ROOT,
    MIN_OBSERVATIONS_FOR_TREND,
    Observation,
    _sort_key,
    parse_line,
    read_observations,
    summarise,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
JAVA_TEST = REPO_ROOT / "java/execution/src/test/java/engine/execution/CostDivergenceTest.java"

# The SLF4J prefix the session log actually wraps the line in.
LOG_PREFIX = "[paper-trading-loop] INFO engine.execution.ExchangeOrderExecutor - "


def canonical_line_from_java() -> str:
    """Read `CANONICAL_LOG_LINE` out of the Java test that pins it.

    Not a copy. A hand-copied example keeps passing while the real
    integration breaks, and the first version of the Java test proved the
    point by getting its own arithmetic wrong (6.928 against a real
    6.928628) -- an error no amount of reading either side would have
    caught. Extracting the literal means a format change on the Java side
    fails here rather than silently in production.
    """
    source = JAVA_TEST.read_text(encoding="utf-8")
    start = source.index("CANONICAL_LOG_LINE =")
    end = source.index(";", start)
    # Concatenated Java string literals -> the string they build.
    return "".join(re.findall(r'"([^"]*)"', source[start:end]))


def test_parses_the_exact_line_java_emits():
    obs = parse_line(LOG_PREFIX + canonical_line_from_java())
    assert obs is not None, "the reader cannot parse what the writer emits"
    assert obs.client_order_id == "00000000-0000-4000-8000-000000000001"
    assert obs.symbol == "BTC-USDT"
    assert obs.notional == Decimal("7.93808")
    assert obs.realised_fee_bps == Decimal("6.928628")
    assert obs.divergence_bps == Decimal("1.928628")


def test_an_unrelated_log_line_is_not_an_observation():
    assert parse_line("[paper-trading-loop] INFO ... tick complete: equity=100000") is None
    assert parse_line("") is None


def test_a_truncated_line_is_skipped_rather_than_raising():
    """A log is written concurrently and can be interleaved or cut. A
    reader that raises on one bad line loses the whole series with it."""
    assert parse_line("cost_divergence clientOrderId=abc symbol=BTC-USDT") is None
    assert parse_line("cost_divergence notional=notanumber modelledFeeBps=5") is None


def test_a_non_finite_number_is_skipped_rather_than_poisoning_the_report():
    """`Decimal("NaN")` constructs fine and then makes `median`/`min`/`max`
    raise, so one malformed line would kill the entire report — the opposite
    of the tolerance this module promises. Verified directly: `median([NaN,
    1, 2])` raises `InvalidOperation`."""
    base = canonical_line_from_java()
    for bad in ("NaN", "Infinity", "-Infinity"):
        assert parse_line(base.replace("realisedFeeBps=6.928628", f"realisedFeeBps={bad}")) is None
        assert parse_line(base.replace("notional=7.93808", f"notional={bad}")) is None


def test_a_report_survives_a_poisoned_line_beside_good_ones(tmp_path: Path):
    """The whole point of skipping: the good observations still summarise."""
    sessions = tmp_path / "var/live/sessions"
    sessions.mkdir(parents=True)
    good = LOG_PREFIX + canonical_line_from_java()
    poisoned = good.replace("realisedFeeBps=6.928628", "realisedFeeBps=NaN").replace(
        "clientOrderId=00000000-0000-4000-8000-000000000001",
        "clientOrderId=00000000-0000-4000-8000-000000000002",
    )
    (sessions / "a.log").write_text(good + "\n" + poisoned + "\n", encoding="utf-8")

    observations = read_observations(tmp_path)

    assert len(observations) == 1
    assert summarise(observations)["n"] == 1


def _obs(order_id: str, divergence: str, at: str) -> Observation:
    return Observation(
        client_order_id=order_id,
        symbol="BTC-USDT",
        notional=Decimal("1000"),
        modelled_fee_bps=Decimal("5"),
        realised_fee_bps=Decimal(5) + Decimal(divergence),
        divergence_bps=Decimal(divergence),
        observed_at=at,
    )


def test_one_order_counts_once_even_if_the_log_is_read_twice(tmp_path: Path):
    """`n` is what every judgement rests on, so double-counting a
    re-read or a restarted session would overstate the evidence."""
    sessions = tmp_path / "var/live/sessions"
    sessions.mkdir(parents=True)
    line = LOG_PREFIX + canonical_line_from_java()
    (sessions / "a.log").write_text(line + "\n" + line + "\n", encoding="utf-8")
    (sessions / "b.log").write_text(line + "\n", encoding="utf-8")

    assert len(read_observations(tmp_path)) == 1


def test_ordering_is_chronological_not_lexicographic():
    """`Instant.toString()` omits trailing zeros, so a stamp exactly on the
    second sorts *after* one 100ms earlier when compared as text. Verified
    directly: `sorted(["...00:00:00Z", "...00:00:00.100Z"])` puts the
    fractional one first, which is backwards.

    It matters because `summarise` reads `modelled_fee_bps` off the last
    element and reports first/last timestamps."""
    on_the_second = _obs("id-a", "1", "2026-09-12T00:00:00Z")
    hundred_ms_later = _obs("id-b", "2", "2026-09-12T00:00:00.100Z")

    # Text order disagrees with time order -- that is the whole trap.
    assert hundred_ms_later.observed_at < on_the_second.observed_at

    ordered = sorted([hundred_ms_later, on_the_second], key=_sort_key)
    assert [o.client_order_id for o in ordered] == ["id-a", "id-b"]


def test_read_observations_returns_them_in_chronological_order(tmp_path: Path):
    """Through the real path, not just the key function.

    The first version of the test above called `_sort_key` directly, so
    reverting `read_observations` to a lexicographic sort left every test
    green -- an inert test guarding against an ordering bug, which is the
    shape this repository keeps rediscovering."""
    sessions = tmp_path / "var/live/sessions"
    sessions.mkdir(parents=True)
    base = LOG_PREFIX + canonical_line_from_java()

    def line(order_id: str, stamp: str) -> str:
        return base.replace(
            "clientOrderId=00000000-0000-4000-8000-000000000001",
            f"clientOrderId={order_id}",
        ).replace("observedAt=2026-09-12T10:07:00.351091713Z", f"observedAt={stamp}")

    # The later one carries a fraction, so text order and time order disagree.
    (sessions / "a.log").write_text(
        line("00000000-0000-4000-8000-00000000000b", "2026-09-12T00:00:00.100Z") + "\n"
        + line("00000000-0000-4000-8000-00000000000a", "2026-09-12T00:00:00Z") + "\n",
        encoding="utf-8",
    )

    ids = [o.client_order_id[-1] for o in read_observations(tmp_path)]
    assert ids == ["a", "b"], "observations must come back oldest-first by time, not by text"


def test_an_unparseable_timestamp_sorts_last_rather_than_raising():
    """Same tolerance as everywhere else here: one bad line must not take
    the report down with it."""
    good = _obs("id-good", "1", "2026-09-12T00:00:00Z")
    bad = _obs("id-bad", "1", "not-a-timestamp")

    ordered = sorted([bad, good], key=_sort_key)
    assert [o.client_order_id for o in ordered] == ["id-good", "id-bad"]


def test_the_default_root_is_the_repository_not_the_working_directory():
    """The documented invocation is `cd python && ... -m live.cost_divergence`,
    so a default of "." resolved against `python/` -- where no session log
    has ever existed. The tool would have reported n=0 on a real machine and
    read as "no data yet" rather than "looking in the wrong place"."""
    assert (DEFAULT_REPO_ROOT / "python").is_dir(), DEFAULT_REPO_ROOT
    assert (DEFAULT_REPO_ROOT / "CLAUDE.md").is_file(), DEFAULT_REPO_ROOT
    assert DEFAULT_REPO_ROOT.name != "python"


def test_partial_fills_of_one_order_are_separate_observations(tmp_path: Path):
    """A partially filled order produces one cost observation per fill.

    Keying dedup on `clientOrderId` alone threw all but the first away --
    a real loss in a series whose value is its distribution, and one no
    existing test caught because every fixture used a single fill."""
    sessions = tmp_path / "var/live/sessions"
    sessions.mkdir(parents=True)
    base = LOG_PREFIX + canonical_line_from_java()

    same_order = "clientOrderId=00000000-0000-4000-8000-000000000001"
    first = base.replace("observedAt=2026-09-12T10:07:00.351091713Z", "observedAt=2026-09-12T10:07:00Z")
    second = base.replace("observedAt=2026-09-12T10:07:00.351091713Z", "observedAt=2026-09-12T10:12:00Z")
    assert same_order in first and same_order in second

    (sessions / "a.log").write_text(first + "\n" + second + "\n", encoding="utf-8")

    observations = read_observations(tmp_path)
    assert len(observations) == 2, "both fills of one order are observations"
    assert observations[0].observed_at < observations[1].observed_at


def test_the_same_line_read_twice_is_still_one_observation(tmp_path: Path):
    """The reason dedup exists at all must survive making the key finer:
    a re-read log, or a session restarted onto the same file, must not
    inflate `n`."""
    sessions = tmp_path / "var/live/sessions"
    sessions.mkdir(parents=True)
    line = LOG_PREFIX + canonical_line_from_java()
    (sessions / "a.log").write_text(line + "\n" + line + "\n", encoding="utf-8")
    (sessions / "b.log").write_text(line + "\n", encoding="utf-8")

    assert len(read_observations(tmp_path)) == 1


def test_no_observations_says_so_rather_than_summarising_nothing():
    summary = summarise([])
    assert summary["n"] == 0
    assert "no observations yet" in summary["verdict"]


def test_below_the_threshold_it_refuses_to_call_a_trend():
    """The recorded error this guards against: concluding about a domain
    from a handful of points. The figures are still reported — refusing
    to characterise a direction is not refusing to show the data."""
    few = [_obs(f"id-{i}", "2", f"2026-09-{i + 1:02d}T00:00:00Z") for i in range(3)]
    summary = summarise(few)

    assert summary["n"] == 3
    assert summary["divergence_bps_median"] == "2"
    assert "too few to characterise a trend" in summary["verdict"]


def test_above_the_threshold_a_positive_median_is_called_out():
    many = [
        _obs(f"id-{i}", "2", f"2026-09-{i + 1:02d}T00:00:00Z")
        for i in range(MIN_OBSERVATIONS_FOR_TREND)
    ]
    summary = summarise(many)

    assert "erodes a backtested edge" in summary["verdict"]


def test_above_the_threshold_a_non_positive_median_is_not():
    many = [
        _obs(f"id-{i}", "-1", f"2026-09-{i + 1:02d}T00:00:00Z")
        for i in range(MIN_OBSERVATIONS_FOR_TREND)
    ]
    summary = summarise(many)

    assert "not optimistic" in summary["verdict"]
