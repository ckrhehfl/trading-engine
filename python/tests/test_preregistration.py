"""Tests for `python/research/preregistration.py` -- Strategy Research
Task S. See `.planning/sr-s-preregistration.md` for the schema's
field-by-field reasoning and CLAUDE.md's "Backtest/Walk-Forward
Eligibility Bar" (amended 2026-07-29) for the criteria this artifact
encodes.

Every fixture here is **synthetic**. No test reads
`runs/experiments.jsonl`, the real kline cache, or any committed
registration -- matching `test_overfitting_check.py`/`test_retrospective.py`.
"""

import hashlib
import json
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from research.preregistration import (
    CRITERION_KIND_HOLDOUT_PSR,
    CRITERION_KIND_WALK_FORWARD_DSR,
    DEFAULT_PREREGISTRATION_DIR,
    ELIGIBILITY_BAR_DSR_THRESHOLD,
    REQUIRED_OUTCOME_REGIONS,
    SPLIT_HOLDOUT,
    SPLIT_RESEARCH,
    GridExpansionError,
    Preregistration,
    PreregistrationError,
    candidate_rows,
    check_run_matches_preregistration,
    enumerate_candidates,
    file_sha256,
    frequency_scaled_min_trades,
    load_preregistration,
    parameter_names,
    validate_preregistration,
    warn_if_uncommitted,
)

PREREG_ID = "sr-s-synthetic-demo"

STEP_MS = 3_600_000  # 1h grid
START_MS = (1_700_000_000_000 // STEP_MS) * STEP_MS
END_MS = START_MS + 100 * STEP_MS


def _valid_config(**overrides) -> dict:
    config = {
        "preregistration_id": PREREG_ID,
        "registered_at": "2026-07-30T00:00:00Z",
        "strategy_family": "synthetic-demo",
        "strategy_id": "sr-s-synthetic-demo",
        "strategy_version": "v1",
        "strategy_entry_point": "research.strategies.ma_crossover:MACrossoverTrainable",
        "hypothesis": (
            "A synthetic hypothesis, falsifiable: the strategy's mean fold Sharpe over the "
            "declared window is greater than zero by more than selection noise admits."
        ),
        "prior_art": ["No prior art -- this is a synthetic fixture, not a real attempt."],
        "data": {
            "symbol": "BTC-USDT",
            "interval": "1h",
            "source": "BingX production klines cache (python/data/var/klines.sqlite3)",
            "split": SPLIT_RESEARCH,
            "holdout_config_path": "configs/research/holdout_1h.json",
            "start_ms": START_MS,
            "end_ms": END_MS,
            "expected_bars": 100,
        },
        "parameter_grid": {"fast": [5, 8], "slow": [20, 30, 40]},
        "total_candidates": 6,
        "free_parameter_count": 2,
        "procedure": {
            "train_bars": 40,
            "validate_bars": 20,
            "step_bars": 20,
            "fee_bps": "5",
            "slippage_bps": "2",
            "bars_per_day": 24,
            "funding_included": False,
        },
        "primary_criterion": {
            "kind": CRITERION_KIND_WALK_FORWARD_DSR,
            "threshold": 0.95,
            "min_fold_consistency": "0.80",
            "sign_test_alpha": 0.05,
            "max_drawdown_ceiling": "0.20",
            "min_total_trades": 30,
            "profit_factor_floor": "1.3",
            "criteria_pinned_at_claude_md_revision": "2026-07-29",
        },
        "secondary_reported_not_gating": [
            "one-sample t-test p-value on the mean fold Sharpe (continuity only)",
            "PSR at N=1",
        ],
        "declared_detection_floor_sharpe": 1.21,
        "declared_power": {
            "assumed_true_sharpe": 0.8,
            "probability": 0.35,
            "derivation": "Synthetic: Phi(sqrt(years)*SR_true - Phi^-1(1-alpha)) at the declared span.",
        },
        "outcome_interpretation": {
            "PASS": "Criterion cleared: report as a candidate for the holdout confirmation protocol.",
            "INCONCLUSIVE": "Below the trade-count floor or the window was not powered: no conclusion either way.",
            "FAIL": "Criterion not cleared on an adequately powered window: the hypothesis is not supported.",
        },
        "stopping_rule": (
            "One run of the enumerated grid. No candidate may be added, removed or re-valued "
            "after this file is committed; a changed grid is a new registration with a new id."
        ),
    }
    config.update(overrides)
    return config


def _write(tmp_path: Path, config: dict, name: str | None = None) -> Path:
    path = tmp_path / (name if name is not None else f"{config['preregistration_id']}.json")
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def _run_kwargs(**overrides) -> dict:
    kwargs = {
        "strategy_id": "sr-s-synthetic-demo",
        "strategy_version": "v1",
        "strategy_family": "synthetic-demo",
        "symbol": "BTC-USDT",
        "interval": "1h",
        "train_bars": 40,
        "validate_bars": 20,
        "step_bars": 20,
        "bars_per_day": 24,
        "fee_bps": Decimal("5"),
        "slippage_bps": Decimal("2"),
        "funding_included": False,
        "is_holdout_run": False,
        "total_candidates": 6,
    }
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# validate_preregistration -- the happy path
# ---------------------------------------------------------------------------


def test_a_complete_registration_validates():
    validate_preregistration(_valid_config())  # must not raise


def test_a_holdout_confirmation_registration_validates_without_fold_geometry():
    # A single-window holdout run has no folds, so train/validate/step bars
    # are meaningless there -- CLAUDE.md's single-window variant explicitly
    # drops every fold-based clause.
    config = _valid_config(
        data={**_valid_config()["data"], "split": SPLIT_HOLDOUT},
        procedure={
            "fee_bps": "5",
            "slippage_bps": "2",
            "bars_per_day": 24,
            "funding_included": False,
        },
        primary_criterion={
            "kind": CRITERION_KIND_HOLDOUT_PSR,
            "threshold": 0.95,
            "max_drawdown_ceiling": "0.20",
            "min_total_trades": 41,
            "profit_factor_floor": "1.3",
            "require_sharpe_above_detection_floor": True,
            "criteria_pinned_at_claude_md_revision": "2026-07-29",
        },
    )

    validate_preregistration(config)


# ---------------------------------------------------------------------------
# validate_preregistration -- one test per individually-missing field
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "preregistration_id",
        "registered_at",
        "strategy_family",
        "strategy_id",
        "strategy_version",
        "strategy_entry_point",
        "hypothesis",
        "prior_art",
        "data",
        "parameter_grid",
        "total_candidates",
        "free_parameter_count",
        "procedure",
        "primary_criterion",
        "secondary_reported_not_gating",
        "declared_detection_floor_sharpe",
        "declared_power",
        "outcome_interpretation",
        "stopping_rule",
    ],
)
def test_every_required_top_level_field_is_individually_mandatory(field):
    config = _valid_config()
    del config[field]

    with pytest.raises(PreregistrationError, match=field):
        validate_preregistration(config)


