"""Fresh EL classical learners at the explicitly numerical statistical boundary.

Training targets are a separate type. Inference accepts only target-free Query
objects. This proposal has no acquisition, execution, accounting or actual-run
authority. A paired caller must seal the identical common training roster.
200 clusters / 30 dates are descriptive warnings, not power or execution gates.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
import json
import math
import re

from research.intraday_event_features import CHANNELS, STATIC, STEPS


FAMILIES = ("A_DOWNFAIL", "B_ORL30", "C_FIRSTPULLBACK")
STATES = ("WAITING", "WAITING_PULLBACK", "PULLBACK", "CONFIRMED", "INVALIDATED",
          "EXPIRED_NO_CONFIRMATION", "EXPIRED_NO_PULLBACK", "UNKNOWN_GAP", "SESSION_END",
          "HELD_10", "HELD_20")
HELD_STATES = ("HELD_10", "HELD_20")
PEER_NAMES = ("median5", "median15", "positive15_fraction")
POSITION_NAMES = ("holding_age_fraction", "remaining_expiry_fraction", "entry_distance_in_scale")
HEADS = ("path_class", "fill", "net_immediate", "net_wait3", "remaining_gross")
Q_HEADS = ("net_immediate", "net_wait3")
TASK_SEMANTICS = {
    "entry_heads": list(HEADS[:4]),
    "entry_labels": "four separately adjudicated caller labels; unknown is never inferred as zero",
    "q_training": "immediate and wait3 train on their identical joint-known rows; original labels retained; inference never filters by future label support",
    "holding_states": list(HELD_STATES),
    "holding_head": "remaining_gross",
    "holding_target": "gross return from next execution open to original fixed expiry or causal stop; no repeated entry cost",
    "holding_labels": "held-alive rows only; all four entry labels must be unknown",
    "inference": "heads outside the query's entry/holding task return None with not_applicable_to_state",
    "position": "age/30, remaining-to-original-expiry/30, (current-reference-original-entry-price)/scale",
}
MAX_ROWS = 100_000
MAX_TREES_BYTES = 16 * 1024 * 1024
PRIOR_STRENGTH = 20.0
LGB_PARAMETERS = {"max_depth": 3, "num_leaves": 8, "min_data_in_leaf": 20,
    "learning_rate": 0.05, "lambda_l2": 10, "num_threads": 1, "seed": 20261111,
    "deterministic": True, "force_col_wise": True, "verbosity": -1,
    "feature_pre_filter": False}
ROUNDS = 100


def _day(value):
    if type(value) is not str or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Canonical session date required")
    return value


def _name(value):
    if type(value) is not str or not value or value != value.strip() or len(value) > 512:
        raise ValueError("Bounded nonempty identity required")
    return value


def _number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal, Fraction)):
        raise TypeError("Exact feature or finite statistical scalar required")
    number = float(Fraction(value)) if isinstance(value, str) else float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite numerical input")
    return number


@dataclass(frozen=True)
class Query:
    key: str
    session: str
    symbol: str
    episode_id: str
    cluster_id: str
    decision_offset: int
    family: str
    state: str
    sequence: tuple
    static: tuple
    peer: tuple | None = None
    position: tuple | None = None

    def __post_init__(self):
        for field in ("key", "symbol", "episode_id", "cluster_id"):
            _name(getattr(self, field))
        _day(self.session)
        if type(self.decision_offset) is not int or self.decision_offset < 0:
            raise ValueError("Nonnegative decision clock required")
        if self.family not in FAMILIES or self.state not in STATES:
            raise ValueError("Unregistered family/state")
        sequence = tuple(tuple(row) for row in self.sequence)
        static = tuple(self.static)
        if len(sequence) != STEPS or any(len(row) != len(CHANNELS) for row in sequence) or len(static) != len(STATIC):
            raise ValueError("EL 64x13 plus static16 feature shape required")
        peer = None if self.peer is None else tuple(self.peer)
        if peer is not None and len(peer) != len(PEER_NAMES):
            raise ValueError("EL peer feature shape differs")
        position = None if self.position is None else tuple(self.position)
        if self.state in HELD_STATES:
            if position is None or len(position) != len(POSITION_NAMES) or any(v is None for v in position):
                raise ValueError("Held query requires three observed position features")
            age, remaining, _ = map(_number, position)
            expected_age = 10 / 30 if self.state == "HELD_10" else 20 / 30
            if abs(age - expected_age) > 1e-12 or abs(remaining - (1 - expected_age)) > 1e-12:
                raise ValueError("Position clock differs from held state and original thirty-minute expiry")
        elif position is not None:
            raise ValueError("Position context is reserved for held queries")
        for value in (*static, *(v for row in sequence for v in row), *(peer or ()), *(position or ())):
            _number(value)
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "static", static)
        object.__setattr__(self, "peer", peer)
        object.__setattr__(self, "position", position)


@dataclass(frozen=True)
class TrainingLabels:
    """Separate entry and holding targets, never inferred from another head.

    Caller-declared known no-fill zeros are observations. No-entry and an
    unavailable wait3 outcome must not be silently supplied as numerical zero.
    """

    key: str
    path_class: int | None
    fill: int | None
    net_immediate: Fraction | Decimal | float | int | None
    net_wait3: Fraction | Decimal | float | int | None
    matured_session: str | None
    remaining_gross: Fraction | Decimal | float | int | None = None

    def __post_init__(self):
        _name(self.key)
        for field, allowed in (("path_class", (0, 1, 2)), ("fill", (0, 1))):
            value = getattr(self, field)
            if value is not None and (type(value) is not int or value not in allowed):
                raise ValueError("Known categorical label outside fixed classes")
        _number(self.net_immediate)
        _number(self.net_wait3)
        _number(self.remaining_gross)
        if any(getattr(self, field) is not None for field in HEADS) and self.matured_session is None:
            raise ValueError("Known targets require maturity date")
        if self.matured_session is not None:
            _day(self.matured_session)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _identity(row):
    return (row.session, row.symbol, row.episode_id, row.decision_offset)


def _queries(rows):
    rows = tuple(rows)
    if not 1 <= len(rows) <= MAX_ROWS or any(type(row) is not Query for row in rows):
        raise ValueError("Bounded target-free Query roster required")
    if len({r.key for r in rows}) != len(rows) or len({_identity(r) for r in rows}) != len(rows):
        raise ValueError("Duplicate original key/coordinate; S1/S2 copies cannot be independent rows")
    ownership, coordinate_clusters, episodes = {}, {}, {}
    for row in rows:
        owner = (row.session, row.symbol)
        if ownership.setdefault(row.cluster_id, owner) != owner:
            raise ValueError("Cluster must identify one original symbol/session")
        if coordinate_clusters.setdefault(owner, row.cluster_id) != row.cluster_id:
            raise ValueError("One original symbol/session cannot be split into independent clusters")
        episode_owner = (row.session, row.symbol, row.cluster_id, row.family)
        if episodes.setdefault(row.episode_id, episode_owner) != episode_owner:
            raise ValueError("Episode identity changed across original coordinates")
    return tuple(sorted(rows, key=lambda row: row.key))


def support_seal(rows):
    """Caller-visible paired common roster identity, independent of feature mode."""
    rows = _queries(rows)
    return sha256(_json([(r.key, *_identity(r), r.cluster_id, r.family, r.state) for r in rows])).hexdigest()


def head_applicable(row, head):
    """Keep original entry outcomes separate from the already-held remainder."""
    if head not in HEADS:
        raise ValueError("Unregistered prediction head")
    return (row.state in HELD_STATES) == (head == "remaining_gross")


def training_head_known(label, head):
    """Two action values share a training cohort; other outcomes stay separate."""
    if head not in HEADS:
        raise ValueError("Unregistered training head")
    if head in Q_HEADS:
        return all(getattr(label, name) is not None for name in Q_HEADS)
    return getattr(label, head) is not None


def training_q_support(labels):
    """Account for original Q observations excluded solely by paired training."""
    labels = tuple(labels)
    original = {head: sum(getattr(label, head) is not None for label in labels) for head in Q_HEADS}
    paired = sum(training_head_known(label, Q_HEADS[0]) for label in labels)
    return {"original_known_rows": original, "paired_known_rows": paired,
            "dropped_unpaired_rows": {head: original[head] - paired for head in Q_HEADS},
            "policy": "both Q heads use identical joint-known rows; no change to labels or inference population"}


def _backend(kind):
    import numpy as np
    if np.__version__ != "2.5.2":
        raise ValueError("Unregistered NumPy runtime")
    if kind == "empirical":
        return np, None, {"numpy": np.__version__}
    import lightgbm as lgb
    import scipy
    if lgb.__version__ != "4.6.0" or scipy.__version__ != "1.18.1":
        raise ValueError("Unregistered LightGBM/SciPy runtime")
    return np, lgb, {"numpy": np.__version__, "lightgbm": lgb.__version__, "scipy": scipy.__version__}


def _numeric(rows, mode, np):
    values = []
    for row in rows:
        flat = [*(_number(v) for step in row.sequence for v in step), *map(_number, row.static)]
        if mode == "chart_peer":
            flat.extend(map(_number, row.peer or (None,) * len(PEER_NAMES)))
        flat.extend(map(_number, row.position or (None,) * len(POSITION_NAMES)))
        values.append([float("nan") if value is None else value for value in flat])
    return np.asarray(values, dtype=np.float64)


def _normalizer(matrix, np):
    present = np.isfinite(matrix)
    counts = present.sum(axis=0)
    total = np.where(present, matrix, 0).sum(axis=0)
    mean = total / np.maximum(counts, 1)
    centered = np.where(present, matrix - mean, 0)
    variance = (centered * centered).sum(axis=0) / np.maximum(counts, 1)
    scale = np.sqrt(variance)
    scale[scale == 0] = 1
    if not np.isfinite(mean).all() or not np.isfinite(scale).all():
        raise ValueError("Training normalization overflow")
    return {"mean": mean.tolist(), "std": scale.tolist(), "finite_counts": counts.tolist(),
            "policy": "unweighted finite training rows only; standardized missing=0 plus explicit missing flag"}


def _transform(rows, mode, normalization, np):
    raw = _numeric(rows, mode, np)
    mean, scale = np.asarray(normalization["mean"]), np.asarray(normalization["std"])
    if mean.shape != (raw.shape[1],) or scale.shape != mean.shape or not np.isfinite(mean).all() or not (np.isfinite(scale) & (scale > 0)).all():
        raise ValueError("Malformed normalization artifact")
    missing = np.isnan(raw)
    values = np.where(missing, 0, (raw - mean) / scale)
    categories = np.asarray([[float(row.family == name) for name in FAMILIES]
                             + [float(row.state == name) for name in STATES] for row in rows])
    result = np.concatenate((values, missing.astype(np.float64), categories), axis=1)
    if not np.isfinite(result).all():
        raise ValueError("Nonfinite transformed feature")
    return result


def head_weights(rows):
    """Recompute date->cluster->episode->frame balance on this head's known rows."""
    dates = defaultdict(set)
    clusters = defaultdict(set)
    frames = Counter()
    for row in rows:
        dates[row.session].add(row.cluster_id)
        clusters[row.session, row.cluster_id].add(row.episode_id)
        frames[row.session, row.cluster_id, row.episode_id] += 1
    return [1 / (len(dates) * len(dates[r.session]) * len(clusters[r.session, r.cluster_id])
                 * frames[r.session, r.cluster_id, r.episode_id]) for r in rows]


