"""Curated `strategy_id` -> research-family lineage, for honest trial
accounting.

See `.planning/sr-p-trial-accounting.md` for the full finding this module
acts on. The short version: `research/overfitting_check.py`'s MinBTL-style
combination count was keyed on the bare `strategy_id`, so **renaming a
strategy silently reset its data-snooping meter**. The real example from
this project's own log: `ensemble-momentum-configuration-c` reports 10
combinations (`risk_level="low"`), but it is a direct descendant of
`ensemble-momentum`'s 234-combination `"high"`-risk search -- the same
research thread, split across two ids. Bailey et al.'s `N` ("the number of
trials from which this maximum was selected") does not care what a
researcher renamed a file to between runs, so the counting unit has to be
the research *family*, not the `strategy_id`.

## Why this map is hand-curated, not derived

Lineage genuinely is not recoverable from `runs/experiments.jsonl`.
Verified directly against the real 1,839-record log: **every** multi-fold
`run_walk_forward` record logs `params` as `{}`, `{"symbol": "BTC-USDT"}`,
or `{"candidates": [[15], [20], [25], [30]]}` -- the actual configuration
under test (ADX thresholds, volatility-target parameters, lookback pairs,
risk fractions) lives in the `Trainable`'s **constructor arguments**, which
`run_walk_forward` never sees and therefore never logs. Six separate
`ensemble-momentum`/`ensemble-momentum-configuration-c` records are
byte-identical in `params`/`walk_forward_config`/`data_range` while
representing materially different work. There is nothing in the log to
derive a family from, so the attribution is written down by hand, once,
with a citation to the `.planning/sr-*.md` document that establishes it.

Going forward this stops being necessary: `experiment_log.log_run` accepts
an optional `strategy_family`, threaded through `run_walk_forward`, and
**omits the key entirely when `None`** (deliberately not `null`, the shape
`parent_run_id` uses). That makes "key absent" unambiguously mean
"pre-lineage record, attribute via the curated map below" and "key present"
mean "trust the record" -- a `null` would collide the two cases.

`resolve_family` never silently invents a family: an unmapped, unlogged
`strategy_id` resolves to itself with `source="unmapped"` and an attached
note, so the gap shows up in the output instead of being papered over.
"""

from dataclasses import dataclass
from typing import Any, Mapping

# Purpose values. "infrastructure" runs are real backtests, but they exist
# to prove the harness works end to end, not to select a trading edge --
# counting them in the same `N` as genuine strategy search would overstate
# the selection bias behind the project's best research result.
RESEARCH_PURPOSE = "research"
INFRASTRUCTURE_PURPOSE = "infrastructure"

# `FamilyResolution.source` values -- how a family attribution was reached.
LOGGED_SOURCE = "logged"
CURATED_MAP_SOURCE = "curated_map"
UNMAPPED_SOURCE = "unmapped"

# The optional self-describing field `log_run` writes (only when non-None).
STRATEGY_FAMILY_KEY = "strategy_family"


@dataclass(frozen=True)
class FamilyEntry:
    """One curated attribution. `citation` is the `.planning/sr-*.md`
    document that establishes it -- required, not decorative: a future
    reader must be able to check the attribution rather than trust it.
    """

    family: str
    purpose: str
    citation: str


