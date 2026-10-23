"""Explicit invented fixtures only; no market DB or protected-file collection."""
from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
import gzip
import json
import sqlite3

import pytest

from research import intraday_activity_readiness as cx
from test_intraday_opening_range_calibration import inputs, raw_rows, SYMBOLS


def invented(days=("2025-09-04",), count=30):
    cb, saved = inputs(days)
    current = []
    for day, symbol in sorted(saved):
        chunk = saved[day, symbol]
        chunk['rows'] = raw_rows(day, symbol, count)
        new = deepcopy(chunk)
        new['rows'] = [{key: row[key] for key in ('open_time_ms', 'fetched_at')} | {
            'volume': '10', 'quote_volume': None} for row in chunk['rows']]
        new['daily_rows'] = [{'open_time_ms': int(cx.datetime.fromisoformat(day + 'T00:00:00+00:00').timestamp()) * 1000,
            'volume': '1000', 'quote_volume': '100000', 'fetched_at': '2026-10-23T00:00:00+00:00'}]
        new['daily_retrieved_at'] = '2026-10-23T00:00:00+00:00'
        current.append(new)
    return cb, list(saved.values()), current


def schema(quote=True):
    connection = sqlite3.connect(':memory:')
    connection.execute('CREATE TABLE klines(symbol TEXT,interval TEXT,open_time_ms INTEGER,volume TEXT,fetched_at TEXT'
        + (',quote_volume TEXT' if quote else '') + ',PRIMARY KEY(symbol,interval,open_time_ms))')
    return connection, cx.inventory_schema(connection)


def build(days=("2025-09-04",), count=30):
    cb, saved, current = invented(days, count)
    connection, inventory = schema()
    connection.close()
    return cx.build_readiness(cb, saved, current, inventory)


def weekdays(count=21, start='2025-09-04'):
    day, result = date.fromisoformat(start), []
    while len(result) < count:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day += timedelta(days=1)
    return tuple(result)


@pytest.mark.parametrize('value,state', [(None,'null'),('bad','invalid'),('NaN','invalid'),
    ('Infinity','invalid'),(10,'invalid'),('-1','negative'),('0','zero'),('-0.00','zero'),
    ('0.001','nonzero'),('1e101','invalid'),('1e-101','invalid')])
def test_exact_activity_states(value, state):
    assert cx.number_state(value) == state
    assert cx.number_state(value, False) == 'absent_column'


def test_registration_is_exact():
    spec = json.loads((cx.cc.ROOT/'configs/research/discovery/intraday-activity-readiness-v1.json').read_bytes())
    cx.validate_spec(spec)
    spec['next_hypothesis']['price_impulse_conjunct'] = True
    with pytest.raises(cx.ReadinessRefusal):
        cx.validate_spec(spec)


def test_saved_omission_is_separate_from_DB_presence_and_daily_value():
    report = build()
    assert report['summary']['field_counts']['volume']['nonzero'] == 600
    assert report['summary']['field_counts']['quote_volume']['null'] == 600
    assert report['summary']['daily_field_counts']['quote_volume']['nonzero'] == 20
    assert report['summary']['opening_prefix_ready_counts'] == {'15':20,'30':20}
    assert report['summary']['feature_ready_union'] == 0
    row = report['target_days'][0]
    assert row['opening_prefixes']['15']['volume_sum'] == Fraction(150)
    assert row['saved_archive_activity_fields'] == 'omitted_not_evidence_of_DB_absence'
    assert report['performance_calculated'] is False
    assert report['signal_selection_performed'] is False
    assert report['new_performance_families'] == 0


def test_previous20_requires_all_calendar_sessions_and_positive_median():
    report = build(weekdays())
    assert report['summary']['feature_ready_union'] == 20
    assert report['variants']['D+1']['feature_readiness_counts'] == {'ready':20,'unknown':400}
    assert report['target_days'][-1]['next_hypothesis_feature_readiness']['ratio_or_threshold_evaluated'] is False


def test_missing_prior_target_never_uses_twenty_observed_or_expands_coordinates():
    days = weekdays(22)
    cb, saved, current = invented(days)
    symbol = SYMBOLS[0]
    replacement = 'KRX:999999'
    for policy in cb['variants'].values():
        policy['days'][1]['targets'][0]['symbol'] = replacement
    for chunks in (saved, current):
        chunk = next(row for row in chunks if row['session'] == days[1] and row['symbol'] == symbol)
        chunk['symbol'] = replacement
        for row in chunk['rows']:
            if 'symbol' in row:
                row['symbol'] = replacement
        chunks.sort(key=lambda row:(row['session'],row['symbol']))
    connection, inventory = schema();connection.close()
    report = cx.build_readiness(cb,saved,current,inventory)
    row = next(row for row in report['target_days'] if row['session']==days[-1] and row['symbol']==symbol)
    assert row['next_hypothesis_feature_readiness']['missing_target_coordinates'] == 1
    assert row['next_hypothesis_feature_readiness']['state'] == 'unknown'