def _known_stats(rows, total):
    dates = len({r.session for r in rows})
    cluster_count = len({r.cluster_id for r in rows})
    return {"known_rows": len(rows), "excluded_unknown_rows": total - len(rows),
            "known_episodes": len({r.episode_id for r in rows}), "known_clusters": cluster_count,
            "known_dates": dates, "low_support": cluster_count < 200 or dates < 30,
            "precision_warning": "200 clusters/30 dates are warning references only, not power or execution thresholds"}


def _empirical(rows, targets, head, np):
    classes = 3 if head == "path_class" else 2 if head == "fill" else None
    observed = np.eye(classes)[np.asarray(targets, dtype=int)] if classes else np.asarray(targets)[:, None]
    # One total mass unit per known cluster; the hierarchical weights keep dates
    # balanced rather than letting additional frames or overlapping families win.
    weights = np.asarray(head_weights(rows)) * len({r.cluster_id for r in rows})
    pooled = (observed * weights[:, None]).sum(axis=0) / weights.sum()
    families, states = {}, {}
    for family in FAMILIES:
        mask = np.asarray([r.family == family for r in rows])
        mass = float(weights[mask].sum())
        if not mass:
            continue
        mean = ((observed[mask] * weights[mask, None]).sum(axis=0) + PRIOR_STRENGTH * pooled) / (mass + PRIOR_STRENGTH)
        families[family] = mean.tolist()
        for state in STATES:
            selected = np.asarray([r.family == family and r.state == state for r in rows])
            state_mass = float(weights[selected].sum())
            if state_mass:
                value = ((observed[selected] * weights[selected, None]).sum(axis=0)
                         + PRIOR_STRENGTH * mean) / (state_mass + PRIOR_STRENGTH)
                states[family + "/" + state] = value.tolist()
    return {"status": "fitted_empirical", "pooled": pooled.tolist(), "families": families, "states": states,
            "prior_strength": PRIOR_STRENGTH, "mass_unit": "date-balanced known cluster-equivalent mass"}


