"""Offline-only EL observation contract and passive decision journal.

This module has no feed, file, account, credential, broker or order interface.
It prepares a no-order observer; constructing it does not activate collection.
Forecast values are statistical outputs, not certified probabilities or fills.
"""

from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
import json
import math
import re


CONTRACT = {
    "schema": "EL-offline-observation-v2", "activation": "not_authorized_by_preparation",
    "orders_enabled": False, "new_period_acquisition": False,
    "entry": "first positive max(immediate,wait3) commits once; larger value wins, tie immediate; otherwise observe without valuing that observation",
    "wait3_commitment": "three-minute conditional entry under the frozen wait target, never another forecast-based postponement",
    "held": "nonpositive remaining gross: full exit or half then exit at next consecutive nonpositive update",
    "cost": "entry Q already includes registered cost; holding remaining gross excludes sunk cost",
    "stale": "forecast and complete required input prefix must match this decision clock",
    "revisions": "new vintage required; never silently rewrite a saved decision",
    "meaning": "passive proposed action only; no broker command and no execution proof",
}


def _scalar(value):
    if value is None:
        return None
    if type(value) not in (int, float, str, Fraction):
        raise ValueError("Finite statistical output required")
    number = float(Fraction(value))
    if not math.isfinite(number):
        raise ValueError("Nonfinite statistical output")
    return Fraction(str(number))


@dataclass(frozen=True)
class Observation:
    series_id: str
    event_id: str
    family: str
    state: str
    decision_offset: int
    input_watermark: int
    forecast_available_offset: int
    source_sha256: str
    model_sha256: str
    data_vintage: str
    input_supported: bool
    position: str = "flat"
    path_probabilities: tuple | None = None
    fill_probability: float | None = None
    net_immediate: float | None = None
    net_wait3: float | None = None
    remaining_gross: float | None = None
    previous_held_remaining_gross: float | None = None

    def __post_init__(self):
        for name in ("series_id", "event_id", "data_vintage"):
            value = getattr(self, name)
            if type(value) is not str or not value or len(value) > 512:
                raise ValueError("Bounded observation identity required")
        for name in ("source_sha256", "model_sha256"):
            if re.fullmatch("[a-f0-9]{64}", getattr(self, name)) is None:
                raise ValueError("Exact source/model identity required")
        if self.family not in ("A_DOWNFAIL", "B_ORL30", "C_FIRSTPULLBACK"):
            raise ValueError("Unregistered hypothesis")
        if self.position not in ("flat", "full", "half") or type(self.input_supported) is not bool:
            raise ValueError("Explicit position and support required")
        flat_states = ("WAITING", "WAITING_PULLBACK", "PULLBACK", "CONFIRMED", "INVALIDATED",
                       "EXPIRED_NO_CONFIRMATION", "EXPIRED_NO_PULLBACK", "UNKNOWN_GAP", "SESSION_END")
        if self.state not in (flat_states if self.position == "flat" else ("HELD_10", "HELD_20")):
            raise ValueError("Observation state and actual replay position differ")
        for value in (self.decision_offset, self.input_watermark, self.forecast_available_offset):
            if type(value) is not int or value < 0:
                raise ValueError("Nonnegative integer clocks required")
        for name in ("fill_probability", "net_immediate", "net_wait3", "remaining_gross", "previous_held_remaining_gross"):
            value = _scalar(getattr(self, name))
            if name == "fill_probability" and value is not None and not 0 <= value <= 1:
                raise ValueError("Fill probability outside unit interval")
        if self.previous_held_remaining_gross is not None and self.state != "HELD_20":
            raise ValueError("Previous held forecast is available only at the second checkpoint")
        if self.path_probabilities is not None:
            values = tuple(_scalar(value) for value in self.path_probabilities)
            if (len(values) != 3 or any(value is None or not 0 <= value <= 1 for value in values)
                    or abs(sum(values) - 1) > Fraction(1, 1_000_000)):
                raise ValueError("Three normalized path probabilities required")
            object.__setattr__(self, "path_probabilities", tuple(float(value) for value in values))


