from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer

# JSON has no native arbitrary-precision decimal type; serializing Decimal
# as a JSON number round-trips lossily through float on the Java/Jackson
# side. Force string serialization instead — see schemas/README.md.
DecimalString = Annotated[
    Decimal,
    PlainSerializer(lambda v: str(v), return_type=str, when_used="json"),
]