def validate_training(training_queries, labels, eval_first_session, *, mode, support_binding):
    """Shared target separation, chronology and paired-input admission; no backend."""
    if mode not in ("chart", "chart_peer"):
        raise ValueError("Unregistered feature mode")
    rows = _queries(training_queries)
    _day(eval_first_session)
    if any(row.session >= eval_first_session for row in rows):
        raise ValueError("Training session overlaps evaluation period")
    if not isinstance(support_binding, str) or not re.fullmatch("[0-9a-f]{64}", support_binding) or support_binding != support_seal(rows):
        raise ValueError("Caller paired common-support seal missing or different")
    if mode == "chart_peer" and any(r.peer is None or any(v is None for v in r.peer) for r in rows):
        raise ValueError("Paired common training roster contains unsupported peer context")
    labels = tuple(labels)
    if len(labels) != len(rows) or any(type(value) is not TrainingLabels for value in labels):
        raise ValueError("Separate complete TrainingLabels roster required")
    by_key = {value.key: value for value in labels}
    if len(by_key) != len(labels) or set(by_key) != {r.key for r in rows}:
        raise ValueError("Training target key roster differs")
    for row in rows:
        target = by_key[row.key]
        if row.state in HELD_STATES:
            if any(getattr(target, head) is not None for head in HEADS[:4]):
                raise ValueError("Held rows cannot supply entry-head labels")
        elif target.remaining_gross is not None:
            raise ValueError("Remaining gross target requires a held-alive query")
        maturity = target.matured_session
        if maturity is not None and not row.session <= maturity < eval_first_session:
            raise ValueError("Training labels have not matured strictly before evaluation")
    return rows, by_key