@pytest.mark.parametrize(
    "field",
    [
        "preregistration_id",
        "registered_at",
        "strategy_family",
        "strategy_id",
        "strategy_version",
        "strategy_entry_point",
        "hypothesis",
        "stopping_rule",
    ],
)
def test_a_blank_string_field_is_rejected_like_a_missing_one(field):
    with pytest.raises(PreregistrationError, match=field):
        validate_preregistration(_valid_config(**{field: "   "}))


def test_a_non_mapping_registration_is_rejected():
    with pytest.raises(PreregistrationError):
        validate_preregistration(["not", "a", "mapping"])


def test_an_unknown_top_level_key_is_rejected():
    with pytest.raises(PreregistrationError, match="total_candidate"):
        validate_preregistration(_valid_config(total_candidate=6))


def test_the_documented_optional_keys_are_accepted():
    validate_preregistration(_valid_config(rationale="why this grid", notes=["a note"]))


def test_strategy_entry_point_must_name_a_module_and_an_attribute():
    with pytest.raises(PreregistrationError, match="strategy_entry_point"):
        validate_preregistration(_valid_config(strategy_entry_point="research.strategies.ma_crossover"))


def test_strategy_entry_point_must_live_inside_the_research_package():
    # A config-driven import is an execution surface; it is confined to
    # this project's own research package rather than left open.
    with pytest.raises(PreregistrationError, match="strategy_entry_point"):
        validate_preregistration(_valid_config(strategy_entry_point="os:getcwd"))


# ---------------------------------------------------------------------------
# prior_art / secondary_reported_not_gating
# ---------------------------------------------------------------------------


def test_prior_art_may_not_be_an_empty_list():
    # An empty list is ambiguous between "none exists" and "didn't look";
    # the honest entry for genuinely novel work is a string saying so.
    with pytest.raises(PreregistrationError, match="prior_art"):
        validate_preregistration(_valid_config(prior_art=[]))


def test_prior_art_entries_must_be_non_blank_strings():
    with pytest.raises(PreregistrationError, match="prior_art"):
        validate_preregistration(_valid_config(prior_art=["ok", ""]))


def test_secondary_reported_not_gating_may_not_be_empty():
    with pytest.raises(PreregistrationError, match="secondary_reported_not_gating"):
        validate_preregistration(_valid_config(secondary_reported_not_gating=[]))


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["symbol", "interval", "source", "split", "holdout_config_path", "start_ms", "end_ms", "expected_bars"],
)
def test_every_data_field_is_individually_mandatory(field):
    data = dict(_valid_config()["data"])
    del data[field]

    with pytest.raises(PreregistrationError, match=field):
        validate_preregistration(_valid_config(data=data))


