"""Synthetic-only TCN checks; stdlib unittest also runs without pytest/NumPy.

Runtime tests explicitly skip when the separately admitted CPU torch is absent.
The guarded old CPU environment can run this file through unittest discovery.
"""

from dataclasses import replace
from datetime import date, timedelta
from fractions import Fraction
from importlib.util import find_spec
import copy
import json
import math
import unittest
from unittest.mock import patch

from research import intraday_event_models as common
from research import intraday_event_sequence_model as sequence


def query(index, *, session=None, value=1, family="A_DOWNFAIL", state="WAITING",
          peer=None, episode=None, cluster=None, symbol="KRX:000001", offset=70):
    day = session or (date(2026, 1, 1) + timedelta(days=index)).isoformat()
    position = None
    if state in common.HELD_STATES:
        age = Fraction(1, 3) if state == "HELD_10" else Fraction(2, 3)
        position = (age, 1 - age, Fraction(1, 2))
    return common.Query(f"key:{index}:{day}:{offset}", day, symbol,
                        episode or f"episode:{index}:{day}", cluster or f"cluster:{index}:{day}",
                        offset, family, state, tuple((value,) * 13 for _ in range(64)),
                        (value,) * 16, peer, position)


def label(row, *, path=2, fill=0, immediate=None, wait=None, remaining=None, maturity=None):
    if row.state in common.HELD_STATES:
        path, fill, immediate, wait = None, None, None, None
    return common.TrainingLabels(row.key, path, fill, immediate, wait,
                                 maturity or row.session, remaining)


def fit(rows, labels, mode="chart"):
    return sequence.fit("tcn", rows, labels, "2026-06-01", mode=mode,
                        support_binding=common.support_seal(rows))


