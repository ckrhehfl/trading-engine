"""Why purging is unnecessary here, pinned where a future change could undo it.

An external review noted the absence of purging and embargo. The answer is that
this harness has no forward-looking label for a purge to protect — `CLAUDE.md`'s
look-ahead clause carries the four properties behind that, and this file pins the
one of them that is a signature rather than a behaviour:

**`TrainableStrategy.fit` receives no validation slice.** "Must not read it" is
enforced by the parameter list, not by discipline. A change to
`fit(train, validate, ...)` would turn that structural guarantee into a comment,
and nothing else in the suite would notice.

The other three are covered elsewhere or deliberately not mechanised:

- *each fold receives only its own training slice* — already asserted by
  `test_walkforward.py`, which compares every `fit` call's argument against
  `klines[fold.train_start_index : fold.train_end_index]`;
- *each fold's validation run starts at `starting_equity`* — already asserted by
  `test_run_walk_forward_threads_starting_equity_into_run_backtest_bounding_a_runaway_fold`;
- *no `fit` implementation reads an outside data source, and none fits labels* —
  verified by inspection across all sixteen, and not mechanically checkable
  without constraining how a strategy may be written. Recorded in `CLAUDE.md`
  instead of enforced here.
"""

from __future__ import annotations

import inspect

from research.walkforward import TrainableStrategy


def test_fit_has_no_parameter_that_could_carry_the_validation_slice():
    """The structural guarantee, read off the protocol itself.

    If this fails because `fit` gained a validation argument, the purging
    argument in `CLAUDE.md` no longer holds and needs revisiting — a supervised
    strategy with forward-looking labels would reintroduce the original
    requirement and must state its own purge.
    """
    params = list(inspect.signature(TrainableStrategy.fit).parameters)
    assert params == ["self", "train_klines", "params", "parent_run_id"], (
        f"fit's signature is now {params}. If a validation slice reaches fit, "
        "CLAUDE.md's look-ahead clause needs revisiting before this is relaxed."
    )


def test_no_parameter_name_suggests_validation_or_future_data():
    """A looser companion to the exact-list check above, which would pass a
    rename like `holdout_klines` or `future_bars` only because the list was
    updated to match. This one states the property rather than the spelling."""
    params = [p.lower() for p in inspect.signature(TrainableStrategy.fit).parameters]
    forbidden = ("valid", "test", "holdout", "future", "forward", "label", "target")
    offenders = [p for p in params for word in forbidden if word in p]
    assert not offenders, (
        f"fit's parameters now include {offenders}, which suggests it can see "
        "beyond its training window or is being fitted to labels. Either would "
        "mean purging is back on the table."
    )
