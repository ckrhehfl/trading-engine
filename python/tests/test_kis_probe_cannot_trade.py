"""`data.kis_probe` must stay incapable of placing an order or printing a
credential.

That is not a stylistic preference. Task MS-A §7 decision 2 proposed
running a *credentialed* read-only client against KIS, and the whole basis
for it is that the module structurally cannot submit an order. A docstring
asserting so is worth nothing; CLAUDE.md's change-check record is explicit
that "a verification that shares an assumption with its implementation
confirms the misunderstanding rather than catching it", and that three
inert guards in this repo all read fine.

So each rule is extracted as a function, fed **known-bad** fixtures it
must reject, and only then applied to the real module. Without the
known-bad cases this file would pass even if the rules matched nothing --
the exact defect found on PR #150.

**A first version of this file was itself bypassable**, found on review of
PR #164: it stripped *every* triple-quoted string before matching, so a
triple-quoted order path was invisible to it. Docstrings are now removed
by `ast`, which knows which strings are documentation and which are data,
and every other string literal is kept.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

_DATA = Path(__file__).resolve().parents[1] / "data"
PROBE = _DATA / "kis_probe.py"

# Every credentialed, read-only KIS client. `kis_klines.py` (Multi-Asset
# Task C) is held to the same contract as the probe: it authenticates with
# a real app key, so the argument that made a credentialed client
# acceptable at all applies to it identically.
CREDENTIALED_KIS_MODULES = (PROBE, _DATA / "kis_klines.py")

# KIS's own naming: order submission and cancellation live under /trading/,
# and their TR ids are (V)TTO/(V)TTC-shaped. The Java adapter's real
# constants are the reference -- ORDER_PATH, ORDER_CANCEL_PATH, VTTO1101U.
FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"/trading/", "a KIS trading path"),
    (r"order-rvsecncl", "the order-cancel endpoint"),
    (r"V?TT[OC]\d{4}[A-Z]", "a trading TR id"),
    (r"\bCANO\b", "an account number parameter"),
    (r"\bACNT_PRDT_CD\b", "an account product code parameter"),
)

# Names the probe binds credentials to.
CREDENTIAL_NAMES = frozenset({"key", "sec", "app_key", "app_secret"})


def executable_source(source: str) -> str:
    """`source` with comments and **docstrings only** removed.

    Every other string literal survives, which is the point: a docstring
    explaining that the module sends no `CANO` must not trip the rule, and
    a triple-quoted string actually *holding* an order path must.
    """
    tree = ast.parse(source)
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            doc_lines.update(range(first.value.lineno, (first.value.end_lineno or first.value.lineno) + 1))

    kept: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.start[0] in doc_lines and tok.type == tokenize.STRING:
            continue
        kept.append(tok.string)
    return "\n".join(kept)


def order_capable_findings(source: str) -> list[str]:
    """Every reason `source` looks able to trade. Empty means it cannot."""
    import re

    code = executable_source(source)
    return [
        f"{label} ({pattern!r})"
        for pattern, label in FORBIDDEN_PATTERNS
        if re.search(pattern, code)
    ]


def credential_leak_findings(source: str) -> list[str]:
    """Places a credential value could reach output.

    Checks the two sinks that actually matter here -- `print(...)` calls and
    f-strings -- for a bare credential name. `len(key)` is fine; `key` is
    not. This is narrower than full taint analysis on purpose: the contract
    being defended is "presence and length only", and that is exactly what
    this can decide.
    """
    tree = ast.parse(source)
    findings: list[str] = []

    def bare_credential_names(node: ast.AST) -> list[str]:
        out = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = sub.func
                # len(key) launders it -- do not descend into the length call.
                if isinstance(fn, ast.Name) and fn.id == "len":
                    continue
            if isinstance(sub, ast.Name) and sub.id in CREDENTIAL_NAMES:
                out.append(sub.id)
        return out

    def scan(node: ast.AST, sink: str) -> None:
        # Re-walk manually so len() subtrees can be skipped.
        stack = [node]
        while stack:
            cur = stack.pop()
            if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Name) and cur.func.id == "len":
                continue
            if isinstance(cur, ast.Name) and cur.id in CREDENTIAL_NAMES:
                findings.append(f"{cur.id!r} reaches {sink} at line {getattr(cur, 'lineno', '?')}")
            stack.extend(ast.iter_child_nodes(cur))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            for arg in node.args:
                scan(arg, "print()")
        elif isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    scan(value.value, "an f-string")
    return findings


# --------------------------------------------------------------- fixtures

KNOWN_BAD_ORDER = '''
"""A probe that talks about not ordering, and then does."""
ORDER_PATH = "/uapi/domestic-futureoption/v1/trading/order"
TR_ID_ORDER = "VTTO1101U"

def submit(token, cano):
    return _post_json(ORDER_PATH, {"CANO": cano}, {"tr_id": TR_ID_ORDER})
'''

# The bypass that defeated this file's first version: the order path hides
# in a triple-quoted string, which a blanket triple-quote strip removes.
KNOWN_BAD_TRIPLE_QUOTED = '''
"""Quotations only, honest."""
X = 1
ORDER_PATH = """/uapi/domestic-futureoption/v1/trading/order"""

def submit(token):
    return _post_json(ORDER_PATH, {}, {})
'''

KNOWN_BAD_PRINTS_CREDENTIAL = '''
"""Prints what it must not."""
def main(key, sec):
    print(key)
    print(f"secret is {sec}")
'''

KNOWN_GOOD = '''
"""Quotations only. Never places an order -- see MS-A section 7."""
DAILY_ITEM_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
TR_DAILY_ITEM = "FHKST03010100"

def main(key, sec):
    print(f"credentials: len={len(key)}, len={len(sec)}")
    return _get_json(DAILY_ITEM_PATH, {}, {"tr_id": TR_DAILY_ITEM})
'''


# ------------------------------------------------------------ rule checks


def test_the_rule_rejects_a_module_that_can_trade():
    findings = order_capable_findings(KNOWN_BAD_ORDER)
    assert findings, "the rule failed to notice a module that submits orders"
    assert len(findings) >= 3, f"expected the path, the TR id and CANO; got {findings}"


def test_the_rule_rejects_an_order_path_hidden_in_a_triple_quoted_string():
    """The bypass found on review of PR #164. This is the regression."""
    findings = order_capable_findings(KNOWN_BAD_TRIPLE_QUOTED)
    assert findings, "a triple-quoted order path slipped past the rule"


