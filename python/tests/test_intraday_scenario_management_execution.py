"""Independent readback and tamper checks on an invented EH execution trace."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from hashlib import sha256
import json

import pytest

from research.intraday_campaign_readiness import _opening
from research.intraday_campaign_wsl_transport import encode
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_management_execution import (
    MAX_COORDINATES, seal_invented_execution, verify_invented_execution,
)
from research.intraday_scenario_snapshot import _write, write_features
from test_intraday_scenario_management_campaign import DATES, projected, run
from test_intraday_scenario_management_compare import at, row
from test_intraday_scenario_management_entry import breakout_frame


@pytest.fixture(scope="module")
def invented_campaign(tmp_path_factory):
    folder = tmp_path_factory.mktemp("eh-execution")
    _, _, template, _ = breakout_frame()

    def shifted(day, *, replacement=None, omit=None):
        delta = _opening(day) - template.opening
        prices = template.minutes | {at(29): row(29, 600, high=680)} | {
            at(index): row(index, 580) for index in range(36, 65)
        }
        if replacement is not None:
            prices[replacement.start_at] = replacement
        return replace(template, opening=template.opening + delta, closing=template.closing + delta,
            minutes={clock + delta: replace(minute, start_at=clock + delta, available_at=minute.available_at + delta)
                     for clock, minute in prices.items() if clock != omit})

    known = {day: shifted(day) for day in (*DATES[:30], DATES[100], DATES[103])}
    known.update({DATES[101]: shifted(DATES[101], replacement=row(28, opened=606)),
                  DATES[102]: shifted(DATES[102], omit=at(28))})
    frames = tuple(projected(day, day in known, series=known.get(day, template)) for day in DATES)
    features = write_features(folder / "features.jsonl", frames)
    campaign = run(frames, lambda day, symbol: known[day] if symbol == template.symbol else None, held_comparison=True)

    def price(day, symbol, clock):
        assert symbol == template.symbol and day in known
        minute = known[day].minutes.get(clock)
        return Fraction(minute.ohlc[0]) if minute is not None else None

    symbols = (template.symbol, *(f"INVENTED-{rank}" for rank in range(2, 21)))
    calendar = dict.fromkeys(DATES, symbols)
    path = folder / "execution.jsonl"
    seal = seal_invented_execution(path, campaign, features)
    options = dict(features=features, calendar=calendar, scenario="S1", mode="held", cost_bps=33,
                   fresh_reentry=True, modeled_open_at=price, downside_penalty=Fraction(1))
    limits = {}
    for index in range(100, 104):
        frame = next(item.frame for item in frames[index].rows if item.frame.initial.stage == "breakout")
        question = frame.initial.question
        limits[DATES[index], template.symbol] = (frame.observed_at, frame.event_expires_at, question.stop,
                                               frame.initial.start_close + question.band)
    reference = dict(read_session=lambda day, symbol: known[day] if symbol == template.symbol else None,
                     breakout_limits=limits)
    return path, seal, options, reference


def test_reopens_all_ranked_coordinates_and_attempts(invented_campaign):
    path, seal, options, reference = invented_campaign
    assert seal.records == MAX_COORDINATES
    assert verify_invented_execution(path, seal, **options) == {"modeled": 6, "declined": 1, "indeterminate": 1}
    assert verify_invented_execution(path, seal, **(options | reference))["payoff_checks"] == 2
    with pytest.raises(InputRefusal, match="header_differs"):
        verify_invented_execution(path, seal, **(options | {"cost_bps": 43}))


@pytest.mark.parametrize("change, reason", [
    ("wrong_price", "open_differs"), ("wrong_inventory", "inventory_differs"),
    ("wrong_terminal", "terminal_inventory_differs"), ("wrong_fresh", "terminal_inventory_differs"),
    ("false_entry_decline", "entry_differs"), ("false_sell_decline", "trade_outcome_differs"),
    ("wrong_utility", "utility_action_differs"), ("missing_decision", "model_sale_without_decision"),
    ("evaluation_source", "decision_source_differs"),
    ("after_hours_source", "decision_source_differs"), ("wrong_mode_reason", "utility_support_differs"),
    ("late_fixed_sale", "sale_cap_differs"),
    ("reorder", "coordinate_differs"), ("omit", "original_roster_required"),
])
def test_rejects_resealed_but_wrong_execution_records(tmp_path, invented_campaign, change, reason):
    path, _, options, _ = invented_campaign
    header, *rows, _ = map(json.loads, path.read_bytes().splitlines())
    if change == "wrong_price":
        rows[0]["initial"]["trade"][6] = "601"
    elif change == "wrong_inventory":
        trade = rows[0]["arms"]["fixed"][1][0]
        trade[3] = trade[5] = trade[3] + 1
    elif change == "wrong_terminal":
        rows[0]["arms"]["fixed"][0] = "unresolved"
    elif change == "wrong_fresh":
        rows[0]["arms"]["dynamic"] = ["unresolved", []]
        rows[0]["arms"]["fresh_increment"] = ["not_reentered", []]
    elif change == "false_entry_decline":
        rows[0]["status"] = "declined"
        rows[0]["initial"]["trade"][4:6] = ["declined", 0]
        rows[0]["arms"] = {}
        rows[0]["decisions"] = []
        rows[0]["event_expiry"] = None
    elif change == "false_sell_decline":
        rows[0]["arms"]["fixed"][0] = "unresolved"
        rows[0]["arms"]["fixed"][1][0][4:6] = ["declined", 0]
    elif change == "wrong_utility":
        assessment = rows[0]["decisions"][0]
        assert assessment[3] == "ready"
        assessment[5][2] = assessment[5][0]
    elif change == "missing_decision":
        rows[0]["decisions"] = []
    elif change == "evaluation_source":
        assessment = rows[60]["decisions"][0]
        assert rows[60]["day"] == DATES[103] and assessment[3] == "ready"
        source = assessment[4][0]
        source[0] = DATES[100]
        source[2] = (_opening(DATES[100]) + timedelta(minutes=32)).astimezone(UTC).isoformat()
    elif change == "after_hours_source":
        source = rows[0]["decisions"][0][4][0]
        source[2] = _opening(source[0]).replace(hour=23, minute=57).astimezone(UTC).isoformat()
    elif change == "wrong_mode_reason":
        rows[0]["decisions"][0][3:] = ["chart_question_unavailable", [], None]
    elif change == "late_fixed_sale":
        trade = rows[0]["arms"]["fixed"][1][0]
        for index in (1, 2):
            trade[index] = (datetime.fromisoformat(trade[index]) + timedelta(minutes=1)).isoformat()
    elif change == "reorder":
        rows[0], rows[1] = rows[1], rows[0]
    else:
        rows.pop()
    altered = tmp_path / "changed.jsonl"
    new_seal = _write(altered, header, rows, maximum=MAX_COORDINATES)
    with pytest.raises(InputRefusal, match=reason):
        verify_invented_execution(altered, new_seal, **options)


def test_rejects_extra_eof_even_with_correct_new_digest(tmp_path, invented_campaign):
    path, seal, options, _ = invented_campaign
    altered = tmp_path / "extra.jsonl"
    raw = path.read_bytes() + encode({"extra": True})
    altered.write_bytes(raw)
    altered.chmod(0o400)
    with pytest.raises(InputRefusal, match="footer_or_EOF_differs"):
        verify_invented_execution(altered, replace(seal, sha256=sha256(raw).hexdigest(), size=len(raw)), **options)


def test_rejects_wrong_scores_even_when_the_order_is_unchanged(tmp_path, invented_campaign):
    path, _, options, reference = invented_campaign
    header, *rows, _ = map(json.loads, path.read_bytes().splitlines())
    assessment = rows[0]["decisions"][0]
    assessment[5] = [str(Fraction(value) + 1) if value is not None else None for value in assessment[5]]
    altered = tmp_path / "same-order.jsonl"
    seal = _write(altered, header, rows, maximum=MAX_COORDINATES)
    verify_invented_execution(altered, seal, **options)
    with pytest.raises(InputRefusal, match="recomputed_utility_differs"):
        verify_invented_execution(altered, seal, **(options | reference))


def test_rejects_expiry_changed_from_the_sealed_feature(tmp_path, invented_campaign):
    path, _, options, reference = invented_campaign
    header, *rows, _ = map(json.loads, path.read_bytes().splitlines())
    day, symbol = rows[0]["day"], rows[0]["symbol"]
    rows[0]["event_expiry"] = (reference["breakout_limits"][day, symbol][1] + timedelta(minutes=1)).isoformat()
    altered = tmp_path / "wrong-expiry.jsonl"
    seal = _write(altered, header, rows, maximum=MAX_COORDINATES)
    with pytest.raises(InputRefusal, match="event_expiry_differs"):
        verify_invented_execution(altered, seal, **(options | reference))


def test_rejects_stop_changed_from_the_sealed_feature(tmp_path, invented_campaign):
    path, _, options, reference = invented_campaign
    header, *rows, _ = map(json.loads, path.read_bytes().splitlines())
    rows[0]["initial"]["stop"] = str(Fraction(rows[0]["initial"]["stop"]) - 1)
    altered = tmp_path / "wrong-stop.jsonl"
    seal = _write(altered, header, rows, maximum=MAX_COORDINATES)
    with pytest.raises(InputRefusal, match="breakout_bounds_differ"):
        verify_invented_execution(altered, seal, **(options | reference))


def test_rejects_entry_shifted_from_the_sealed_breakout_before_open_read(tmp_path, invented_campaign):
    path, _, options, reference = invented_campaign
    header, *rows, _ = map(json.loads, path.read_bytes().splitlines())
    trade = rows[0]["initial"]["trade"]
    for index in (1, 2):
        trade[index] = (datetime.fromisoformat(trade[index]) + timedelta(minutes=1)).isoformat()
    altered = tmp_path / "later-entry.jsonl"
    seal = _write(altered, header, rows, maximum=MAX_COORDINATES)
    with pytest.raises(InputRefusal, match="breakout_clock_differs"):
        verify_invented_execution(altered, seal, **(options | reference | {
            "modeled_open_at": lambda *_: pytest.fail("opened a price for an altered decision clock"),
        }))
