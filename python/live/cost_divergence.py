"""Read the accumulating record of modelled-versus-realised trading costs.

    cd python && .venv/bin/python -m live.cost_divergence
    cd python && .venv/bin/python -m live.cost_divergence --json

## What this measures, and why it is the first thing worth measuring

`.planning/agent-factory-discuss.md` §6 argues that the first thing to
build toward long-horizon operation is **live-versus-backtest divergence
tracking** — because an edge being competed away by other participants
*looks like* realised performance decaying relative to what the backtest
predicted, and because measuring it needs no agent autonomy at all.

Costs are the half of that which is measurable immediately. Returns need
many trades to say anything, and `daily-tsmom-ensemble` trades 9-17 times
a year. **Every fill, by contrast, is one cost observation.**

## The gap this closes

Until 2026-09-12 there was nothing to read. `ExchangeOrderExecutor`
computed `notional x feeBps` — the identical formula `PaperBroker` and
`backtest/fill.py` use — and the venue's own `commission` field was
parsed nowhere. Live and backtest agreed on fees **by construction**, so
the catalogue's row 2.5 ("`FEE_BPS = 5` ... CONFIRMED once — a single
data point, not a distribution") could never become more than one point.

## What it reads, and why a log rather than a new file

The Java loop emits one `cost_divergence key=value ...` line per fill
carrying a commission, into the session log that is already persisted and
already rotated. No new write path was introduced: a second durable file
would bring its own permissions, growth and failure modes, for a series
that greps cleanly out of one that already exists.

## What it deliberately does not claim

A divergence is **not** by itself evidence of an edge decaying. Fee tiers
change, venues change schedules, and a single observation is a single
observation. What this produces is the series; reading a trend into it is
a judgement that needs the count to support it, which is why `summarise`
reports `n` first and refuses to characterise a trend below
`MIN_OBSERVATIONS_FOR_TREND`.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from statistics import median

LOG_GLOB = "var/live/sessions/*.log"

# The repository root, derived from this file rather than from the working
# directory. The documented invocation is `cd python && .venv/bin/python -m
# live.cost_divergence`, and a default of "." resolves that against
# `python/`, where no session log has ever existed -- so the tool would have
# reported `n = 0` on a real machine and looked like "no data yet" rather
# than "looking in the wrong place". CodeRabbit on PR #163.
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

PREFIX = "cost_divergence "

# Below this, a "trend" is a shape read into noise. Not a statistical
# threshold -- a refusal to characterise a direction from a handful of
# points, which is the error this project has made repeatedly and
# recorded under "never conclude about a domain from one setting".
MIN_OBSERVATIONS_FOR_TREND = 20

_FIELD = re.compile(r"(\w+)=([^\s]+)")


@dataclass(frozen=True)
class Observation:
    """One fill's modelled cost against the venue's own figure."""

    client_order_id: str
    symbol: str
    notional: Decimal
    modelled_fee_bps: Decimal
    realised_fee_bps: Decimal
    divergence_bps: Decimal
    cumulative_quantity: Decimal
    observed_at: str


def parse_line(line: str) -> Observation | None:
    """One log line to an `Observation`, or `None` if it is not one.

    Tolerant by design: a truncated or interleaved line is skipped rather
    than raising. A log is a shared, concurrently-written surface, and a
    reader that dies on one malformed line loses the whole series with it.
    """
    start = line.find(PREFIX)
    if start < 0:
        return None
    fields = dict(_FIELD.findall(line[start + len(PREFIX) :]))
    try:
        numbers = {
            name: _finite_decimal(fields[name])
            for name in (
                "notional", "modelledFeeBps", "realisedFeeBps", "divergenceBps", "cumulativeQty"
            )
        }
        return Observation(
            client_order_id=fields["clientOrderId"],
            symbol=fields["symbol"],
            notional=numbers["notional"],
            modelled_fee_bps=numbers["modelledFeeBps"],
            realised_fee_bps=numbers["realisedFeeBps"],
            divergence_bps=numbers["divergenceBps"],
            cumulative_quantity=numbers["cumulativeQty"],
            # Validated here, not merely tolerated at sort time. An
            # unparseable stamp used to be kept and sorted last, which made
            # `summarise` read `modelled_fee_bps` and `last_observed_at` off
            # it -- one truncated line masquerading as the newest
            # observation. CodeRabbit on PR #163.
            observed_at=_valid_timestamp(fields["observedAt"]),
        )
    except (KeyError, ArithmeticError, ValueError):
        return None


def _valid_timestamp(text: str) -> str:
    """The stamp unchanged if it parses **and carries an offset**.

    An offset is required, not merely preferred: `Instant.toString()`
    always emits one, so a naive stamp means a corrupted line -- and a
    naive `datetime` compared against an aware one raises `TypeError`,
    which would take the sort and the whole report down. Verified
    directly. Raised by CodeRabbit on PR #163.
    """
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp carries no offset: {text!r}")
    return text


def _finite_decimal(text: str) -> Decimal:
    """A `Decimal` that is a real number, or `ValueError`.

    `Decimal("NaN")` and `Decimal("Infinity")` construct without complaint,
    and a single one reaching an `Observation` makes `median`, `min` and
    `max` raise `InvalidOperation` — **killing the whole report over one
    malformed line**, which is exactly what this module's tolerance is
    supposed to prevent. Verified directly rather than assumed. Raised by
    CodeRabbit on PR #163.
    """
    value = Decimal(text)
    if not value.is_finite():
        raise ValueError(f"not a finite number: {text!r}")
    return value


def read_observations(repo_root: Path | str | None = None) -> list[Observation]:
    """Every observation across every session log, oldest first.

    Deduplicated on `(clientOrderId, observedAt, cumulativeQty)`, **not on
    `clientOrderId` alone**.

    A log can be re-read and a session can be restarted onto the same
    file, so some dedup is needed or `n` gets overstated — and `n` is the
    figure every judgement here rests on. But a *partially filled* order
    produces one observation per fill, and keying on the order alone threw
    all but the first away: a real loss of data in a series whose point is
    its distribution. Raised by CodeRabbit on PR #163.

    The cumulative filled quantity is what actually separates two fills of
    one order: it is strictly increasing per fill by construction, where
    `Instant.now()` guarantees neither uniqueness nor monotonicity and so
    could collide and silently drop one. The timestamp stays in the key
    because it makes a genuine re-read identical, which is what dedup
    existed for in the first place.
    """
    root = DEFAULT_REPO_ROOT if repo_root is None else Path(repo_root)
    seen: dict[tuple[str, str, Decimal], Observation] = {}
    for path in sorted(root.glob(LOG_GLOB)):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            obs = parse_line(line)
            if obs is not None:
                seen.setdefault(
                    (obs.client_order_id, obs.observed_at, obs.cumulative_quantity), obs
                )
    return sorted(seen.values(), key=_sort_key)


def _sort_key(observation: Observation) -> tuple[int, object]:
    """Chronological order, not lexicographic.

    `Instant.toString()` omits trailing zeros from the fraction, so
    `2026-09-12T00:00:00Z` sorts *after* `2026-09-12T00:00:00.100Z` as text
    while being 100ms *earlier* in time. Verified directly. That would put
    `first_observed_at`, `last_observed_at` and the `modelled_fee_bps` taken
    from the last element out of order. An unparseable stamp sorts last
    rather than raising, keeping this module's tolerance intact.
    """
    try:
        parsed = datetime.fromisoformat(observation.observed_at.replace("Z", "+00:00"))
    except ValueError:
        return (1, observation.observed_at)
    if parsed.tzinfo is None:
        # Sorting a naive datetime beside an aware one raises TypeError, so
        # it goes in the unsortable group rather than into the comparison.
        return (1, observation.observed_at)
    return (0, parsed)


def summarise(observations: list[Observation]) -> dict:
    """What the series supports saying, and no more."""
    n = len(observations)
    summary: dict = {"n": n}
    if n == 0:
        summary["verdict"] = (
            "no observations yet -- every fill carrying a venue commission adds one"
        )
        return summary

    divergences = [o.divergence_bps for o in observations]
    realised = [o.realised_fee_bps for o in observations]
    summary["modelled_fee_bps"] = str(observations[-1].modelled_fee_bps)
    summary["realised_fee_bps_median"] = str(median(realised))
    summary["realised_fee_bps_min"] = str(min(realised))
    summary["realised_fee_bps_max"] = str(max(realised))
    summary["divergence_bps_median"] = str(median(divergences))
    summary["worst_divergence_bps"] = str(max(divergences, key=abs))
    summary["first_observed_at"] = observations[0].observed_at
    summary["last_observed_at"] = observations[-1].observed_at

    if n < MIN_OBSERVATIONS_FOR_TREND:
        summary["verdict"] = (
            f"{n} observation(s) -- reported, but too few to characterise a trend "
            f"(needs {MIN_OBSERVATIONS_FOR_TREND}). These are data points, not a direction."
        )
    elif median(divergences) > 0:
        summary["verdict"] = (
            f"realised cost runs above modelled by a median of "
            f"{median(divergences)}bps across {n} fills -- the direction that quietly "
            f"erodes a backtested edge. Worth investigating the fee assumption."
        )
    else:
        summary["verdict"] = (
            f"realised cost is at or below modelled across {n} fills -- "
            f"the cost model is not optimistic on this evidence."
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", default=None, help="defaults to the repository root")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    observations = read_observations(args.repo_root)
    summary = summarise(observations)

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"cost observations: {summary['n']}")
    for key in (
        "modelled_fee_bps",
        "realised_fee_bps_median",
        "realised_fee_bps_min",
        "realised_fee_bps_max",
        "divergence_bps_median",
        "worst_divergence_bps",
        "first_observed_at",
        "last_observed_at",
    ):
        if key in summary:
            print(f"  {key:26} {summary[key]}")
    print(f"\n{summary['verdict']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
