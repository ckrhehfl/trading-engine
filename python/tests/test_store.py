from decimal import Decimal

import pytest

from data.bingx_funding import FUNDING_INTERVAL_MS, FundingRow
from data.bingx_klines import KlineRow
from data.store import (
    connect,
    fetch_funding_rates,
    fetch_klines,
    find_missing_funding_ranges,
    find_missing_ranges,
    upsert_funding_rates,
    upsert_klines,
)

STEP = 900_000
BASE = (1_700_000_000_000 // STEP) * STEP

FUNDING_STEP = FUNDING_INTERVAL_MS
FUNDING_BASE = (1_700_000_000_000 // FUNDING_STEP) * FUNDING_STEP


def _row(offset: int, price: str = "100") -> KlineRow:
    return KlineRow(
        open_time_ms=BASE + offset * STEP,
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=Decimal("1"),
    )


def _funding_row(offset: int, rate: str = "0.0001", mark_price: str = "50000") -> FundingRow:
    return FundingRow(
        funding_time_ms=FUNDING_BASE + offset * FUNDING_STEP,
        funding_rate=Decimal(rate),
        mark_price=Decimal(mark_price),
    )


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


# ---------------------------------------------------------------------------
# connect / schema
# ---------------------------------------------------------------------------


def test_connect_creates_the_klines_table(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(klines)")}
    assert columns == {
        "symbol",
        "interval",
        "open_time_ms",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "fetched_at",
    }


def test_connect_is_idempotent_against_an_existing_database(tmp_path):
    db_path = tmp_path / "klines.sqlite3"
    conn_a = connect(db_path)
    upsert_klines(conn_a, "BTC-USDT", "15m", [_row(0)])
    conn_a.close()

    conn_b = connect(db_path)  # must not fail or wipe existing data
    rows = list(conn_b.execute("SELECT open_time_ms FROM klines"))
    conn_b.close()

    assert rows == [(BASE,)]


def test_connect_creates_missing_parent_directory(tmp_path):
    db_path = tmp_path / "nested" / "var" / "klines.sqlite3"
    conn = connect(db_path)
    conn.close()

    assert db_path.exists()


# ---------------------------------------------------------------------------
# upsert_klines
# ---------------------------------------------------------------------------


def test_upsert_klines_inserts_new_rows_and_returns_the_inserted_count(conn):
    inserted = upsert_klines(conn, "BTC-USDT", "15m", [_row(0), _row(1), _row(2)])

    assert inserted == 3
    stored = list(conn.execute("SELECT open_time_ms FROM klines ORDER BY open_time_ms"))
    assert stored == [(BASE,), (BASE + STEP,), (BASE + 2 * STEP,)]


def test_upsert_klines_is_a_no_op_for_rows_already_present(conn):
    upsert_klines(conn, "BTC-USDT", "15m", [_row(0), _row(1)])

    second_inserted = upsert_klines(conn, "BTC-USDT", "15m", [_row(0), _row(1), _row(2)])

    assert second_inserted == 1  # only _row(2) is genuinely new
    count = conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
    assert count == 3  # no duplicate rows from the overlapping re-fetch


def test_upsert_klines_with_empty_rows_is_a_no_op(conn):
    assert upsert_klines(conn, "BTC-USDT", "15m", []) == 0
    assert conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0] == 0


def test_upsert_klines_stores_decimal_values_as_exact_text_not_float(conn):
    # A value that would be silently altered by a float round-trip --
    # this is the same class of bug schemas/_types.py's
    # PositiveDecimalString exists to prevent on the Java/Jackson wire
    # side; here it's about SQLite storage instead.
    precise = Decimal("64175.123456789012345")
    row = KlineRow(open_time_ms=BASE, open=precise, high=precise, low=precise, close=precise, volume=precise)

    upsert_klines(conn, "BTC-USDT", "15m", [row])

    raw_open = conn.execute("SELECT open, typeof(open) FROM klines WHERE open_time_ms = ?", (BASE,)).fetchone()
    assert raw_open[1] == "text"
    assert Decimal(raw_open[0]) == precise
    assert raw_open[0] == str(precise)  # exact text, not a reformatted/rounded value


def test_upsert_klines_scopes_rows_to_the_given_symbol(conn):
    upsert_klines(conn, "BTC-USDT", "15m", [_row(0)])
    upsert_klines(conn, "ETH-USDT", "15m", [_row(0)])  # same open_time_ms, different symbol

    count = conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
    assert count == 2  # both kept -- PK includes symbol, so this isn't a PK collision


# ---------------------------------------------------------------------------
# find_missing_ranges
# ---------------------------------------------------------------------------


def test_find_missing_ranges_returns_the_full_range_when_store_is_empty(conn):
    gaps = find_missing_ranges(conn, "BTC-USDT", "15m", BASE, BASE + 5 * STEP)

    assert gaps == [(BASE, BASE + 5 * STEP)]


def test_find_missing_ranges_returns_no_gaps_when_fully_populated(conn):
    upsert_klines(conn, "BTC-USDT", "15m", [_row(i) for i in range(5)])

    gaps = find_missing_ranges(conn, "BTC-USDT", "15m", BASE, BASE + 5 * STEP)

    assert gaps == []


def test_find_missing_ranges_detects_a_leading_gap_when_backfilling_earlier_history(conn):
    # Existing data starts partway through the queried range -- e.g. a
    # prior sync only ever fetched from bar 3 onward, and we're now
    # backfilling further into the past.
    upsert_klines(conn, "BTC-USDT", "15m", [_row(i) for i in range(3, 6)])

    gaps = find_missing_ranges(conn, "BTC-USDT", "15m", BASE, BASE + 6 * STEP)

    assert gaps == [(BASE, BASE + 3 * STEP)]


def test_find_missing_ranges_detects_a_trailing_gap_when_extending_forward(conn):
    # Existing data ends partway through the queried range -- extending
    # the sync window forward to catch up to "now".
    upsert_klines(conn, "BTC-USDT", "15m", [_row(i) for i in range(0, 3)])

    gaps = find_missing_ranges(conn, "BTC-USDT", "15m", BASE, BASE + 6 * STEP)

    assert gaps == [(BASE + 3 * STEP, BASE + 6 * STEP)]


def test_find_missing_ranges_detects_a_mid_range_gap_from_an_interrupted_fetch(conn):
    upsert_klines(conn, "BTC-USDT", "15m", [_row(i) for i in [0, 1, 4, 5]])  # bars 2, 3 missing

    gaps = find_missing_ranges(conn, "BTC-USDT", "15m", BASE, BASE + 6 * STEP)

    assert gaps == [(BASE + 2 * STEP, BASE + 4 * STEP)]


def test_find_missing_ranges_detects_multiple_disjoint_gaps_in_one_pass(conn):
    upsert_klines(conn, "BTC-USDT", "15m", [_row(i) for i in [1, 2, 5]])  # missing 0, 3-4, 6

    gaps = find_missing_ranges(conn, "BTC-USDT", "15m", BASE, BASE + 7 * STEP)

    assert gaps == [
        (BASE, BASE + 1 * STEP),
        (BASE + 3 * STEP, BASE + 5 * STEP),
        (BASE + 6 * STEP, BASE + 7 * STEP),
    ]


def test_find_missing_ranges_is_scoped_to_symbol_and_interval(conn):
    upsert_klines(conn, "ETH-USDT", "15m", [_row(i) for i in range(5)])  # different symbol

    gaps = find_missing_ranges(conn, "BTC-USDT", "15m", BASE, BASE + 5 * STEP)

    assert gaps == [(BASE, BASE + 5 * STEP)]  # ETH-USDT rows don't count as BTC-USDT coverage


def test_find_missing_ranges_rejects_misaligned_start_ms(conn):
    with pytest.raises(ValueError):
        find_missing_ranges(conn, "BTC-USDT", "15m", BASE + 1, BASE + STEP)


def test_find_missing_ranges_rejects_misaligned_end_ms(conn):
    with pytest.raises(ValueError):
        find_missing_ranges(conn, "BTC-USDT", "15m", BASE, BASE + STEP + 1)


def test_find_missing_ranges_rejects_inverted_range(conn):
    with pytest.raises(ValueError):
        find_missing_ranges(conn, "BTC-USDT", "15m", BASE + STEP, BASE)


# ---------------------------------------------------------------------------
# fetch_klines
# ---------------------------------------------------------------------------


def test_fetch_klines_returns_rows_in_range_ordered_ascending(conn):
    # Inserted out of order -- the read path must sort, not trust
    # insertion order.
    upsert_klines(conn, "BTC-USDT", "15m", [_row(2), _row(0), _row(1)])

    rows = fetch_klines(conn, "BTC-USDT", "15m", BASE, BASE + 3 * STEP)

    assert [row.open_time_ms for row in rows] == [BASE, BASE + STEP, BASE + 2 * STEP]


def test_fetch_klines_excludes_rows_outside_the_half_open_range(conn):
    upsert_klines(conn, "BTC-USDT", "15m", [_row(i) for i in range(5)])

    rows = fetch_klines(conn, "BTC-USDT", "15m", BASE + STEP, BASE + 3 * STEP)

    assert [row.open_time_ms for row in rows] == [BASE + STEP, BASE + 2 * STEP]  # end excluded


def test_fetch_klines_returns_empty_list_when_nothing_stored(conn):
    assert fetch_klines(conn, "BTC-USDT", "15m", BASE, BASE + 5 * STEP) == []


def test_fetch_klines_is_scoped_to_symbol_and_interval(conn):
    upsert_klines(conn, "ETH-USDT", "15m", [_row(0)])  # different symbol
    upsert_klines(conn, "BTC-USDT", "15m", [_row(0)])

    rows = fetch_klines(conn, "BTC-USDT", "15m", BASE, BASE + STEP)

    assert len(rows) == 1


def test_fetch_klines_returns_exact_decimal_values_not_float_rounded(conn):
    precise = Decimal("64175.123456789012345")
    row = KlineRow(open_time_ms=BASE, open=precise, high=precise, low=precise, close=precise, volume=precise)
    upsert_klines(conn, "BTC-USDT", "15m", [row])

    rows = fetch_klines(conn, "BTC-USDT", "15m", BASE, BASE + STEP)

    assert rows[0].open == precise
    assert rows[0].high == precise
    assert rows[0].low == precise
    assert rows[0].close == precise
    assert rows[0].volume == precise


def test_fetch_klines_rejects_misaligned_range(conn):
    with pytest.raises(ValueError):
        fetch_klines(conn, "BTC-USDT", "15m", BASE + 1, BASE + STEP)


def test_fetch_klines_rejects_inverted_range(conn):
    with pytest.raises(ValueError):
        fetch_klines(conn, "BTC-USDT", "15m", BASE + STEP, BASE)


# ---------------------------------------------------------------------------
# funding_rates table -- schema, upsert_funding_rates, find_missing_funding_
# ranges, fetch_funding_rates. Mirrors the klines tests above; funding rows
# have no "interval" the way klines do (see bingx_funding.py's docstring),
# so these functions are scoped by (symbol, funding_time_ms) only.
# ---------------------------------------------------------------------------


def test_connect_creates_the_funding_rates_table(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(funding_rates)")}
    assert columns == {
        "symbol",
        "funding_time_ms",
        "funding_rate",
        "mark_price",
        "fetched_at",
    }


def test_connect_creates_both_tables_from_a_single_call(tmp_path):
    # klines and funding_rates share one cache file/connection -- a
    # research script joining both needs only one connect() call, not
    # two separate DB files.
    db_path = tmp_path / "shared.sqlite3"
    conn = connect(db_path)
    upsert_klines(conn, "BTC-USDT", "15m", [_row(0)])
    upsert_funding_rates(conn, "BTC-USDT", [_funding_row(0)])
    kline_count = conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
    funding_count = conn.execute("SELECT COUNT(*) FROM funding_rates").fetchone()[0]
    conn.close()

    assert kline_count == 1
    assert funding_count == 1


def test_upsert_funding_rates_inserts_new_rows_and_returns_the_inserted_count(conn):
    inserted = upsert_funding_rates(conn, "BTC-USDT", [_funding_row(0), _funding_row(1), _funding_row(2)])

    assert inserted == 3
    stored = list(conn.execute("SELECT funding_time_ms FROM funding_rates ORDER BY funding_time_ms"))
    assert stored == [(FUNDING_BASE,), (FUNDING_BASE + FUNDING_STEP,), (FUNDING_BASE + 2 * FUNDING_STEP,)]


def test_upsert_funding_rates_is_a_no_op_for_rows_already_present(conn):
    upsert_funding_rates(conn, "BTC-USDT", [_funding_row(0), _funding_row(1)])

    second_inserted = upsert_funding_rates(conn, "BTC-USDT", [_funding_row(0), _funding_row(1), _funding_row(2)])

    assert second_inserted == 1
    count = conn.execute("SELECT COUNT(*) FROM funding_rates").fetchone()[0]
    assert count == 3


def test_upsert_funding_rates_with_empty_rows_is_a_no_op(conn):
    assert upsert_funding_rates(conn, "BTC-USDT", []) == 0
    assert conn.execute("SELECT COUNT(*) FROM funding_rates").fetchone()[0] == 0


def test_upsert_funding_rates_stores_decimal_values_as_exact_text_not_float(conn):
    precise = Decimal("0.000123456789012345678")
    row = FundingRow(funding_time_ms=FUNDING_BASE, funding_rate=precise, mark_price=precise)

    upsert_funding_rates(conn, "BTC-USDT", [row])

    raw = conn.execute(
        "SELECT funding_rate, typeof(funding_rate) FROM funding_rates WHERE funding_time_ms = ?", (FUNDING_BASE,)
    ).fetchone()
    assert raw[1] == "text"
    assert Decimal(raw[0]) == precise
    assert raw[0] == str(precise)


def test_upsert_funding_rates_scopes_rows_to_the_given_symbol(conn):
    upsert_funding_rates(conn, "BTC-USDT", [_funding_row(0)])
    upsert_funding_rates(conn, "ETH-USDT", [_funding_row(0)])

    count = conn.execute("SELECT COUNT(*) FROM funding_rates").fetchone()[0]
    assert count == 2


def test_find_missing_funding_ranges_returns_the_full_range_when_store_is_empty(conn):
    gaps = find_missing_funding_ranges(conn, "BTC-USDT", FUNDING_BASE, FUNDING_BASE + 5 * FUNDING_STEP)

    assert gaps == [(FUNDING_BASE, FUNDING_BASE + 5 * FUNDING_STEP)]


def test_find_missing_funding_ranges_returns_no_gaps_when_fully_populated(conn):
    upsert_funding_rates(conn, "BTC-USDT", [_funding_row(i) for i in range(5)])

    gaps = find_missing_funding_ranges(conn, "BTC-USDT", FUNDING_BASE, FUNDING_BASE + 5 * FUNDING_STEP)

    assert gaps == []


def test_find_missing_funding_ranges_detects_a_mid_range_gap(conn):
    upsert_funding_rates(conn, "BTC-USDT", [_funding_row(i) for i in [0, 1, 4, 5]])

    gaps = find_missing_funding_ranges(conn, "BTC-USDT", FUNDING_BASE, FUNDING_BASE + 6 * FUNDING_STEP)

    assert gaps == [(FUNDING_BASE + 2 * FUNDING_STEP, FUNDING_BASE + 4 * FUNDING_STEP)]


def test_find_missing_funding_ranges_is_scoped_to_symbol(conn):
    upsert_funding_rates(conn, "ETH-USDT", [_funding_row(i) for i in range(5)])

    gaps = find_missing_funding_ranges(conn, "BTC-USDT", FUNDING_BASE, FUNDING_BASE + 5 * FUNDING_STEP)

    assert gaps == [(FUNDING_BASE, FUNDING_BASE + 5 * FUNDING_STEP)]


def test_find_missing_funding_ranges_accepts_a_range_not_aligned_to_funding_interval_ms(conn):
    # Deliberately NOT a rejection test, unlike find_missing_ranges'
    # klines equivalent: real historical funding timestamps are not
    # always aligned to FUNDING_INTERVAL_MS (a genuine finding from a
    # real backfill -- see bingx_funding.py's module docstring and
    # .planning/sr-m-funding-rate-pipeline.md), so this module must
    # accept an off-grid start_ms/end_ms rather than reject it -- e.g. a
    # gap boundary this same function previously returned, fed back in
    # by a caller like backfill_funding.sync_funding_range.
    gaps = find_missing_funding_ranges(conn, "BTC-USDT", FUNDING_BASE + 1, FUNDING_BASE + FUNDING_STEP + 1)

    assert gaps == [(FUNDING_BASE + 1, FUNDING_BASE + FUNDING_STEP + 1)]


def test_find_missing_funding_ranges_rejects_inverted_range(conn):
    with pytest.raises(ValueError):
        find_missing_funding_ranges(conn, "BTC-USDT", FUNDING_BASE + FUNDING_STEP, FUNDING_BASE)


def test_fetch_funding_rates_returns_rows_in_range_ordered_ascending(conn):
    upsert_funding_rates(conn, "BTC-USDT", [_funding_row(2), _funding_row(0), _funding_row(1)])

    rows = fetch_funding_rates(conn, "BTC-USDT", FUNDING_BASE, FUNDING_BASE + 3 * FUNDING_STEP)

    assert [r.funding_time_ms for r in rows] == [FUNDING_BASE, FUNDING_BASE + FUNDING_STEP, FUNDING_BASE + 2 * FUNDING_STEP]


def test_fetch_funding_rates_excludes_rows_outside_the_half_open_range(conn):
    upsert_funding_rates(conn, "BTC-USDT", [_funding_row(i) for i in range(5)])

    rows = fetch_funding_rates(conn, "BTC-USDT", FUNDING_BASE + FUNDING_STEP, FUNDING_BASE + 3 * FUNDING_STEP)

    assert [r.funding_time_ms for r in rows] == [FUNDING_BASE + FUNDING_STEP, FUNDING_BASE + 2 * FUNDING_STEP]


def test_fetch_funding_rates_returns_empty_list_when_nothing_stored(conn):
    assert fetch_funding_rates(conn, "BTC-USDT", FUNDING_BASE, FUNDING_BASE + 5 * FUNDING_STEP) == []


def test_fetch_funding_rates_returns_exact_decimal_values_not_float_rounded(conn):
    precise = Decimal("0.000123456789012345678")
    row = FundingRow(funding_time_ms=FUNDING_BASE, funding_rate=precise, mark_price=precise)
    upsert_funding_rates(conn, "BTC-USDT", [row])

    rows = fetch_funding_rates(conn, "BTC-USDT", FUNDING_BASE, FUNDING_BASE + FUNDING_STEP)

    assert rows[0].funding_rate == precise
    assert rows[0].mark_price == precise


def test_fetch_funding_rates_accepts_a_range_not_aligned_to_funding_interval_ms(conn):
    row = _funding_row(0)
    # Stored row sits inside an off-grid query range -- must not be
    # rejected. See test_find_missing_funding_ranges_accepts_a_range_
    # not_aligned_to_funding_interval_ms above for why.
    upsert_funding_rates(conn, "BTC-USDT", [row])

    rows = fetch_funding_rates(conn, "BTC-USDT", FUNDING_BASE - 1, FUNDING_BASE + FUNDING_STEP - 1)

    assert [r.funding_time_ms for r in rows] == [row.funding_time_ms]


def test_fetch_funding_rates_rejects_inverted_range(conn):
    with pytest.raises(ValueError):
        fetch_funding_rates(conn, "BTC-USDT", FUNDING_BASE + FUNDING_STEP, FUNDING_BASE)