def test_an_unrecognized_split_is_rejected():
    with pytest.raises(PreregistrationError, match="split"):
        validate_preregistration(_valid_config(data={**_valid_config()["data"], "split": "train"}))


def test_expected_bars_must_be_positive():
    with pytest.raises(PreregistrationError, match="expected_bars"):
        validate_preregistration(_valid_config(data={**_valid_config()["data"], "expected_bars": 0}))


def test_an_inverted_window_is_rejected():
    with pytest.raises(PreregistrationError, match="end_ms"):
        validate_preregistration(_valid_config(data={**_valid_config()["data"], "end_ms": START_MS}))


def test_grid_alignment_is_deliberately_left_to_the_data_layer():
    # `data/_grid.py` is documented as internal to `python/data/`, and
    # `data.store.fetch_klines` already fails loud on a misaligned range.
    # Duplicating its interval table here to fail milliseconds earlier
    # would create the drift that module exists to prevent, so a misaligned
    # window validates here and fails at load time instead -- see
    # `test_run_preregistered.py`, which proves that path still fires.
    validate_preregistration(_valid_config(data={**_valid_config()["data"], "start_ms": START_MS + 1}))


# ---------------------------------------------------------------------------
# parameter_grid and total_candidates
# ---------------------------------------------------------------------------


def test_an_empty_parameter_grid_is_rejected():
    with pytest.raises(PreregistrationError, match="parameter_grid"):
        validate_preregistration(_valid_config(parameter_grid={}, total_candidates=1))


def test_a_parameter_with_no_values_is_rejected():
    with pytest.raises(PreregistrationError, match="parameter_grid"):
        validate_preregistration(_valid_config(parameter_grid={"fast": []}, total_candidates=0))


def test_an_explicit_candidate_list_is_accepted():
    validate_preregistration(
        _valid_config(
            parameter_grid=[{"fast": 5, "slow": 20}, {"fast": 8, "slow": 30}],
            total_candidates=2,
        )
    )


def test_explicit_candidates_must_all_declare_the_same_parameters():
    with pytest.raises(PreregistrationError, match="parameter_grid"):
        validate_preregistration(
            _valid_config(
                parameter_grid=[{"fast": 5, "slow": 20}, {"fast": 8}],
                total_candidates=2,
            )
        )


def test_an_empty_explicit_candidate_list_is_rejected():
    with pytest.raises(PreregistrationError, match="parameter_grid"):
        validate_preregistration(_valid_config(parameter_grid=[], total_candidates=0))


def test_total_candidates_must_equal_the_enumerated_grid_size():
    with pytest.raises(PreregistrationError, match="total_candidates"):
        validate_preregistration(_valid_config(total_candidates=5))


def test_total_candidates_must_be_positive():
    with pytest.raises(PreregistrationError, match="total_candidates"):
        validate_preregistration(_valid_config(parameter_grid={"fast": [5]}, total_candidates=0))


def test_free_parameter_count_may_not_be_negative():
    with pytest.raises(PreregistrationError, match="free_parameter_count"):
        validate_preregistration(_valid_config(free_parameter_count=-1))


def test_free_parameter_count_of_zero_is_allowed():
    # Every parameter fixed by prior art rather than fitted here is a
    # legitimate, and the strongest possible, registration.
    validate_preregistration(_valid_config(free_parameter_count=0))


# ---------------------------------------------------------------------------
# procedure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["train_bars", "validate_bars", "step_bars", "fee_bps", "slippage_bps", "bars_per_day", "funding_included"],
)
def test_every_procedure_field_is_mandatory_for_a_walk_forward_registration(field):
    procedure = dict(_valid_config()["procedure"])
    del procedure[field]

    with pytest.raises(PreregistrationError, match=field):
        validate_preregistration(_valid_config(procedure=procedure))


@pytest.mark.parametrize("field", ["train_bars", "validate_bars", "step_bars", "bars_per_day"])
def test_procedure_bar_counts_must_be_positive(field):
    with pytest.raises(PreregistrationError, match=field):
        validate_preregistration(_valid_config(procedure={**_valid_config()["procedure"], field: 0}))


@pytest.mark.parametrize("field", ["fee_bps", "slippage_bps"])
def test_costs_may_not_be_negative(field):
    with pytest.raises(PreregistrationError, match=field):
        validate_preregistration(_valid_config(procedure={**_valid_config()["procedure"], field: "-1"}))


def test_zero_costs_are_allowed_though_unrealistic():
    validate_preregistration(
        _valid_config(procedure={**_valid_config()["procedure"], "fee_bps": "0", "slippage_bps": "0"})
    )


