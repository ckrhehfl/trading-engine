"""Cross-check frozen source, numerical and accounting registration seams."""
import json
from pathlib import Path
import tomllib

from research import intraday_campaign_ml_labels as labels
from research import intraday_campaign_ml_models as models


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "configs/research/discovery/intraday-campaign-ml-selectors-v1.json"


def test_model_contract_matches_registered_fixed_feature_and_backend_parameters():
    specification = json.loads(PATH.read_text())
    assert tuple(specification["features"]["order"]) == models.FEATURE_NAMES == labels.FEATURE_ORDER
    assert specification["learners"]["P09"]["alpha"] == models.RIDGE_PARAMETERS["alpha"]
    for key, value in models.LIGHTGBM_PARAMETERS.items():
        assert specification["learners"]["P10"][key] == value
    for key in ("absolute_tolerance", "relative_tolerance", "strict_positive_sign_tolerance"):
        assert specification["learners"]["numerical_contract"][key] == models.NUMERICAL_CONTRACT[key]
    assert len(models.lightgbm_parameter_manifest()["native_effective"]) == 120
    assert specification["learners"]["stress43"].startswith("exact primary33")


def test_calendar_fits_and_account_work_are_distinct_from_source_geometry():
    specification = json.loads(PATH.read_text())
    chronology, statistics = specification["chronology"], specification["statistics"]
    assert chronology["source_sessions"] == 257 == specification["geometry"]["sessions"]
    assert chronology["initial_training_sessions"] + chronology["evaluation_sessions"] == 257
    assert chronology["evaluation_sessions"] == 157
    assert tuple(map(tuple, chronology["fold_source_slices"])) == labels.FOLD_SLICES
    assert sum(chronology["fold_lengths"]) == 157
    assert chronology["fits_per_unit"] == 2 * len(labels.FOLD_SLICES) == 10
    assert chronology["planned_fit_slots_total"] == 2 * chronology["fits_per_unit"]
    assert statistics["scenario_scopes"] == 2 * 2 * 2 * 2
    assert statistics["day_replays_per_complete_unit"] == 157 * statistics["scenario_scopes"]
    assert statistics["own_label_planning_count_per_unit"] == 16 * 2 + 4 * (2 * 2 + 1)
    assert specification["population"]["spent_discovery"] is True
    assert specification["population"]["untouched_validation_claim"] is False
    assert specification["account"]["unavailable_day"].startswith("EVERY selected evaluation date")


def test_ordinary_shared_budget_includes_all_roles_without_core_exception():
    execution = json.loads(PATH.read_text())["execution"]
    assert sum(execution["role_cpu_seconds"].values()) == execution["shared_cpu_seconds"] == 300
    assert sum(execution["role_address_space_bytes"].values()) == execution["shared_address_space_bytes"] == 512 * 1024**2
    assert execution["concurrent_units"] == 1 and execution["automatic_actual_retries"] == 0
    assert execution["operator_core_CPU600_scope_inherited"] is False
    assert "durable canonical started" in execution["before_actual"][-1]


def test_ci_and_native_worker_versions_agree_with_unchanged_original_input_pins():
    specification = json.loads(PATH.read_text())
    dependencies = tomllib.loads((ROOT / "python/pyproject.toml").read_text())["dependency-groups"]["dev"]
    for name, version in specification["numerical_environment"]["versions"].items():
        assert f"{name}=={version}" in dependencies
    assert set(specification["numerical_environment"]["thread_environment"]) == set(models.THREAD_VARIABLES)
    assert set(specification["numerical_environment"]["thread_environment"].values()) == {"1"}
    original = json.loads((ROOT / "configs/research/discovery/intraday-campaign-h45-accounts-v1.json").read_text())
    for name in ("EA-A", "EA-B", "CC"):
        assert specification["inputs"][name] == original["inputs"][name]
    assert specification["inputs"]["fourth_derived_input"] is False
    assert specification["inputs"]["original_AB_detectors_rerun"] is False
    assert set(specification["units"]) == {"P09", "P10"}
