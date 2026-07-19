from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from pydantic import Field, PlainSerializer

# JSON has no native arbitrary-precision decimal type; serializing Decimal
# as a JSON number round-trips lossily through float on the Java/Jackson
# side. Force string serialization instead — see schemas/README.md.
_as_json_string = PlainSerializer(lambda v: str(v), return_type=str, when_used="json")

# Every quantity/price/leverage field in these schemas is meant to be a
# positive, finite real number; Java's BigDecimal has no NaN/Infinity concept
# at all, so allow_inf_nan=False keeps Python from silently accepting values
# Java could never represent.
PositiveDecimalString = Annotated[
    Decimal,
    _as_json_string,
    Field(gt=0, allow_inf_nan=False),
]


def normalize_to_utc(value: datetime) -> datetime:
    """Collapse any timezone-aware datetime to UTC.

    `AwareDatetime` alone accepts any offset (e.g. +09:00); the wire
    contract in schemas/README.md is UTC specifically, and Java's `Instant`
    has no non-UTC representation to begin with — normalize here so both
    sides agree on the same instant's textual form.
    """
    return value.astimezone(timezone.utc)