def test_funding_included_must_be_a_boolean():
    with pytest.raises(PreregistrationError, match="funding_included"):
        validate_preregistration(_valid_config(procedure={**_valid_config()["procedure"], "funding_included": "no"}))


# ---------------------------------------------------------------------------
# primary_criterion -- conformance to the human-approved Eligibility Bar
# ---------------------------------------------------------------------------


def test_an_unrecognized_criterion_kind_is_rejected():
    with pytest.raises(PreregistrationError, match="kind"):
        validate_preregistration(
            _valid_config(primary_criterion={**_valid_config()["primary_criterion"], "kind": "sharpe_go_brrr"})
        )


def test_a_threshold_below_the_approved_bar_is_rejected():
    # CLAUDE.md's Bar is human-approval-gated. Registering a laxer
    # threshold would be a de facto amendment by a non-human.
    with pytest.raises(PreregistrationError, match="threshold"):
        validate_preregistration(
            _valid_config(primary_criterion={**_valid_config()["primary_criterion"], "threshold": 0.90})
        )


def test_a_threshold_stricter_than_the_approved_bar_is_accepted():
    validate_preregistration(
        _valid_config(primary_criterion={**_valid_config()["primary_criterion"], "threshold": 0.99})
    )


def test_the_approved_threshold_constant_is_the_one_the_bar_names():
    assert ELIGIBILITY_BAR_DSR_THRESHOLD == 0.95


def test_a_threshold_above_one_is_rejected():
    with pytest.raises(PreregistrationError, match="threshold"):
        validate_preregistration(
            _valid_config(primary_criterion={**_valid_config()["primary_criterion"], "threshold": 1.5})
        )


def test_a_drawdown_ceiling_looser_than_the_approved_band_is_rejected():
    with pytest.raises(PreregistrationError, match="max_drawdown_ceiling"):
        validate_preregistration(
            _valid_config(primary_criterion={**_valid_config()["primary_criterion"], "max_drawdown_ceiling": "0.40"})
        )


def test_a_profit_factor_floor_below_the_approved_band_is_rejected():
    with pytest.raises(PreregistrationError, match="profit_factor_floor"):
        validate_preregistration(
            _valid_config(primary_criterion={**_valid_config()["primary_criterion"], "profit_factor_floor": "1.0"})
        )


def test_a_fold_consistency_floor_below_the_approved_band_is_rejected():
    with pytest.raises(PreregistrationError, match="min_fold_consistency"):
        validate_preregistration(
            _valid_config(primary_criterion={**_valid_config()["primary_criterion"], "min_fold_consistency": "0.50"})
        )


def test_a_sign_test_alpha_looser_than_the_project_convention_is_rejected():
    with pytest.raises(PreregistrationError, match="sign_test_alpha"):
        validate_preregistration(
            _valid_config(primary_criterion={**_valid_config()["primary_criterion"], "sign_test_alpha": 0.10})
        )


def test_the_criteria_revision_date_is_mandatory():
    criterion = dict(_valid_config()["primary_criterion"])
    del criterion["criteria_pinned_at_claude_md_revision"]

    with pytest.raises(PreregistrationError, match="criteria_pinned_at_claude_md_revision"):
        validate_preregistration(_valid_config(primary_criterion=criterion))


def test_a_walk_forward_registration_must_declare_the_fold_clauses():
    criterion = dict(_valid_config()["primary_criterion"])
    del criterion["min_fold_consistency"]

    with pytest.raises(PreregistrationError, match="min_fold_consistency"):
        validate_preregistration(_valid_config(primary_criterion=criterion))


def test_a_holdout_registration_must_require_the_detection_floor_check():
    # CLAUDE.md's single-window variant, clause 3: an observed Sharpe below
    # the holdout window's own detection floor is "not powered to confirm".
    config = _valid_config(
        data={**_valid_config()["data"], "split": SPLIT_HOLDOUT},
        procedure={"fee_bps": "5", "slippage_bps": "2", "bars_per_day": 24, "funding_included": False},
        primary_criterion={
            "kind": CRITERION_KIND_HOLDOUT_PSR,
            "threshold": 0.95,
            "max_drawdown_ceiling": "0.20",
            "min_total_trades": 41,
            "profit_factor_floor": "1.3",
            "require_sharpe_above_detection_floor": False,
            "criteria_pinned_at_claude_md_revision": "2026-07-29",
        },
    )

    with pytest.raises(PreregistrationError, match="require_sharpe_above_detection_floor"):
        validate_preregistration(config)


