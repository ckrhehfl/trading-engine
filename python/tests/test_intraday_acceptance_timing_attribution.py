"""Invented sources only; no evidence body, network, database or archive IO."""
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import inspect
import json
import math

import pytest

from research import intraday_acceptance_timing_attribution as attribution
from test_intraday_opening_acceptance_comparison import build_synthetic_fixture as original_fixture


def build_synthetic_fixture(*, full=False, metadata_padding=0, provenance_density=0):
    """Fixture construction, including invented parent statistics, is unmeasured.

    Actual-source readers never call this factory. Its DX output is a synthetic
    completed receipt, not replay of an actual parent report.
    """
    first, second = original_fixture(full=full, metadata_padding=metadata_padding,
        provenance_density=provenance_density)
    for policy in attribution.POLICIES:
        for index, row in enumerate(second["variants"][policy]["days"]):
            label = row["arms"]["15_acceptance"]["label"]
            if label is None:
                continue
            entry = int(label["entry_open"]) + (index % 7 + 1) * 17
            old_ratio = Fraction(Decimal(label["exit_open"])) / Fraction(Decimal(label["entry_open"]))
            label["entry_open"] = str(entry)
            label["exit_open"] = str(Decimal(entry * old_ratio.numerator) / Decimal(old_ratio.denominator))
    parent = attribution.dx.build_comparison(attribution.project_ct(first), attribution.project_du(second))
    parent["specification_sha256"] = attribution.DX_SPECIFICATION_SHA256
    if full:
        # Structured invented source pressure; no private original body is read.
        parent["invented_provenance"] = [{"sequence": index, "kind": "invented_parent_receipt_atom",
            "source": "invented completed comparison provenance", "observed_at": None,
            "available_at": None, "evidence_level": "invented", "note": "fixture source metadata only"}
            for index in range(1600)]
    return first, second, parent


def _build(sources):
    first, second, parent = sources
    return attribution.build_attribution(attribution.project_ct(first), attribution.project_du(second), parent)


@pytest.fixture
def sources():
    return build_synthetic_fixture()


def test_fixed_specification_and_narrow_reuse():
    attribution.validate_spec(json.loads(attribution.SPECIFICATION_PATH.read_text()))
    assert attribution.project_ct is attribution.dx.project_ct
    assert attribution.project_du is attribution.dx.project_du
    text = inspect.getsource(attribution)
    for forbidden in ("build_comparison(", "build_calibration(", "detect_opening", "sqlite3",
            "read_bytes(", "gzip.open(", "requests."):
        assert forbidden not in text
    assert attribution.RESOURCES == attribution.dx.RESOURCES
    assert attribution.NEW_ARMS == ("B", "C")


@pytest.mark.parametrize("change", [
    lambda spec: spec["parameters"].update(bootstrap_draws=1999),
    lambda spec: spec["upstream_sha256"].update(DX="0" * 64),
    lambda spec: spec["parameters"]["views"].update(C="shifted_exit"),
    lambda spec: spec["resources"].update(cpu_seconds=301),
    lambda spec: spec.update(extra=True),
])
def test_specification_refused_before_source_access(change):
    spec = json.loads(attribution.SPECIFICATION_PATH.read_text())
    change(spec)
    with pytest.raises(attribution.AcceptanceTimingAttributionRefusal, match="specification_scope"):
        attribution.build_attribution({}, {}, {}, specification=spec)


def test_exact_decomposition_denominators_clocks_and_retained_cohort(sources):
    before = deepcopy(sources)
    report = _build(sources)
    assert sources == before
    assert report["comparison_performed"] and report["new_performance_families"] == 1
    assert report["new_labels_calculated"] and report["new_endpoint_ratios_derived"]
    assert not report["upstream_statistics_recalculated"]
    assert not report["hindsight_selected_original_entry_is_executable"]
    for policy, variant in report["variants"].items():
        parent = sources[2]["variants"][policy]
        for key in attribution.COHORT_KEYS:
            assert variant[key] == parent[key]
        assert variant["by_arm"]["A"] == parent["by_arm"]["basic"]
        assert variant["by_arm"]["D"] == parent["by_arm"]["selected"]
        for row in variant["selected_price_attribution"]:
            original, delayed = row["original_CT_label"], row["original_DU_label"]
            e0, x0, e1 = (Fraction(Decimal(value)) for value in
                (original["entry_open"], original["exit_open"], delayed["entry_open"]))
            assert row["C_gross"] == x0 / e1 - 1
            assert row["C_gross"] != row["B_gross"] - (e1 / e0 - 1)
            assert row["C_exit_at"] - row["C_entry_at"] == 25 * attribution.cc.MINUTE
        means = {arm: value["gross_mean"] for arm, value in variant["by_arm"].items()}
        contrast = variant["comparison"]
        assert contrast["selection_B_minus_A"]["gross_mean_difference"] == means["B"] - means["A"]
        assert contrast["waiting_C_minus_B"]["gross_mean_difference"] == means["C"] - means["B"]
        assert contrast["exit_shift_D_minus_C"]["gross_mean_difference"] == means["D"] - means["C"]
        assert contrast["same_name_D_minus_B"]["gross_mean_difference"] == means["D"] - means["B"]
        assert contrast["point_decomposition"]["components_sum"] == means["D"] - means["A"]
        assert contrast["published_D_minus_A"] == parent["comparison"]["selected_minus_basic"]
        assert contrast["selection_B_minus_A"]["uncertainty"]["same_blocks_for_both_arms"]
        for name in ("waiting_C_minus_B", "exit_shift_D_minus_C", "same_name_D_minus_B"):
            uncertainty = contrast[name]["uncertainty"]
            assert uncertainty["paired_coordinate_differences"]
            assert uncertainty["reference_sessions"] == len(report["reference_sessions"])
            assert uncertainty["bootstrap_valid_draws"] + uncertainty["bootstrap_zero_label_draws"] == 2000
        assert variant["by_arm"]["B"]["frequency"]["zero_event_weeks"] == 1
        assert all(variant["by_arm"][arm]["by_week"] == variant["by_arm"]["D"]["by_week"] for arm in ("B", "C"))


