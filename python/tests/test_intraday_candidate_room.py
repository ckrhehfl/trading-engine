from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256

import pytest

from research import intraday_candidate_exit as exits
from research import intraday_candidate_exit_reference as exit_reference
from research import intraday_candidate_room as room
from research import intraday_candidate_room_saved as saved_room
from research.intraday_campaign_wsl_transport import encode
from research.intraday_context_cost_census_inputs import POLICY_SHA, UNITS, invented_rows
from research.intraday_context_questions import ContextQuestion
from research.intraday_context_situations import ResistanceAnchor
from research.intraday_context_snapshot import question_digest
from research.intraday_ed_inputs import Minute, SessionInputs
from research.intraday_scenario_labels import PathQuestion


MINUTE = timedelta(minutes=1)


def _fixture(index):
    original, roster = next((row, roster) for row, roster in invented_rows()
                            if row["questions"] and row["symbol"] == str(100000 + index))
    day, symbol = original["session"], original["symbol"]
    opening = datetime.fromisoformat(day + "T09:00:00+09:00")
    prices = {datetime.fromisoformat(clock): Decimal(price) for clock, price, _ in original["modeled_opens"]}
    decision = datetime.fromisoformat(original["questions"][0]["decision_at"])
    minutes = {}
    for i in range(1, 7):
        at = decision + i * MINUTE
        op = prices.get(at, Decimal(10070))
        close = Decimal(10066) if i == 1 else op
        ohlc = (op, max(op, close), min(op, close), close)
        minutes[at] = Minute(symbol, at, at + 3 * MINUTE, ohlc, None, volume_state="missing")
    session = SessionInputs(symbol, opening, opening + 390 * MINUTE, minutes)
    saved = exits.evaluate_coordinate(original, roster, lambda *_: session)
    exit_reference.verify_coordinate(original, roster, session, saved)
    return room.invented_decision(saved, roster)


def _choice(coordinate):
    choices = room.selections(coordinate)
    room.verify_selections(coordinate, choices)
    return next(iter(choices.values()))


def test_exact_equality_uses_pending_cap_and_original_question_identity():
    coordinate, saved = _fixture(0)
    raw = coordinate["decisions"][0]["opportunity"]["feature"]["question"]
    (symbol, anchor), index = raw["episode_id"]
    episode_anchor = ResistanceAnchor(*(datetime.fromisoformat(anchor[key]) if key in ("start_at", "peak_end", "confirmed_at")
                                       else Fraction(anchor[key]) for key in ResistanceAnchor.__dataclass_fields__))
    p = raw["path"]
    path = PathQuestion(datetime.fromisoformat(p["start_at"]), datetime.fromisoformat(p["end_at"]),
                        *(Fraction(p[key]) for key in ("level", "band", "target", "stop")))
    typed = ContextQuestion(((symbol, episode_anchor), index), raw["stage"], path, Fraction(raw["start_close"]),
                            raw["episode_origin_context"], datetime.fromisoformat(raw["episode_origin_at"]),
                            raw["question_time_context"], raw["phase"], (), True, ())
    assert set(room.selections(coordinate)) == {question_digest(typed)} == {saved["questions"][0]["question_sha256"]}
    assert _choice(coordinate)["state"] == "select" and _choice(coordinate)["room_bps"] == "66"
    below, _ = _fixture(1)
    assert _choice(below)["state"] == "decline" and _choice(below)["room_bps"] == "65"


def test_selection_has_no_outcome_dependency_and_accepts_decimal_causal_close():
    coordinate, saved = _fixture(0)
    choices = room.selections(coordinate)
    pair = coordinate["decisions"][0]
    pair["baseline"]["visible_minute"]["ohlc"] = ["9990.00"] * 4
    pair["selected"] = {"unrelated_future_payoff": "1"}
    coordinate["modeled_opens"] = [["future", "999999", None]]
    saved["questions"][0]["candidate"]["net"] = "999999"
    assert room.selections(coordinate) == choices
    room.verify_selections(coordinate, choices)


@pytest.mark.parametrize("change", ["missing_cap", "invalid_cap", "early_or_late_open", "late_close", "invalid_target"])
def test_unknown_or_invalid_pre_entry_inputs_are_unresolved(change):
    coordinate, _ = _fixture(0)
    pair = coordinate["decisions"][0]
    request = pair["baseline"]["step"]["episode"]["pending"]
    if change == "missing_cap":
        del request["bounds"]["max_price"]
    elif change == "invalid_cap":
        request["bounds"]["max_price"] = "9990"
    elif change == "early_or_late_open":
        request["execute_at"] = pair["opportunity"]["feature"]["forecast_at"]
    elif change == "late_close":
        pair["baseline"]["visible_minute"]["available_at"] = request["execute_at"]
    else:
        pair["opportunity"]["feature"]["question"]["path"]["target"] = "NaN"
    choice = _choice(coordinate)
    assert choice["state"] == "unresolved" and choice["room_bps"] is None