def test_a_holdout_registration_may_not_declare_a_walk_forward_split():
    with pytest.raises(PreregistrationError, match="split"):
        validate_preregistration(
            _valid_config(
                primary_criterion={
                    "kind": CRITERION_KIND_HOLDOUT_PSR,
                    "threshold": 0.95,
                    "max_drawdown_ceiling": "0.20",
                    "min_total_trades": 41,
                    "profit_factor_floor": "1.3",
                    "require_sharpe_above_detection_floor": True,
                    "criteria_pinned_at_claude_md_revision": "2026-07-29",
                }
            )
        )


def test_min_total_trades_must_be_positive():
    with pytest.raises(PreregistrationError, match="min_total_trades"):
        validate_preregistration(
            _valid_config(primary_criterion={**_valid_config()["primary_criterion"], "min_total_trades": 0})
        )


# ---------------------------------------------------------------------------
# The frequency-scaled trade-count floor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "total_evaluated_bars,bars_per_day,expected",
    [
        # CLAUDE.md's own published table (revised 2026-07-29), reproduced
        # exactly -- these three rows are the ones the approved wording
        # states, not values invented here.
        (13_680, 24, 30),  # 1h, 19 folds x 720
        (8_640, 96, 30),  # 15m, 3 folds x 2,880
        (822, 1, 41),  # 1d, sr-t's 822 research bars
        # The 100 cap only re-engages far above anything attempted so far.
        (100_000, 1, 100),
        # And the absolute floor binds at the bottom.
        (0, 1, 30),
    ],
)
def test_the_trade_floor_reproduces_the_approved_table(total_evaluated_bars, bars_per_day, expected):
    assert (
        frequency_scaled_min_trades(total_evaluated_bars=total_evaluated_bars, bars_per_day=bars_per_day)
        == expected
    )


def test_the_trade_floor_rejects_a_non_positive_bars_per_day():
    with pytest.raises(ValueError, match="bars_per_day"):
        frequency_scaled_min_trades(total_evaluated_bars=100, bars_per_day=0)


def test_a_registered_trade_floor_below_the_approved_one_is_rejected():
    with pytest.raises(PreregistrationError, match="min_total_trades"):
        validate_preregistration(
            _valid_config(primary_criterion={**_valid_config()["primary_criterion"], "min_total_trades": 5})
        )


def test_a_registered_trade_floor_stricter_than_the_approved_one_is_accepted():
    validate_preregistration(
        _valid_config(primary_criterion={**_valid_config()["primary_criterion"], "min_total_trades": 60})
    )


def test_the_expected_fold_count_is_derived_and_reported_not_enforced(tmp_path):
    # 100 bars at train=40/validate=20/step=20 gives 3 folds -- below the
    # Bar's 8-10 credibility floor, and deliberately still valid: sr-t's
    # 822-bar 1d window may not reach 8 folds at any sensible sizing, and a
    # criterion a data-limited window cannot satisfy would block the very
    # attempt this machinery exists to enable.
    prereg = load_preregistration(_write(tmp_path, _valid_config()))

    assert prereg.expected_fold_count == 3


def test_a_holdout_registration_has_no_fold_count_at_all(tmp_path):
    config = _valid_config(
        data={**_valid_config()["data"], "split": SPLIT_HOLDOUT},
        procedure={"fee_bps": "5", "slippage_bps": "2", "bars_per_day": 24, "funding_included": False},
        primary_criterion={
            "kind": CRITERION_KIND_HOLDOUT_PSR,
            "threshold": 0.95,
            "max_drawdown_ceiling": "0.20",
            "min_total_trades": 41,
            "profit_factor_floor": "1.3",
            "require_sharpe_above_detection_floor": True,
            "criteria_pinned_at_claude_md_revision": "2026-07-29",
        },
    )

    assert load_preregistration(_write(tmp_path, config)).expected_fold_count is None


# ---------------------------------------------------------------------------
# The two fields that carry disproportionate weight
# ---------------------------------------------------------------------------


def test_the_declared_detection_floor_must_be_positive():
    with pytest.raises(PreregistrationError, match="declared_detection_floor_sharpe"):
        validate_preregistration(_valid_config(declared_detection_floor_sharpe=0))


@pytest.mark.parametrize("field", ["assumed_true_sharpe", "probability", "derivation"])
def test_every_declared_power_field_is_mandatory(field):
    power = dict(_valid_config()["declared_power"])
    del power[field]

    with pytest.raises(PreregistrationError, match=field):
        validate_preregistration(_valid_config(declared_power=power))


def test_a_declared_power_probability_outside_zero_to_one_is_rejected():
    with pytest.raises(PreregistrationError, match="probability"):
        validate_preregistration(
            _valid_config(declared_power={**_valid_config()["declared_power"], "probability": 1.4})
        )


