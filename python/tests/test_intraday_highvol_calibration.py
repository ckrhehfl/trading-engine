import pytest

from research.intraday_ed_inputs import InputRefusal
from research.intraday_highvol_calibration import ProbabilityOutcome, reliability


def test_reliability_keeps_missing_denominators_and_compares_the_same_pairs():
    result = reliability([
        ProbabilityOutcome(0.0, False, 0.5),
        ProbabilityOutcome(0.2, True, 0.5),
        ProbabilityOutcome(1.0, True, None),
        ProbabilityOutcome(None, False, 0.5),
        ProbabilityOutcome(0.7, None, 0.5),
        ProbabilityOutcome(None, None, None),
    ])
    assert (result["population"], result["missing_forecast"], result["missing_outcome"]) == (6, 2, 2)
    assert (result["known_pairs"], result["baseline_pairs"]) == (3, 2)
    assert result["brier"] == pytest.approx(0.64 / 3)
    assert result["model_brier_on_baseline_pairs"] == pytest.approx(0.32)
    assert result["baseline_brier_on_same_pairs"] == 0.25
    assert [row["count"] for row in result["bins"]] == [1, 1, 0, 0, 1]
    assert result["bins"][2]["target_first_rate"] is None


def test_unknown_only_has_no_score_and_malformed_probability_refuses():
    assert reliability([ProbabilityOutcome(None, None, 0.4)])["brier"] is None
    with pytest.raises(InputRefusal, match="invalid_eg_probability_evidence"):
        reliability([ProbabilityOutcome(float("nan"), True, 0.4)])