def validate_prediction_queries(artifact, queries):
    """Shared query-only chronology/key guard; does not validate learner payloads."""
    rows = _queries(queries)
    cutoff = _day(artifact["eval_first_session"])
    dates = artifact["training_sessions"]
    hashes = set(artifact["training_key_hashes"])
    if (dates != sorted(set(dates)) or any(_day(day) >= cutoff for day in dates)
            or len(hashes) > MAX_ROWS or any(not re.fullmatch("[0-9a-f]{64}", value) for value in hashes)):
        raise ValueError("Malformed model training identity")
    if any(r.session < cutoff or r.session in dates or sha256(r.key.encode()).hexdigest() in hashes for r in rows):
        raise ValueError("Query overlaps training keys or dates")
    return rows


def fit(kind, training_queries, labels, eval_first_session, *, mode="chart", support_binding=None):
    if kind not in ("empirical", "lightgbm"):
        raise ValueError("Unregistered learner")
    rows, by_key = validate_training(training_queries, labels, eval_first_session,
                                    mode=mode, support_binding=support_binding)
    np, lgb, versions = _backend(kind)
    normalization = _normalizer(_numeric(rows, mode, np), np)
    matrix = _transform(rows, mode, normalization, np)
    heads, trees_bytes, attempted = {}, 0, 0
    for head in HEADS:
        indices = [i for i, row in enumerate(rows) if training_head_known(by_key[row.key], head)]
        known = [rows[i] for i in indices]
        stats = _known_stats(known, len(rows))
        stats["raw_known_rows"] = sum(getattr(by_key[row.key], head) is not None for row in rows)
        stats["dropped_unpaired_q_rows"] = stats["raw_known_rows"] - len(known) if head in Q_HEADS else 0
        targets = [_number(getattr(by_key[r.key], head)) for r in known]
        stats["known_zero_rows"] = sum(value == 0 for value in targets)
        if not known:
            result = {"status": "unsupported", "reason": "no_known_labels"}
        elif kind == "empirical":
            attempted += 1
            result = _empirical(known, targets, head, np)
        elif len(known) < 2 or stats["known_clusters"] < 2 or stats["known_dates"] < 2:
            result = {"status": "unsupported", "reason": "fewer_than_two_known_rows_clusters_or_dates"}
        elif head in ("path_class", "fill") and len(set(targets)) == 1:
            result = {"status": "constant_single_class", "class": int(targets[0])}
        else:
            params = dict(LGB_PARAMETERS)
            if head == "path_class":
                params.update(objective="multiclass", num_class=3)
            else:
                params["objective"] = "binary" if head == "fill" else "regression_l2"
            weights = np.asarray(head_weights(known)) * len(known)
            dataset = lgb.Dataset(matrix[indices], label=np.asarray(targets), weight=weights, free_raw_data=True)
            attempted += 1
            try:
                model = lgb.train(params, dataset, num_boost_round=ROUNDS)
            except (lgb.basic.LightGBMError, FloatingPointError) as error:
                result = {"status": "failed", "reason": "native_fit_failed", "error_type": type(error).__name__,
                          "error": str(error)[:500], "parameters": params, "rounds_requested": ROUNDS}
            else:
                text = model.model_to_string()
                trees_bytes += len(text.encode())
                if trees_bytes > MAX_TREES_BYTES:
                    raise ValueError("Tree artifact size bound exceeded")
                result = {"status": "trained_lightgbm", "model": text, "num_trees": model.num_trees(),
                          "current_iteration": model.current_iteration(), "parameters": params, "rounds_requested": ROUNDS}
        heads[head] = {**stats, **result, "fit_input_known_rows": len(known)
                       if result["status"] in ("fitted_empirical", "trained_lightgbm", "failed") else 0}
    artifact = {"schema": "EL-classical-model-v1", "kind": kind, "mode": mode,
        "feature_contract": {"steps": STEPS, "channels": list(CHANNELS), "static": list(STATIC),
                             "families": list(FAMILIES), "states": list(STATES), "peer": list(PEER_NAMES),
                             "position": list(POSITION_NAMES)},
        "task_semantics": json.loads(_json(TASK_SEMANTICS)),
        "normalization": normalization, "heads": heads, "backend_versions": versions,
        "eval_first_session": eval_first_session, "training_sessions": sorted({r.session for r in rows}),
        "training_key_hashes": sorted(sha256(r.key.encode()).hexdigest() for r in rows),
        "common_support_sha256": support_binding, "training_rows": len(rows),
        "q_training_support": training_q_support(by_key.values()),
        "weighting": "head-known-only date->cluster->episode->frame equal weights; S1/S2 copies refused",
        "fit_head_count": sum(h["status"] in ("fitted_empirical", "trained_lightgbm") for h in heads.values()),
        "fit_attempt_head_count": attempted,
        "native_fit_head_count": sum(h["status"] == "trained_lightgbm" for h in heads.values()),
        "constant_head_count": sum(h["status"] == "constant_single_class" for h in heads.values()),
        "unsupported_head_count": sum(h["status"] == "unsupported" for h in heads.values()),
        "failed_head_count": sum(h["status"] == "failed" for h in heads.values()),
        "tree_artifact_bytes": trees_bytes, "claim": "exploratory statistical predictions; no account arithmetic, calibration or profitability claim"}
    _json(artifact)
    return artifact