def test_a_blank_power_derivation_is_rejected():
    with pytest.raises(PreregistrationError, match="derivation"):
        validate_preregistration(_valid_config(declared_power={**_valid_config()["declared_power"], "derivation": ""}))


@pytest.mark.parametrize("region", REQUIRED_OUTCOME_REGIONS)
def test_all_three_outcome_regions_are_mandatory(region):
    outcome = dict(_valid_config()["outcome_interpretation"])
    del outcome[region]

    with pytest.raises(PreregistrationError, match=region):
        validate_preregistration(_valid_config(outcome_interpretation=outcome))


def test_the_outcome_regions_are_exactly_pass_inconclusive_fail():
    # Three regions, not two: a binary framing would be dishonest when a
    # real edge can plausibly fail to be detected.
    assert REQUIRED_OUTCOME_REGIONS == ("PASS", "INCONCLUSIVE", "FAIL")


def test_a_blank_outcome_region_is_rejected():
    outcome = {**_valid_config()["outcome_interpretation"], "FAIL": "  "}

    with pytest.raises(PreregistrationError, match="FAIL"):
        validate_preregistration(_valid_config(outcome_interpretation=outcome))


def test_an_extra_outcome_region_is_allowed():
    # CLAUDE.md's holdout variant has its own "not powered to confirm"
    # outcome, so a fourth region is legitimate.
    outcome = {
        **_valid_config()["outcome_interpretation"],
        "NOT-POWERED-TO-CONFIRM": "Observed Sharpe below the window's detection floor.",
    }

    validate_preregistration(_valid_config(outcome_interpretation=outcome))


# ---------------------------------------------------------------------------
# load_preregistration
# ---------------------------------------------------------------------------


def test_load_returns_the_config_the_path_and_the_sha256_of_its_bytes(tmp_path):
    path = _write(tmp_path, _valid_config())

    prereg = load_preregistration(path)

    assert isinstance(prereg, Preregistration)
    assert prereg.preregistration_id == PREREG_ID
    assert prereg.config["strategy_id"] == "sr-s-synthetic-demo"
    assert prereg.path == path
    assert prereg.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_sha_is_computed_from_file_bytes_so_an_edit_changes_it(tmp_path):
    path = _write(tmp_path, _valid_config())
    before = load_preregistration(path).sha256

    # A whitespace-only edit changes no field but does change the bytes --
    # which is exactly the point: the log records what was on disk.
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert load_preregistration(path).sha256 != before
    assert file_sha256(path) == load_preregistration(path).sha256


def test_load_raises_on_an_incomplete_registration(tmp_path):
    config = _valid_config()
    del config["declared_power"]
    path = _write(tmp_path, config)

    with pytest.raises(PreregistrationError, match="declared_power"):
        load_preregistration(path)


def test_load_raises_on_unparseable_json(tmp_path):
    path = tmp_path / f"{PREREG_ID}.json"
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(PreregistrationError):
        load_preregistration(path)


def test_load_raises_when_the_id_does_not_match_the_filename(tmp_path):
    path = _write(tmp_path, _valid_config(), name="something-else.json")

    with pytest.raises(PreregistrationError, match="filename"):
        load_preregistration(path)


def test_load_raises_on_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_preregistration(tmp_path / "absent.json")


def test_preregistration_to_dict_round_trips_the_identity_fields(tmp_path):
    path = _write(tmp_path, _valid_config())

    record = load_preregistration(path).to_dict()

    assert record["preregistration_id"] == PREREG_ID
    assert record["preregistration_sha256"] == file_sha256(path)
    assert record["total_candidates"] == 6


# ---------------------------------------------------------------------------
# Grid enumeration
# ---------------------------------------------------------------------------


def test_a_mapping_grid_enumerates_as_a_cartesian_product_in_declared_key_order():
    candidates = enumerate_candidates({"fast": [5, 8], "slow": [20, 30]})

    assert candidates == [
        {"fast": 5, "slow": 20},
        {"fast": 5, "slow": 30},
        {"fast": 8, "slow": 20},
        {"fast": 8, "slow": 30},
    ]


def test_an_explicit_grid_enumerates_to_itself():
    grid = [{"fast": 5, "slow": 20}, {"fast": 8, "slow": 30}]

    assert enumerate_candidates(grid) == grid


def test_parameter_names_follow_the_declared_order_for_both_grid_shapes():
    assert parameter_names({"fast": [5], "slow": [20]}) == ["fast", "slow"]
    assert parameter_names([{"fast": 5, "slow": 20}]) == ["fast", "slow"]


def test_candidate_rows_are_value_lists_aligned_with_parameter_names():
    grid = {"fast": [5, 8], "slow": [20]}

    assert parameter_names(grid) == ["fast", "slow"]
    assert candidate_rows(grid) == [[5, 20], [8, 20]]