def test_shifted_opening_and_next20_sessions_keep_clock_mismatch_unknown():
    report = build(weekdays(22, '2025-11-12'))
    final = report['target_days'][-1]['next_hypothesis_feature_readiness']
    assert final['prior_opening_clock_mismatches'] == 1
    assert final['state'] == 'unknown'
    special = next(row for row in report['target_days'] if row['session']=='2025-11-13')
    assert special['next_hypothesis_feature_readiness']['same_wall_clock'] == '10:00'


@pytest.mark.parametrize('kind', ['missing','duplicate','negative','null','zero'])
def test_prefix_requires_every_bucket_and_zero_is_observed(kind):
    cb,saved,current= invented()
    rows=current[0]['rows']
    if kind=='missing':
        del rows[3]
    elif kind=='duplicate':
        rows.append(dict(rows[3],open_time_ms=rows[3]['open_time_ms']+1000))
    else:
        rows[3]['volume']={'negative':'-1','null':None,'zero':'0'}[kind]
    connection,inventory=schema();connection.close()
    result=cx.build_readiness(cb,saved,current,inventory)['target_days'][0]
    assert result['opening_prefixes']['15']['state'] == ('ready' if kind=='zero' else 'unknown')


def test_zero_baseline_is_unknown_not_infinite_ratio():
    cb,saved,current=invented(weekdays())
    for chunk in current:
        for row in chunk['rows']:row['volume']='0'
    connection,inventory=schema();connection.close()
    report=cx.build_readiness(cb,saved,current,inventory)
    assert report['target_days'][-1]['next_hypothesis_feature_readiness']['positive_exact_baseline_median'] is False
    assert report['summary']['feature_ready_union']==0


def test_schema_query_guards_allow_only_named_table_and_exact_coordinate():
    connection,inventory=schema(False)
    day,symbol='2025-09-04',SYMBOLS[0]
    stamp=int(cx.cc.session_open(day).timestamp())*1000
    connection.execute('INSERT INTO klines VALUES(?,?,?,?,?)',(symbol,'1m',stamp,'123','retrieved'))
    connection.commit();connection.set_authorizer(cx.authorize)
    result=cx.read_target(connection,symbol,day,{(day,symbol)},inventory)
    assert result['rows']==[{'open_time_ms':stamp,'volume':'123','quote_volume':None,'fetched_at':'retrieved'}]
    for sql in ('SELECT name FROM sqlite_schema','SELECT high FROM klines','DELETE FROM klines','PRAGMA table_info(klines)'):
        with pytest.raises(sqlite3.DatabaseError):connection.execute(sql)
    with pytest.raises(cx.ReadinessRefusal):cx.read_target(connection,symbol,'2018-12-31',{('2018-12-31',symbol)},inventory)
    with pytest.raises(cx.ReadinessRefusal):cx.read_target(connection,symbol,day,set(),inventory)
    with pytest.raises(cx.ReadinessRefusal):cx.read_target(connection,symbol,day,{(day,symbol)},inventory,interval='1h')
    connection.close()


def test_daily_scope_reads_exact_UTC_date_without_prices():
    connection,inventory=schema()
    day,symbol='2025-09-04',SYMBOLS[0]
    stamp=int(cx.datetime.fromisoformat(day+'T00:00:00+00:00').timestamp())*1000
    connection.execute('INSERT INTO klines VALUES(?,?,?,?,?,?)',(symbol,'1d',stamp,'99','fetched','1234'))
    connection.execute('INSERT INTO klines VALUES(?,?,?,?,?,?)',(symbol,'1d',stamp+1,'bad','fetched','bad'))
    connection.commit();connection.set_authorizer(cx.authorize)
    result=cx.read_target(connection,symbol,day,{(day,symbol)},inventory,interval='1d')
    assert len(result['rows'])==1 and result['rows'][0]['quote_volume']=='1234'
    connection.close()


def test_row_limit_and_view_refusal():
    connection,inventory=schema()
    day,symbol='2025-09-04',SYMBOLS[0]
    stamp=int(cx.cc.session_open(day).timestamp())*1000
    connection.executemany('INSERT INTO klines VALUES(?,?,?,?,?,?)',[(symbol,'1m',stamp+n,'1',None,None) for n in range(601)])
    connection.commit();connection.set_authorizer(cx.authorize)
    with pytest.raises(cx.ReadinessRefusal):cx.read_target(connection,symbol,day,{(day,symbol)},inventory)
    connection.close()
    connection=sqlite3.connect(':memory:');connection.execute('CREATE VIEW klines AS SELECT 1')
    with pytest.raises(cx.ReadinessRefusal):cx.inventory_schema(connection)
    connection.close()


def test_chunks_scope_and_floats_fail_closed():
    cb,saved,current=invented()
    connection,inventory=schema();connection.close()
    current[0]['rows'][0]['volume']=1.5
    with pytest.raises(cx.ReadinessRefusal):cx.build_readiness(cb,saved,current,inventory)
    cb,saved,current=invented()
    current[-1]['session']='2025-09-05'
    with pytest.raises(cx.ReadinessRefusal):cx.build_readiness(cb,saved,current,inventory)


def test_typed_archive_framing(tmp_path):
    path=tmp_path/'invented.gz'
    with gzip.open(path,'wb') as stream:stream.write(b'{"not":"canonical"}')
    with pytest.raises(cx.ReadinessRefusal):list(cx.typed_chunks(path,lambda:None))
