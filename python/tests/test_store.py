from decimal import Decimal

import pytest

from data.bingx_funding import FUNDING_INTERVAL_MS, FundingRow
from data.bingx_klines import KlineRow
from data.fred_client import ObservationRow
from data.store import (
    connect,
    fetch_funding_rates,
    fetch_klines,
    fetch_macro_observations,
    find_missing_funding_ranges,
    find_missing_macro_ranges,
    find_missing_ranges,
    upsert_funding_rates,
    upsert_klines,
    upsert_macro_observations,
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
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
    }


def test_connect_migrates_an_old_schema_database_adding_order_flow_columns_without_losing_rows(tmp_path):
    # A real pre-Task-S5 database (the OLD CREATE TABLE shape, built by
    # hand here to simulate it -- not connect(), which already includes
    # the migration) must gain the two new nullable columns on its next
    # connect(), with every pre-existing row's data completely intact and
    # NULL for the two new columns specifically (never a fabricated
    # default).
    db_path = tmp_path / "old_klines.sqlite3"
    import sqlite3

    raw = sqlite3.connect(db_path)
    raw.execute(
        """
        CREATE TABLE klines (
          symbol TEXT NOT NULL,
          interval TEXT NOT NULL,
          open_time_ms INTEGER NOT NULL,
          open TEXT NOT NULL,
          high TEXT NOT NULL,
          low TEXT NOT NULL,
          close TEXT NOT NULL,
          volume TEXT NOT NULL,
          fetched_at TEXT NOT NULL,
          PRIMARY KEY (symbol, interval, open_time_ms)
        );
        """
    )
    raw.execute(
        "INSERT INTO klines (symbol, interval, open_time_ms, open, high, low, close, volume, fetched_at) "
        "VALUES ('BTC-USDT', '15m', ?, '100', '101', '99', '100', '1', '2026-01-01T00:00:00+00:00')",
        (BASE,),
    )
    raw.commit()
    raw.close()

    migrated = connect(db_path)
    columns = {row[1] for row in migrated.execute("PRAGMA table_info(klines)")}
    assert "taker_buy_base_volume" in columns
    assert "taker_buy_quote_volume" in columns

    rows = fetch_klines(migrated, "BTC-USDT", "15m", BASE, BASE + STEP)
    migrated.close()

    assert len(rows) == 1
    assert rows[0].open == Decimal("100")  # pre-existing data intact
    assert rows[0].taker_buy_base_volume is None  # NULL, not fabricated
    assert rows[0].taker_buy_quote_volume is None


def test_connect_migration_is_idempotent_when_columns_already_exist(tmp_path):
    db_path = tmp_path / "klines.sqlite3"
    connect(db_path).close()
    # A second connect() against a database that already has the new
    # columns must not raise ("duplicate column name") and must not
    # touch existing data.
    conn = connect(db_path)
    upsert_klines(conn, "BTC-USDT", "15m", [_row(0)])
    conn.close()

    conn = connect(db_path)  # third connect() -- still a no-op migration
    rows = fetch_klines(conn, "BTC-USDT", "15m", BASE, BASE + STEP)
    conn.close()
    assert len(rows) == 1


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


def test_upsert_and_fetch_klines_round_trips_taker_buy_volume_for_a_binance_style_row(conn):
    row = KlineRow(
        open_time_ms=BASE,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("10"),
        taker_buy_base_volume=Decimal("6.25"),
        taker_buy_quote_volume=Decimal("625.5"),
    )

    upsert_klines(conn, "BINANCE-FUTURES:BTCUSDT", "1m", [row])
    fetched = fetch_klines(conn, "BINANCE-FUTURES:BTCUSDT", "1m", BASE, BASE + STEP)

    assert len(fetched) == 1
    assert fetched[0].taker_buy_base_volume == Decimal("6.25")
    assert fetched[0].taker_buy_quote_volume == Decimal("625.5")

    raw = conn.execute(
        "SELECT taker_buy_base_volume, typeof(taker_buy_base_volume) FROM klines WHERE open_time_ms = ?", (BASE,)
    ).fetchone()
    assert raw[1] == "text"  # exact TEXT storage, same convention as every other volume/price column
    assert raw[0] == "6.25"


