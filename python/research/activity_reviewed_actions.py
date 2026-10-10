"""Pure decoder for caller-pinned reviewed interval and action declarations.

There is no IO, CLI, replay, or source-truth certification here. ``expected_windows``
is the caller's normalized union of possible holding intervals, not realized lots:
each row has exactly code/isin/start/end, with inclusive ISO dates. The caller
separately establishes these identities and pins the declaration, evidence bytes,
and one coherent quote snapshot, including any successor series.

Coverage has schema/status, activity_snapshot_sha256, quote_snapshot_sha256,
required_scope_sha256, calendar_sha256, windows/events/noops, and unresolved=[].
Windows have code/isin/start/end/review_state/event_ids/evidence_refs. Only an
explicit complete interval finding covers a window; reviewed no-ops do not.
Complete findings retain their declared confirmed or inferred evidence strength.
``basis_evidence`` has references and bases maps. A reference binds raw/review
SHA-256, a document/section coordinate, code/ISIN/date interval, reviewed_on,
finding, and event_ids. Basis rows also declare their inspected last-observable,
availability, or last-trading role; a generic assumed price pair is insufficient.
Checking those links does not inspect either source body
or establish that its human declaration is true. Dates and Decimal text are
decoded exactly, then the existing accounting constructors enforce their rules.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

from research.activity_accounting import FinalCashDistribution, PriceBasis
from research.activity_book import CompulsoryStockExchange, FinalCashPayment


def _fields(value: object, names: str) -> dict:
    if type(value) is not dict or set(value) != set(names.split()):
        raise ValueError("unexpected declaration fields")
    return value


def _text(value: object) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError("expected nonempty exact text")
    return value


def _hash(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("expected SHA-256")
    return value


def _day(value: object) -> date:
    try:
        parsed = date.fromisoformat(_text(value))
    except (ValueError, TypeError) as exc:
        raise ValueError("expected ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError("expected canonical ISO date")
    return parsed


def _number(value: object) -> Decimal:
    try:
        text = _text(value)
        if re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", text) is None:
            raise ValueError("expected exact Decimal text")
        parsed = Decimal(text)
    except (ValueError, InvalidOperation) as exc:
        raise ValueError("expected finite Decimal text") from exc
    if not parsed.is_finite():
        raise ValueError("expected finite Decimal text")
    return parsed


def _identity(row: dict) -> tuple[str, str]:
    code, isin = row["code"], row["isin"]
    if (type(code) is not str or re.fullmatch(r"[0-9A-Z]{6}", code) is None or
            type(isin) is not str or re.fullmatch(r"[A-Z]{2}[0-9A-Z]{9}[0-9]", isin) is None):
        raise ValueError("expected exact code and ISIN")
    return code, isin


def _ids(value: object) -> tuple[str, ...]:
    if type(value) is not list:
        raise ValueError("expected identifier list")
    parsed = tuple(_text(item) for item in value)
    if len(set(parsed)) != len(parsed):
        raise ValueError("duplicate identifier")
    return parsed


def reviewed_actions(
    coverage: dict,
    basis_evidence: dict,
    *,
    expected_windows: tuple[dict, ...],
    calendar: tuple[date, ...],
    activity_snapshot_sha256: str,
    quote_snapshot_sha256: str | None,
    read_scope_sha256: str,
) -> tuple[tuple[CompulsoryStockExchange | FinalCashPayment, ...], dict[str, tuple[str, ...]]]:
    """Validate declared coverage and decode supported events without reading data.

    Original requirements may overlap; declarations must cover their exact union
    once, including intervening non-session days. Relevant exchanges add successor
    coverage from effectiveness through the same horizon and quote requirements
    from availability, including the availability basis anchor. Chained exchanges
    are processed in chronological, stable input order. A later delivery may
    remain pending at the horizon, but its basis must be on the supplied calendar.
    A pending entitlement cannot undergo another exchange before or on delivery,
    or qualify for final cash with a record date before delivery.
    One quote snapshot and the existing code-keyed lot model require one ISIN
    per code, including disjoint original periods and pending successors.
    Final-payment relevance and coverage use the inclusive last-trading-to-paid
    lifetime. Known actual payment may be after the horizon/calendar; its evidence
    date is not a quote session or an instruction to recognize cash early.

    Unknown/gapped/conflicting coverage, unsupported/proposed events, mismatched
    evidence coordinates, and absent basis are errors. An empty requirement has
    no events, references, bases, or invented quote hash. Returned objects express
    the supplied reviewed declarations, never a machine certificate of sources.
    """
    _hash(activity_snapshot_sha256)
    _hash(read_scope_sha256)
    if (type(calendar) is not tuple or not calendar or
            any(type(day) is not date for day in calendar) or
            any(left >= right for left, right in zip(calendar, calendar[1:]))):
        raise ValueError("expected immutable ordered session calendar")
    if type(expected_windows) is not tuple:
        raise ValueError("expected immutable requirement windows")
    declaration = _fields(coverage, "schema status activity_snapshot_sha256 quote_snapshot_sha256 "
                          "required_scope_sha256 calendar_sha256 windows events noops unresolved")
    calendar_pin = sha256(json.dumps([day.isoformat() for day in calendar],
                                    separators=(",", ":")).encode()).hexdigest()
    if (declaration["schema"] != "activity-reviewed-actions-v1" or
            declaration["status"] != "reviewed_complete" or
            declaration["activity_snapshot_sha256"] != activity_snapshot_sha256 or
            declaration["quote_snapshot_sha256"] != quote_snapshot_sha256 or
            declaration["required_scope_sha256"] != read_scope_sha256 or
            declaration["calendar_sha256"] != calendar_pin or
            type(declaration["unresolved"]) is not list or declaration["unresolved"]):
        raise ValueError("inconsistent scope or unresolved declaration")
    for name in ("windows", "events", "noops"):
        if type(declaration[name]) is not list:
            raise ValueError("expected declaration list")
    evidence = _fields(basis_evidence, "references bases")
    refs, bases = evidence["references"], evidence["bases"]
    if type(refs) is not dict or type(bases) is not dict:
        raise ValueError("expected evidence maps")

    # Ordinal cells retain calendar-day action coverage, not just price sessions.
    required: dict[tuple[str, int], str] = {}
    issue_isins: dict[str, str] = {}

    def bind_issue(code: str, isin: str) -> None:
        if code in issue_isins and issue_isins[code] != isin:
            raise ValueError("conflicting required issue identity: code reuse unsupported by code-keyed lots")
        issue_isins[code] = isin

    def interval(row: dict, *, bounded: bool = True) -> tuple[str, str, date, date]:
        code, isin = _identity(row)
        start, end = _day(row["start"]), _day(row["end"])
        if start > end or (bounded and not calendar[0] <= start <= end <= calendar[-1]):
            raise ValueError("interval outside supplied calendar")
        return code, isin, start, end

    def require(code: str, isin: str, start: date, end: date) -> None:
        bind_issue(code, isin)
        for ordinal in range(start.toordinal(), end.toordinal() + 1):
            key = code, ordinal
            if key in required and required[key] != isin:
                raise ValueError("conflicting required issue identity")
            required[key] = isin

    for raw in expected_windows:
        row = _fields(raw, "code isin start end")
        code, isin, start, end = interval(row)
        if start not in calendar or end not in calendar:
            raise ValueError("requirement endpoints must be sessions")
        require(code, isin, start, end)
    if not required:
        if (quote_snapshot_sha256 is not None or declaration["windows"] or
                declaration["events"] or declaration["noops"] or refs or bases):
            raise ValueError("empty requirement cannot invent inputs or events")
        return (), {}
    _hash(quote_snapshot_sha256)

    used_refs: set[str] = set()

    def link(ref_id: object, *, code: str, isin: str, start: date, end: date,
             finding: str, event_ids: tuple[str, ...]) -> str:
        ref_id = _text(ref_id)
        if ref_id not in refs:
            raise ValueError("missing reviewed evidence reference")
        ref = _fields(refs[ref_id], "raw_sha256 review_sha256 coordinate code isin start end "
                      "reviewed_on finding event_ids")
        _hash(ref["raw_sha256"])
        _hash(ref["review_sha256"])
        coordinate = _text(ref["coordinate"])
        # Review/payment evidence can post-date the quote evaluation calendar.
        rcode, risin, rstart, rend = interval(ref, bounded=False)
        if ((rcode, risin) != (code, isin) or not rstart <= start <= end <= rend or
                _day(ref["reviewed_on"]) < rend or ref["finding"] != finding or
                set(_ids(ref["event_ids"])) != set(event_ids)):
            raise ValueError("inconsistent reviewed evidence coordinates or finding")
        used_refs.add(ref_id)
        return (f"review_ref:{ref_id};coordinate:{coordinate};raw_sha256:{ref['raw_sha256']};"
                f"review_sha256:{ref['review_sha256']}")

    used_bases: set[str] = set()

    def basis(basis_id: object, code: str, isin: str, role: str) -> PriceBasis:
        basis_id = _text(basis_id)
        if basis_id not in bases:
            raise ValueError("missing successor or original price basis")
        row = _fields(bases[basis_id], "code isin session role raw_close adjusted_close "
                      "quote_snapshot_sha256 evidence_ref")
        session = _day(row["session"])
        if (_identity(row) != (code, isin) or row["role"] != role or session not in calendar or
                row["quote_snapshot_sha256"] != quote_snapshot_sha256):
            raise ValueError("inconsistent basis identity, session or quote snapshot")
        source = link(row["evidence_ref"], code=code, isin=isin, start=session, end=session,
                      finding="verified_" + role + "_basis", event_ids=())
        parsed = PriceBasis(code, session, _number(row["raw_close"]),
                            _number(row["adjusted_close"]), quote_snapshot_sha256, source)
        used_bases.add(basis_id)
        return parsed

    raw_events = []
    event_ids: set[str] = set()
    for raw in declaration["events"]:
        if type(raw) is not dict or raw.get("kind") not in ("compulsory_stock_exchange", "final_cash_payment"):
            raise ValueError("unsupported action kind")
        stock = raw["kind"] == "compulsory_stock_exchange"
        fields = ("successor_code successor_isin ratio old_basis_id new_basis_id effective_on available_on"
                  if stock else "last_trading_on record_on paid_on net_cash_per_raw_share basis_id")
        row = _fields(raw, "event_id kind status code isin evidence_ref " + fields)
        event_id = _text(row["event_id"])
        if event_id in event_ids:
            raise ValueError("duplicate event id")
        event_ids.add(event_id)
        on = _day(row["effective_on"] if stock else row["paid_on"])
        raw_events.append((on, row))

    decoded = []
    coordinates: dict[str, tuple[str, str, date, date]] = {}
    identities_with_event: set[tuple[str, str]] = set()
    successor: dict[str, set[date]] = {}
    successor_identities: dict[str, tuple[str, str]] = {}
    for on, row in sorted(raw_events, key=lambda item: item[0]):
        code, isin = _identity(row)
        first_effect = (on if row["kind"] == "compulsory_stock_exchange"
                        else _day(row["last_trading_on"]))
        if not any(required_code == code and identity == isin and
                   first_effect.toordinal() <= ordinal <= on.toordinal()
                   for (required_code, ordinal), identity in required.items()):
            raise ValueError("event lifetime outside required issue interval")
        if (code, isin) in identities_with_event:
            raise ValueError("conflicting full-issue events")
        identities_with_event.add((code, isin))
        event_id = row["event_id"]
        if row["kind"] == "compulsory_stock_exchange":
            if row["status"] != "verified_effective":
                raise ValueError("proposed or unverified exchange")
            new_code, new_isin = _identity({"code": row["successor_code"], "isin": row["successor_isin"]})
            bind_issue(new_code, new_isin)
            available = _day(row["available_on"])
            source = link(row["evidence_ref"], code=code, isin=isin, start=on, end=on,
                          finding="verified_compulsory_stock_exchange", event_ids=(event_id,))
            event = CompulsoryStockExchange(event_id, _number(row["ratio"]),
                basis(row["old_basis_id"], code, isin, "last_observable_pre_event"),
                basis(row["new_basis_id"], new_code, new_isin, "successor_availability"),
                on, available, source)
            # Keep each disjoint required interval; do not fill unrelated gaps.
            relevant = sorted(ordinal for (required_code, ordinal), identity in required.items()
                              if required_code == code and identity == isin and ordinal >= on.toordinal())
            for ordinal in relevant:
                require(new_code, new_isin, date.fromordinal(ordinal), date.fromordinal(ordinal))
            successor_identities[event_id] = new_code, new_isin
            days = successor.setdefault(new_code, set())
            days.add(available)  # basis anchor also required for a pending delivery
            days.update(day for day in calendar if day >= available and day.toordinal() in relevant)
        else:
            if row["status"] != "verified_paid":
                raise ValueError("proposed or unverified payment")
            record = _day(row["record_on"])
            source = link(row["evidence_ref"], code=code, isin=isin, start=on, end=on,
                          finding="verified_final_cash_payment", event_ids=(event_id,))
            event = FinalCashPayment(event_id, FinalCashDistribution(code,
                first_effect, record, on,
                _number(row["net_cash_per_raw_share"]),
                basis(row["basis_id"], code, isin, "last_trading"), source))
        decoded.append(event)
        coordinates[event_id] = code, isin, first_effect, on
    # Check the complete declared graph, independent of decode order and lifetime
    # overlap: a parent cannot deliver an issue after that exact issue's child
    # conversion/record boundary. Code reuse was rejected before object creation.
    for parent in decoded:
        if not isinstance(parent, CompulsoryStockExchange):
            continue
        for child in decoded:
            if coordinates[child.event_id][:2] != successor_identities[parent.event_id]:
                continue
            if isinstance(child, CompulsoryStockExchange):
                if parent.available_on >= child.effective_on:
                    raise ValueError("unsupported re-exchange of a pending entitlement")
            elif parent.available_on > child.distribution.record_on:
                raise ValueError("unsupported final payout eligibility before parent delivery")
    if used_bases != set(bases):
        raise ValueError("unrelated or unused basis")

    covered: set[tuple[str, int]] = set()
    for raw in declaration["windows"]:
        row = _fields(raw, "code isin start end review_state event_ids evidence_refs")
        code, isin, start, end = interval(row)
        cells = {(code, ordinal) for ordinal in range(start.toordinal(), end.toordinal() + 1)}
        if any(required.get(cell) != isin for cell in cells) or covered.intersection(cells):
            raise ValueError("overlapping or out-of-scope coverage")
        expected_ids = {event_id for event_id, (ec, ei, first, last) in coordinates.items()
                        if (ec, ei) == (code, isin) and start <= last and first <= end}
        ids = _ids(row["event_ids"])
        state = "reviewed_supported_events" if expected_ids else "reviewed_no_event"
        if row["review_state"] != state or set(ids) != expected_ids:
            raise ValueError("unknown, conflicting or incomplete action finding")
        links = _ids(row["evidence_refs"])
        if not links:
            raise ValueError("complete interval requires explicit reviewed evidence")
        findings = (("confirmed_supported_events_complete", "inferred_supported_events_complete")
                    if expected_ids else ("confirmed_no_compulsory_action", "inferred_no_compulsory_action"))
        for ref_id in links:
            ref = refs.get(ref_id)
            if type(ref) is not dict or ref.get("finding") not in findings:
                raise ValueError("unsupported complete interval evidence finding")
            link(ref_id, code=code, isin=isin, start=start, end=end,
                 finding=ref["finding"], event_ids=ids)
        covered.update(cells)
    if covered != set(required):
        raise ValueError("uncovered required action interval")

    for raw in declaration["noops"]:
        row = _fields(raw, "code isin on kind evidence_ref")
        code, isin = _identity(row)
        on = _day(row["on"])
        if (row["kind"] not in ("voluntary_offer", "name_change", "market_movement") or
                required.get((code, on.toordinal())) != isin):
            raise ValueError("unsupported or out-of-scope reviewed no-op")
        link(row["evidence_ref"], code=code, isin=isin, start=on, end=on,
             finding="reviewed_" + row["kind"], event_ids=())
    if used_refs != set(refs):
        raise ValueError("unrelated or unused reviewed reference")
    return tuple(decoded), {code: tuple(day.isoformat() for day in sorted(days))
                            for code, days in sorted(successor.items())}