def test_enumeration_is_deterministic_across_calls():
    grid = {"a": [1, 2], "b": [3, 4], "c": [5]}

    assert enumerate_candidates(grid) == enumerate_candidates(grid)
    assert len(enumerate_candidates(grid)) == 4


# ---------------------------------------------------------------------------
# warn_if_uncommitted
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    return repo


def test_a_committed_unmodified_registration_does_not_warn(git_repo, caplog):
    path = _write(git_repo, _valid_config())
    _git(git_repo, "add", path.name)
    _git(git_repo, "commit", "--quiet", "-m", "add registration")

    with caplog.at_level("WARNING"):
        assert warn_if_uncommitted(path) is True

    assert not caplog.records


def test_an_edited_registration_warns_loudly(git_repo, caplog):
    path = _write(git_repo, _valid_config())
    _git(git_repo, "add", path.name)
    _git(git_repo, "commit", "--quiet", "-m", "add registration")
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        assert warn_if_uncommitted(path) is False

    assert any("uncommitted" in record.message.lower() for record in caplog.records)


def test_a_never_committed_registration_warns(git_repo, caplog):
    # An untracked file produces no `git diff` at all, so this case has to
    # be detected explicitly -- it is the strongest version of the failure
    # this check exists for.
    path = _write(git_repo, _valid_config())

    with caplog.at_level("WARNING"):
        assert warn_if_uncommitted(path) is False

    assert any("uncommitted" in record.message.lower() for record in caplog.records)


def test_a_gitignored_registration_warns(git_repo, caplog):
    # `git status --porcelain` WITHOUT `--ignored` reports nothing at all for
    # an ignored file, so it would be read as "clean" -- verified by direct
    # probe. This repo gitignores `runs/` and `python/data/var/`, so a
    # registration dropped into either would otherwise look committed while
    # being invisible to git. (CodeRabbit review finding on this task's PR.)
    (git_repo / ".gitignore").write_text(f"{PREREG_ID}.json\n", encoding="utf-8")
    path = _write(git_repo, _valid_config())

    with caplog.at_level("WARNING"):
        assert warn_if_uncommitted(path) is False

    assert any("not committed" in record.message.lower() for record in caplog.records)


def test_a_registration_inside_a_gitignored_directory_warns(git_repo, caplog):
    (git_repo / ".gitignore").write_text("reserved/\n", encoding="utf-8")
    directory = git_repo / "reserved"
    directory.mkdir()
    path = _write(directory, _valid_config())

    with caplog.at_level("WARNING"):
        assert warn_if_uncommitted(path) is False

    assert any("not committed" in record.message.lower() for record in caplog.records)


