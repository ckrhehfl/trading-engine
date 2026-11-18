"""Invented-only EP cohort, weighting, serialization and matched-fit checks."""

from dataclasses import replace
from datetime import date, timedelta
from fractions import Fraction
from importlib.util import find_spec
import copy
import json
import math
import unittest
from unittest.mock import patch

from research import intraday_event_factorized_model as ep
from research import intraday_event_models as common
from research import intraday_event_sequence_model as sequence


def query(index, *, session=None, state="WAITING", family="C_FIRSTPULLBACK", episode=None,
          cluster=None, symbol="KRX:000001", value=1, peer=(0, 0, 1)):
    session = session or (date(2026, 1, 1) + timedelta(days=index)).isoformat()
    episode = episode or f"episode:{index}:{session}"
    age = Fraction(1, 3) if state == "HELD_10" else Fraction(2, 3)
    return common.Query(f"key:{index}", session, symbol, episode, cluster or f"cluster:{index}:{session}",
                        70, family, state, tuple((value,) * 13 for _ in range(64)), (value,) * 16,
                        peer, (age, 1 - age, Fraction(1, 2)) if state in common.HELD_STATES else None,
                        episode if state in common.HELD_STATES else None)


def labels(row, *, path=0, fill=0, immediate=None, wait=None, remaining=None):
    if row.state in common.HELD_STATES:
        path, fill, immediate, wait = None, None, None, None
    return common.TrainingLabels(row.key, path, fill, immediate, wait, row.session, remaining)


def fills(row, immediate=None, wait3=None):
    return ep.ActionFills(row.key, immediate, wait3,
                          row.session if immediate is not None else None,
                          row.session if wait3 is not None else None)


def fit(arm, rows, targets, action_fills, **kwargs):
    return ep.fit(arm, rows, targets, action_fills, "2026-06-01",
                  support_binding=common.support_seal(rows), **kwargs)


def plan(arm, rows, targets, action_fills):
    return ep._head_plan(arm, rows, {t.key: t for t in targets}, {f.key: f for f in action_fills})


