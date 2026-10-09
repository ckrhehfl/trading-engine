"""Synthetic scan schema and hand-calculated input-to-book acceptance cases."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D
import sqlite3

import pytest

from data.krx_scan import SCAN_SCHEMA
from research import activity_inputs
from research.activity_accounting import IdentityPeriod
from research.activity_inputs import (
    FormationSource, SourceLiquidity, join_formation, load_scan,
)
from research.activity_replay import DecimalSeries, replay_synthetic
from research.activity_screen import PublishedValue, ScreenObservation, SyntheticListing
from research.activity_timing import AvailabilityMetadata, SessionLagPolicy


KST = timezone(timedelta(hours=9))
A, B, C = "000001", "000002", "000003"


def calendar():
    # Deliberately artificial index sessions. Only temporary fixtures are read;
    # the sparse interval is not represented as a real complete market calendar.
    return tuple(date(2019, 1, 2) + timedelta(days=i) for i in range(13)) + (
        date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22),
    )


def sync_progress(connection, code):
    first, last, count = connection.execute(
        "SELECT MIN(bsop_date),MAX(bsop_date),COUNT(*) FROM scan_bars WHERE code=?", (code,),
    ).fetchone()
    frozen = connection.execute(
        "SELECT COUNT(*) FROM scan_bars WHERE code=? AND open=high AND high=low "
        "AND low=close AND (turnover IS NULL OR CAST(turnover AS REAL)=0)", (code,),
    ).fetchone()[0]
    connection.execute("INSERT OR REPLACE INTO scan_progress VALUES (?,?,?,?,?,?,?)",
                       (code, first, last, count, frozen, "done", "2026-10-09T00:00:00+00:00"))


def database(tmp_path, codes=(A, B)):
    path = tmp_path / "synthetic.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCAN_SCHEMA)
        connection.execute("INSERT INTO scan_panel VALUES (1,'20190102','20260918')")
        for code in codes:
            for day in calendar()[:-2]:
                connection.execute("INSERT INTO scan_bars VALUES (?,?,?,?,?,?,?,?)",
                                   (code, day.strftime("%Y%m%d"), "100", "100", "100", "100", "2", "10"))
            sync_progress(connection, code)
    return path


def update(path, code, index, *, opening="100", close="100", turnover="10"):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE scan_bars SET open=?,high=?,low=?,close=?,turnover=? WHERE code=? AND bsop_date=?",
            (opening, str(max(D(opening), D(close))), str(min(D(opening), D(close))), close,
             turnover, code, calendar()[index].strftime("%Y%m%d")),
        )
        sync_progress(connection, code)


def params():
    return dict(lookback=2, holding_sessions=3, slots=1, threshold=3,
                commission_bps_per_side=0, slippage_bps_per_side=0,
                seed=7, end=calendar()[10].isoformat())


def join_args(scan, index=2, codes=(A, B)):
    days = scan.calendar
    cutoff = datetime.combine(days[index + 1], time(8, 30), KST)
    published = lambda day: datetime.combine(day, time(18), KST)
    return dict(
        scan=scan, formation_on=days[index], population=codes,
        sources=tuple(FormationSource(days[index], code, PublishedValue(D(100), published(days[index])),
                                      tuple((day, SourceLiquidity("observed", D(100), published(day)))
                                            for day in days[max(0, index - 2):index])) for code in codes),
        periods=tuple(IdentityPeriod(code, days[0], days[-1] + timedelta(days=1), days[0], True,
                                     "hand-inspected synthetic combined domestic operating common issue")
                      for code in codes), listings={},
        quote_available_at={code: {day: published(day) for day in days} for code in scan.panel},
        params=params(), liquidity_lookback=2, liquidity_floor=D(50), capitalization_floor=D(100),
        decision_at=cutoff,
    )


def timed_join_args(scan, lag=2):
    args = join_args(scan)
    policy = SessionLagPolicy(lag, paired=True)

    def metadata(day, source):
        return AvailabilityMetadata(
            day, policy.selection_at(scan.calendar, scan.calendar.index(day)),
            "unknown", source, None, "synthetic-source-snapshot", "assumed",
            availability_policy="synthetic-daily-delay", evidence_reference="BH toy fixture",
        )

    args["timing_policy"] = policy
    args["decision_at"] = policy.selection_at(scan.calendar, 2)
    args["quote_available_at"] = {code: {} for code in scan.panel}
    args["quote_metadata"] = {
        code: {day: metadata(day, "synthetic KIS adjusted quote") for day in scan.calendar[:3]}
        for code in scan.panel
    }
    args["sources"] = tuple(replace(
        row,
        capitalization=PublishedValue(
            row.capitalization.value, None, metadata(row.formation_on, "synthetic KRX MKTCAP"),
        ),
        liquidity=tuple((day, replace(
            value, public_available_at=None, availability=metadata(day, "synthetic KRX ACC_TRDVAL"),
        )) for day, value in row.liquidity),
    ) for row in args["sources"])
    return args


def test_explicit_d2_join_keeps_unknown_actual_times_and_precise_provenance(tmp_path):
    scan = load_scan(database(tmp_path), calendar(), (A, B))
    args = timed_join_args(scan)
    result = join_formation(**args)
    assert result.selection.formation_on == scan.calendar[2]
    assert result.selection.decision_on == scan.calendar[4]
    assert result.selection.selection_at == args["decision_at"]
    assert result.selection.candidates[0].size_pass is True
    assert args["sources"][0].capitalization.public_available_at is None
    assert args["sources"][0].capitalization.availability.is_final is None
    assert args["quote_metadata"][A][scan.calendar[2]].retrieved_at == "unknown"


def test_absent_decimal_replay_quote_keeps_null_instead_of_actual_zero():
    from research.activity_replay import _quote

    series = DecimalSeries((None,), (None,), (None,), (None,), (None,), (False,))
    assert _quote(series, 0) == (None, None, False, False)


def test_availability_metadata_cannot_backdate_a_daily_observation(tmp_path):
    scan = load_scan(database(tmp_path), calendar(), (A, B))
    args = timed_join_args(scan)
    metadata = args["quote_metadata"][A][scan.calendar[2]]
    with pytest.raises(ValueError, match="precede observation_date"):
        replace(metadata, available_at=datetime.combine(scan.calendar[1], time(23, 59), KST))


@pytest.mark.parametrize("field", ["quote", "capitalization", "liquidity"])
def test_explicit_timing_join_cannot_discard_required_metadata(tmp_path, field):
    scan = load_scan(database(tmp_path), calendar(), (A, B))
    args = timed_join_args(scan)
    if field == "quote":
        args["quote_metadata"] = None
    else:
        row = args["sources"][0]
        if field == "capitalization":
            row = replace(row, capitalization=replace(row.capitalization, availability=None))
        else:
            row = replace(row, liquidity=tuple((day, replace(value, availability=None))
                                              for day, value in row.liquidity))
        args["sources"] = (row, *args["sources"][1:])
    with pytest.raises(ValueError, match="metadata"):
        join_formation(**args)


@pytest.mark.parametrize("field", ["quote", "capitalization", "liquidity"])
def test_timing_join_rejects_one_late_field_without_patching_other_metadata(tmp_path, field):
    scan = load_scan(database(tmp_path), calendar(), (A, B))
    args = timed_join_args(scan)

    def late(meta):
        return replace(meta, available_at=args["decision_at"] + timedelta(seconds=1))

    if field == "quote":
        day = scan.calendar[2]
        args["quote_metadata"][A][day] = late(args["quote_metadata"][A][day])
    else:
        row = args["sources"][0]
        if field == "capitalization":
            row = replace(row, capitalization=replace(
                row.capitalization, availability=late(row.capitalization.availability),
            ))
        else:
            row = replace(row, liquidity=tuple((day, replace(value, availability=late(value.availability)))
                                              for day, value in row.liquidity))
        args["sources"] = (row, *args["sources"][1:])
    with pytest.raises(ValueError, match="later than selection"):
        join_formation(**args)


def test_loader_preserves_large_text_precision_null_absence_and_file_bytes(tmp_path):
    path = database(tmp_path)
    large = "10000000000000000000000000000000000000001"
    update(path, A, 0, opening=large, close=large, turnover=large)
    update(path, A, 1, turnover=None)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM scan_bars WHERE code=? AND bsop_date=?",
                           (A, calendar()[4].strftime("%Y%m%d")))
        sync_progress(connection, A)
    before = path.read_bytes()
    loaded = load_scan(path, calendar(), (B, A))
    series = loaded.panel[A]
    assert series.opens[0] == series.closes[0] == series.turnover[0] == D(large)
    assert series.observed[1] is True and series.turnover[1] is None
    assert series.frozen[1] is series.locked[1] is None
    assert series.observed[4] is False and series.opens[4] is series.turnover[4] is None
    assert path.read_bytes() == before
    assert tuple(loaded.panel) == (A, B)
    assert load_scan(path, calendar(), (A, B)).dataset_sha256 == loaded.dataset_sha256
    with pytest.raises(TypeError):
        loaded.panel[A] = loaded.panel[B]
    with pytest.raises(FrozenInstanceError):
        series.opens = ()


@pytest.mark.parametrize("bounds", [("19910828", "20181231"), ("20190102", "20260917")])
def test_reserved_or_wrong_panel_is_refused_before_any_price_or_progress_select(tmp_path, monkeypatch, bounds):
    path = database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE scan_panel SET start=?,end=?", bounds)
    trace = []
    original = activity_inputs.readonly

    def traced(path):
        connection = original(path)
        connection.set_trace_callback(trace.append)
        return connection

    monkeypatch.setattr(activity_inputs, "readonly", traced)
    with pytest.raises(ValueError, match="registered spent"):
        load_scan(path, calendar(), (A, B))
    assert any(statement == "BEGIN" for statement in trace)
    assert not any("scan_bars" in statement or "scan_progress" in statement for statement in trace)


def test_loader_pins_one_read_snapshot_when_writer_changes_later_price(tmp_path, monkeypatch):
    path = database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    original, changed = activity_inputs.readonly, []

    def traced(path):
        connection = original(path)

        def mutate(statement):
            if "FROM scan_progress" in statement and not changed:
                changed.append(True)
                update(path, A, 2, turnover="999")

        connection.set_trace_callback(mutate)
        return connection

    monkeypatch.setattr(activity_inputs, "readonly", traced)
    old = load_scan(path, calendar(), (A, B))
    assert changed and old.panel[A].turnover[2] == 10
    monkeypatch.setattr(activity_inputs, "readonly", original)
    new = load_scan(path, calendar(), (A, B))
    assert new.panel[A].turnover[2] == 999 and new.dataset_sha256 != old.dataset_sha256


@pytest.mark.parametrize("codes", [(), (A, A), ("1",), ("00000a",), (" 00001",), "000001"])
def test_requested_code_membership_is_explicit_and_unique(tmp_path, codes):
    path = database(tmp_path)
    with pytest.raises(ValueError, match="codes"):
        load_scan(path, calendar(), codes)


@pytest.mark.parametrize("sql", [
    "DELETE FROM scan_progress WHERE code='000002'",
    "UPDATE scan_progress SET status='failed:transport' WHERE code='000002'",
    "UPDATE scan_progress SET first_date='20190230' WHERE code='000002'",
    "UPDATE scan_progress SET last_date='20270101' WHERE code='000002'",
    "UPDATE scan_progress SET bars=bars+1 WHERE code='000002'",
    "UPDATE scan_progress SET frozen=1 WHERE code='000002'",
])
def test_missing_incomplete_or_inconsistent_progress_stops_loader(tmp_path, sql):
    path = database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(sql)
    with pytest.raises(ValueError):
        load_scan(path, calendar(), (A, B))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "bad", " 10", ""])
def test_malformed_scan_text_is_never_replaced_with_zero(tmp_path, value):
    path = database(tmp_path)
    update(path, A, 2, turnover=value)
    with pytest.raises(ValueError, match="scan amount"):
        load_scan(path, calendar(), (A, B))


def test_duplicate_or_off_calendar_dates_are_refused(tmp_path):
    path = database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.executescript("CREATE TABLE copied AS SELECT * FROM scan_bars; DROP TABLE scan_bars; "
                                 "ALTER TABLE copied RENAME TO scan_bars;")
        connection.execute("INSERT INTO scan_bars SELECT * FROM scan_bars WHERE code=? LIMIT 1", (A,))
        sync_progress(connection, A)
    with pytest.raises(ValueError, match="duplicate"):
        load_scan(path, calendar(), (A, B))
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM scan_bars WHERE rowid=(SELECT MAX(rowid) FROM scan_bars)")
        connection.execute("UPDATE scan_bars SET bsop_date='20190202' WHERE code=? AND bsop_date='20190102'", (A,))
        sync_progress(connection, A)
    with pytest.raises(ValueError, match="calendar disagree"):
        load_scan(path, calendar(), (A, B))


@pytest.mark.parametrize("kind", ["duplicate", "omitted", "truncated", "datetime"])
def test_invalid_supplied_calendar_is_refused(tmp_path, kind):
    path, days = database(tmp_path), list(calendar())
    if kind == "duplicate":
        days[2] = days[1]
    elif kind == "omitted":
        del days[3]
    elif kind == "truncated":
        days.pop()
    else:
        days[0] = datetime(2019, 1, 2)
    with pytest.raises(ValueError, match="calendar"):
        load_scan(path, days, (A, B))


@pytest.mark.parametrize("codes", [(A,), (A, B, C), (A, A)])
def test_independent_population_cannot_omit_add_or_duplicate_formation_members(tmp_path, codes):
    loaded = load_scan(database(tmp_path), calendar(), (A, B))
    args = join_args(loaded)
    args["population"] = codes  # Fixed independently of the quote-derived pool.
    with pytest.raises(ValueError, match="population|codes"):
        join_formation(**args)


@pytest.mark.parametrize("state", ["absent", "frozen"])
def test_fixed_population_member_cannot_silently_disappear_as_absent_or_frozen(tmp_path, state):
    path = database(tmp_path)
    if state == "absent":
        with sqlite3.connect(path) as connection:
            connection.execute("DELETE FROM scan_bars WHERE code=? AND bsop_date='20190104'", (B,))
            sync_progress(connection, B)
    else:
        update(path, B, 2, turnover="0")
    loaded = load_scan(path, calendar(), (A, B))
    with pytest.raises(ValueError, match="population"):
        join_formation(**join_args(loaded))
    # Independently excluding that absent/frozen member is consistent. Its
    # original series stays in the loaded panel for holding/entry accounting.
    result = join_formation(**join_args(loaded, codes=(A,)))
    assert tuple(row.code for row in result.selection.candidates) == (A,)
    assert B in loaded.panel


@pytest.mark.parametrize("when", ["unknown", "naive", "late"])
def test_frozen_formation_exclusion_requires_known_quote_publication(tmp_path, when):
    path = database(tmp_path)
    update(path, B, 2, turnover="0")
    args = join_args(load_scan(path, calendar(), (A, B)), codes=(A,))
    stamp = {"unknown": None, "naive": args["decision_at"].replace(tzinfo=None),
             "late": args["decision_at"] + timedelta(microseconds=1)}[when]
    args["quote_available_at"][B][calendar()[2]] = stamp
    with pytest.raises(ValueError, match="availability"):
        join_formation(**args)


def test_independently_empty_all_frozen_population_retains_requested_series(tmp_path):
    path = database(tmp_path)
    update(path, A, 2, turnover="0")
    update(path, B, 2, turnover="0")
    loaded = load_scan(path, calendar(), (A, B))
    result = join_formation(**join_args(loaded, codes=()))
    assert result.selection.candidates == () and result.diagnostics == ()
    assert tuple(loaded.panel) == (A, B)


@pytest.mark.parametrize("kind", ["omitted", "extra", "duplicate", "wrong_formation", "duplicate_date", "off_calendar", "quote_substitution"])
def test_source_join_requires_exact_code_date_and_separate_acc_trdval(tmp_path, kind):
    args = join_args(load_scan(database(tmp_path), calendar(), (A, B)))
    first, second = args["sources"]
    if kind == "omitted":
        args["sources"] = (first,)
    elif kind == "extra":
        args["sources"] += (replace(first, code=C),)
    elif kind == "duplicate":
        args["sources"] = (first, first, second)
    elif kind == "wrong_formation":
        args["sources"] = (replace(first, formation_on=calendar()[1]), second)
    else:
        rows = first.liquidity
        if kind == "duplicate_date":
            rows += rows[:1]
        elif kind == "off_calendar":
            rows = ((date(2019, 2, 2), rows[0][1]),)
        else:
            rows = ((rows[0][0], ScreenObservation("observed", D(100), args["decision_at"])),)
        args["sources"] = (replace(first, liquidity=rows), second)
    with pytest.raises(ValueError, match="population|source|substituted"):
        join_formation(**args)


@pytest.mark.parametrize("field", ["quote", "liquidity", "capitalization"])
@pytest.mark.parametrize("when", ["unknown", "naive", "late"])
def test_unknown_naive_or_late_field_publication_stops_before_selection(tmp_path, field, when):
    args = join_args(load_scan(database(tmp_path), calendar(), (A, B)))
    stamp = {"unknown": None, "naive": args["decision_at"].replace(tzinfo=None),
             "late": args["decision_at"] + timedelta(microseconds=1)}[when]
    if field == "quote":
        args["quote_available_at"][A][calendar()[2]] = stamp
    else:
        first, second = args["sources"]
        if field == "capitalization":
            first = replace(first, capitalization=replace(first.capitalization, public_available_at=stamp))
        else:
            day, value = first.liquidity[0]
            first = replace(first, liquidity=((day, replace(value, public_available_at=stamp)),
                                               first.liquidity[1]))
        args["sources"] = (first, second)
    with pytest.raises(ValueError, match="availability"):
        join_formation(**args)


@pytest.mark.parametrize("kind", ["missing", "overlap", "unknown_time", "next_session", "false_snapshot"])
def test_unknown_conflicting_or_late_identity_cannot_be_hidden_by_failed_size(tmp_path, kind):
    args = join_args(load_scan(database(tmp_path), calendar(), (A, B)))
    first, second = args["sources"]
    args["sources"] = (replace(first, capitalization=PublishedValue(D(0), args["decision_at"])), second)
    period, other = args["periods"]
    if kind == "missing":
        args["periods"] = (other,)
    elif kind == "overlap":
        args["periods"] += (replace(period, common_stock=False),)
    else:
        args["periods"] = (replace(period, known_on=None if kind == "unknown_time" else
                                  calendar()[3] if kind == "next_session" else date(2026, 10, 9)), other)
    with pytest.raises(ValueError, match="identity"):
        join_formation(**args)


def test_observed_null_and_internal_missing_do_not_become_short_history_or_zero(tmp_path):
    path = database(tmp_path)
    update(path, A, 0, turnover=None)
    loaded = load_scan(path, calendar(), (A, B))
    with pytest.raises(ValueError, match="unresolved synthetic observation"):
        join_formation(**join_args(loaded))
    # NULL in a replayed observed bar also fails; absence alone retains the
    # established no-fill/no-mark convention instead of becoming an observation.
    update(path, A, 0)
    update(path, A, 2, turnover="30")
    stable = load_scan(path, calendar(), (A, B))
    selections = tuple(join_formation(**join_args(stable, index)).selection for index in (2, 5))
    update(path, A, 3, turnover=None)
    loaded = load_scan(path, calendar(), (A, B))
    # Feed explicit toy selections to exercise replay's independent NULL guard;
    # the actual join also rejects this unknown prior observation at formation5.
    with pytest.raises(ValueError, match="unresolved"):
        replay_synthetic(loaded.calendar, loaded.panel, params(), selections,
                         dataset_sha256=loaded.dataset_sha256)


def test_verified_listing_short_history_is_separate_from_missing_observations(tmp_path):
    path = database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM scan_bars WHERE code=? AND bsop_date='20190102'", (A,))
        sync_progress(connection, A)
    args = join_args(load_scan(path, calendar(), (A, B)))
    args["listings"] = {A: SyntheticListing(calendar()[1], calendar()[0])}
    result = join_formation(**args)
    first = result.selection.candidates[0]
    assert first.operating_baseline == first.absolute_liquidity == "insufficient"
    assert first.activity_pass is False
    args["listings"] = {}
    with pytest.raises(ValueError, match="unresolved"):
        join_formation(**args)


def chain_database(tmp_path):
    path = database(tmp_path)
    update(path, A, 2, turnover="30")
    update(path, B, 5, turnover="30")
    for index, close in ((3, "110"), (4, "120"), (5, "130")):
        update(path, A, index, close=close)
    update(path, A, 6, opening="200", close="200")
    for index, close in ((6, "120"), (7, "130"), (8, "140")):
        update(path, B, index, close=close)
    update(path, B, 9, opening="150", close="150")
    return path


def chain(path):
    loaded = load_scan(path, calendar(), (A, B))
    screens = tuple(join_formation(**join_args(loaded, index)) for index in (2, 5))
    replay = replay_synthetic(loaded.calendar, loaded.panel, params(),
                              tuple(result.selection for result in screens),
                              dataset_sha256=loaded.dataset_sha256)
    return loaded, screens, replay


def test_actual_schema_loader_join_screen_replay_matches_hand_calculated_nav_and_codes(tmp_path):
    loaded, screens, replay = chain(chain_database(tmp_path))
    assert replay.selections == ((calendar()[2], (A,)), (calendar()[5], (B,)))
    assert screens[0].diagnostics[0].activity_median == 10
    assert screens[0].diagnostics[0].liquidity_median == 100
    # Start KRW1: buy A .01 shares at 100, sell 200*(1-.003):1.994.
    # Buy B .01994 at100; sell150*(1-.003):2.982027. Tax is the existing
    # 2019 KOSPI bound; no cost/tax accounting is duplicated in the adapter.
    assert replay.navs == tuple(map(D, ("1.1", "1.2", "1.3", "2.3928", "2.5922", "2.7916",
                                       "2.982027", "2.982027")))
    assert replay.closed_trades == 2 and replay.books[-1].cash == D("2.982027")
    assert replay.books[-1].slot_count == 0
    assert dict(replay.diagnostics)["entries"] == 2
    assert loaded.panel[A].turnover[2] == 30


def test_unused_future_values_change_digest_but_not_earlier_choices_or_books(tmp_path):
    path = chain_database(tmp_path)
    before, screens_before, replay_before = chain(path)
    update(path, A, 13, opening="123456789123456789123456789", close="123456789123456789123456789",
           turnover="999999999999999999999999999")
    after, screens_after, replay_after = chain(path)
    assert before.dataset_sha256 != after.dataset_sha256
    assert screens_before == screens_after
    assert replay_before.selections == replay_after.selections
    assert replay_before.navs == replay_after.navs
    # Lot provenance changes with snapshot hash; economic state cannot.
    assert tuple((book.cash, tuple((lot.code, lot.shares, lot.mark, lot.entered_on, lot.due_on)
                                  for lot in book.lots)) for book in replay_before.books) == tuple(
        (book.cash, tuple((lot.code, lot.shares, lot.mark, lot.entered_on, lot.due_on)
                         for lot in book.lots)) for book in replay_after.books)


@pytest.mark.parametrize("state", ["absent", "frozen"])
def test_decimal_due_sale_retains_cash_slot_and_mark_until_resumption(tmp_path, state):
    path = chain_database(tmp_path)
    if state == "absent":
        with sqlite3.connect(path) as connection:
            connection.execute("DELETE FROM scan_bars WHERE code=? AND bsop_date=?",
                               (A, calendar()[6].strftime("%Y%m%d")))
            sync_progress(connection, A)
    else:
        update(path, A, 6, opening="999", close="999", turnover="0")
    update(path, A, 7, opening="200", close="200")
    _, _, replay = chain(path)
    pending = next(book for book in replay.books if book.as_of == calendar()[6])
    assert pending.cash == 0 and pending.slot_count == 1
    assert pending.nav == D("1.3")
    assert pending.lots[0].shares == D(".01") and pending.lots[0].mark == 130
    assert pending.lots[0].mark_date == calendar()[5]
    assert pending.lots[0].due_on == calendar()[6]
    resumed = next(book for book in replay.books if book.as_of == calendar()[7])
    assert resumed.cash == D("1.994") and resumed.slot_count == 0
    # B's formation5 signal cannot replace the retained due lot or retry after
    # its later sale. No free slot exists on the entry session.
    assert replay.selections == ((calendar()[2], (A,)), (calendar()[5], ()))
    assert replay.closed_trades == 1
    assert dict(replay.diagnostics)["delayed_exits"] == 1
    assert dict(replay.diagnostics)["pending_exit_sessions"] == 1


def test_decimal_series_rejects_mutable_nondecimal_and_absent_values():
    with pytest.raises(ValueError, match="immutable"):
        DecimalSeries([D(1)], (D(1),), (D(1),), (False,), (True,), (True,))
    with pytest.raises(ValueError, match="Decimals"):
        DecimalSeries((1.0,), (D(1),), (D(1),), (False,), (True,), (True,))
    with pytest.raises(ValueError, match="absent"):
        DecimalSeries((D(1),), (D(1),), (D(1),), (False,), (True,), (False,))


@pytest.mark.parametrize("opening,close", [(D(0), D(1)), (D(1), D(0))])
def test_explicit_observed_decimal_zero_price_cannot_become_an_absent_replay_bar(opening, close):
    with pytest.raises(ValueError, match="positive prices"):
        DecimalSeries((opening,), (close,), (D(100),), (False,), (False,), (True,))


@pytest.mark.parametrize("kind", ["duplicate", "length", "calendar", "mutable", "digest"])
def test_loaded_scan_cannot_hide_duplicate_codes_or_inconsistent_constructor_inputs(tmp_path, kind):
    loaded = load_scan(database(tmp_path), calendar(), (A, B))
    changes = {
        "duplicate": dict(series=(loaded.series[0], loaded.series[0])),
        "length": dict(calendar=loaded.calendar[:-1]),
        "calendar": dict(calendar=(loaded.calendar[1], loaded.calendar[0])),
        "mutable": dict(series=list(loaded.series)),
        "digest": dict(dataset_sha256="saved today means known historically"),
    }
    with pytest.raises(ValueError):
        replace(loaded, **changes[kind])


def test_known_excluded_control_is_preserved_without_operating_source_screens(tmp_path):
    args = join_args(load_scan(database(tmp_path), calendar(), (A, B)))
    first, control = args["sources"]
    args["sources"] = (first, replace(control, capitalization=None, liquidity=()))
    period, control_period = args["periods"]
    args["periods"] = (period, replace(control_period, common_stock=False))
    result = join_formation(**args)
    assert tuple(row.code for row in result.selection.candidates) == (A, B)
    assert result.selection.candidates[1].classification == "excluded"


@pytest.mark.parametrize("kind", ["null", "missing", "unknown_state"])
def test_source_liquidity_unknowns_are_not_replaced_with_quote_activity(tmp_path, kind):
    args = join_args(load_scan(database(tmp_path), calendar(), (A, B)))
    first, second = args["sources"]
    day, value = first.liquidity[0]
    rows = first.liquidity[1:] if kind == "missing" else (
        (day, replace(value, acc_trdval=None) if kind == "null" else replace(value, state="unknown")),
        first.liquidity[1],
    )
    args["sources"] = (replace(first, liquidity=rows), second)
    with pytest.raises(ValueError, match="turnover|unresolved"):
        join_formation(**args)