def test_git_being_unavailable_warns_and_returns_none(tmp_path, monkeypatch, caplog):
    path = _write(tmp_path, _valid_config())

    def _boom(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", _boom)

    with caplog.at_level("WARNING"):
        assert warn_if_uncommitted(path) is None

    assert any("git" in record.message.lower() for record in caplog.records)


def test_a_path_outside_any_git_checkout_returns_none(tmp_path, caplog):
    path = _write(tmp_path, _valid_config())

    with caplog.at_level("WARNING"):
        assert warn_if_uncommitted(path) is None


# ---------------------------------------------------------------------------
# check_run_matches_preregistration -- warns on everything but one thing
# ---------------------------------------------------------------------------


def test_a_matching_run_reports_no_mismatches(tmp_path, caplog):
    prereg = load_preregistration(_write(tmp_path, _valid_config()))

    with caplog.at_level("WARNING"):
        result = check_run_matches_preregistration(prereg, _run_kwargs())

    assert result.mismatches == []
    assert result.matched is True
    assert result.preregistration_id == PREREG_ID
    assert result.preregistration_sha256 == prereg.sha256
    assert not caplog.records


@pytest.mark.parametrize(
    "override",
    [
        {"strategy_id": "something-else"},
        {"strategy_version": "v2"},
        {"strategy_family": "another-family"},
        {"symbol": "ETH-USDT"},
        {"interval": "15m"},
        {"train_bars": 41},
        {"validate_bars": 21},
        {"step_bars": 10},
        {"bars_per_day": 96},
        {"fee_bps": Decimal("7")},
        {"slippage_bps": Decimal("0")},
        {"funding_included": True},
        {"is_holdout_run": True},
        {"total_candidates": 3},
        {"total_candidates": None},
    ],
)
def test_every_non_grid_mismatch_warns_and_never_raises(tmp_path, caplog, override):
    prereg = load_preregistration(_write(tmp_path, _valid_config()))

    with caplog.at_level("WARNING"):
        result = check_run_matches_preregistration(prereg, _run_kwargs(**override))

    assert result.mismatches, f"expected a mismatch for {override}"
    assert result.matched is False
    assert caplog.records, "a mismatch must be loud"
    field = next(iter(override))
    assert any(field in mismatch for mismatch in result.mismatches)


def test_growing_the_grid_is_the_one_hard_block(tmp_path):
    prereg = load_preregistration(_write(tmp_path, _valid_config()))

    with pytest.raises(GridExpansionError, match="total_candidates"):
        check_run_matches_preregistration(prereg, _run_kwargs(total_candidates=7))


def test_the_grid_block_fires_even_when_every_other_field_matches(tmp_path):
    prereg = load_preregistration(_write(tmp_path, _valid_config()))

    with pytest.raises(GridExpansionError):
        check_run_matches_preregistration(prereg, _run_kwargs(total_candidates=6000))


def test_the_grid_block_names_both_numbers(tmp_path):
    prereg = load_preregistration(_write(tmp_path, _valid_config()))

    with pytest.raises(GridExpansionError, match=r"registered 6"):
        check_run_matches_preregistration(prereg, _run_kwargs(total_candidates=9))


@pytest.mark.parametrize("bogus", ["7", "6", 7.0, 6.0, True, False, [7], {"n": 7}])
def test_a_non_integer_candidate_count_cannot_slip_past_the_block(tmp_path, bogus):
    # `"7" > 6` never compares greater, so a quoted count would previously
    # have fallen through to the warning-only path -- an evasion route around
    # the one hard block. It now fails loudly instead. (CodeRabbit review
    # finding on this task's PR.)
    prereg = load_preregistration(_write(tmp_path, _valid_config()))

    with pytest.raises(PreregistrationError, match="total_candidates"):
        check_run_matches_preregistration(prereg, _run_kwargs(total_candidates=bogus))


def test_an_explicit_none_candidate_count_still_only_warns(tmp_path, caplog):
    # `None` is this codebase's established "not a grid search" value --
    # `log_run` writes it for every standalone run -- so it is a claim of
    # nothing, treated exactly like the absent case: warned, never raised.
    prereg = load_preregistration(_write(tmp_path, _valid_config()))

    with caplog.at_level("WARNING"):
        result = check_run_matches_preregistration(prereg, _run_kwargs(total_candidates=None))

    assert any("total_candidates" in mismatch for mismatch in result.mismatches)
    assert caplog.records


def test_an_exactly_equal_candidate_count_is_not_a_mismatch(tmp_path):
    prereg = load_preregistration(_write(tmp_path, _valid_config()))

    result = check_run_matches_preregistration(prereg, _run_kwargs(total_candidates=6))

    assert result.mismatches == []


def test_a_smaller_candidate_count_warns_rather_than_blocks(tmp_path, caplog):
    # Running fewer candidates than registered is not an expansion, so it
    # cannot be the failure pre-registration exists to prevent -- but it is
    # still a deviation from the registered procedure.
    prereg = load_preregistration(_write(tmp_path, _valid_config()))

    with caplog.at_level("WARNING"):
        result = check_run_matches_preregistration(prereg, _run_kwargs(total_candidates=4))

    assert any("total_candidates" in mismatch for mismatch in result.mismatches)
    assert caplog.records


def test_the_check_result_records_which_fields_it_compared(tmp_path):
    prereg = load_preregistration(_write(tmp_path, _valid_config()))

    result = check_run_matches_preregistration(prereg, _run_kwargs())

    assert "total_candidates" in result.fields_compared
    assert "fee_bps" in result.fields_compared
    assert result.to_dict()["fields_compared"] == result.fields_compared


# ---------------------------------------------------------------------------
# The committed registrations themselves
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _committed_registrations() -> list[Path]:
    directory = _REPO_ROOT / DEFAULT_PREREGISTRATION_DIR
    return sorted(directory.glob("*.json"))


def test_at_least_one_registration_is_committed():
    # Guards the guard below: a glob over an empty (or moved) directory
    # would otherwise pass vacuously.
    assert _committed_registrations()


@pytest.mark.parametrize("path", _committed_registrations(), ids=lambda p: p.stem)
def test_every_committed_registration_validates(path):
    # Reads git-tracked config only -- not the experiment log, not market
    # data. A real regression guard on the schema: an incompatible change to
    # either the schema or a committed registration breaks here.
    load_preregistration(path)


def test_fields_the_caller_did_not_supply_are_not_invented(tmp_path):
    # A caller that never passes `symbol` should not be told it mismatched.
    prereg = load_preregistration(_write(tmp_path, _valid_config()))
    kwargs = _run_kwargs()
    del kwargs["symbol"]
    del kwargs["interval"]

    result = check_run_matches_preregistration(prereg, kwargs)

    assert result.mismatches == []
    assert "symbol" not in result.fields_compared
