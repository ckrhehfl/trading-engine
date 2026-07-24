# Implementation Priority #4: Python Deterministic Backtest (retrospective summary)

**PR**: #12 (merged 2026-07-20)

`python/backtest/`: a fill simulator, not a P&L/portfolio backtester —
given historical klines and a strategy callback, simulates what each
`OrderIntent` signal would have filled at. `run_backtest` calls the
strategy with only `klines[:i+1]` at each step, so lookahead bias is
structurally impossible rather than merely avoided by convention. No
wall-clock reads or unseeded randomness anywhere in the loop.

CodeRabbit caught three real bugs across three review rounds, in order:
(1) slippage was applied to LIMIT fills, violating a limit order's
price-guarantee — fixed to apply slippage only to `GUARDED_MARKET`; (2) a
favorable gap (price moves past the limit in the trader's favor) wasn't
filling at all — fixed to fill at the better price; (3) the fix for (2)
still missed the case where the bar *opens* favorable but its high/low
later crosses back — fixed to check `next_bar.open` against the limit
before falling back to the in-range touch check. All three became
regression tests and were designed in correctly from the start in
Priority #6's `PaperBroker` instead of being relearned.