def test_four_new_plans_before_new_means_and_no_parent_replay(monkeypatch, sources):
    first, second = attribution.project_ct(sources[0]), attribution.project_du(sources[1])
    original_plan, original_mean = attribution.dm._dispersion, attribution._performance
    observed = []
    def plan(*args):
        observed.append("dispersion")
        return original_plan(*args)
    def performance(*args):
        assert observed.count("dispersion") >= 4
        observed.append("mean")
        return original_mean(*args)
    monkeypatch.setattr(attribution.dm, "_dispersion", plan)
    monkeypatch.setattr(attribution, "_performance", performance)
    monkeypatch.setattr(attribution.dx, "build_comparison", lambda *_: pytest.fail("parent pipeline replay"))
    result = attribution.build_attribution(first, second, sources[2])
    assert result["comparison_performed"]
    assert observed[:4] == ["dispersion"] * 4
    assert observed.count("dispersion") == 10  # Four own plans, six paired contrasts.
    assert observed.count("mean") == 4


@pytest.mark.parametrize("mde", [None, True, -1, math.inf, math.nan, 33.00001])
def test_failed_fresh_sizing_retains_calibration_and_opaque_receipts(monkeypatch, sources, mde):
    original = attribution.dm._dispersion
    def plan(*args):
        result = original(*args)
        result["mde_bps"] = mde
        return result
    monkeypatch.setattr(attribution.dm, "_dispersion", plan)
    monkeypatch.setattr(attribution, "_performance", lambda *_: pytest.fail("premature mean"))
    report = _build(sources)
    assert report["calibration_only"] and not report["comparison_performed"]
    assert report["new_performance_families"] == 0
    for variant in report["variants"].values():
        assert variant["comparison"] is None
        assert "gross_mean" not in variant["by_arm"]["B"]
        assert "gross_mean" not in variant["by_arm"]["C"]
        assert "gross_mean" in variant["by_arm"]["A"]
        assert variant["published_DX_total"]


@pytest.mark.parametrize("change", [
    lambda p: p.update(study_id="wrong"),
    lambda p: p.update(specification_sha256="0" * 64),
    lambda p: p.update(comparison_performed=1),
    lambda p: p["source_identity"]["CT"].update(report_sha256="0" * 64),
    lambda p: p["variants"]["D+1"]["target_states"][0].update(in_common_scope=True),
    lambda p: p["variants"]["D+1"]["cohort_flow"].update(targets=1),
    lambda p: p["variants"]["D+1"]["selected_coordinates"].reverse(),
    lambda p: p["variants"]["D+1"]["by_arm"]["basic"]["own_label_dispersion"].update(mde_bps=34),
    lambda p: p["variants"]["D+1"]["by_arm"]["selected"].update(gross_mean=Fraction(1)),
    lambda p: p["variants"]["D+1"]["comparison"]["selected_minus_basic"].update(gross_mean_difference=Fraction(1)),
])
def test_changed_dx_identity_mask_or_receipts_refused(sources, change):
    change(sources[2])
    with pytest.raises(attribution.AcceptanceTimingAttributionRefusal):
        _build(sources)


def test_changed_endpoint_projection_refused(sources):
    first, second = attribution.project_ct(sources[0]), attribution.project_du(sources[1])
    selected = next(row for row in second["variants"]["D+1"]["targets"] if row["status"] == "signal")
    selected["label"]["exit_at"] += attribution.cc.MINUTE
    with pytest.raises(attribution.AcceptanceTimingAttributionRefusal):
        attribution.build_attribution(first, second, sources[2])


def test_paired_difference_zero_dispersion_preserves_dependence():
    records = [{"session": day, "symbol": "KRX:000001", "label": Fraction(1, 1000)}
        for day in ("2025-09-04", "2025-09-05", "2025-09-08", "2025-09-09")]
    result = attribution._paired_uncertainty(records, [row["session"] for row in records], lambda: None)
    assert result["naive_se"] == 0 and result["design_se"] < 1e-15
    assert result["paired_coordinate_differences"]
