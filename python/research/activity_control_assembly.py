"""Pure BR normal-input connection, removing only the activity predicate.

The caller supplies an independently adjudicated bounded positive record and
exact typed inputs. BI numeric failures alone cannot supply that record. This
module reads nothing and makes no source-truth, selection, holding, accounting
or performance claim. Historical continuity remains inferred and modeled
publication remains assumed, with actual unknowns preserved in the audit.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from research.activity_normal_assembly import assemble_window
from research.activity_screen import PublishedValue, ScreenObservation


def assemble_control_window(
    calendar: tuple[date, ...], formation: date, code: str, isin: str,
    listing_date: date, observations: Mapping[date, ScreenObservation],
    capitalization: PublishedValue, liquidity: Mapping[date, ScreenObservation],
    accepted_record: dict, params: dict,
) -> dict:
    """Retain the original screen, then remove its activity-only exclusion.

    A control ``eligible`` flag is a normal-input disposition, never a target or
    filled entry. The original threshold is still evaluated and its candidate,
    dates, diagnostics and flags remain unchanged under ``activity_screen``.
    All non-activity conditions and both policies' cutoff checks are shared.
    """
    threshold = params.get("threshold") if isinstance(params, dict) else None
    if (not isinstance(params, dict) or type(params.get("lookback")) is not int
            or params["lookback"] != 60 or isinstance(threshold, bool)
            or not isinstance(threshold, (int, Decimal))
            or (isinstance(threshold, Decimal) and not threshold.is_finite())
            or threshold != 3):
        raise ValueError("BR requires the unchanged 60-observation lookback and exact threshold 3")
    audit = assemble_window(
        calendar, formation, code, isin, listing_date, observations, capitalization,
        liquidity, accepted_record, params, evidence_label="BR control",
    )
    arms = {}
    for name, arm in audit["arms"].items():
        reasons = [reason for reason in arm["exclusion_reasons"]
                   if reason != "activity_predicate_failed"]
        arms[name] = {
            "session_lag": arm["session_lag"], "decision_on": arm["decision_on"],
            "selection_at": arm["selection_at"], "eligible": not reasons,
            "exclusion_reasons": reasons,
        }
    if any(arms["D1"][key] != arms["D2"][key] for key in ("eligible", "exclusion_reasons")):
        raise ValueError("paired control inputs unexpectedly changed eligibility")
    return {
        "activity_screen": audit, "control_arms": arms,
        "classification_evidence_level": "inferred",
        "historical_publication_evidence_level": "assumed",
        "paired_control_eligibility_equal": True,
        "eligibility_meaning": "normal-input eligible only; no portfolio target or filled entry",
    }
