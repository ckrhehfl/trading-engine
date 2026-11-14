"""Fixed EL comparison and its private predecessor identity.

Validation is pure. The launcher checks the dataset release against Git and
authenticates each referenced audit before opening its admitted data.
"""

from dataclasses import dataclass
from hashlib import sha256
from itertools import product
import json
import re

from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_dataset_run as dataset
from research import intraday_event_evaluation as evaluation
from research import intraday_event_execution as execution
from research import intraday_event_models as models
from research import intraday_event_sequence_model as sequence
from research.intraday_event_model_workflow import FOLDS, KINDS, SCOPES


UNIT = "EL-MODEL-1"
SPEC = "configs/research/discovery/intraday-event-model-v1.json"
DATASET_REGISTRATION_SHA256 = "62706bb39acd9a5ba542cab7830e05234c0d66c8a2b46ef90ccd35efc3478fdb"
ROSTER = tuple(product(KINDS, SCOPES, range(len(FOLDS))))
QUALIFICATION_ROSTER = tuple(product(KINDS, ("full_chart", "common_chart_peer"), (len(FOLDS) - 1,)))


def unit_id(kind: str, scope: str, fold: int) -> str:
    wire.require(type(fold) is int and (kind, scope, fold) in ROSTER,
                 "Unregistered EL comparison unit")
    return f"{UNIT}-{kind}-{scope}-f{fold}"


def validate_spec(spec: dict) -> None:
    """Require the full registered roster, assumptions and discovery scope."""
    expected = {
        "id": UNIT,
        "execution_host": "local_wsl_only",
        "resource_profile": "ej-local-cpu1800-as4096-v1",
        "window": dataset.WINDOW,
        "orders_enabled": False,
        "confirmation": False,
        "new_data_acquisition": False,
        "promotion_allowed": False,
        "cost_bps": int(execution.COST * 10_000),
        "dataset": {
            "unit": dataset.UNIT,
            "registration": dataset.SPEC,
            "registration_sha256": DATASET_REGISTRATION_SHA256,
            "geometry": {"sessions": FOLDS[-1][-1], "per_scenario": 5140, "union": 5158},
            "session_shards": [list(bounds) for bounds in dataset.SHARDS],
        },
        "qualification": {"invented_only": True, "full_source_geometry": True,
                          "independent_shard_audits": len(dataset.SHARDS), "entry_and_holding_heads_required": True,
                          "fixed_policy_witness": True, "roster": [list(unit) for unit in QUALIFICATION_ROSTER]},
        "technical_recovery": {"max_automatic_actual_retries": 1, "controlled_pre_fit_timeout_only": True},
        "kinds": list(KINDS),
        "feature_scopes": {
            "full_chart": {"cohort": "own_chart_supported", "mode": "chart"},
            "common_chart": {"cohort": "own_chart_and_peer_supported", "mode": "chart"},
            "common_chart_peer": {"cohort": "own_chart_and_peer_supported", "mode": "chart_peer"},
        },
        "folds": [list(bounds) for bounds in FOLDS],
        "maximum_fitted_artifacts": len(ROSTER),
        "model_selection": "fixed_without_evaluation_targets",
        "families": list(models.FAMILIES),
        "heads": list(models.HEADS),
        "task_semantics": models.TASK_SEMANTICS,
        "learners": {
            "empirical": {"prior_strength": models.PRIOR_STRENGTH},
            "lightgbm": {"parameters": models.LGB_PARAMETERS, "rounds": models.ROUNDS},
            "tcn": {"parameters": sequence.PARAMETERS, "diagnostics": sequence.DIAGNOSTICS},
        },
        "evaluation": evaluation.CONTRACT,
        "entry_policies": list(execution.ENTRY_POLICIES),
        "management_policies": list(execution.MANAGEMENT_POLICIES),
    }
    wire.require(type(spec) is dict and wire.encode(spec) == wire.encode(expected),
                 "EL model registration differs")


@dataclass(frozen=True)
class DatasetBinding:
    _release_bytes: bytes
    qualification_audit_sha256: str
    shard_audit_sha256: tuple[str, ...]
    inputs_sha256: str

    @property
    def release(self) -> dict:
        """Return a copy so the admitted identity cannot be mutated in place."""
        return json.loads(self._release_bytes)


def validate_dataset_binding(binding: dict, expected_sha256: str) -> DatasetBinding:
    """Bind the recovery release and nine audit digests in fixed shard order."""
    wire.require(type(binding) is dict and set(binding) == {
        "dataset_release", "qualification_audit_sha256", "shard_audit_sha256"
    } and wire.valid_sha(expected_sha256) and sha256(wire.encode(binding)).hexdigest() == expected_sha256,
        "EL private dataset binding pin differs")

    release = binding["dataset_release"]
    wire.require(type(release) is dict and set(release) == {
        "merge", "reviewed", "tree", "registration_sha256", "parents_sha256",
        "sources", "runtime_sha256", "support_audit_sha256", "original_failed_attempt"
    } and all(type(release[key]) is str and re.fullmatch("[0-9a-f]{40}", release[key])
              for key in ("merge", "reviewed", "tree"))
        and release["merge"] != release["reviewed"]
        and release["registration_sha256"] == DATASET_REGISTRATION_SHA256
        and release["parents_sha256"] == dataset.ORIGINAL_SHA256
        and all(wire.valid_sha(release[key]) for key in ("runtime_sha256", "support_audit_sha256")),
        "EL dataset release identity differs")
    sources = release["sources"]
    wire.require(type(sources) is dict and {dataset.OWN, "python/research/intraday_event_dataset.py"} <= sources.keys()
                 and all(type(path) is str and re.fullmatch(r"python/research/(?:[A-Za-z_0-9]+/)*[A-Za-z_0-9]+\.py", path)
                         and wire.valid_sha(pin) for path, pin in sources.items()),
                 "EL dataset source closure differs")

    failed = release["original_failed_attempt"]
    wire.require(type(failed) is dict and set(failed) == {"marker_sha256", "start", "end", "run_id", "terminal_sha256"}
                 and all(wire.valid_sha(failed[key]) for key in ("marker_sha256", "terminal_sha256"))
                 and type(failed["start"]) is int and type(failed["end"]) is int
                 and 0 <= failed["start"] < failed["end"] and failed["end"] - failed["start"] < dataset.MIB
                 and type(failed["run_id"]) is str and bool(failed["run_id"]),
                 "EL dataset recovery lineage differs")

    qualification, shards = binding["qualification_audit_sha256"], binding["shard_audit_sha256"]
    wire.require(wire.valid_sha(qualification) and type(shards) is list and len(shards) == len(dataset.SHARDS)
                 and all(wire.valid_sha(pin) for pin in shards)
                 and len({qualification, *shards}) == len(dataset.SHARDS) + 1,
                 "EL dataset requires one qualification and nine ordered independent shard audit pins")
    inputs = {"qualification_audit_sha256": qualification, "shard_audit_sha256": shards}
    return DatasetBinding(wire.encode(release), qualification, tuple(shards), sha256(wire.encode(inputs)).hexdigest())
