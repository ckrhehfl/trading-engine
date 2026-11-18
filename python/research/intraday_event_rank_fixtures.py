"""Invented saved-EN geometry for EO; outcomes are produced only on demand."""

from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction


FULL_EVENTS = 160 * 23
FULL_ROWS = FULL_EVENTS * 4
_PHASES = ("onset", "waiting_pullback", "pullback", "confirmation")
_HEADS = ("net_immediate", "net_wait3")


def full_fixture(*, names=23):
    """Return a causal roster, causal rows and a deferred outcome factory.

    All 160 original-calendar slots and four folds remain present even when
    a test requests fewer names. The final event shares the first name/day
    cluster when names exceeds one, exercising unequal event multiplicities.
    No saved outcome is constructed until the returned callable is invoked.
    """
    if type(names) is not int or not 1 <= names <= 23:
        raise ValueError("Invented names must be between one and twenty-three")
    calendar, day = [], date(2024, 1, 2)
    while len(calendar) < 160:
        if day.weekday() < 5:
            calendar.append(day.isoformat())
        day += timedelta(days=1)
    events, causal = {}, []
    for day_index, session in enumerate(calendar):
        for event_index in range(names):
            name_index = 0 if names > 1 and event_index == names - 1 else event_index
            symbol = f"INVENTED{name_index:02d}"
            event_id = f"C:{session}:{symbol}:{event_index}"
            cluster_id = f"{session}:{symbol}"
            event = {"event_id": event_id, "session": session, "symbol": symbol,
                     "cluster_id": cluster_id, "onset_decision_offset": 10,
                     "terminal_state": "CONFIRMED", "confirmation_key": event_id + "@19",
                     "confirmation_decision_offset": 19, "confirmation_reference": "100"}
            events[event_id] = event
            for phase_index, phase in enumerate(_PHASES):
                clock = 10 + 3 * phase_index
                state = ("WAITING_PULLBACK" if phase_index < 2 else
                         "PULLBACK" if phase_index == 2 else "CONFIRMED")
                # Scores deliberately include ties, negative values and zero.
                score = (day_index * 3 + event_index * 5 + phase_index) % 11 - 5
                predictions = {"net_immediate": str(Fraction(score, 1000)),
                               "net_wait3": str(Fraction((score * 3) % 7 - 3, 1000))}
                label_query = {"event_id": event_id, "family": "C_FIRSTPULLBACK", "state": state,
                               "terminal": phase_index == 3, "onset_offset": 9,
                               "event_offset": clock - 1, "decision_offset": clock,
                               "reference_offset": clock - 1, "reference_close": "100",
                               "scale": "2", "structure_stop": "98", "session_length": 100}
                causal.append({"schema": "EN-causal-query-v1", "key": event_id + "@" + str(clock),
                               "event_id": event_id, "session": session, "symbol": symbol,
                               "cluster_id": cluster_id, "fold": day_index // 40,
                               "decision_offset": clock, "onset_decision_offset": 10,
                               "state": state, "terminal": phase_index == 3, "phase": phase,
                               "clock_bin": "<60", "chart_supported": True, "peer_supported": True,
                               "scope_supported": True, "query_status": "supported_chart",
                               "prediction_status": "supported_features",
                               "head_status": {"path_class": "trained_tcn", "fill": "trained_tcn",
                                               "net_immediate": "trained_tcn", "net_wait3": "trained_tcn",
                                               "remaining_gross": "not_applicable_to_state"},
                               "admission_reason": None, "entry_admissibility": "admissible",
                               "initial_denominator": "99960", "session_length": 100,
                               "predictions": predictions, "label_query": label_query})

    def outcomes():
        """Materialize known original-target records independently of scores."""
        rows = []
        for index, original in enumerate(causal):
            row = deepcopy(original)
            event_index, phase_index = (index // 4) % names, index % 4
            day_index = index // (4 * names)
            heads = {}
            for head_index, head in enumerate(_HEADS):
                target = Fraction((day_index + 2 * event_index + phase_index + head_index) % 5 - 2, 1000)
                prediction = row["predictions"][head]
                clock = row["decision_offset"] + 3 * head_index
                heads[head] = {"prediction": prediction, "target": str(target),
                               "training_target": str(target), "target_state": "known",
                               "raw_status": "no_fill" if target == 0 else "filled",
                               "reason": "invented_known_zero" if target == 0 else None,
                               "net_cash": str(target * Fraction(row["initial_denominator"])),
                               "denominator": row["initial_denominator"], "fill": int(target != 0),
                               "matured_offset": clock + 31, "fill_matured_offset": clock + 1,
                               "scored": True, "error": str(Fraction(prediction) - target)}
            observed = Fraction(heads["net_wait3"]["target"]) - Fraction(heads["net_immediate"]["target"])
            predicted = Fraction(row["predictions"]["net_wait3"]) - Fraction(row["predictions"]["net_immediate"])
            row.update(schema="EN-query-value-v1", selected=False, selected_head=None, heads=heads,
                       paired={"scored": True, "predicted_wait_advantage": str(predicted),
                               "observed_wait_advantage": str(observed), "error": str(predicted - observed),
                               "direction_agreement": ((predicted > 0) - (predicted < 0)
                                                       == (observed > 0) - (observed < 0))})
            rows.append(row)
        return rows

    return ({"calendar": calendar, "events": events,
             "fold_by_day": {session: index // 40 for index, session in enumerate(calendar)},
             "source_pins": [], "source_support": {"invented": True, "original_events": len(events)}},
            causal, outcomes)


def fixture(*, names=1):
    """Small name-count variant retaining the original four-fold geometry."""
    return full_fixture(names=names)
