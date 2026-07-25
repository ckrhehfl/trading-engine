from typing import Iterator, Sequence, overload

from backtest.kline import Kline


class KlineWindow:
    """O(1), bounds-checked view over a prefix of an underlying kline list.

    Replaces `klines[: i + 1]` in `engine.py`'s bar loop, which was a full
    O(n) list copy every iteration (O(n^2) over a full run). `KlineWindow`
    holds a reference to the original list plus an `int` length instead of
    copying, so construction is O(1) and indexed access stays O(1).

    This class is what makes lookahead bias structurally impossible rather
    than merely avoided by convention, so it must preserve the *exact*
    guarantee the old `klines[: i + 1]` copy gave: nothing constructed from
    a `KlineWindow(klines, length)` can ever observe `klines[length]` or
    anything beyond it — not via positive indexing, not via negative
    indexing (relative to `length`, not the underlying list's real length),
    not via slicing, and not via iteration. Every access path below is
    computed against `self._length`, never against `len(self._klines)`.

    Deliberately implements the `Sequence[Kline]` protocol (`__len__`,
    `__getitem__` for both `int` and `slice`, `__iter__`) directly rather
    than subclassing `collections.abc.Sequence`: the mixin methods that ABC
    would provide for free (`__contains__`, `__reversed__`, `index`,
    `count`) aren't needed by any current caller, and keeping this class to
    exactly the methods it defines itself keeps the safety-critical surface
    small and easy to audit in full.
    """

    __slots__ = ("_klines", "_length")

    def __init__(self, klines: Sequence[Kline], length: int) -> None:
        if length < 0:
            raise ValueError(f"length must be non-negative, got {length}")
        if length > len(klines):
            raise ValueError(
                f"length ({length}) must not exceed the underlying sequence's "
                f"length ({len(klines)})"
            )
        self._klines = klines
        self._length = length

    def __len__(self) -> int:
        return self._length

    @overload
    def __getitem__(self, index: int) -> Kline: ...

    @overload
    def __getitem__(self, index: slice) -> list[Kline]:  # noqa: F811
        ...

    def __getitem__(self, index: int | slice):  # noqa: F811
        if isinstance(index, slice):
            start, stop, step = index.indices(self._length)
            return [self._klines[i] for i in range(start, stop, step)]
        if isinstance(index, int):
            normalized = index + self._length if index < 0 else index
            if normalized < 0 or normalized >= self._length:
                raise IndexError("KlineWindow index out of range")
            return self._klines[normalized]
        raise TypeError(
            f"KlineWindow indices must be int or slice, not {type(index).__name__}"
        )

    def __iter__(self) -> Iterator[Kline]:
        for i in range(self._length):
            yield self._klines[i]
