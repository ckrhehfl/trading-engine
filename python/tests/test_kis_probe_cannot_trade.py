"""`data.kis_probe` must stay incapable of placing an order.

That is not a stylistic preference. Task MS-A §7 decision 2 proposed
running a *credentialed* read-only client against KIS -- possibly against
the production host -- and the whole basis for that proposal is that the
module structurally cannot submit an order. A docstring asserting it is
worth nothing; CLAUDE.md's change-check record is explicit that "a
verification that shares an assumption with its implementation confirms
the misunderstanding rather than catching it", and that three inert
guards in this repo all read fine.

So the rule is extracted as a function, tested against a **known-bad**
fixture that must be rejected, and only then applied to the real module.
Without the known-bad case this file would pass even if the rule matched
nothing at all -- the exact defect found on PR #150.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[1] / "data" / "kis_probe.py"

# KIS's own naming: order submission and cancellation live under
# /trading/, and their TR ids are (V)TTO/(V)TTC-shaped. The Java adapter's
# real constants are the reference -- ORDER_PATH, ORDER_CANCEL_PATH,
# VTTO1101U, VTTO1103U.
FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"/trading/", "a KIS trading path"),
    (r"\border-rvsecncl\b", "the order-cancel endpoint"),
    (r"[\"']V?TT[OC]\d{4}[A-Z][\"']", "a trading TR id"),
    (r"\bCANO\b", "an account number parameter"),
    (r"\bACNT_PRDT_CD\b", "an account product code parameter"),
)


def order_capable_findings(source: str) -> list[str]:
    """Every reason `source` looks able to trade. Empty means it cannot.

    Comments and docstrings are stripped first: this module's own prose
    necessarily discusses orders in order to explain why it has none, and a
    rule that cannot tell prose from code would either fire on the
    docstring or be weakened until it fired on nothing.
    """
    code = _strip_prose(source)
    return [
        f"{label} ({pattern!r})"
        for pattern, label in FORBIDDEN_PATTERNS
        if re.search(pattern, code)
    ]


def _strip_prose(source: str) -> str:
    without_strings = re.sub(r'"""(?:.|\n)*?"""', '""', source)
    without_strings = re.sub(r"'''(?:.|\n)*?'''", "''", without_strings)
    return "\n".join(
        line.split("#", 1)[0] for line in without_strings.splitlines()
    )


KNOWN_BAD = '''
"""A probe that talks about not ordering, and then does."""
ORDER_PATH = "/uapi/domestic-futureoption/v1/trading/order"
TR_ID_ORDER = "VTTO1101U"

def submit(token, cano):
    return _post_json(ORDER_PATH, {"CANO": cano}, {"tr_id": TR_ID_ORDER})
'''

KNOWN_GOOD = '''
"""Quotations only. Never places an order -- see MS-A section 7."""
DAILY_ITEM_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
TR_DAILY_ITEM = "FHKST03010100"

def fetch(token, code):
    return _get_json(DAILY_ITEM_PATH, {"FID_INPUT_ISCD": code}, {"tr_id": TR_DAILY_ITEM})
'''


def test_the_rule_rejects_a_module_that_can_trade():
    """The guard-against-a-vacuous-guard. If this passes while
    `order_capable_findings` matches nothing, the real assertion below is
    meaningless."""
    findings = order_capable_findings(KNOWN_BAD)
    assert findings, "the rule failed to notice a module that submits orders"
    assert len(findings) >= 3, f"expected the path, the TR id and CANO; got {findings}"


def test_the_rule_accepts_a_quotation_only_module():
    assert order_capable_findings(KNOWN_GOOD) == []


def test_the_rule_is_not_fooled_by_prose_alone():
    """Discussing orders in a docstring must not trip it -- otherwise the
    only way to pass is to stop explaining the constraint."""
    prose_only = '"""This module never calls /trading/order and sends no CANO."""\nX = 1\n'
    assert order_capable_findings(prose_only) == []


def test_kis_probe_cannot_place_an_order():
    findings = order_capable_findings(PROBE.read_text(encoding="utf-8"))
    assert findings == [], (
        "data/kis_probe.py has gained an order-capable surface: "
        + "; ".join(findings)
        + ". MS-A section 7's approval for a credentialed KIS client rests on "
        "this module being quotation-only."
    )


def test_kis_probe_does_not_import_the_trading_path():
    source = PROBE.read_text(encoding="utf-8")
    for banned in ("from live", "import live", "from execution", "import oms"):
        assert banned not in source, f"kis_probe imports {banned!r}"


@pytest.mark.parametrize("secret", ["KIS_APP_KEY", "KIS_APP_SECRET"])
def test_credentials_are_read_from_the_environment_and_never_written(secret):
    """Presence-and-length only, per CLAUDE.md's handling after the CRLF
    credential incident, where an exception message embedded a real key."""
    source = PROBE.read_text(encoding="utf-8")
    assert f'os.environ.get("{secret}")' in source
    # The value must never reach a print/format slot; only len() may.
    assert f"{{{secret.lower()}}}" not in source