# One entry per `strategy_id` that has ever appeared in
# `runs/experiments.jsonl` (all 15 as of 2026-07-28). Hand-written, per
# this module's docstring. Add a new entry here only alongside the
# `.planning/` document that justifies it -- and prefer passing
# `strategy_family=` at run time for genuinely new work, so the map does
# not have to keep growing.
FAMILY_BY_STRATEGY_ID: dict[str, FamilyEntry] = {
    # --- trend / momentum -------------------------------------------------
    # One continuous research thread: naive SMA crossover -> ATR-risk-managed
    # crossover (15m, then a native-1h variant) -> single-lookback momentum
    # -> the multi-lookback ensemble -> "Configuration C". Each step reused
    # the previous step's findings, which is exactly why they share one `N`.
    "regime-momentum-btc-15m": FamilyEntry(
        family="trend-momentum",
        purpose=RESEARCH_PURPOSE,
        citation=".planning/sr-e-regime-momentum.md",
    ),
    "regime_momentum": FamilyEntry(
        family="trend-momentum",
        purpose=RESEARCH_PURPOSE,
        citation=".planning/sr-f-risk-management-and-1h-variant.md",
    ),
    "hourly_momentum": FamilyEntry(
        family="trend-momentum",
        purpose=RESEARCH_PURPOSE,
        citation=".planning/sr-f-risk-management-and-1h-variant.md",
    ),
    "single-lookback-momentum": FamilyEntry(
        family="trend-momentum",
        purpose=RESEARCH_PURPOSE,
        citation=".planning/sr-h-ensemble-regime-voltargeting.md",
    ),
    "ensemble-momentum": FamilyEntry(
        family="trend-momentum",
        purpose=RESEARCH_PURPOSE,
        citation=".planning/sr-h-ensemble-regime-voltargeting.md",
    ),
    "ensemble-momentum-configuration-c": FamilyEntry(
        family="trend-momentum",
        purpose=RESEARCH_PURPOSE,
        citation=".planning/sr-i-ensemble-refinement.md",
    ),
    # --- mean reversion ---------------------------------------------------
    # The blend is a regime-gated combination of the mean-reversion signal
    # with the momentum one; it was designed and evaluated inside the same
    # task as mean-reversion-alone, against the same folds, so it shares
    # that task's selection history rather than starting a fresh count.
    "mean-reversion": FamilyEntry(
        family="mean-reversion",
        purpose=RESEARCH_PURPOSE,
        citation=".planning/sr-k-mean-reversion-and-blend.md",
    ),
    "momentum-reversion-blend": FamilyEntry(
        family="mean-reversion",
        purpose=RESEARCH_PURPOSE,
        citation=".planning/sr-k-mean-reversion-and-blend.md",
    ),
    # --- volume -----------------------------------------------------------
    # `obv-trend-diag` is the diagnostic re-run of `obv-trend` from the same
    # task, not a second hypothesis.
    "obv-trend": FamilyEntry(
        family="volume",
        purpose=RESEARCH_PURPOSE,
        citation=".planning/sr-l-volume-signal.md",
    ),
    "obv-trend-diag": FamilyEntry(
        family="volume",
        purpose=RESEARCH_PURPOSE,
        citation=".planning/sr-l-volume-signal.md",
    ),
    # --- funding ----------------------------------------------------------
    "funding-extremity-contrarian": FamilyEntry(
        family="funding",
        purpose=RESEARCH_PURPOSE,
        citation=".planning/sr-n-funding-rate-strategy.md",
    ),
    # --- infrastructure (not science) -------------------------------------
    # Pipeline-validation runs: they prove the walk-forward/experiment-log/
    # sensitivity machinery works end to end against real data. None of them
    # was ever a candidate for paper trading (see `ma_crossover.py`'s own
    # module docstring), so none belongs in the research `N`.
    "task-c-e2e-verification": FamilyEntry(
        family="infrastructure",
        purpose=INFRASTRUCTURE_PURPOSE,
        citation=".planning/sr-c-walkforward-holdout.md",
    ),
    "ma-crossover-task-d-e2e": FamilyEntry(
        family="infrastructure",
        purpose=INFRASTRUCTURE_PURPOSE,
        citation=".planning/sr-d-placeholder-strategy.md",
    ),
    "ma-crossover-task-g-sensitivity-demo": FamilyEntry(
        family="infrastructure",
        purpose=INFRASTRUCTURE_PURPOSE,
        citation=".planning/sr-g-overfitting-safeguards.md",
    ),
    "ma-crossover-task-g-sensitivity-demo-v2": FamilyEntry(
        family="infrastructure",
        purpose=INFRASTRUCTURE_PURPOSE,
        citation=".planning/sr-g-overfitting-safeguards.md",
    ),
}