def propose(observation: Observation, *, management="forecast_half_then_exit"):
    if not isinstance(observation, Observation):
        raise TypeError("EL Observation required")
    if management not in ("fixed_exit", "forecast_full_exit", "forecast_half_then_exit"):
        raise ValueError("Unregistered management policy")
    action, reason = "ABSTAIN", "unsupported_or_stale"
    fresh = (observation.input_supported and observation.input_watermark == observation.decision_offset
             and observation.forecast_available_offset == observation.decision_offset)
    if fresh:
        if observation.position == "flat":
            if observation.state not in ("WAITING", "WAITING_PULLBACK", "PULLBACK", "CONFIRMED"):
                action, reason = "NO_ENTRY", "known_inadmissible_state"
            elif observation.net_immediate is not None and observation.net_wait3 is not None:
                enter, wait = _scalar(observation.net_immediate), _scalar(observation.net_wait3)
                if max(enter, wait) <= 0:
                    action, reason = "OBSERVE_NO_COMMITMENT", "no_positive_known_action_value_observation_value_unestimated"
                elif enter >= wait:
                    action, reason = "PROPOSE_ENTRY", "positive_immediate_value_not_less_than_fixed_wait"
                else:
                    action, reason = "COMMIT_WAIT3_ENTRY", "positive_fixed_wait_value_exceeds_immediate"
        elif management == "fixed_exit":
            action, reason = "HOLD", "keep_original_stop_and_expiry"
        elif observation.remaining_gross is not None:
            if _scalar(observation.remaining_gross) <= 0:
                reason = "nonpositive_remaining_gross_without_recharging_sunk_cost"
                previous = _scalar(observation.previous_held_remaining_gross)
                if management == "forecast_full_exit" or (previous is not None and previous <= 0):
                    action = "PROPOSE_EXIT"
                elif observation.position == "full":
                    action = "PROPOSE_REDUCTION"
                else:
                    action, reason = "HOLD", "await_consecutive_nonpositive_held_forecasts"
            else:
                action, reason = "HOLD", "positive_remaining_gross"
    return {"schema": CONTRACT["schema"], "orders_enabled": False,
            "series_id": observation.series_id, "event_id": observation.event_id,
            "family": observation.family, "state": observation.state,
            "decision_offset": observation.decision_offset, "data_vintage": observation.data_vintage,
            "input_watermark": observation.input_watermark, "forecast_available_offset": observation.forecast_available_offset,
            "input_supported": observation.input_supported,
            "source_sha256": observation.source_sha256, "model_sha256": observation.model_sha256,
            "position": observation.position, "management": management, "action": action, "reason": reason,
            "path_probabilities": observation.path_probabilities, "fill_probability": observation.fill_probability,
            "forecast": {key: None if getattr(observation, key) is None else str(_scalar(getattr(observation, key)))
                         for key in ("net_immediate", "net_wait3", "remaining_gross", "previous_held_remaining_gross")},
            "execution": "not_attempted", "claim": CONTRACT["meaning"]}


class OfflineJournal:
    """Bounded in-memory replay; an identical packet is idempotent.

    Position is an external replay fact. Proposed actions never mutate it or
    claim a fill. Structural stops, cash and scheduling belong to execution.
    """

    def __init__(self):
        self._records = {}
        self._clock = {}
        self._commitments = {}

    def record(self, observation, *, management="forecast_half_then_exit"):
        result = propose(observation, management=management)
        key = (observation.series_id, observation.event_id, observation.decision_offset,
               observation.data_vintage, management)
        raw_proposal = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        digest = sha256(raw_proposal).hexdigest()
        if key in self._records:
            if self._records[key][0] != digest:
                raise ValueError("Saved decision revision requires new data vintage")
            return json.loads(self._records[key][1])
        stream = (observation.series_id, observation.data_vintage, management)
        if observation.decision_offset < self._clock.get(stream, -1):
            raise ValueError("Replay observation arrived out of decision order")
        if len(self._records) >= 10_000:
            raise ValueError("Offline replay record limit reached")
        episode = (observation.series_id, observation.event_id, observation.data_vintage, management)
        commitment = self._commitments.get(episode)
        if observation.position == "flat" and commitment is not None:
            result["original_commitment"] = commitment
            if result["reason"] != "unsupported_or_stale":
                result.update(action="FOLLOW_EXISTING_COMMITMENT", reason="one_attempt_per_episode_no_repeated_wait")
        elif result["action"] in ("PROPOSE_ENTRY", "COMMIT_WAIT3_ENTRY"):
            self._commitments[episode] = {"action": result["action"], "decision_offset": observation.decision_offset,
                                         "entry_decision_offset": observation.decision_offset + (3 if result["action"] == "COMMIT_WAIT3_ENTRY" else 0)}
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        self._clock[stream] = observation.decision_offset
        self._records[key] = digest, encoded
        return json.loads(encoded)
