"""Closed EH opening scenario census over the original CB and CC archives."""

from dataclasses import asdict
from pathlib import Path

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_scenario_support_unit as price_archive


WINDOW = price_archive.WINDOW
FULL = price_archive.FULL
COUNTS = price_archive.COUNTS
OUTPUT = bridge.EH_OPEN_OUTPUT_FILENAME
MAX_OUTPUT = bridge.EH_OPEN_OUTPUT_MAX_BYTES


def contract(specification: bytes, original: bytes) -> tuple[dict, tuple[bridge.InputPin, ...], str]:
    return price_archive._price_contract(specification, original, bridge.EH_OPEN_UNIT_ID)


def make_invented(directory: Path, sessions: int, checkpoint: price_archive.CHECKPOINT) -> bridge.InputEnvelope:
    return price_archive._make_invented_price_archive(directory, sessions, checkpoint,
                                                       adjust_chunk=_invent_opening_paths)


def _invent_opening_paths(chunk: dict, index: int) -> None:
    from decimal import Decimal

    route = index % 13
    rows = chunk["rows"]
    if route >= 5 or len(rows) < 60:
        return
    base = Decimal(rows[0]["open"])
    level = base + 1

    def price(offset, op, high, low, close):
        rows[offset].update(open=str(op), high=str(high), low=str(low), close=str(close))

    for offset in range(15):
        price(offset, base, level, base - 1, base)
    for offset in range(15, 60):
        price(offset, level, level, level, level)
    if route == 0:
        price(22, level, level + 1, level, level + 1)
    elif route == 1:
        departed = level + Decimal("0.3")
        price(22, level, departed, level, departed)
        price(23, level, level, level, level)
        price(24, level, level + 1, level, level + 1)
    elif route == 2:
        price(22, level - 1, level - 1, level - 1, level - 1)
    elif route == 4:
        price(22, level, level + 1, level - 1, level)


def compute(directory: Path, envelope: bridge.InputEnvelope, plan: bridge.UnitPlan,
            *, invented_only: bool, checkpoint: price_archive.CHECKPOINT) -> bridge.OutputReceipt:
    from research import intraday_scenario_open_storage as storage

    def measure(output, universe, reader):
        seal = storage.write_opening_support(output, universe, reader, checkpoint)
        verified = storage.verify_opening_support(output, seal, universe, reader, checkpoint)
        return seal, asdict(verified), verified.support_totals

    return price_archive._compute_price_archive(directory, envelope, plan, invented_only=invented_only,
                                                 checkpoint=checkpoint, filename=OUTPUT, maximum=MAX_OUTPUT,
                                                 schema=storage.SCHEMA, measure=measure)
