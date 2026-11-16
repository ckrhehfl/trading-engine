"""Deterministic invented EM qualification cases; no original inputs or IO."""

from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
from itertools import product

from research.intraday_event_evaluation import SessionMarket
from research.intraday_event_execution import EntryRequest, Forecast, evaluate
from research.intraday_event_scenarios import MinuteBar


FULL_EVENTS = 160 * 23


def case(index=0, *, day="2024-01-02", symbol="INVENTED"):
    """Twelve invented regimes, including absent, unknown and partial exits."""
    kind = index % 12
    bars = {i: MinuteBar(i, 100, 100, 100, 100, i + 1) for i in range(70)}

    def quote(i, opening=100, closing=None, receipt=None):
        closing = opening if closing is None else closing
        bars[i] = MinuteBar(i, opening, max(opening, closing), min(opening, closing), closing,
                            i + 1 if receipt is None else receipt)

    if kind in (1, 2, 6, 7, 8):
        quote(20, 101)
    if kind == 2:
        quote(46, 101)
    if kind == 3:
        quote(16, 101)
    if kind == 4:
        del bars[16]
    if kind == 5:
        del bars[22]
    if kind == 7:
        quote(19, closing=97)
        quote(20, 101, receipt=25)
    if kind == 10:
        quote(20, Fraction(10033, 100))
    if kind == 11:
        quote(16, receipt=70)
    event = f"C:{day}:{symbol}:{index}"
    roster = {"event_id": event, "session": day, "symbol": symbol, "cluster_id": day + ":cluster",
              "onset_decision_offset": 10, "terminal_state": "EXPIRED_NO_CONFIRMATION" if kind == 9 else "CONFIRMED",
              "confirmation_key": None if kind == 9 else event + "@15",
              "confirmation_decision_offset": None if kind == 9 else 15,
              "confirmation_reference": None if kind == 9 else "100"}
    ledgers = {}
    for control, entry, management in product(
            ("onset", "confirmation"), ("next_open_capped", "reference_limit_one_shot"),
            ("fixed_exit", "forecast_full_exit", "forecast_half_then_exit")):
        clock = 10 if control == "onset" else 15
        committed = not (kind == 9 and control == "confirmation")
        if committed:
            report = evaluate(EntryRequest(clock, 100, 2, 97 if kind == 8 else 98), bars, 70, entry_policy=entry,
                              management=management,
                              forecasts=(Forecast(clock + 11, clock + 11, -1),
                                         Forecast(clock + 21, clock + 21, -1)))
        else:
            report = {"status": "no_entry", "reason": "no_causal_frame", "net": "0", "gross": "0",
                      "filled_quantity": 0, "requested_quantity": None, "entry_price": None,
                      "entry_notional": "0", "remaining_quantity": 0, "sales": []}
        outcome = {key: report[key] for key in ("status", "reason", "net", "gross", "filled_quantity", "entry_notional")}
        outcome.update(matured_offset=None, fill_matured_offset=None)
        if kind == 6 and control == "confirmation":
            outcome.update(status="unknown", reason="outcome_not_mature_in_session", net=None, gross=None)
        if kind == 11 and control == "confirmation":
            outcome.update(status="unknown", reason="outcome_not_mature_in_session", net=None, gross=None,
                           filled_quantity=None, entry_notional=None)
        ledgers[control, entry, management] = {
            "session": day, "symbol": symbol, "family": "C_FIRSTPULLBACK", "original_episode_id": event,
            "cluster_id": roster["cluster_id"], "model": "tcn", "scope": "common_chart_peer", "control": control,
            "action": "immediate" if committed else "no_commit",
            "selection_reason": "fixed_" + control if committed else "no_causal_frame",
            "candidate_key": event + "@" + str(clock) if committed else None,
            "state": "CONFIRMED" if control == "confirmation" and committed else "ONSET" if committed else "NO_COMMIT",
            "entry_policy": entry, "management": management, "entry_decision_offset": clock if committed else None,
            "execution": report, "outcome": deepcopy(outcome)}
    return roster, ledgers, SessionMarket(70, bars, {}, {})


def invented_cases(calendar_sessions=160, names=23):
    calendar, day = [], date(2024, 1, 2)
    while len(calendar) < calendar_sessions:
        if day.weekday() < 5:
            calendar.append(day.isoformat())
        day += timedelta(days=1)

    def cases():
        for position, session in enumerate(calendar):
            for name in range(names):
                yield case(position * names + name, day=session, symbol=f"INVENTED{name:02}")

    return calendar, cases()


def full_cases():
    return invented_cases()
