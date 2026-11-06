"""Invented feature and training-only outcome frames for EG model folds."""

from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import InputRefusal, MINUTE
from research.intraday_highvol_coverage import project
from research.intraday_highvol_model_dataset import assemble_fold
from research.intraday_highvol_snapshot import HOLD_STEPS
from research.intraday_pattern_calibration import session_close, session_open


DAYS = tuple((date(2025, 8, 1) + timedelta(days=i)).isoformat() for i in range(257))
SYMBOL = "KRX:000001"
CHART_ROW = [0.0, 1.0, -1.0, 0.5, 0.5, 2.0, 0.75, 0.0, 0.0, 0.0, 1.0, 0.0]
PEER_ROW = [0.5, 1.0, 0.75, 0.5, 1.0, 0.0]


def frame(day, onset=15, *, symbol=SYMBOL, branch="A", timings=("S1", "S2"), chart=True, peer=True, delay=10):
    opening = session_open(day)
    decision = opening + (onset + delay + 2) * MINUTE
    inside = decision + 31 * MINUTE < session_close(day) - 15 * MINUTE

    def context(step):
        at = decision + step * MINUTE
        identity = project({"symbol": symbol, "opening": opening, "bar_end": at - 2 * MINUTE, "decision_at": at})
        padding = max(0, 60 - int((at - opening - 2 * MINUTE) / MINUTE))

        def sequence(row, supported):
            padded = [0.0] * (len(row) - 1) + [1.0]
            return {**identity, "status": "available" if supported else "unavailable",
                    "sequence": [padded.copy() for _ in range(padding)]
                    + [row.copy() for _ in range(60 - padding)] if supported else None}

        return {"decision_at": at.isoformat(), "last_close": project(Fraction(100 + step, 1)),
                "chart": sequence(CHART_ROW, chart), "peers": {name: sequence(PEER_ROW, peer) for name in timings}}

    return {
        "key": [day, symbol, onset], "timings": list(timings),
        "attempt": {"scenario": branch, "state": "confirmed", "confirmation": project({
            "decision_at": decision, "intended_entry_at": decision + MINUTE,
            "stop": Fraction(99), "target": Fraction(102),
        })},
        "entry": {**context(0), "primary_clock_supported": inside},
        "holding": [{**context(step), "step_minutes": step} for step in HOLD_STEPS] if inside else [],
    }


def label(value, *, entry_open=Fraction(100), first_touch="target_first"):
    entry = session_open(value["key"][0]) + (value["key"][2] + 13) * MINUTE
    gross = Fraction(1, 100)
    exit_open = entry_open * (1 + gross)
    fixed_mature = entry + 33 * MINUTE
    primary = project({"entry_at": entry, "exit_at": entry + 30 * MINUTE, "status": "admitted",
                       "entry_open": entry_open, "fixed_exit_open": exit_open, "fixed_gross": gross,
                       "fixed_mature_at": fixed_mature,
                       "first_touch": {"status": first_touch, "minute": entry, "mature_at": entry + 3 * MINUTE}})
    holds = []
    for step in HOLD_STEPS:
        next_open = entry_open if step == 5 else None
        holds.append(project({
            "step_minutes": step, "decision_at": entry + (step - 1) * MINUTE, "next_open_at": entry + step * MINUTE,
            "status": "available" if next_open is not None else "next_open_unavailable",
            "next_open": next_open, "next_open_mature_at": entry + (step + 3) * MINUTE if next_open is not None else None,
            "mature_at": fixed_mature if next_open is not None else None,
            "value": (exit_open - next_open) / entry_open if next_open is not None else None,
        }))
    return {"key": list(value["key"]), "primary": primary, "holding": holds}