def test_the_rule_accepts_a_quotation_only_module():
    assert order_capable_findings(KNOWN_GOOD) == []


def test_the_rule_is_not_fooled_by_prose_alone():
    """Discussing orders in a docstring must not trip it -- otherwise the
    only way to pass is to stop explaining the constraint."""
    prose_only = '"""This module never calls /trading/order and sends no CANO."""\nX = 1\n'
    assert order_capable_findings(prose_only) == []


def test_the_credential_rule_rejects_a_module_that_prints_one():
    findings = credential_leak_findings(KNOWN_BAD_PRINTS_CREDENTIAL)
    assert len(findings) >= 2, f"expected both the print and the f-string; got {findings}"


def test_the_credential_rule_allows_length_only():
    assert credential_leak_findings(KNOWN_GOOD) == []


# ------------------------------------------------------- the real module


@pytest.mark.parametrize("module", CREDENTIALED_KIS_MODULES, ids=lambda p: p.name)
def test_a_credentialed_kis_module_cannot_place_an_order(module):
    findings = order_capable_findings(module.read_text(encoding="utf-8"))
    assert findings == [], (
        f"data/{module.name} has gained an order-capable surface: "
        + "; ".join(findings)
        + ". MS-A section 7's approval for a credentialed KIS client rests on "
        "these modules being quotation-only."
    )


@pytest.mark.parametrize("module", CREDENTIALED_KIS_MODULES, ids=lambda p: p.name)
def test_a_credentialed_kis_module_never_prints_a_credential(module):
    findings = credential_leak_findings(module.read_text(encoding="utf-8"))
    assert findings == [], (
        f"a credential value reaches an output sink in {module.name}: " + "; ".join(findings)
    )


@pytest.mark.parametrize("module", CREDENTIALED_KIS_MODULES, ids=lambda p: p.name)
def test_a_credentialed_kis_module_does_not_import_the_trading_path(module):
    source = module.read_text(encoding="utf-8")
    for banned in ("from live", "import live", "from execution", "import oms"):
        assert banned not in source, f"{module.name} imports {banned!r}"


def test_every_credentialed_kis_module_exists():
    """Guards the guard: a renamed or deleted module must not silently
    reduce this file to testing nothing."""
    for module in CREDENTIALED_KIS_MODULES:
        assert module.is_file(), f"{module} is listed but does not exist"
    assert len(CREDENTIALED_KIS_MODULES) >= 2


@pytest.mark.parametrize("secret", ["KIS_APP_KEY", "KIS_APP_SECRET"])
def test_credentials_come_from_the_environment(secret):
    assert f'os.environ.get("{secret}")' in PROBE.read_text(encoding="utf-8")