def test_upsert_and_fetch_klines_round_trips_null_taker_buy_volume_for_a_bingx_style_row(conn):
    # Regression: a plain BingX-sourced row (no taker-buy fields at all,
    # matching every KlineRow constructed before Task S5) must still
    # round-trip cleanly with the two new columns genuinely NULL, not
    # some fabricated zero/empty-string default.
    upsert_klines(conn, "BTC-USDT", "15m", [_row(0)])
    fetched = fetch_klines(conn, "BTC-USDT", "15m", BASE, BASE + STEP)

    assert len(fetched) == 1
    assert fetched[0].taker_buy_base_volume is None
    assert fetched[0].taker_buy_quote_volume is None

    raw = conn.execute(
        "SELECT taker_buy_base_volume FROM klines WHERE open_time_ms = ?", (BASE,)
    ).fetchone()
    assert raw[0] is None  # a real SQL NULL, not the string "None"


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


def test_find_missing_funding_ranges_reports_no_gap_for_a_real_off_grid_row_between_two_aligned_rows(conn):
    # Real, confirmed-live pattern (see bingx_funding.py's module
    # docstring): a genuine off-grid row can sit strictly between two
    # otherwise-normal-cadence rows -- e.g. the real 2021-11-11T18:00:00Z
    # settlement, 2h after a 16:00:00Z row and 6h before the following
    # day's 00:00:00Z row. All three are actually stored here (unlike the
    # disclosed-limitation scenario documented in this function's own
    # docstring, where the off-grid row itself is missing) -- this test
    # only asserts the *present* off-grid row doesn't trigger a false
    # gap around itself, not that a *dropped* off-grid row would be
    # caught (it structurally can't be -- see the docstring).
    row_16 = FundingRow(funding_time_ms=FUNDING_BASE, funding_rate=Decimal("0.0001"), mark_price=Decimal("50000"))
    row_18_off_grid = FundingRow(
        funding_time_ms=FUNDING_BASE + 2 * 3_600_000, funding_rate=Decimal("0.0001"), mark_price=Decimal("50000")
    )
    row_next_00 = FundingRow(
        funding_time_ms=FUNDING_BASE + FUNDING_STEP, funding_rate=Decimal("0.0001"), mark_price=Decimal("50000")
    )
    upsert_funding_rates(conn, "BTC-USDT", [row_16, row_18_off_grid, row_next_00])

    gaps = find_missing_funding_ranges(conn, "BTC-USDT", FUNDING_BASE, FUNDING_BASE + FUNDING_STEP + 1)

    assert gaps == []


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


# ---------------------------------------------------------------------------
# macro_series table -- schema, upsert_macro_observations,
# find_missing_macro_ranges, fetch_macro_observations. Keyed by
# (series_id, observation_date), a calendar date not a fixed-step ms
# timestamp -- gap detection here has no fixed-grid equivalent to
# find_missing_ranges'/find_missing_funding_ranges' arithmetic-sequence
# diff, since FRED never returns a row at all for a weekend (see
# fred_client.py's module docstring). The tests below specifically
# exercise that weekday-vs-weekend distinction, not just mirror the
# klines/funding shape.
# ---------------------------------------------------------------------------

# A real Mon-Fri work week: 2026-01-05 (Mon) through 2026-01-09 (Fri).
# 2026-01-10/11 are the following Sat/Sun. 2026-01-12 is the next Monday.
MON = "2026-01-05"
TUE = "2026-01-06"
WED = "2026-01-07"
THU = "2026-01-08"
FRI = "2026-01-09"
SAT = "2026-01-10"
SUN = "2026-01-11"
NEXT_MON = "2026-01-12"
WORK_WEEK = [MON, TUE, WED, THU, FRI]

# 2026-01-01 is a real weekday US holiday (New Year's Day observed) --
# used for the "." missing-value-marker tests below.
HOLIDAY_WEEKDAY = "2026-01-01"


def _obs(observation_date: str, value: str | None = "4.0") -> ObservationRow:
    return ObservationRow(observation_date=observation_date, value=None if value is None else Decimal(value))