def _build_purpose_by_family() -> dict[str, str]:
    """Derive family -> purpose from `FAMILY_BY_STRATEGY_ID` rather than
    writing it out a second time (two hand-maintained copies would drift).
    Raises at import time if a family ever ends up with mixed purposes,
    which would make the project-level split by purpose meaningless.
    """
    purposes: dict[str, str] = {}
    for strategy_id, entry in FAMILY_BY_STRATEGY_ID.items():
        existing = purposes.setdefault(entry.family, entry.purpose)
        if existing != entry.purpose:
            raise ValueError(
                f"family {entry.family!r} has mixed purposes ({existing!r} vs {entry.purpose!r}); "
                f"introduced by strategy_id={strategy_id!r}"
            )
    return purposes


PURPOSE_BY_FAMILY: dict[str, str] = _build_purpose_by_family()


@dataclass(frozen=True)
class FamilyResolution:
    """How one `strategy_id` was attributed to a family. `source` records
    which of the three resolution steps produced it, so a reader can tell a
    self-describing record from a curated attribution from an unresolved
    gap without re-deriving any of it.
    """

    strategy_id: str
    family: str
    purpose: str
    source: str
    citation: str | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "family": self.family,
            "purpose": self.purpose,
            "source": self.source,
            "citation": self.citation,
            "note": self.note,
        }


def resolve_family(strategy_id: str, record: Mapping[str, Any] | None = None) -> FamilyResolution:
    """Resolve `strategy_id` to a research family, in this order:

    1. `record["strategy_family"]`, when present and non-`None` -- the
       record describes its own lineage, so trust it (`source="logged"`).
    2. `FAMILY_BY_STRATEGY_ID` -- the curated historical attribution
       (`source="curated_map"`), carrying its `.planning/` citation.
    3. Neither -- fall back to the bare `strategy_id` as its own
       single-member family (`source="unmapped"`), with a note saying so.
       **Never silently invent a family**: an unmapped id showing up in the
       output as its own family is the visible signal that this module
       needs a new entry (or that the run should have passed
       `strategy_family=`).

    A logged family's `purpose` is looked up in `PURPOSE_BY_FAMILY` (itself
    derived from the curated map). An unrecognized logged family defaults to
    `"research"` -- the conservative choice, since counting an unknown
    family's runs toward the research `N` overstates rather than understates
    selection bias -- with a note recording the default.
    """
    logged_family = (record or {}).get(STRATEGY_FAMILY_KEY)
    if logged_family is not None:
        purpose = PURPOSE_BY_FAMILY.get(logged_family)
        note = None
        if purpose is None:
            purpose = RESEARCH_PURPOSE
            note = (
                f"strategy_id={strategy_id!r} logged strategy_family={logged_family!r}, which is not one of the "
                f"families research/lineage.py knows about; defaulting purpose to {RESEARCH_PURPOSE!r}"
            )
        return FamilyResolution(
            strategy_id=strategy_id,
            family=logged_family,
            purpose=purpose,
            source=LOGGED_SOURCE,
            citation=None,
            note=note,
        )

    entry = FAMILY_BY_STRATEGY_ID.get(strategy_id)
    if entry is not None:
        return FamilyResolution(
            strategy_id=strategy_id,
            family=entry.family,
            purpose=entry.purpose,
            source=CURATED_MAP_SOURCE,
            citation=entry.citation,
            note=None,
        )

    return FamilyResolution(
        strategy_id=strategy_id,
        family=strategy_id,
        purpose=RESEARCH_PURPOSE,
        source=UNMAPPED_SOURCE,
        citation=None,
        note=(
            f"strategy_id={strategy_id!r} has no curated family in research/lineage.py and its records carry no "
            f"{STRATEGY_FAMILY_KEY!r} field; counted as its own single-member family rather than guessing one. "
            "Add a curated entry (with a .planning/ citation), or pass strategy_family= when running it."
        ),
    )