class ContractTests(unittest.TestCase):
    def test_constant_heads_and_unknown_value_heads_need_no_native_runtime(self):
        rows = (query(0), query(1))
        with patch.object(sequence, "_runtime", side_effect=AssertionError("native import forbidden")):
            artifact = fit(rows, tuple(label(row) for row in rows))
            result = sequence.predict(artifact, (query(9, session="2026-06-01"),))[0]
        self.assertEqual(artifact["constant_head_count"], 2)
        self.assertEqual(artifact["unsupported_head_count"], 3)
        self.assertEqual(artifact["native_joint_fit_count"], 0)
        self.assertEqual(result["predictions"]["path_class"], [0.0, 0.0, 1.0])
        self.assertEqual(result["predictions"]["fill"], 0.0)
        self.assertIsNone(result["predictions"]["net_wait3"])
        self.assertEqual(result["head_status"]["remaining_gross"], "not_applicable_to_state")

    def test_known_zero_is_not_unknown_and_q_heads_share_support(self):
        rows = (query(0), query(1))
        labels = (label(rows[0], immediate=0, wait=0), label(rows[1], immediate=None))
        artifact = fit(rows, labels)
        immediate = artifact["heads"]["net_immediate"]
        self.assertEqual(immediate["known_rows"], 1)
        self.assertEqual(immediate["known_zero_rows"], 1)
        self.assertEqual(immediate["excluded_unknown_rows"], 1)
        self.assertEqual(immediate["status"], "unsupported")
        self.assertEqual(artifact["heads"]["net_wait3"]["known_zero_rows"], 1)
        self.assertEqual(artifact["heads"]["net_wait3"]["status"], "unsupported")

    def test_training_maturity_seal_and_target_separation_before_native_import(self):
        rows = (query(0), query(1))
        labels = tuple(label(row) for row in rows)
        with patch.object(sequence, "_runtime", side_effect=AssertionError("native import forbidden")):
            with self.assertRaisesRegex(ValueError, "seal"):
                sequence.fit("tcn", rows, labels, "2026-06-01")
            with self.assertRaisesRegex(ValueError, "matured"):
                fit(rows, (label(rows[0], maturity="2026-06-01"), labels[1]))
            with self.assertRaisesRegex(ValueError, "overlaps"):
                sequence.fit("tcn", rows, labels, rows[0].session, support_binding=common.support_seal(rows))
            artifact = fit(rows, labels)
            with self.assertRaisesRegex(ValueError, "target-free"):
                sequence.predict(artifact, labels)
            with self.assertRaisesRegex(ValueError, "overlaps"):
                sequence.predict(artifact, rows)
            with self.assertRaisesRegex(ValueError, "overlaps"):
                sequence.predict(artifact, (replace(rows[0], session="2026-06-01"),))

    def test_date_cluster_episode_frame_weights_recompute_per_head(self):
        a = query(0, episode="a", cluster="one")
        a2 = replace(a, key="a-second", decision_offset=73)
        b = query(1, session=a.session, episode="b", cluster="one", family="B_ORL30")
        c = query(2, session=a.session, episode="c", cluster="two", symbol="KRX:000002")
        d = query(3, session="2026-01-02", episode="d", cluster="three")
        rows = (a, a2, b, c, d)
        labels = {row.key: label(row, immediate=10 if row is not d else 0, wait=0) for row in rows}
        heads, weights = sequence._head_plan(rows, labels)
        self.assertEqual(weights["net_immediate"], [1/16, 1/16, 1/8, 1/4, 1/2])
        self.assertEqual(heads["net_immediate"]["target_normalization"], {"mean": 5.0, "std": 5.0})
        self.assertEqual(heads["net_immediate"]["known_zero_rows"], 1)
        self.assertEqual(heads["net_immediate"]["known_clusters"], 3)
        self.assertTrue(heads["net_immediate"]["low_support"])

    def test_train_only_channel_moments_and_explicit_missing_masks(self):
        rows = (query(0, value=None), query(1, value=0), query(2, value=2))
        artifact = fit(rows, tuple(label(row) for row in rows))
        norm = artifact["normalization"]
        self.assertAlmostEqual(norm["sequence"]["mean"][0], 1.0)
        self.assertAlmostEqual(norm["sequence"]["std"][0], 1.0)
        self.assertEqual(norm["sequence"]["finite_counts"][0], 128)
        self.assertEqual(norm["static"]["finite_counts"][0], 2)
        missing = sequence._standardize((None,) * 13, norm["sequence"])
        zero = sequence._standardize((0,) * 13, norm["sequence"])
        self.assertEqual(missing, [0.0] * 13 + [1.0] * 13)
        self.assertEqual(zero[13:], [0.0] * 13)
        saved = json.dumps(artifact, sort_keys=True)
        sequence.predict(artifact, (query(9, session="2026-06-01", value=1_000_000),))
        self.assertEqual(json.dumps(artifact, sort_keys=True), saved)

    def test_peer_common_roster_and_state_applicability(self):
        rows = (query(0, peer=(1, 2, 1)), query(1, peer=(2, 3, 0)))
        labels = tuple(label(row) for row in rows)
        chart, peer = fit(rows, labels), fit(rows, labels, "chart_peer")
        self.assertEqual(chart["common_support_sha256"], peer["common_support_sha256"])
        result = sequence.predict(peer, (query(9, session="2026-06-01"),))[0]
        self.assertEqual(result["query_status"], "unsupported_peer_context")
        self.assertTrue(all(value is None for value in result["predictions"].values()))
        held = sequence.predict(chart, (query(9, session="2026-06-01", state="HELD_10"),))[0]
        self.assertTrue(all(held["head_status"][head] == "not_applicable_to_state" for head in common.HEADS[:4]))
        with self.assertRaisesRegex(ValueError, "unsupported peer"):
            fit((replace(rows[0], peer=None), rows[1]), labels, "chart_peer")

    def test_malformed_artifacts_and_float_overflow_fail_closed(self):
        rows = (query(0), query(1))
        original = fit(rows, tuple(label(row) for row in rows))
        out = (query(9, session="2026-06-01"),)
        damaged = copy.deepcopy(original)
        damaged["heads"]["fill"]["status"] = "imagined"
        with self.assertRaisesRegex(ValueError, "Unknown head"):
            sequence.predict(damaged, out)
        damaged = copy.deepcopy(original)
        damaged["normalization"]["sequence"]["std"][0] = 0
        with self.assertRaisesRegex(ValueError, "normalization"):
            sequence.predict(damaged, out)
        damaged = copy.deepcopy(original)
        damaged["parameters"]["dilations"][0] = 99
        self.assertEqual(sequence.PARAMETERS["dilations"][0], 1)
        with self.assertRaisesRegex(ValueError, "contract"):
            sequence.predict(damaged, out)
        damaged = copy.deepcopy(original)
        damaged["feature_contract"]["task_semantics"]["entry_heads"].clear()
        self.assertEqual(len(common.TASK_SEMANTICS["entry_heads"]), 4)
        with self.assertRaisesRegex(ValueError, "contract"):
            sequence.predict(damaged, out)
        damaged = copy.deepcopy(original)
        damaged["state"] = "x" * (sequence.MAX_MODEL_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "byte budget"):
            sequence.predict(damaged, out)
        with self.assertRaisesRegex(ValueError, "overflow"):
            sequence._normalizer((query(2, value=-1e308), query(3, value=1e308)), "chart")

    def test_holding_labels_are_separate_and_keep_gross_target_units(self):
        rows = (query(0, state="HELD_10"), query(1, state="HELD_20"))
        labels = {rows[0].key: label(rows[0], remaining=Fraction(1, 100)),
                  rows[1].key: label(rows[1], remaining=Fraction(3, 100))}
        heads, weights = sequence._head_plan(rows, labels)
        self.assertEqual(set(weights), {"remaining_gross"})
        self.assertAlmostEqual(heads["remaining_gross"]["target_normalization"]["mean"], 0.02)
        self.assertAlmostEqual(heads["remaining_gross"]["target_normalization"]["std"], 0.01)
        self.assertTrue(all(heads[head]["status"] == "unsupported" for head in common.HEADS[:4]))
        wrong = replace(labels[rows[0].key], net_immediate=0)
        with self.assertRaises(ValueError):
            fit(rows, (wrong, labels[rows[1].key]))

    def test_q_pairing_keeps_raw_labels_and_recomputes_both_head_weights(self):
        rows = tuple(query(i) for i in range(4))
        labels = {rows[0].key: label(rows[0], immediate=0, wait=2),
                  rows[1].key: label(rows[1], immediate=2, wait=4),
                  rows[2].key: label(rows[2], immediate=999, wait=None),
                  rows[3].key: label(rows[3], immediate=None, wait=-999)}
        unchanged = dict(labels)
        heads, weights = sequence._head_plan(rows, labels)
        self.assertEqual(weights["net_immediate"], [0.5, 0.5, 0.0, 0.0])
        self.assertEqual(weights["net_immediate"], weights["net_wait3"])
        self.assertEqual(heads["net_immediate"]["target_normalization"], {"mean": 1.0, "std": 1.0})
        self.assertEqual(heads["net_wait3"]["target_normalization"], {"mean": 3.0, "std": 1.0})
        self.assertTrue(all(heads[head]["raw_known_rows"] == 3 for head in common.Q_HEADS))
        self.assertTrue(all(heads[head]["dropped_unpaired_q_rows"] == 1 for head in common.Q_HEADS))
        self.assertEqual(heads["path_class"]["known_rows"], 4)
        self.assertEqual(labels, unchanged)

    def test_diagnostic_selection_is_bounded_identity_only_and_missing_stays_none(self):
        rows = tuple(query(i, session=(date(2024, 1, 1) + timedelta(days=i)).isoformat(), state="HELD_10")
                     for i in range(515))
        labels = {row.key: label(row) for row in rows}
        heads, _ = sequence._head_plan(rows, labels)
        selected, baseline = sequence._diagnostic_plan(rows, labels, heads)
        excluded = sorted(set(range(len(rows))) - set(selected))
        for index in excluded[:2]:
            labels[rows[index].key] = label(rows[index], remaining=index)
        heads, _ = sequence._head_plan(rows, labels)
        selected_again, diagnostic = sequence._diagnostic_plan(rows, labels, heads)
        _, reversed_diagnostic = sequence._diagnostic_plan(tuple(reversed(rows)), labels, heads)
        self.assertEqual(selected, selected_again)
        self.assertEqual(diagnostic["sampled_rows"], 512)
        self.assertEqual(diagnostic["sample_identity_sha256"], reversed_diagnostic["sample_identity_sha256"])
        self.assertEqual(diagnostic["sample_sha256"], baseline["sample_sha256"])
        remaining = diagnostic["heads"]["remaining_gross"]
        self.assertEqual(remaining["status"], "no_known_sampled_rows")
        self.assertEqual(remaining["sampled_known_rows"], 0)
        self.assertIsNone(remaining["initial_loss"])
        self.assertIsNone(remaining["final_loss"])

    def test_no_native_fit_has_explicit_unavailable_diagnostics(self):
        rows = (query(0), query(1))
        artifact = fit(rows, tuple(label(row) for row in rows))
        diagnostics = artifact["training_diagnostics"]
        self.assertEqual(diagnostics["status"], "not_trained")
        self.assertEqual(diagnostics["completed_updates"], 0)
        self.assertEqual(diagnostics["loss_blocks"], [])
        self.assertTrue(all(info["initial_loss"] is None and info["final_loss"] is None
                            for info in diagnostics["heads"].values()))


