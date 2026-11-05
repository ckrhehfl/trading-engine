"""Finite model inputs from the exact EF chart and peer projections.

Tensor placeholders carry explicit masks. Raw prices, share counts and their
states stay in the authenticated snapshot. This boundary reads no outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import math
from statistics import fmean, median, pstdev

from research.intraday_chart_inputs import ChartInputs, PeerContext
from research.intraday_ed_inputs import MINUTE, InputRefusal


MINUTE_CHANNELS = (
    "open_pct", "high_pct", "low_pct", "close_pct", "body_pct", "range_pct",
    "close_location", "zero_range", "volume_log_ratio", "volume_observed",
)
COARSE_CHANNELS = ("open_pct", "high_pct", "low_pct", "close_pct", "observed", "preopening")
STATIC_CHANNELS = ("elapsed_minutes_over_390", "prior_high_pct", "prior_low_pct", "prior_close_pct", "prior_observed")
PEER_CHANNELS = ("peer_minute_oc_pct", "peer_cumulative_pct", "peer_positive_fraction")
SUMMARY_WINDOWS = (5, 15, 30, 60)
COARSE_WINDOWS = (6, 12, 24)
SUMMARY_METRICS = ("return_pct", "span_pct", "body_mean_pct", "body_pstd_pct", "volume_fraction", "volume_log_mean")
TABULAR_CHART_NAMES = (
    tuple(f"minute{window}__{name}" for window in SUMMARY_WINDOWS for name in SUMMARY_METRICS)
    + tuple(f"last_coarse__{name}" for name in COARSE_CHANNELS)
    + STATIC_CHANNELS
    + tuple(f"coarse{window}__observed_fraction" for window in COARSE_WINDOWS)
)
TABULAR_PEER_NAMES = ("peer_mean_minute_oc_pct", "peer_last_cumulative_pct", "peer_last_positive_fraction", "peer_coverage")


@dataclass(frozen=True)
class ChartTensor:
    minute: tuple[tuple[float, ...], ...]
    coarse: tuple[tuple[float, ...], ...]
    static: tuple[float, ...]


@dataclass(frozen=True)
class PeerTensor:
    minute: tuple[tuple[float, ...], ...]
    coverage: float


def _finite(value: Fraction | float) -> float:
    try:
        result = float(value)
    except OverflowError as error:
        raise InputRefusal("chart_tensor_value_out_of_range") from error
    if not math.isfinite(result):
        raise InputRefusal("nonfinite_chart_tensor")
    return result


def chart_tensor(chart: ChartInputs) -> ChartTensor | None:
    """Encode a chart at the decision cutoff, without peer or symbol features."""
    if chart.status == "unavailable":
        return None
    if chart.sequence is None or len(chart.sequence) != 60 or len(chart.volume) != 60 or len(chart.coarse) != 24:
        raise InputRefusal("invalid_chart_tensor_geometry")
    last = chart.coarse[-1]
    if last.status != "observed" or last.ohlc is None or last.ohlc[3] <= 0:
        raise InputRefusal("chart_tensor_requires_last_coarse_close")
    close = last.ohlc[3]

    positives = [row.shares for row in chart.volume if row.shares is not None and row.shares > 0]
    volume_scale = median(positives) if positives else Fraction(1)
    minute = []
    for row, volume in zip(chart.sequence, chart.volume, strict=True):
        if row.start_at != volume.start_at or len(row.channels) != 8:
            raise InputRefusal("chart_tensor_minute_mismatch")
        has_volume = volume.state == "observed"
        if has_volume != (volume.shares is not None) or (has_volume and volume.shares < 0):
            raise InputRefusal("chart_tensor_volume_state_mismatch")
        scaled = tuple(_finite(value * 100) for value in row.channels[:6])
        shape = tuple(map(_finite, row.channels[6:]))
        log_volume = math.log1p(_finite(volume.shares / volume_scale)) if has_volume else 0.0
        minute.append((*scaled, *shape, log_volume, float(has_volume)))

    coarse = []
    for bar in chart.coarse:
        if bar.status == "observed":
            if bar.ohlc is None:
                raise InputRefusal("missing_observed_coarse_prices")
            coarse.append((*(_finite(100 * (price / close - 1)) for price in bar.ohlc), 1.0, 0.0))
        elif bar.ohlc is None and bar.status in ("unavailable", "not_yet_in_session"):
            coarse.append((0.0, 0.0, 0.0, 0.0, 0.0, float(bar.status == "not_yet_in_session")))
        else:
            raise InputRefusal("chart_tensor_coarse_state_mismatch")

    previous = (chart.previous.high, chart.previous.low, chart.previous.close)
    has_previous = chart.previous.status == "available"
    if (has_previous and any(price is None or price <= 0 for price in previous)
            or not has_previous and any(price is not None for price in previous)):
        raise InputRefusal("chart_tensor_previous_state_mismatch")
    prior = tuple(_finite(100 * (price / close - 1)) for price in previous) if has_previous else (0.0,) * 3
    elapsed = Fraction((chart.bar_end - chart.opening) // MINUTE, 390)
    return ChartTensor(tuple(minute), tuple(coarse), (_finite(elapsed), *prior, float(has_previous)))


def peer_tensor(peer: PeerContext) -> PeerTensor | None:
    """Keep leave-one-out peer data in a separate optional tensor."""
    if peer.status == "unavailable":
        return None
    channels = (peer.minute_oc_return, peer.cumulative_anchored_return, peer.positive_fraction)
    count = len(peer.common_peer_ids)
    if any(values is None or len(values) != 60 for values in channels) or not 5 <= count <= peer.denominator:
        raise InputRefusal("invalid_peer_tensor_geometry")
    rows = tuple((_finite(100 * minute), _finite(100 * cumulative), _finite(positive))
                 for minute, cumulative, positive in zip(*channels, strict=True))
    return PeerTensor(rows, _finite(Fraction(count, peer.denominator)))


def remove_temporal_order(tensor: ChartTensor, *, coordinate: str) -> ChartTensor:
    """Deterministic retraining control with the latest tokens unchanged.

    Only observed prefix rows move. Scheduled padding and unavailable slots keep
    their positions. No absolute position or inverse-permutation key is a feature.
    """
    if not coordinate:
        raise InputRefusal("ablation_coordinate_required")

    def permute(rows: tuple, indices: tuple[int, ...], domain: str) -> tuple:
        prefix = ("EF-order-v1\x00" + domain + "\x00" + coordinate + "\x00").encode()
        order = sorted(indices, key=lambda index: hashlib.sha256(prefix + bytes([index])).digest())
        values = list(rows)
        for destination, source in zip(indices, order, strict=True):
            values[destination] = rows[source]
        return tuple(values)

    minute = permute(tensor.minute, tuple(range(59)), "minute")
    coarse = permute(tensor.coarse, tuple(i for i, row in enumerate(tensor.coarse[:-1]) if row[4] == 1), "coarse")
    return ChartTensor(minute, coarse, tensor.static)


def tabular_chart(tensor: ChartTensor) -> tuple[float, ...]:
    """The fixed LightGBM chart view, derived from the same sequence inputs."""
    features = []
    for count in SUMMARY_WINDOWS:
        rows = tensor.minute[-count:]
        first = rows[0][0]
        scale = 1 + first / 100
        if scale <= 0:
            raise InputRefusal("tabular_chart_anchor_out_of_range")
        bodies = [row[4] for row in rows]
        volumes = [row[8] for row in rows if row[9] == 1]
        features.extend((
            (rows[-1][3] - first) / scale,
            (max(row[1] for row in rows) - min(row[2] for row in rows)) / scale,
            fmean(bodies), pstdev(bodies), len(volumes) / count,
            fmean(volumes) if volumes else 0.0,
        ))
    features.extend(tensor.coarse[-1])
    features.extend(tensor.static)
    features.extend(fmean(row[4] for row in tensor.coarse[-count:]) for count in COARSE_WINDOWS)
    return tuple(map(_finite, features))


def tabular_peer(tensor: PeerTensor) -> tuple[float, ...]:
    return (fmean(row[0] for row in tensor.minute), tensor.minute[-1][1], tensor.minute[-1][2], tensor.coverage)