class ContractTests(unittest.TestCase):
    def test_binary_action_identity_and_independent_maturity_fail_closed(self):
        rows = (query(0), query(1))
        targets = tuple(labels(row) for row in rows)
        valid = tuple(fills(row, 0, 0) for row in rows)
        with patch.object(sequence, "_runtime", side_effect=AssertionError("no backend admission")):
            for invalid in (True, -1, 2, "1"):
                with self.assertRaisesRegex(ValueError, "binary"):
                    ep.ActionFills(rows[0].key, 0, invalid, rows[0].session, rows[0].session)
            with self.assertRaisesRegex(ValueError, "action-specific maturity"):
                replace(valid[0], wait3=1, wait3_matured_session=None)
            for bad in (replace(valid[0], wait3_matured_session="2026-06-01"),
                        replace(valid[0], wait3_matured_session="2025-01-01")):
                with self.assertRaisesRegex(ValueError, "matured"):
                    fit("direct", rows, targets, (bad, valid[1]))
            with self.assertRaisesRegex(ValueError, "key roster"):
                fit("direct", rows, targets, (valid[0], valid[0]))
            with self.assertRaisesRegex(ValueError, "disagrees"):
                fit("direct", rows, targets, (replace(valid[0], immediate=1), valid[1]))

    def test_each_action_no_fill_nonzero_rejected_even_outside_joint_support(self):
        rows = (query(0), query(1))
        for action in ("immediate", "wait"):
            targets = (labels(rows[0], **{action: Fraction(1, 10**300)}), labels(rows[1]))
            with self.assertRaisesRegex(ValueError, "no-fill.*nonzero"):
                fit("factorized", rows, targets, tuple(fills(row, 0, 0) for row in rows))

    def test_joint_unknowns_never_supply_zero_and_no_fill_zero_has_no_conditional_target(self):
        rows = tuple(query(i) for i in range(4))
        targets = (labels(rows[0], immediate=0, wait=0), labels(rows[1], fill=1, immediate=2, wait=None),
                   labels(rows[2], fill=1, immediate=2, wait=3), labels(rows[3], fill=1, immediate=0, wait=0))
        actions = (fills(rows[0], 0, 0), fills(rows[1], 1, 1), fills(rows[2], 1, None), fills(rows[3], 1, 1))
        heads, bases, weights, values, support = plan("factorized", rows, targets, actions)
        for head in ep.ACTION_HEADS:
            self.assertEqual(bases[head], [0.5, 0, 0, 0.5])
        self.assertEqual(support["joint_known_rows"], 2)
        self.assertEqual(heads["net_immediate"]["known_rows"], 1)
        self.assertEqual(heads["net_immediate"]["known_zero_rows"], 1)
        self.assertEqual(values["net_immediate"], [None, None, None, 0])
        self.assertNotIn("net_immediate", weights)
        self.assertEqual(support["zero_decomposition"]["immediate"]["filled_zero_rows"], 1)
        self.assertEqual(support["zero_decomposition"]["wait3"]["unknown_fill_rows"], 1)
        self.assertEqual(support["zero_decomposition"]["immediate"]["fill_known_cash_unknown_rows"], 0)
        self.assertEqual(support["zero_decomposition"]["wait3"]["fill_known_cash_unknown_rows"], 1)
        self.assertEqual(support["zero_decomposition"]["immediate"]["joint_excluded_fill_known_rows"], 2)
        self.assertEqual(support["zero_decomposition"]["wait3"]["joint_excluded_fill_known_rows"], 1)
        changed_targets = (targets[0], replace(targets[1], net_immediate=None, net_wait3=2), *targets[2:])
        _, changed_bases, _, _, changed_support = plan("factorized", rows, changed_targets, actions)
        self.assertEqual(changed_bases, bases)
        self.assertEqual(changed_support["zero_decomposition"]["immediate"]["fill_known_cash_unknown_rows"], 1)
        self.assertEqual(changed_support["zero_decomposition"]["wait3"]["fill_known_cash_unknown_rows"], 0)

    def test_conditional_weights_mask_original_hierarchy_without_rebalancing(self):
        a = query(0, episode="a", cluster="one", family="A_DOWNFAIL")
        a2 = replace(a, key="a:second", decision_offset=73)
        b = query(1, session=a.session, episode="b", cluster="one", family="B_ORL30")
        c = query(2, session=a.session, episode="c", cluster="two", symbol="KRX:000002")
        d = query(3, session="2026-01-02", episode="d", cluster="three")
        rows = (a, a2, b, c, d)
        actions = tuple(fills(row, int(i in (0, 4)), int(i in (2, 4))) for i, row in enumerate(rows))
        targets = tuple(labels(row, path=i % 3, fill=action.immediate,
                               immediate=0 if i != 4 else 9, wait=6 if i == 2 else 0)
                        for i, (row, action) in enumerate(zip(rows, actions)))
        direct = plan("direct", rows, targets, actions)
        factor = plan("factorized", rows, targets, actions)
        self.assertEqual(direct[1], factor[1])
        self.assertEqual(ep._proposal(direct[1]), ep._proposal(factor[1]))
        self.assertEqual(factor[1]["fill"], [1/16, 1/16, 1/8, 1/4, 1/2])
        self.assertEqual(factor[2]["net_immediate"], [1/9, 0, 0, 0, 8/9])
        self.assertEqual(factor[2]["net_wait3"], [0, 0, 1/5, 0, 4/5])
        self.assertAlmostEqual(factor[0]["net_immediate"]["target_normalization"]["mean"], 8)
        self.assertEqual(factor[0]["net_immediate"]["known_zero_rows"], 1)
        self.assertEqual(factor[2]["path_class"], direct[2]["path_class"])

    def test_original_auxiliary_heads_and_all_families_remain(self):
        rows = tuple(query(i, state="HELD_10" if i >= 3 else "WAITING", family=common.FAMILIES[i % 3])
                     for i in range(6))
        targets = tuple(labels(row, path=i % 3, remaining=i / 100 if i >= 3 else None) for i, row in enumerate(rows))
        actions = tuple(fills(row) for row in rows)
        heads, _, weights, _, _ = plan("factorized", rows, targets, actions)
        old_heads, old_weights = sequence._head_plan(rows, {t.key: t for t in targets})
        for head in ("path_class", "remaining_gross"):
            self.assertEqual(weights[head], old_weights[head])
            self.assertEqual(heads[head]["known_rows"], old_heads[head]["known_rows"])
        self.assertEqual(heads["remaining_gross"]["target_normalization"], old_heads["remaining_gross"]["target_normalization"])

    def test_no_class_constant_not_fitted_and_inference_requires_no_targets(self):
        rows = (query(0), query(1))
        targets = tuple(labels(row, fill=1) for row in rows)
        actions = tuple(fills(row, 1, 0) for row in rows)
        with patch.object(sequence, "_runtime", side_effect=AssertionError("no native runtime")):
            artifact = fit("factorized", rows, targets, actions)
            result = ep.predict(artifact, (query(9, session="2026-06-01"),))[0]
            self.assertEqual(artifact["constant_head_count"], 1)
            self.assertEqual(artifact["native_joint_fit_count"], 0)
            self.assertEqual(set(result["predictions"]), set(common.HEADS))
            self.assertIsNone(result["predictions"]["fill"])
            self.assertIsNone(result["predictions"]["net_immediate"])
            with self.assertRaisesRegex(ValueError, "target-free"):
                ep.predict(artifact, targets)
            with self.assertRaisesRegex(ValueError, "overlaps"):
                ep.predict(artifact, rows)

    def test_unsupported_and_constant_artifact_tampering(self):
        rows = (query(0), query(1))
        artifact = fit("factorized", rows, tuple(labels(row) for row in rows), tuple(fills(row) for row in rows))
        out = (query(9, session="2026-06-01"),)
        for field, value in (("arm", "new"), ("schema", "EL-sequence-model-v1")):
            bad = copy.deepcopy(artifact)
            bad[field] = value
            with self.assertRaises(ValueError):
                ep.predict(bad, out)
        bad = copy.deepcopy(artifact)
        bad["heads"]["fill_wait3"]["status"] = "fabricated"
        with self.assertRaisesRegex(ValueError, "Unknown"):
            ep.predict(bad, out)
        bad = copy.deepcopy(artifact)
        bad["parameters"]["updates"] = 1
        with self.assertRaisesRegex(ValueError, "contract"):
            ep.predict(bad, out)
        bad = copy.deepcopy(artifact)
        bad["state"] = "x" * (sequence.MAX_MODEL_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "budget"):
            ep.predict(bad, out)
        bad = copy.deepcopy(artifact)
        bad["arm"] = "direct"
        with self.assertRaisesRegex(ValueError, "conditional"):
            ep.predict(bad, out)

    def test_single_class_fills_are_constants_not_native_fits(self):
        rows = (query(0), query(1))
        with patch.object(sequence, "_runtime", side_effect=AssertionError("no native runtime")):
            artifact = fit("factorized", rows, tuple(labels(row, immediate=0, wait=0) for row in rows),
                           tuple(fills(row, 0, 0) for row in rows))
            self.assertEqual(artifact["constant_head_count"], 3)
            self.assertEqual(artifact["fit_head_count"], 0)
            self.assertEqual(artifact["fit_attempt_head_count"], 0)
            prediction = ep.predict(artifact, (query(9, session="2026-06-01"),))[0]
            self.assertEqual(prediction["predictions"]["fill"], 0.0)
            self.assertEqual(prediction["components"]["fill_wait3"], 0.0)
            self.assertIsNone(prediction["predictions"]["net_immediate"])


