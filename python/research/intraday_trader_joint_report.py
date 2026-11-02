"""Thin EE day framing and independent saved joint-account reconstruction.

Caller-owned authenticated raw streams only; no ranking, paths, actual runner,
transport or storage framework. Exhaust read_report through gzip EOF and pins.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
import gzip
import hashlib
from typing import BinaryIO
import zlib

from research import intraday_ed_snapshot as codec
from research import intraday_ed_sources as sources
from research import intraday_campaign_independent as arithmetic
from research import intraday_trader_joint_account as account
from research import intraday_trader_joint_reference as reference
from research import intraday_trader_joint_inputs as inputs
from research import intraday_trader_report as framing


SCHEMA = "EE-original-population-joint-v1"
BINDING_KEYS = framing.BINDING_KEYS
MAX_COMPRESSED, MAX_DECODED, MAX_LINE = framing.MAX_COMPRESSED, framing.MAX_DECODED, framing.MAX_LINE
ReportRefusal, ReportDigest, _Sink = framing.ReportRefusal, framing.ReportDigest, framing._Sink
_need, _pin = framing._need, framing._pin
ARMS = {"JOINT": ("A", "B"), "A_ALLOCATION_CONTROL": ("A",), "B_ALLOCATION_CONTROL": ("B",)}
SCOPE = ("archived nominal price-unit counterfactual; full original-population uncertainty retained separately "
         "from observed-candidate conditional account; no physical-share/KRW/broker-fill/investor-return/edge/promotion certification")
SPARSE = "flat event-free minute snapshots omitted; every held minute, opening, closing and entry attempt evaluated"


def scenario_keys():
    return tuple((clock, cost, arm) for clock in ("S1", "S2") for cost in (33, 43) for arm in ARMS)


def scenario_name(clock, cost, arm):
    return f"{clock}|{cost}|{arm}"


@dataclass(frozen=True)
class AccountArm:
    clock: str
    cost_bps: int
    arm: str
    result: account.AccountResult


@dataclass(frozen=True)
class DayVerification:
    verified: bool
    account_scenarios: int
    claim: str = "fresh raw-decoded causal candidates and producer-independent continuous account mathematics"


@dataclass(frozen=True)
class DayRecord:
    day: str
    coordinates: tuple[inputs.CoordinateSupport, ...]
    accounts: tuple[AccountArm, ...]
    verification: DayVerification


def _header(universe, binding, geometry, invented_only, selected_pair):
    framing._population(universe, geometry)
    inputs.followup.population(universe)
    inputs.selected_pair(selected_pair)
    _need(type(invented_only) is bool, "joint_invented_flag_required")
    return dict(kind="header", schema=SCHEMA, binding=framing._binding(binding), geometry=dict(geometry),
        invented_only=invented_only, selected_pair=list(selected_pair), calendar=codec.encode(universe),
        arms={key: list(value) for key, value in ARMS.items()}, mode="A+B", scope=SCOPE,
        idle_minutes_compressed=True, sparse_snapshots=SPARSE,
        selection_scope="caller freezes/authenticates selected distinct A/B policies; this module does no ranking")


def decode_day(record):
    _need(type(record) is dict and set(record) == {"kind", "payload"} and record["kind"] == "day",
          "joint_day_fields_differ")
    return codec._decode(DayRecord, record["payload"])


class _Summary:
    """Bounded aggregate counts and terminal states; no whole-period NAV panel."""
    def __init__(self):
        self.candidates = {clock: dict(A=0, B=0) for clock in ("S1", "S2")}
        self.rows = {scenario_name(*key): dict(original_unknown_opportunities=0, missing_price_targets=0,
            entries=0, refusals={}, exit_unknown=0, assumed_cost=Fraction(), maximum_held_lots=0,
            full_original_population_status="identified_under_supplied_causal_support",
            observed_candidate_status="identified", final_nav=None, original_population_return=None,
            cash=account.cash.INITIAL_CAPITAL, unresolved_lots=[], unknown_at=None,
            maximum_drawdown=Fraction(), entry_notional=Fraction(),
            maximum_observed_held_value=Fraction(), maximum_observed_exposure=Fraction(),
            exposure_scope="known NAV prefix only; no forward-fill after account unknown") for key in scenario_keys()}

    def add(self, day):
        unknown = {clock: dict(A=0, B=0) for clock in ("S1", "S2")}
        missing = dict(S1=0, S2=0)
        for coordinate in day.coordinates:
            for target in coordinate.targets:
                clock = target.scenario
                missing[clock] += coordinate.price_chunk_missing
                for slot in coordinate.slots:
                    for leg in slot.legs:
                        self.candidates[clock][slot.slot] += leg.candidate is not None
                        unknown[clock][slot.slot] += (leg.state in ("unknown", "invalid") or leg.eligibility == "unknown")
        for item in day.accounts:
            row = self.rows[scenario_name(item.clock, item.cost_bps, item.arm)]
            row["original_unknown_opportunities"] += sum(unknown[item.clock][slot] for slot in ARMS[item.arm])
            row["missing_price_targets"] += missing[item.clock]
            result, state = item.result, item.result.state
            if row["original_unknown_opportunities"]:
                row["full_original_population_status"] = "unidentified"
            held = len(state.book.lots) - sum(e.quantity > 0 and e.kind == "entry" for e in result.events) \
                + sum(e.quantity > 0 and e.kind == "exit" for e in result.events)
            for event in result.events:
                if event.kind == "entry":
                    if event.quantity:
                        row["entries"] += 1
                        row["assumed_cost"] += event.assumed_cost
                        row["entry_notional"] += -event.cash_delta - event.assumed_cost
                        held += 1
                    else:
                        framing._reason(row["refusals"], event.reason)
                elif event.kind == "exit":
                    if event.quantity:
                        held -= 1
                    elif event.reason == "exit_price_unknown":
                        row["exit_unknown"] += 1
                row["maximum_held_lots"] = max(row["maximum_held_lots"], held)
            row["observed_candidate_status"] = "unidentified" if state.book.indeterminate else "identified"
            row["cash"], row["unknown_at"] = state.book.cash, state.unknown_at
            row["unresolved_lots"] = list(state.book.lots)
            row["maximum_drawdown"] = state.maximum_drawdown
            for snapshot in result.snapshots:
                held_value = snapshot.nav - snapshot.cash
                row["maximum_observed_held_value"] = max(row["maximum_observed_held_value"], held_value)
                row["maximum_observed_exposure"] = max(row["maximum_observed_exposure"], held_value / snapshot.nav)
            row["final_nav"] = None if state.book.indeterminate else result.snapshots[-1].nav
            row["original_population_return"] = (row["final_nav"] / state.book.initial_capital - 1
                if row["final_nav"] is not None and not row["original_unknown_opportunities"] else None)

    def finish(self):
        return codec.encode(self.rows)


class _Order:
    def __init__(self, *, summarize=True):
        self.header = None
        self.days = iter(())
        self.groups = None
        self.summary = _Summary()
        self.counts = dict(days=0, targets=dict(S1=0, S2=0), union_coordinates=0)
        self.prefix, self.done, self.summarize = hashlib.sha256(), False, summarize

    def accept(self, record, raw, *, typed: DayRecord | None = None):
        _need(type(record) is dict and not self.done, "joint_record_after_footer")
        decoded = None
        if self.header is None:
            _need(record.get("kind") == "header" and "calendar" in record, "joint_header_required")
            universe = codec._decode(sources.UniverseCalendar, record["calendar"])
            expected = _header(universe, record.get("binding"), record.get("geometry"),
                               record.get("invented_only"), tuple(record.get("selected_pair", ())))
            _need(codec.canonical(record) == codec.canonical(expected), "joint_header_differs")
            self.groups = inputs.followup.population(universe)
            self.days, self.header = iter(universe.sessions), record
        elif record.get("kind") == "day":
            # The generator owns this just-encoded immutable value. The writer
            # still closes/decodes its wire payload, and the saved verifier uses
            # a fresh raw reconstruction; this avoids a producer-only duplicate.
            decoded = decode_day(record) if typed is None else typed
            _need(type(decoded) is DayRecord, "joint_typed_day_required")
            _need(decoded.day == next(self.days, None), "joint_calendar_day_order_differs")
            expected = tuple((symbol, tuple(self.groups[day, symbol])) for day, symbol in sorted(self.groups)
                             if day == decoded.day)
            _need(tuple((c.symbol, c.targets) for c in decoded.coordinates) == expected,
                  "joint_original_day_coordinates_differ")
            _need(tuple((a.clock, a.cost_bps, a.arm) for a in decoded.accounts) == scenario_keys(),
                  "joint_twelve_account_order_differs")
            _need(decoded.verification == DayVerification(True, len(scenario_keys())), "joint_inline_receipt_differs")
            for item in decoded.accounts:
                state = item.result.state
                _need(state.mode == "A+B" and state.policies == tuple(self.header["selected_pair"])
                      and state.cost_bps == item.cost_bps and item.result.idle_minutes_compressed
                      and item.result.scenario == account.CLAIM, "joint_account_contract_differs")
            for coordinate in decoded.coordinates:
                framing._metadata(coordinate.source_metadata)
                _need(tuple(s.slot for s in coordinate.slots) == ("A", "B")
                      and tuple(s.policy_id for s in coordinate.slots) == tuple(self.header["selected_pair"]),
                      "joint_slot_contract_differs")
                for target in coordinate.targets:
                    self.counts["targets"][target.scenario] += 1
            self.counts["days"] += 1
            self.counts["union_coordinates"] += len(decoded.coordinates)
            if self.summarize:
                self.summary.add(decoded)
        elif record.get("kind") == "footer":
            _need(next(self.days, None) is None, "joint_missing_calendar_day")
            expected = self.footer()
            if not self.summarize:
                expected["summary"], expected["candidate_counts"] = record.get("summary"), record.get("candidate_counts")
            _need(codec.canonical(record) == codec.canonical(expected), "joint_footer_differs")
            self.done = True
            return None
        else:
            raise ReportRefusal("joint_record_kind_differs")
        self.prefix.update(raw)
        return decoded

    def footer(self):
        return dict(kind="footer", record_counts=self.counts, candidate_counts=self.summary.candidates,
                    summary=self.summary.finish(), decoded_prefix_sha256=self.prefix.hexdigest())


def _day_arguments(day):
    parsed = date.fromisoformat(day.day)
    return inputs.Prices(day), inputs.Prices(day, opening=True), \
        {parsed: sources.calendar_source.session_open(day.day)}, {parsed: sources.calendar_source.session_close(day.day)}




def report_records(universe, reader, *, binding, geometry, invented_only, selected_pair, checkpoint):
    _need(callable(checkpoint), "joint_checkpoint_required")
    order = _Order()
    header = _header(universe, binding, geometry, invented_only, selected_pair)
    order.accept(header, codec.canonical(header) + b"\n")
    yield header
    cache = inputs.followup.DayRawCache(universe, reader)
    producer_states, reference_states = {}, {}
    for day in universe.sessions:
        checkpoint("ee_joint_day_before")
        reader.assert_unchanged()
        try:
            produced = inputs.prepare_day(universe, cache, day, selected_pair)
            independent = inputs.prepare_day(universe, cache, day, selected_pair, independent=True)
            arithmetic._equal(produced.coordinates, independent.coordinates, "joint_causal_support")
            accounts = []
            for clock, cost, arm in scenario_keys():
                key = scenario_name(clock, cost, arm)
                result = account.replay(produced.candidates(clock, ARMS[arm]), *_day_arguments(produced),
                    policies=selected_pair, cost_bps=cost, state=producer_states.get(key), compress_idle_minutes=True)
                receipt = reference.verify(result, independent.candidates(clock, ARMS[arm]), *_day_arguments(independent),
                    policies=selected_pair, cost_bps=cost, reference_state=reference_states.get(key), compress_idle_minutes=True)
                producer_states[key], reference_states[key] = result.state, receipt["reference_state"]
                accounts.append(AccountArm(clock, cost, arm, result))
            typed = DayRecord(day, produced.coordinates, tuple(accounts), DayVerification(True, len(accounts)))
            del produced, independent
        finally:
            reader.assert_unchanged()
            checkpoint("ee_joint_day_after")
        record = dict(kind="day", payload=codec.encode(typed))
        order.accept(record, codec.canonical(record) + b"\n", typed=typed)
        yield record
    footer = order.footer()
    order.accept(footer, codec.canonical(footer) + b"\n")
    yield footer


def verify_report(records, *, universe, raw_reader, binding, geometry, invented_only, selected_pair, checkpoint):
    """Rebuild from fresh raw/independent states; inline receipts are assertions."""
    expected_header = _header(universe, binding, geometry, invented_only, selected_pair)
    _need(callable(checkpoint), "joint_checkpoint_required")
    order, summary = _Order(summarize=False), _Summary()
    cache, reference_states = inputs.followup.DayRawCache(universe, raw_reader), {}
    footer = None
    raw_reader.assert_unchanged()
    try:
        for index, record in enumerate(records):
            if index == 0:
                _need(codec.canonical(record) == codec.canonical(expected_header), "joint_expected_header_differs")
            typed = order.accept(record, codec.canonical(record) + b"\n")
            if typed is not None:
                checkpoint("ee_joint_verify_before")
                raw_reader.assert_unchanged()
                day = inputs.prepare_day(universe, cache, typed.day, selected_pair, independent=True)
                arithmetic._equal(typed.coordinates, day.coordinates, "joint_saved_causal_support")
                for item, (clock, cost, arm) in zip(typed.accounts, scenario_keys(), strict=True):
                    key = scenario_name(clock, cost, arm)
                    expected = reference.reconstruct(day.candidates(clock, ARMS[arm]), *_day_arguments(day),
                        policies=selected_pair, cost_bps=cost, reference_state=reference_states.get(key), compress_idle_minutes=True)
                    reference_states[key] = expected["state"]
                    # Compare the independently owned exact state/events/NAV
                    # directly. No expected result is serialized and decoded
                    # merely to regain the already closed producer schema.
                    arithmetic._equal(item.result, expected, "joint_saved_day." + key)
                # Every field of the closed day has now been checked: original
                # date/support above, scenario order/receipt in _Order, and all
                # account fields against independent expectations. Only these
                # verified observations may enter descriptive aggregation.
                summary.add(typed)
                del day
                raw_reader.assert_unchanged()
                checkpoint("ee_joint_verify_after")
            elif record.get("kind") == "footer":
                footer = record
        _need(order.done and footer is not None, "joint_footer_missing")
        _need(codec.canonical(footer["summary"]) == codec.canonical(summary.finish())
              and codec.canonical(footer["candidate_counts"]) == codec.canonical(summary.candidates),
              "joint_independent_summary_differs")
    finally:
        raw_reader.assert_unchanged()
    return dict(verified=True, schema=SCHEMA, record_counts=order.counts,
        candidate_counts=summary.candidates, summary=summary.rows, record_stream_exhausted=True,
        claim=SCOPE, trusted_boundary="existing closed scalar codec/shared raw parser/immutable input schema")

def write_report(records: Iterator[dict], sink: BinaryIO,
                 checkpoint: Callable[[str], object]) -> ReportDigest:
    _need(callable(checkpoint), "report_checkpoint_required")
    hashed, decoded, size, count, order = _Sink(sink), hashlib.sha256(), 0, 0, _Order()
    with gzip.GzipFile(filename="", fileobj=hashed, mode="wb", compresslevel=1, mtime=0) as compressed:
        for record in records:
            raw = codec.canonical(record) + b"\n"
            _need(len(raw) <= MAX_LINE and size + len(raw) <= MAX_DECODED, "report_decoded_limit")
            order.accept(record, raw)
            compressed.write(raw)
            decoded.update(raw)
            size, count = size + len(raw), count + 1
            checkpoint("ee_report_write")
    _need(order.done, "report_footer_missing")
    return ReportDigest(hashed.digest.hexdigest(), decoded.hexdigest(), hashed.size, size, count)


def read_report(source: BinaryIO, expected_sha256: str, expected_decoded_sha256: str,
                checkpoint: Callable[[str], object]) -> Iterator[dict]:
    """Bounded single-member gzip JSONL; records remain provisional until EOF."""
    _need(_pin(expected_sha256) and _pin(expected_decoded_sha256), "report_external_pins_required")
    _need(callable(checkpoint), "report_checkpoint_required")
    packed, decoded = hashlib.sha256(), hashlib.sha256()
    packed_size, size, pending, header = 0, 0, b"", b""
    inflater, order = zlib.decompressobj(31), _Order()
    while True:
        chunk = source.read(65536)
        _need(type(chunk) is bytes, "report_binary_source_required")
        if not chunk:
            break
        packed_size += len(chunk)
        _need(packed_size <= MAX_COMPRESSED, "report_compressed_limit")
        packed.update(chunk)
        if len(header) < 10:
            # GzipFile's deterministic empty-name, mtime0, level1 header.
            header += chunk[:10 - len(header)]
            _need(b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x04\xff".startswith(header),
                  "report_gzip_header_differs")
        _need(not inflater.eof, "report_extra_gzip_content")
        while chunk:
            try:
                part = inflater.decompress(chunk, 65536)
            except zlib.error as error:
                raise ReportRefusal("invalid_report_gzip") from error
            _need(not inflater.unused_data, "report_extra_gzip_content")
            chunk = inflater.unconsumed_tail
            size += len(part)
            _need(size <= MAX_DECODED, "report_decoded_limit")
            decoded.update(part)
            pending += part
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                raw = line + b"\n"
                _need(len(raw) <= MAX_LINE, "report_line_limit")
                record = codec._json(raw)
                _need(codec.canonical(record) + b"\n" == raw, "noncanonical_report_jsonl")
                order.accept(record, raw)
                checkpoint("ee_report_read")
                yield record
            _need(len(pending) < MAX_LINE, "report_line_limit")
    _need(inflater.eof and not pending and order.done, "report_truncated_or_footer_missing")
    _need(packed.hexdigest() == expected_sha256, "report_compressed_pin_differs")
    _need(decoded.hexdigest() == expected_decoded_sha256, "report_decoded_pin_differs")