def test_connect_creates_the_macro_series_table(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(macro_series)")}
    assert columns == {"series_id", "observation_date", "value", "fetched_at"}


def test_connect_creates_all_three_tables_from_a_single_call(tmp_path):
    db_path = tmp_path / "shared.sqlite3"
    conn = connect(db_path)
    upsert_klines(conn, "BTC-USDT", "15m", [_row(0)])
    upsert_funding_rates(conn, "BTC-USDT", [_funding_row(0)])
    upsert_macro_observations(conn, "DGS10", [_obs(MON)])
    kline_count = conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
    funding_count = conn.execute("SELECT COUNT(*) FROM funding_rates").fetchone()[0]
    macro_count = conn.execute("SELECT COUNT(*) FROM macro_series").fetchone()[0]
    conn.close()

    assert kline_count == 1
    assert funding_count == 1
    assert macro_count == 1


def test_upsert_macro_observations_inserts_new_rows_and_returns_the_inserted_count(conn):
    inserted = upsert_macro_observations(conn, "DGS10", [_obs(d) for d in WORK_WEEK])

    assert inserted == 5
    stored = [row[0] for row in conn.execute("SELECT observation_date FROM macro_series ORDER BY observation_date")]
    assert stored == WORK_WEEK


def test_upsert_macro_observations_is_a_no_op_for_rows_already_present(conn):
    upsert_macro_observations(conn, "DGS10", [_obs(MON), _obs(TUE)])

    second_inserted = upsert_macro_observations(conn, "DGS10", [_obs(MON), _obs(TUE), _obs(WED)])

    assert second_inserted == 1  # only WED is genuinely new
    count = conn.execute("SELECT COUNT(*) FROM macro_series").fetchone()[0]
    assert count == 3


def test_upsert_macro_observations_with_empty_rows_is_a_no_op(conn):
    assert upsert_macro_observations(conn, "DGS10", []) == 0
    assert conn.execute("SELECT COUNT(*) FROM macro_series").fetchone()[0] == 0


def test_upsert_macro_observations_stores_decimal_values_as_exact_text_not_float(conn):
    precise = Decimal("4.123456789012345678")
    upsert_macro_observations(conn, "DGS10", [ObservationRow(observation_date=MON, value=precise)])

    raw = conn.execute(
        "SELECT value, typeof(value) FROM macro_series WHERE observation_date = ?", (MON,)
    ).fetchone()
    assert raw[1] == "text"
    assert Decimal(raw[0]) == precise
    assert raw[0] == str(precise)


def test_upsert_macro_observations_stores_null_for_the_missing_value_marker(conn):
    # FRED's own "." marker (a real weekday holiday) is parsed to
    # value=None by fred_client.py -- this must land as a real SQL NULL,
    # not the literal string "None" or ".".
    upsert_macro_observations(conn, "DGS10", [_obs(HOLIDAY_WEEKDAY, value=None)])

    raw = conn.execute(
        "SELECT value, typeof(value) FROM macro_series WHERE observation_date = ?", (HOLIDAY_WEEKDAY,)
    ).fetchone()
    assert raw[0] is None
    assert raw[1] == "null"


def test_upsert_macro_observations_scopes_rows_to_the_given_series_id(conn):
    upsert_macro_observations(conn, "DGS10", [_obs(MON)])
    upsert_macro_observations(conn, "SP500", [_obs(MON)])  # same date, different series

    count = conn.execute("SELECT COUNT(*) FROM macro_series").fetchone()[0]
    assert count == 2  # both kept -- PK includes series_id, not a collision


def test_find_missing_macro_ranges_returns_the_full_weekday_range_when_store_is_empty(conn):
    gaps = find_missing_macro_ranges(conn, "DGS10", MON, FRI)

    assert gaps == [(MON, FRI)]


def test_find_missing_macro_ranges_returns_no_gaps_when_fully_populated(conn):
    upsert_macro_observations(conn, "DGS10", [_obs(d) for d in WORK_WEEK])

    gaps = find_missing_macro_ranges(conn, "DGS10", MON, FRI)

    assert gaps == []


def test_find_missing_macro_ranges_does_not_flag_a_weekend_as_a_gap(conn):
    # The core behavior this task exists to get right: a fully-populated
    # work week plus its following weekend must show zero gaps, even
    # though no row for SAT/SUN was ever stored -- because a weekend was
    # never *expected* to have one. A naive "one row per calendar day"
    # check would misfire here.
    upsert_macro_observations(conn, "DGS10", [_obs(d) for d in WORK_WEEK])

    gaps = find_missing_macro_ranges(conn, "DGS10", MON, SUN)

    assert gaps == []


def test_find_missing_macro_ranges_detects_a_gap_that_spans_a_weekend(conn):
    # THU and FRI missing, then the weekend (never expected), then the
    # next Monday present. The gap must stop at FRI, not swallow the
    # weekend dates into its own (start, end) bounds as if they were
    # also missing weekdays.
    upsert_macro_observations(conn, "DGS10", [_obs(MON), _obs(TUE), _obs(WED), _obs(NEXT_MON)])

    gaps = find_missing_macro_ranges(conn, "DGS10", MON, NEXT_MON)

    assert gaps == [(THU, FRI)]


def test_find_missing_macro_ranges_treats_a_null_value_row_as_present_not_missing(conn):
    # A stored "." (holiday) row must count as present, exactly like any
    # other stored row -- only a date with no row at all is a gap.
    upsert_macro_observations(
        conn, "DGS10", [_obs(HOLIDAY_WEEKDAY, value=None), _obs("2026-01-02")]
    )

    gaps = find_missing_macro_ranges(conn, "DGS10", HOLIDAY_WEEKDAY, "2026-01-02")

    assert gaps == []


def test_find_missing_macro_ranges_detects_a_leading_gap(conn):
    upsert_macro_observations(conn, "DGS10", [_obs(d) for d in [WED, THU, FRI]])

    gaps = find_missing_macro_ranges(conn, "DGS10", MON, FRI)

    assert gaps == [(MON, TUE)]


def test_find_missing_macro_ranges_detects_a_trailing_gap(conn):
    upsert_macro_observations(conn, "DGS10", [_obs(d) for d in [MON, TUE, WED]])

    gaps = find_missing_macro_ranges(conn, "DGS10", MON, FRI)

    assert gaps == [(THU, FRI)]


def test_find_missing_macro_ranges_detects_a_mid_range_gap(conn):
    upsert_macro_observations(conn, "DGS10", [_obs(d) for d in [MON, TUE, FRI]])  # WED, THU missing

    gaps = find_missing_macro_ranges(conn, "DGS10", MON, FRI)

    assert gaps == [(WED, THU)]


def test_find_missing_macro_ranges_is_scoped_to_series_id(conn):
    upsert_macro_observations(conn, "SP500", [_obs(d) for d in WORK_WEEK])  # different series

    gaps = find_missing_macro_ranges(conn, "DGS10", MON, FRI)

    assert gaps == [(MON, FRI)]  # SP500 rows don't count as DGS10 coverage


def test_find_missing_macro_ranges_accepts_a_single_day_range(conn):
    gaps = find_missing_macro_ranges(conn, "DGS10", MON, MON)

    assert gaps == [(MON, MON)]


def test_find_missing_macro_ranges_returns_no_gaps_for_a_weekend_only_range(conn):
    # Neither day in [SAT, SUN] is ever expected to have a row.
    gaps = find_missing_macro_ranges(conn, "DGS10", SAT, SUN)

    assert gaps == []


def test_find_missing_macro_ranges_rejects_inverted_range(conn):
    with pytest.raises(ValueError):
        find_missing_macro_ranges(conn, "DGS10", FRI, MON)


# ---------------------------------------------------------------------------
# fetch_macro_observations
# ---------------------------------------------------------------------------


def test_fetch_macro_observations_returns_rows_in_range_ordered_ascending(conn):
    upsert_macro_observations(conn, "DGS10", [_obs(FRI), _obs(MON), _obs(WED)])

    rows = fetch_macro_observations(conn, "DGS10", MON, FRI)

    assert [r.observation_date for r in rows] == [MON, WED, FRI]


def test_fetch_macro_observations_excludes_rows_outside_the_inclusive_range(conn):
    upsert_macro_observations(conn, "DGS10", [_obs(d) for d in WORK_WEEK])

    rows = fetch_macro_observations(conn, "DGS10", TUE, THU)

    assert [r.observation_date for r in rows] == [TUE, WED, THU]  # both ends included


def test_fetch_macro_observations_returns_empty_list_when_nothing_stored(conn):
    assert fetch_macro_observations(conn, "DGS10", MON, FRI) == []


def test_fetch_macro_observations_is_scoped_to_series_id(conn):
    upsert_macro_observations(conn, "SP500", [_obs(MON)])
    upsert_macro_observations(conn, "DGS10", [_obs(MON)])

    rows = fetch_macro_observations(conn, "DGS10", MON, MON)

    assert len(rows) == 1


def test_fetch_macro_observations_returns_exact_decimal_values_not_float_rounded(conn):
    precise = Decimal("4.123456789012345678")
    upsert_macro_observations(conn, "DGS10", [ObservationRow(observation_date=MON, value=precise)])

    rows = fetch_macro_observations(conn, "DGS10", MON, MON)

    assert rows[0].value == precise


def test_fetch_macro_observations_returns_none_for_a_stored_missing_value_marker_row(conn):
    upsert_macro_observations(conn, "DGS10", [_obs(HOLIDAY_WEEKDAY, value=None)])

    rows = fetch_macro_observations(conn, "DGS10", HOLIDAY_WEEKDAY, HOLIDAY_WEEKDAY)

    assert rows[0].value is None


def test_fetch_macro_observations_rejects_inverted_range(conn):
    with pytest.raises(ValueError):
        fetch_macro_observations(conn, "DGS10", FRI, MON)