@unittest.skipUnless(find_spec("torch") is not None, "admitted CPU torch absent; no installation")
class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        if torch.__version__ != sequence.TORCH_VERSION or torch.version.cuda is not None:
            raise unittest.SkipTest("registered torch 2.7.0+cpu required")
        cls.rows = tuple(query(i, value=i / 8, family=common.FAMILIES[i % 3],
                               state="HELD_10" if i == 6 else "HELD_20" if i == 7 else "WAITING")
                         for i in range(8))
        immediate, wait = (0, 1, 1, 0, 1, 1), (1, 0, 1, 1, 0, 1)
        cls.actions = tuple(fills(row, immediate[i], wait[i]) if i < 6 else fills(row) for i, row in enumerate(cls.rows))
        cls.targets = tuple(labels(row, path=i % 3, fill=immediate[i],
                                   immediate=(i - 1) / 100 if immediate[i] else 0,
                                   wait=(i - 2) / 100 if wait[i] else 0) if i < 6 else
                            labels(row, remaining=(i - 5) / 100) for i, row in enumerate(cls.rows))
        cls.artifacts = {arm: fit(arm, cls.rows, cls.targets, cls.actions) for arm in ep.ARMS}

    def test_both_fixed_200_step_fits_share_init_proposal_samples_and_normalizer(self):
        direct, factor = (self.artifacts[arm] for arm in ep.ARMS)
        self.assertEqual(direct["normalization"], factor["normalization"])
        self.assertEqual(direct["training_key_hashes"], factor["training_key_hashes"])
        for name in ("initial_state_sha256", "proposal_sha256", "sampled_indices_sha256", "sample_sha256"):
            self.assertEqual(direct["training_diagnostics"][name], factor["training_diagnostics"][name])
            self.assertIsNotNone(direct["training_diagnostics"][name])
        for artifact in self.artifacts.values():
            self.assertEqual(artifact["native_updates"], 200)
            self.assertEqual(artifact["native_joint_fit_count"], 1)
            self.assertEqual(artifact["native_fit_head_count"], 6)
            self.assertEqual(artifact["constant_head_count"], 0)
            for info in artifact["training_diagnostics"]["heads"].values():
                self.assertEqual(info["status"], "recorded")
                self.assertTrue(math.isfinite(info["initial_loss"]) and math.isfinite(info["final_loss"]))
        self.assertEqual(factor["heads"]["net_immediate"]["known_zero_rows"], 1)
        self.assertNotEqual(direct["heads"]["net_immediate"]["target_normalization"],
                            factor["heads"]["net_immediate"]["target_normalization"])

    def test_json_roundtrip_and_exact_product_composition_without_labels(self):
        queries = (query(20, session="2026-06-01"), query(21, session="2026-06-02", state="HELD_20"))
        for arm, artifact in self.artifacts.items():
            saved = json.dumps(artifact, sort_keys=True)
            result = ep.predict(artifact, queries)
            self.assertEqual(result, ep.predict(json.loads(saved), queries))
            self.assertEqual(saved, json.dumps(artifact, sort_keys=True))
            self.assertEqual(set(result[0]["predictions"]), set(common.HEADS))
            self.assertTrue(0 <= result[0]["components"]["fill_wait3"] <= 1)
            self.assertTrue(all(result[1]["predictions"][head] is None for head in common.HEADS[:4]))
            self.assertIsNone(result[1]["components"]["fill_wait3"])
            if arm == "factorized":
                values, parts = result[0]["predictions"], result[0]["components"]
                self.assertEqual(values["net_immediate"], values["fill"] * parts["conditional_net_immediate"])
                self.assertEqual(values["net_wait3"], parts["fill_wait3"] * parts["conditional_net_wait3"])

    def test_numeric_parameter_tampering_rejected(self):
        artifact = self.artifacts["factorized"]
        for raw in (float("nan"), 1e300):
            bad = copy.deepcopy(artifact)
            name = next(iter(bad["state"]))
            bad["state"][name]["values"][0] = raw
            with self.assertRaises(ValueError):
                ep.predict(bad, (query(20, session="2026-06-01"),))
        bad = copy.deepcopy(artifact)
        bad["state"]["heads.fill_wait3.weight"]["shape"] = [3, 16]
        with self.assertRaisesRegex(ValueError, "shape"):
            ep.predict(bad, (query(20, session="2026-06-01"),))


if __name__ == "__main__":
    unittest.main()
