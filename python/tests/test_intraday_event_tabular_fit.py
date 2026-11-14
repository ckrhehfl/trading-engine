"""Invented checks for bounded EL classical fitting against the predecessor."""

from datetime import date, timedelta
from fractions import Fraction

import numpy as np
import pytest

from research import intraday_event_models as models
from research import intraday_event_tabular_fit as tabular


def _query(i, *, held=False):
    session = (date(2026, 1, 1) + timedelta(days=i)).isoformat()
    path = (1e16, 1, -1e16, Fraction(1, 3), -0.0, None, 0)
    row = tuple(path[(i + column) % len(path)] for column in range(len(models.CHANNELS)))
    return models.Query(f"key{i:03d}", session, "KRX:000001", f"episode{i}", f"cluster{i}",
        80 if held else 70, models.FAMILIES[i % len(models.FAMILIES)], "HELD_10" if held else "WAITING",
        (row,) * models.STEPS, (None,) + (Fraction(i + 1, 7),) * (len(models.STATIC) - 1),
        (i / 11, -0.0, Fraction(i, 15)), (1 / 3, 2 / 3, Fraction(-i, 7)) if held else None,
        f"original{i}" if held else None)


@pytest.mark.parametrize("mode", ("chart", "chart_peer"))
def test_block_reductions_and_selected_features_match_predecessor_bytes(monkeypatch, mode):
    monkeypatch.setattr(tabular, "BLOCK_ROWS", 5)
    rows = tuple(_query(i, held=i % 3 == 0) for i in range(23))
    expected = models._normalizer(models._numeric(rows, mode, np), np)
    actual = tabular._normalizer(rows, mode, np)
    assert models._json(actual) == models._json(expected)
    selected = [i for i in range(len(rows)) if i % 3 != 1]
    old_matrix = models._transform(rows, mode, expected, np)[selected]
    new_matrix = tabular._head_matrix([rows[i] for i in selected], mode, actual, np)
    assert new_matrix.shape == old_matrix.shape
    assert new_matrix.dtype == old_matrix.dtype == np.float64
    assert new_matrix.tobytes() == old_matrix.tobytes()


@pytest.mark.parametrize("kind", ("empirical", "lightgbm"))
def test_complete_artifact_matches_predecessor_across_blocks(monkeypatch, kind):
    monkeypatch.setattr(tabular, "BLOCK_ROWS", 5)
    rows = tuple(_query(i, held=i % 5 == 0) for i in range(32))
    labels = tuple(models.TrainingLabels(row.key, None, None, None, None, row.session, Fraction(i, 31))
        if row.state in models.HELD_STATES else models.TrainingLabels(row.key, i % 3,
            None if i % 7 == 0 else i % 2, Fraction(-i, 13),
            None if i % 4 == 0 else Fraction(i, 17), row.session) for i, row in enumerate(rows))
    kwargs = {"mode": "chart_peer", "support_binding": models.support_seal(rows)}
    old = models.fit(kind, rows, labels, "2026-04-01", **kwargs)
    new = tabular.fit(kind, rows, labels, "2026-04-01", **kwargs)
    assert models._json(new) == models._json(old)


def test_block_normalization_still_rejects_finite_inputs_with_overflow(monkeypatch):
    monkeypatch.setattr(tabular, "BLOCK_ROWS", 1)
    rows = tuple(models.Query(f"key{i}", f"2026-01-0{i+1}", "KRX:000001", f"episode{i}",
        f"cluster{i}", 70, models.FAMILIES[0], "WAITING", ((sign * 1e308,) * 13,) * 64,
        (None,) * 16) for i, sign in enumerate((1, -1)))
    with np.errstate(over="ignore"):
        with pytest.raises(ValueError, match="Training normalization overflow"):
            tabular._normalizer(rows, "chart", np)