def test_only_common_training_labels_and_same_membership_for_all_arms():
    training = [frame(day, onset) for day in DAYS[:10] for onset in (15, 20, 25)]
    bypassed = [frame(DAYS[11], branch="B"), frame(DAYS[12], timings=("S2",)),
                frame(DAYS[13], peer=False), frame(DAYS[14], chart=False)]
    common, chart_only = frame(DAYS[100]), frame(DAYS[100], 20, peer=False)
    common["holding"][1]["peers"]["S1"].update(status="unavailable", sequence=None)
    absent = frame(DAYS[100], 25, chart=False)
    outcomes = {tuple(item["key"]): label(item, first_touch="stop_first" if i % 2 else "target_first")
                for i, item in enumerate(training)}
    reads = []

    def training_only(key):
        reads.append(key)
        return outcomes[key]

    view = assemble_fold(DAYS, "A", "S1", 1, [*training, *bypassed, common, chart_only, absent], training_only)
    assert reads == list(outcomes)
    assert view.selection.supported
    assert dict(view.counts)["training_hold_next_open_unavailable"] == 120
    assert dict(view.counts)["training_hold_used"] == 30
    chart = dict(view.training_for("TCN_CHART"))
    peers = dict(view.training_for("TCN_CHART_PEER"))
    assert len(chart) == 60 and list(chart) == list(peers) == [row.context.key for row in view.training]
    for key, item in chart.items():
        peer_item = peers[key]
        assert item.input.minute == tuple(row[:12] for row in peer_item.input.minute)
        assert item.input.state == peer_item.input.state
        assert (item.gross30, item.target_first, item.hold_advantage) == (
            peer_item.gross30, peer_item.target_first, peer_item.hold_advantage)
    assert view.training_for("LGB_CHART") == view.training_for("TCN_CHART")
    assert len(view.inference) == 18
    assert [item.support for item in view.inference if item.key[3] == 0] == ["common", "chart_only", "chart_unavailable"]
    assert len(view.predictions_for("TCN_CHART")) == 12
    assert len(view.predictions_for("TCN_CHART_PEER")) == 5


def test_unknown_hold_quotes_and_purge_do_not_change_predecision_context():
    train, test = frame(DAYS[0]), frame(DAYS[100])
    outcome = label(train, entry_open=Fraction(101), first_touch="same_minute_unknown")
    cutoff = session_open(DAYS[100]).isoformat()
    outcome["holding"][0].update(mature_at=cutoff, next_open_mature_at=cutoff)
    view = assemble_fold(DAYS, "A", "S1", 1, (train, test), lambda key: outcome)
    assert not view.selection.supported and view.training_for("LGB_CHART") == ()
    assert len(view.training) == 1 and view.training[0].target_first is None
    assert dict(view.counts)["training_hold_immature"] == 1
    assert dict(view.counts)["training_hold_next_open_unavailable"] == 4
    before = view.predictions_for("TCN_CHART")
    changed = label(train, entry_open=Fraction(100))
    new = assemble_fold(DAYS, "A", "S1", 1, (train, test), lambda key: changed)
    assert new.training[0].context.chart == view.training[0].context.chart
    assert new.predictions_for("TCN_CHART") == before
    assert len(new.training) == 2


def test_outside_clock_stays_in_evaluation_and_never_reads_outcome():
    day = "2026-01-02"
    assert day in DAYS[132:164]
    outside = frame(day, 275)

    def no_outcome(key):
        raise AssertionError(key)

    view = assemble_fold(DAYS, "A", "S1", 2, (outside,), no_outcome)
    assert len(view.inference) == 1 and view.inference[0].support == "outside_clock"
    assert view.predictions_for("TCN_CHART") == ()


def test_sealed_stream_uses_onset_before_symbol_within_each_session():
    day = DAYS[100]
    earlier = frame(day, 15, symbol="KRX:000002")
    tied = frame(day, 20, symbol="KRX:000001")
    later = frame(day, 20, symbol="KRX:000002")

    def no_outcome(key):
        raise AssertionError(key)

    view = assemble_fold(DAYS, "A", "S1", 1, (earlier, tied, later), no_outcome)
    assert [row.key for row in view.selection.evaluation] == [tuple(item["key"]) for item in (earlier, tied, later)]
    with pytest.raises(InputRefusal, match="feature_order_or_identity_changed"):
        assemble_fold(DAYS, "A", "S1", 1, (tied, earlier, later), no_outcome)


def test_sealed_context_and_label_identity_checks():
    train = frame(DAYS[0])
    malformed = deepcopy(train)
    malformed["entry"]["peers"]["S1"]["sequence"][0].pop()
    with pytest.raises(InputRefusal, match="sequence_shape"):
        assemble_fold(DAYS, "A", "S1", 1, (malformed,), lambda key: None)
    wrong = label(train)
    wrong["key"] = [DAYS[100], SYMBOL, 15]
    with pytest.raises(InputRefusal, match="training_label_key_changed"):
        assemble_fold(DAYS, "A", "S1", 1, (train,), lambda key: wrong)
