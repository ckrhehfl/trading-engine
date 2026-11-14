"""Bounded classical fitting for the EL model-stage worker.

Use the predecessor's numerical transforms and artifact format while avoiding
full-roster raw and transformed matrices. The predecessor dataset closure
continues to import the original Query implementation unchanged.
"""

from hashlib import sha256
import json

from research import intraday_event_models as models


BLOCK_ROWS = 512


def _blocks(rows):
    for first in range(0, len(rows), BLOCK_ROWS):
        yield first, rows[first:first + BLOCK_ROWS]


def _column_totals(rows, mode, np, *, mean=None):
    totals = counts = scratch = None
    for _, block in _blocks(rows):
        raw = models._numeric(block, mode, np)
        if scratch is None:
            scratch = np.empty((len(raw) + 1, raw.shape[1]), dtype=np.float64)
            totals = np.zeros(raw.shape[1], dtype=np.float64)
            counts = np.zeros(raw.shape[1], dtype=np.int64)
        present = np.isfinite(raw)
        if mean is None:
            counts += present.sum(axis=0)
        else:
            np.subtract(raw, mean, out=raw)
        raw[~present] = 0
        if mean is not None:
            np.square(raw, out=raw)

        # A C-order axis-0 sum visits each column in row order. Start this
        # reduction with the previous subtotal, not a separate block sum.
        partial = scratch[:len(raw) + 1]
        partial[0] = totals
        partial[1:] = raw
        totals = partial.sum(axis=0)
    return totals, counts


def _normalizer(rows, mode, np):
    total, counts = _column_totals(rows, mode, np)
    denominator = np.maximum(counts, 1)
    mean = total / denominator
    centered_squares, _ = _column_totals(rows, mode, np, mean=mean)
    scale = np.sqrt(centered_squares / denominator)
    scale[scale == 0] = 1
    if not np.isfinite(mean).all() or not np.isfinite(scale).all():
        raise ValueError("Training normalization overflow")
    return {"mean": mean.tolist(), "std": scale.tolist(), "finite_counts": counts.tolist(),
            "policy": "unweighted finite training rows only; standardized missing=0 plus explicit missing flag"}


def _head_matrix(rows, mode, normalization, np):
    matrix = None
    for first, block in _blocks(rows):
        transformed = models._transform(block, mode, normalization, np)
        if matrix is None:
            matrix = np.empty((len(rows), transformed.shape[1]), dtype=np.float64)
        matrix[first:first + len(transformed)] = transformed
    return matrix


def fit(kind, training_queries, labels, eval_first_session, *, mode="chart", support_binding=None):
    """Fit the registered heads without holding a second full matrix for selection."""
    if kind not in ("empirical", "lightgbm"):
        raise ValueError("Unregistered learner")
    rows, by_key = models.validate_training(training_queries, labels, eval_first_session,
        mode=mode, support_binding=support_binding)
    np, lgb, versions = models._backend(kind)
    normalization = _normalizer(rows, mode, np)
    # Apply the original finiteness checks even to rows excluded from all
    # heads. The empirical estimator never reads the transformed features.
    for _, block in _blocks(rows):
        models._transform(block, mode, normalization, np)

    heads, trees_bytes, attempted = {}, 0, 0
    matrix, matrix_indices = None, None
    for head in models.HEADS:
        indices = [i for i, row in enumerate(rows) if models.training_head_known(by_key[row.key], head)]
        known = [rows[i] for i in indices]
        stats = models._known_stats(known, len(rows))
        stats["raw_known_rows"] = sum(getattr(by_key[row.key], head) is not None for row in rows)
        stats["dropped_unpaired_q_rows"] = stats["raw_known_rows"] - len(known) if head in models.Q_HEADS else 0
        targets = [models._number(getattr(by_key[row.key], head)) for row in known]
        stats["known_zero_rows"] = sum(value == 0 for value in targets)
        if not known:
            result = {"status": "unsupported", "reason": "no_known_labels"}
        elif kind == "empirical":
            attempted += 1
            result = models._empirical(known, targets, head, np)
        elif len(known) < 2 or stats["known_clusters"] < 2 or stats["known_dates"] < 2:
            result = {"status": "unsupported", "reason": "fewer_than_two_known_rows_clusters_or_dates"}
        elif head in ("path_class", "fill") and len(set(targets)) == 1:
            result = {"status": "constant_single_class", "class": int(targets[0])}
        else:
            params = dict(models.LGB_PARAMETERS)
            if head == "path_class":
                params.update(objective="multiclass", num_class=3)
            else:
                params["objective"] = "binary" if head == "fill" else "regression_l2"
            if indices != matrix_indices:
                matrix = None
                matrix = _head_matrix(known, mode, normalization, np)
                matrix_indices = indices
            weights = np.asarray(models.head_weights(known)) * len(known)
            dataset = lgb.Dataset(matrix, label=np.asarray(targets), weight=weights, free_raw_data=True)
            attempted += 1
            try:
                model = lgb.train(params, dataset, num_boost_round=models.ROUNDS)
            except (lgb.basic.LightGBMError, FloatingPointError) as error:
                result = {"status": "failed", "reason": "native_fit_failed", "error_type": type(error).__name__,
                          "error": str(error)[:500], "parameters": params, "rounds_requested": models.ROUNDS}
            else:
                text = model.model_to_string()
                trees_bytes += len(text.encode())
                if trees_bytes > models.MAX_TREES_BYTES:
                    raise ValueError("Tree artifact size bound exceeded")
                result = {"status": "trained_lightgbm", "model": text, "num_trees": model.num_trees(),
                          "current_iteration": model.current_iteration(), "parameters": params,
                          "rounds_requested": models.ROUNDS}
                del model
            finally:
                del dataset
        heads[head] = {**stats, **result, "fit_input_known_rows": len(known)
                       if result["status"] in ("fitted_empirical", "trained_lightgbm", "failed") else 0}
    artifact = {"schema": "EL-classical-model-v1", "kind": kind, "mode": mode,
        "feature_contract": {"steps": models.STEPS, "channels": list(models.CHANNELS),
                             "static": list(models.STATIC), "families": list(models.FAMILIES),
                             "states": list(models.STATES), "peer": list(models.PEER_NAMES),
                             "position": list(models.POSITION_NAMES)},
        "task_semantics": json.loads(models._json(models.TASK_SEMANTICS)),
        "normalization": normalization, "heads": heads, "backend_versions": versions,
        "eval_first_session": eval_first_session, "training_sessions": sorted({r.session for r in rows}),
        "training_key_hashes": sorted(sha256(r.key.encode()).hexdigest() for r in rows),
        "common_support_sha256": support_binding, "training_rows": len(rows),
        "q_training_support": models.training_q_support(by_key.values()),
        "weighting": "head-known-only date->cluster->episode->frame equal weights; S1/S2 copies refused",
        "fit_head_count": sum(h["status"] in ("fitted_empirical", "trained_lightgbm") for h in heads.values()),
        "fit_attempt_head_count": attempted,
        "native_fit_head_count": sum(h["status"] == "trained_lightgbm" for h in heads.values()),
        "constant_head_count": sum(h["status"] == "constant_single_class" for h in heads.values()),
        "unsupported_head_count": sum(h["status"] == "unsupported" for h in heads.values()),
        "failed_head_count": sum(h["status"] == "failed" for h in heads.values()),
        "tree_artifact_bytes": trees_bytes,
        "claim": "exploratory statistical predictions; no account arithmetic, calibration or profitability claim"}
    models._json(artifact)
    return artifact
