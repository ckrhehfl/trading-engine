"""Source-only scientific/resource contract checks; never open evidence paths."""
import json
from pathlib import Path

from research import intraday_campaign_features as features
from research import intraday_campaign_wsl_transport as wire


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "configs/research/discovery/intraday-campaign-context-input-readiness-v1.json"


def registration():
    return json.loads(SPEC.read_bytes())


def test_original_three_pins_and_full_geometry_are_preserved():
    spec = registration()
    original = json.loads((ROOT / "configs/research/discovery/intraday-campaign-readiness-v1.json").read_bytes())
    price = json.loads((ROOT / "configs/research/discovery/intraday-context-comparison-v2.json").read_bytes())
    assert set(spec["inputs"]) >= {"EA-A", "EA-B", "CC"}
    for setup in ("EA-A", "EA-B"):
        assert {key: spec["inputs"][setup][key] for key in ("path", "sha256")} == original["inputs"][setup]
    assert spec["inputs"]["CC"]["path"] == price["inputs"]["price_path"]
    assert spec["inputs"]["CC"]["sha256"] == price["inputs"]["price_compressed_sha256"]
    assert spec["inputs"]["CC"]["decoded_stream_sha256"] == price["inputs"]["price_stream_sha256"]
    assert spec["geometry"] == {key: price["geometry"][key] for key in spec["geometry"]}
    assert spec["inputs"]["database_reads"] is False
    assert spec["inputs"]["volume_archive_reads"] is False
    assert spec["inputs"]["new_source_acquisition"] is False


def test_ordinary_profile_bounds_the_entire_unit_without_core_scope():
    execution = registration()["execution"]
    profile = wire.ORDINARY300
    assert execution["resource_profile"] == profile.id
    assert execution["role_cpu_seconds"] == {name: caps["cpu_seconds"] for name, caps in profile.role_limits().items()}
    assert execution["role_address_space_bytes"] == {name: caps["address_space_bytes"] for name, caps in profile.role_limits().items()}
    assert execution["helper_reservations_within_roles"] == profile.helper_limits()
    assert sum(execution["role_cpu_seconds"].values()) == execution["shared_cpu_seconds"] == 300
    assert sum(execution["role_address_space_bytes"].values()) == execution["shared_address_space_bytes"] == 512 * wire.MIB
    assert execution["operator_core_CPU600_scope_inherited"] is False
    assert execution["automatic_actual_retries"] == 0
    assert execution["concurrent_units"] == 1


def test_new_proxy_clocks_and_individual_feature_masks_are_explicit():
    spec = registration()
    proxy = spec["market_proxy"]
    assert proxy["id"] == features.PROXY_ID
    assert proxy["minimum_common_peers"] == features.MINIMUM_COMMON_PEERS == 5
    assert set(proxy["windows"]) == set(features.WINDOWS)
    assert tuple(spec["prepared_features_only"]) == features.FEATURE_NAMES
    assert spec["feature_definitions"]["feature_imputation"] is False
    assert spec["feature_definitions"]["entry_OPEN_as_feature"] is False
    assert spec["feature_definitions"]["unrelated_missing_feature_erases_observed_feature"] is False
    assert spec["clocks_and_metadata"]["historical_clock_finality_and_revision_certified"] is False
    assert "original decision" in spec["clocks_and_metadata"]["derived_feature_available_at"]
    assert spec["stored_volume"]["minute_availability_reauthenticated"] is False


def test_readiness_is_not_performance_training_or_a_promotion():
    spec = registration()
    output = spec["readiness_output"]
    assert spec["mode"] == "causal_feature_input_readiness_no_postdecision_labels_or_performance"
    assert type(spec["actual_runner_ready"]) is bool
    assert output["training_label_analysis_or_fit"] is False
    assert output["new_policy_selection_predicates_or_postdecision_labels"] is False
    assert output["strategy_profit_means_or_power_or_strategy_pass"] is False
    assert sum(output["first100_and_five_past_only_feature_support_blocks"]) + 100 == spec["geometry"]["sessions"]
    assert spec["promotion_allowed"] is False
    assert "normal PR merge" in spec["execution"]["before_actual"]
    assert spec["execution"]["existing_journal_transport"] is False