def predict(artifact, queries):
    rows = validate_prediction_queries(artifact, queries)
    if artifact.get("schema") != "EL-classical-model-v1" or artifact.get("kind") not in ("empirical", "lightgbm") or artifact.get("mode") not in ("chart", "chart_peer"):
        raise ValueError("Unregistered model artifact")
    expected = {"steps": STEPS, "channels": list(CHANNELS), "static": list(STATIC),
                "families": list(FAMILIES), "states": list(STATES), "peer": list(PEER_NAMES),
                "position": list(POSITION_NAMES)}
    if (artifact.get("feature_contract") != expected or set(artifact["heads"]) != set(HEADS)
            or artifact.get("task_semantics") != TASK_SEMANTICS):
        raise ValueError("Model feature/head contract differs")
    np, lgb, versions = _backend(artifact["kind"])
    if versions != artifact["backend_versions"]:
        raise ValueError("Model numerical runtime changed")
    active = [i for i, row in enumerate(rows) if artifact["mode"] != "chart_peer"
              or (row.peer is not None and all(value is not None for value in row.peer))]
    supported = tuple(rows[i] for i in active)
    matrix = _transform(supported, artifact["mode"], artifact["normalization"], np) if supported else None
    predictions = {head: [] for head in HEADS}
    sizes = 0
    for head, model in artifact["heads"].items():
        status = model["status"]
        eligible = [i for i, row in enumerate(supported) if head_applicable(row, head)]
        head_rows = tuple(supported[i] for i in eligible)
        if status in ("unsupported", "failed"):
            values = [None] * len(head_rows)
        elif status == "constant_single_class":
            values = [([float(i == model["class"]) for i in range(3)] if head == "path_class" else float(model["class"])) for _ in head_rows]
        elif status == "fitted_empirical":
            values = []
            for row in head_rows:
                value = model["states"].get(row.family + "/" + row.state,
                    model["families"].get(row.family, model["pooled"]))
                values.append(list(value) if head == "path_class" else value[1] if head == "fill" else value[0])
        elif status == "trained_lightgbm":
            sizes += len(model["model"].encode())
            if sizes > MAX_TREES_BYTES:
                raise ValueError("Tree artifact size bound exceeded")
            if head_rows:
                fitted = lgb.Booster(model_str=model["model"])
                raw = fitted.predict(matrix[eligible], num_threads=1)
                values = raw.tolist()
            else:
                values = []
        else:
            raise ValueError("Unknown head status; never substitute zero")
        for value in values:
            if value is None:
                continue
            numbers = value if isinstance(value, list) else [value]
            if not all(math.isfinite(v) for v in numbers):
                raise ValueError("Nonfinite model prediction")
            if head == "path_class" and (len(numbers) != 3 or any(v < 0 or v > 1 for v in numbers) or abs(sum(numbers) - 1) > 1e-8):
                raise ValueError("Invalid path probability vector")
            if head == "fill" and not 0 <= value <= 1:
                raise ValueError("Invalid fill probability")
        if len(values) != len(head_rows):
            raise ValueError("Prediction population differs")
        predictions[head] = [None] * len(rows)
        for index, value in zip(eligible, values, strict=True):
            predictions[head][active[index]] = value
    result = []
    for i, row in enumerate(rows):
        peer_unknown = artifact["mode"] == "chart_peer" and (row.peer is None or any(v is None for v in row.peer))
        result.append({"key": row.key, "session": row.session,
            "query_status": "unsupported_peer_context" if peer_unknown else "supported_features",
            "predictions": {head: None if peer_unknown else predictions[head][i] for head in HEADS},
            "head_status": {head: "unsupported_peer_context" if peer_unknown else
                "not_applicable_to_state" if not head_applicable(row, head) else artifact["heads"][head]["status"] for head in HEADS}})
    return tuple(result)
