"""Byte equivalence of the fixed EI wire projection and its original encoder."""

from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_support as storage
from research.intraday_context_situations import scan_context_frames
from research.intraday_context_support_reference import reference_frames
from research.intraday_context_support_unit import _invented_bars
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPEN = datetime(2031, 2, 12, 9, tzinfo=timezone(timedelta(hours=9)))


def session(case):
    bars = _invented_bars(case)
    rows = {}
    for index in range(390):
        at = OPEN + index * MINUTE
        if case == 4 and index == 61:
            continue
        prices = tuple(Decimal(value) / Decimal(7) for value in bars[index // 5])
        volume = None if case == 6 and index == 26 else Decimal(index % 5) / Decimal(3)
        available = at + (20 if case == 5 and index == 17 else 3) * MINUTE
        rows[at] = Minute("invented", at, available, prices, volume,
                          volume_state="missing" if volume is None else "observed")
    return SessionInputs("invented", OPEN, OPEN + 390 * MINUTE, rows)


def original(frame):
    return {field.name: storage.exact(getattr(frame, field.name)) for field in fields(frame) if field.name != "prefix"}


@pytest.mark.parametrize("case", range(7))
@pytest.mark.parametrize("scanner", (scan_context_frames, reference_frames))
def test_every_completed_frame_keeps_all_wire_fields_and_canonical_bytes(case, scanner):
    value = session(case)
    frames = tuple(scanner(value, through=value.closing))
    assert len(frames) == 72
    for frame in frames:
        assert storage.frame_payload(frame) == original(frame)
        assert wire.encode(storage.frame_payload(frame)) == wire.encode(original(frame))


def test_large_exact_values_and_optional_times_have_no_float_rounding():
    value = session(2)
    frame = next(row for row in scan_context_frames(value, through=value.closing) if row.anchor is not None)
    large = Fraction(10**45 + 11, 10**18 + 3)
    frame = replace(frame, anchor=replace(frame.anchor, start_price=large, price=large + Fraction(1, 7)),
                    prefix_support=replace(frame.prefix_support, volume_total=large))
    assert wire.encode(storage.frame_payload(frame)) == wire.encode(original(frame))
    assert storage.frame_payload(frame)["anchor"]["start_price"] == [large.numerator, large.denominator]


def test_returned_payload_cannot_mutate_another_frame_or_source_object():
    value = session(2)
    frame = next(row for row in scan_context_frames(value, through=value.closing) if row.anchor is not None)
    expected = original(frame)
    changed = storage.frame_payload(frame)
    changed["windows"][0]["price_counts"][0][1] = -999
    changed["anchor"]["price"][0] = -999
    assert storage.frame_payload(frame) == expected
