"""Model matrices and training targets from the separately sealed EF files."""
from __future__ import annotations

from array import array
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from hashlib import sha256

from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_campaign_wsl_transport import valid_sha
from research.intraday_chart_folds import EntrySupport, Key, ObservedLabel, TrainingRow, select_fold
from research.intraday_chart_snapshot import FeatureSnapshot, LabelSnapshot
from research.intraday_chart_tcn import SequenceBatch
from research.intraday_chart_tensors import (
    TABULAR_CHART_NAMES, TABULAR_PEER_NAMES, remove_temporal_order, tabular_chart, tabular_peer,
)
from research.intraday_ed_inputs import InputRefusal


TCN_ARMS = ("TCN_CHART", "TCN_CHART_PEER", "TCN_CHART_ORDER_REMOVED")
LGB_ARMS = ("LGB_CHART", "LGB_CHART_PEER")
MAX_ROWS = 5140 * 17
ADMISSION_PCT = 0.33


@dataclass(frozen=True)
class FitBinding:
    source_sha256: str
    runtime_sha256: str
    registration_sha256: str
    features_sha256: str
    labels_sha256: str
    scenario: str
    fold: int
    arm: str


def fit_binding(binding: FitBinding, arms: tuple[str, ...]) -> dict:
    if (type(binding) is not FitBinding or binding.arm not in arms or binding.scenario not in ("S1", "S2")
            or type(binding.fold) is not int or not 1 <= binding.fold <= 5
            or not all(valid_sha(value) for value in (binding.source_sha256, binding.runtime_sha256,
                binding.registration_sha256, binding.features_sha256, binding.labels_sha256))):
        raise InputRefusal("exact_ef_model_binding_required")
    return asdict(binding)


def snapshot_fold(features: FeatureSnapshot, labels: LabelSnapshot, scenario: str, fold_index: int):
    """Pass only the registered historical dates into the label connection."""
    if type(fold_index) is not int or not 1 <= fold_index <= 5:
        raise InputRefusal("registered_ef_fold_required")
    dates = features.metadata.sessions
    fold = fixed_calendar(dates).folds[fold_index - 1]
    past = {(row.session, row.symbol, row.offset): tuple(
        ObservedLabel(head.value, head.mature_at) if head is not None else None for head in row.heads)
        for row in labels.training_rows(fold.training_sessions, fold.first_test_opening)}
    support = (EntrySupport((day, symbol, offset), chart, peers) for day, symbol, offset, chart, peers in
               features.feature_support(scenario, (*fold.training_sessions, *fold.test_sessions)))
    return select_fold(dates, scenario, fold_index, support, past.__getitem__)


def _features(snapshot, key, scenario, *, with_peer):
    if scenario not in ("S1", "S2"):
        raise InputRefusal("registered_ef_scenario_required")
    row = snapshot.row(*key)
    if row.chart is None or (row.session, row.symbol, row.offset) != key:
        raise InputRefusal("selected_ef_chart_unavailable_or_changed")
    matches = [peer.tensor for peer in row.peers if peer.scenario == scenario]
    if len(matches) != 1 or (with_peer and matches[0] is None):
        raise InputRefusal("selected_ef_peer_support_unavailable_or_changed")
    return row.chart, matches[0] if with_peer else None


def lightgbm_matrix(snapshot: FeatureSnapshot, keys: tuple[Key, ...], scenario: str, arm: str):
    if arm not in LGB_ARMS or len(keys) > MAX_ROWS:
        raise InputRefusal("bounded_registered_ef_lightgbm_arm_required")
    with_peer = arm == "LGB_CHART_PEER"
    names = TABULAR_CHART_NAMES + (TABULAR_PEER_NAMES if with_peer else ())
    rows = []
    for key in keys:
        chart, peer = _features(snapshot, key, scenario, with_peer=with_peer)
        rows.append(tabular_chart(chart) + (tabular_peer(peer) if peer is not None else ()))
    return names, tuple(rows)


@dataclass(frozen=True)
class SequenceBuffers:
    rows: int
    minute: array
    coarse: array
    static: array

    def sha256(self) -> str:
        value = sha256()
        value.update(self.rows.to_bytes(8, "big"))
        for data in (self.minute, self.coarse, self.static):
            value.update(memoryview(data))
        return value.hexdigest()

    def torch_batch(self, torch) -> SequenceBatch:
        # PyTorch retains the owning CPU buffer. Do not resize it after this point.
        return SequenceBatch(torch.frombuffer(self.minute, dtype=torch.float32).reshape(self.rows, 60, 13),
                             torch.frombuffer(self.coarse, dtype=torch.float32).reshape(self.rows, 24, 6),
                             torch.frombuffer(self.static, dtype=torch.float32).reshape(self.rows, 6))


def sequence_buffers(snapshot: FeatureSnapshot, keys: tuple[Key, ...], scenario: str, arm: str) -> SequenceBuffers:
    if arm not in TCN_ARMS or not 0 < len(keys) <= MAX_ROWS:
        raise InputRefusal("bounded_registered_ef_sequence_arm_required")
    minute, coarse, static = (array("f") for _ in range(3))
    if minute.itemsize != 4:
        raise InputRefusal("native_float32_buffer_required")
    for key in keys:
        chart, peer = _features(snapshot, key, scenario, with_peer=arm == "TCN_CHART_PEER")
        if arm == "TCN_CHART_ORDER_REMOVED":
            chart = remove_temporal_order(chart, coordinate=":".join((scenario, key[0], key[1], str(key[2]))))
        for index, row in enumerate(chart.minute):
            minute.extend(row)
            minute.extend(peer.minute[index] if peer is not None else (0.0, 0.0, 0.0))
        for row in chart.coarse:
            coarse.extend(row)
        static.extend((*chart.static, peer.coverage if peer is not None else 0.0))
    return SequenceBuffers(len(keys), minute, coarse, static)


def training_targets(rows: Iterable[TrainingRow]):
    """Missing auxiliary payloads become zero only beside a separate false mask."""
    values, known = array("f"), bytearray()
    if values.itemsize != 4:
        raise InputRefusal("native_float32_target_buffer_required")
    for row in rows:
        if len(row.outcomes) != 6 or row.outcomes[0] is None:
            raise InputRefusal("six_mature_primary_and_auxiliary_heads_required")
        for head in row.outcomes:
            values.append(0.0 if head is None else float(head.value * 100))
            known.append(head is not None)
    if not 0 < len(known) <= 6 * MAX_ROWS:
        raise InputRefusal("bounded_ef_training_targets_required")
    return values, known