@unittest.skipUnless(find_spec("torch") is not None, "admitted torch CPU runtime is absent; no install attempted")
class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        if torch.__version__ != sequence.TORCH_VERSION or torch.version.cuda is not None:
            raise unittest.SkipTest("registered torch 2.7.0+cpu runtime required")
        cls.rows = tuple(query(i, value=i - 1, state="HELD_10" if i == 4 else "HELD_20" if i == 5 else "WAITING")
                         for i in range(6))
        cls.labels = tuple(label(row, path=i % 3, fill=i % 2, immediate=None if i == 1 else i - 2,
                                wait=None if i == 2 else 3 - i,
                                remaining=Fraction(i - 3, 100) if i >= 4 else None)
                           for i, row in enumerate(cls.rows))
        cls.artifact = fit(cls.rows, cls.labels)

    def test_actual_fixed_update_fit_has_all_five_native_heads_and_no_numpy_bridge(self):
        artifact = self.artifact
        self.assertEqual(artifact["native_updates"], 200)
        self.assertEqual(artifact["native_joint_fit_count"], 1)
        self.assertEqual(artifact["native_fit_head_count"], 5)
        self.assertTrue(all(head["low_support"] for head in artifact["heads"].values()))
        self.assertEqual(artifact["heads"]["net_wait3"]["excluded_unknown_rows"], 4)
        self.assertEqual(artifact["heads"]["net_immediate"]["known_zero_rows"], 0)
        self.assertEqual(artifact["q_training_support"]["paired_known_rows"], 2)
        self.assertEqual(artifact["q_training_support"]["original_known_rows"], {"net_immediate": 3, "net_wait3": 3})
        self.assertTrue(all(head["updates_with_known_target"] > 0 for head in artifact["heads"].values()))

    def test_diagnostics_use_existing_updates_and_fixed_known_weighted_training_sample(self):
        diagnostics = self.artifact["training_diagnostics"]
        self.assertEqual(diagnostics["status"], "complete")
        self.assertEqual(diagnostics["completed_updates"], 200)
        self.assertEqual(diagnostics["contract"]["extra_optimizer_updates"], 0)
        self.assertEqual(diagnostics["sampled_rows"], 6)
        self.assertEqual([(block["first_update"], block["last_update"], block["updates"])
                          for block in diagnostics["loss_blocks"]], [(1, 50, 50), (51, 100, 50), (101, 150, 50), (151, 200, 50)])
        for head, info in diagnostics["heads"].items():
            self.assertEqual(info["status"], "recorded")
            self.assertTrue(math.isfinite(info["initial_loss"]) and math.isfinite(info["final_loss"]))
            self.assertGreaterEqual(info["initial_loss"], 0)
            self.assertGreaterEqual(info["final_loss"], 0)
            self.assertEqual(info["sampled_known_rows"], self.artifact["heads"][head]["known_rows"])
            self.assertEqual(info["sampled_known_clusters"], self.artifact["heads"][head]["known_clusters"])
            self.assertEqual(self.artifact["heads"][head]["fit_input_known_rows"], info["sampled_known_rows"])
        self.assertTrue(any(info["initial_loss"] != info["final_loss"] for info in diagnostics["heads"].values()))
        self.assertTrue(all(math.isfinite(block["mean_joint_loss"]) for block in diagnostics["loss_blocks"]))

    def test_json_roundtrip_predictions_state_masks_and_unchanged_normalizer(self):
        queries = (query(20, session="2026-06-01", value=0),
                   query(21, session="2026-06-01", value=1, state="HELD_20", symbol="KRX:000002"))
        saved = json.dumps(self.artifact, sort_keys=True)
        result = sequence.predict(self.artifact, queries)
        self.assertEqual(result, sequence.predict(json.loads(saved), queries))
        self.assertEqual(saved, json.dumps(self.artifact, sort_keys=True))
        self.assertAlmostEqual(sum(result[0]["predictions"]["path_class"]), 1, places=6)
        self.assertTrue(0 <= result[0]["predictions"]["fill"] <= 1)
        self.assertIsNone(result[0]["predictions"]["remaining_gross"])
        self.assertTrue(math.isfinite(result[1]["predictions"]["remaining_gross"]))
        self.assertTrue(all(result[1]["predictions"][head] is None for head in common.HEADS[:4]))

    def test_same_inputs_seed_and_budget_reproduce_exactly(self):
        self.assertEqual(self.artifact, fit(self.rows, self.labels))

    def test_causal_encoder_prefix_cannot_see_changed_later_steps(self):
        with sequence._runtime() as torch:
            model = sequence._network(torch, "chart")
            values = torch.randn(2, 26, 64)
            changed = values.clone()
            changed[:, :, 40:] += 100
            with torch.no_grad():
                original = model.encoder(values)
                future_changed = model.encoder(changed)
            self.assertTrue(torch.equal(original[:, :, :40], future_changed[:, :, :40]))
            self.assertFalse(torch.equal(original[:, :, 40:], future_changed[:, :, 40:]))

    def test_inference_preserves_cpu_rng_and_runtime_settings(self):
        import torch
        rng = torch.random.get_rng_state().clone()
        threads = torch.get_num_threads()
        deterministic = torch.are_deterministic_algorithms_enabled()
        sequence.predict(self.artifact, (query(20, session="2026-06-01"),))
        self.assertTrue(torch.equal(rng, torch.random.get_rng_state()))
        self.assertEqual(threads, torch.get_num_threads())
        self.assertEqual(deterministic, torch.are_deterministic_algorithms_enabled())

    def test_native_peer_auxiliary_dimensions_and_unsupported_peer_query(self):
        rows = tuple(replace(row, peer=(Fraction(1, 100), Fraction(2, 100), Fraction(1, 2))) for row in self.rows)
        artifact = fit(rows, self.labels, "chart_peer")
        self.assertEqual(artifact["native_fit_head_count"], 5)
        query_rows = (query(20, session="2026-06-01", peer=(0, 0, 1)),
                      query(21, session="2026-06-01", symbol="KRX:000002"))
        results = sequence.predict(artifact, query_rows)
        self.assertEqual(results[0]["query_status"], "supported_features")
        self.assertEqual(results[1]["query_status"], "unsupported_peer_context")
        self.assertTrue(all(value is None for value in results[1]["predictions"].values()))

    def test_parameter_payload_shape_and_nonfinite_values_are_rejected(self):
        for replacement in (float("nan"), 1e300):
            artifact = copy.deepcopy(self.artifact)
            name = next(iter(artifact["state"]))
            artifact["state"][name]["values"][0] = replacement
            with self.assertRaises(ValueError):
                sequence.predict(artifact, (query(20, session="2026-06-01"),))
        artifact = copy.deepcopy(self.artifact)
        name = next(iter(artifact["state"]))
        artifact["state"][name]["shape"] = [999]
        with self.assertRaisesRegex(ValueError, "shape"):
            sequence.predict(artifact, (query(20, session="2026-06-01"),))


if __name__ == "__main__":
    unittest.main()