def test_matched_projection_preserves_full_population_and_common_exit_support():
    rows = []
    # Closed selected, rejected, unknown room, pre-entry wait, no fill, unsupported, unresolved holding, overlap.
    for index in (0, 1, 2, 3, 4, 5, 6, 18):
        coordinate, saved = _fixture(index)
        choices = room.selections(coordinate)
        projected = room.project(saved, choices)
        room.verify_projection(saved, choices, projected)
        rows.append(projected)
    report = exits.summarize(rows)
    exit_reference.verify_summary(rows, report)
    totals = report["groups"]["all"]["totals"]
    assert report["coordinates"] == 8 and totals["opportunities"] == 7
    assert report["question_states"] == {"opportunity": 7, "overlap_suppressed": 1}
    for arm in ("baseline", "candidate"):
        assert totals[arm]["status_counts"] == {"closed": 1, "no_entry": 2, "no_fill": 1,
                                                 "unsupported": 1, "unresolved": 2}
    assert totals["paired"]["known_outcomes"] == 4 and totals["paired"]["delta_net_cash"] == "-30"


def test_audits_reject_forged_selection_and_selected_economics():
    coordinate, saved = _fixture(0)
    choices = room.selections(coordinate)
    altered = deepcopy(choices)
    next(iter(altered.values()))["state"] = "decline"
    with pytest.raises(ValueError, match="sealed selection differs"):
        room.verify_selections(coordinate, altered)

    projected = deepcopy(room.project(saved, choices))
    projected["questions"][0]["candidate"]["net"] = "9999"
    with pytest.raises(ValueError, match="retained payoff differs"):
        room.verify_projection(saved, choices, projected)

    saved["questions"][0]["episode_origin_context"] = "B"
    with pytest.raises(ValueError, match="question metadata differs"):
        room.project(saved, choices)


def test_declined_arm_does_not_relabel_old_ei_trade_deltas():
    coordinate, saved = _fixture(1)
    prior = saved["questions"][0]
    prior.update(selected={"net": "0"}, selected_minus_baseline_net="-67", same_fill_management={})
    choices = room.selections(coordinate)
    projected = room.project(saved, choices)
    room.verify_projection(saved, choices, projected)
    new = projected["questions"][0]
    assert new["baseline"]["net"] == "0"
    assert {"selected", "selected_minus_baseline_net", "same_fill_management"}.isdisjoint(new)
    assert prior["selected_minus_baseline_net"] == "-67"


def test_pre_entry_reader_authenticates_exact_original_roster_and_payload(tmp_path):
    roster_hash = sha256()
    lines = []
    for index in range(20):
        coordinate, outcome = _fixture(index)
        questions = [[q[key] for key in ("question_sha256", "status", "stage", "decision_at", "episode_origin_context")]
                     for q in outcome["questions"]]
        roster_hash.update(encode([outcome["session"], outcome["symbol"], outcome["coordinate_status"],
                                   outcome["original_frames"], questions]))
        lines.append(encode({"coordinate": coordinate}))
    header = {"scenario": "S1", "cost_bps": 33, "execution_policy_sha256": POLICY_SHA,
              "coordinates": 20, "fold": {"index": 1}, "question_roster_sha256": roster_hash.hexdigest()}
    raw = encode({"schema": "ei-context-entry-decisions-draft-v1", "header": header}) + b"".join(lines)
    cache = tmp_path / "cache" / (UNITS["S1"] + "-actual1")
    cache.mkdir(parents=True)
    path = cache / "ei-entry-decisions-fold-1.jsonl"
    path.write_bytes(raw)
    parent = {"scenario": "S1", "unit": UNITS["S1"],
              "decisions": [{"sha256": sha256(raw).hexdigest(), "size": len(raw), "coordinates": 20}]}
    selections = list(saved_room._decision_rows(tmp_path, parent))
    assert len(selections) == 20 and next(iter(selections[0]["choices"].values()))["state"] == "select"

    path.write_bytes(raw.replace(b"causal_close_missing", b"causal_close_invalid"))
    assert path.stat().st_size == len(raw)
    with pytest.raises(ValueError, match="digest or EOF differs"):
        list(saved_room._decision_rows(tmp_path, parent))
