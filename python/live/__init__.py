"""`python/live/` -- operational scripts that drive a real (paper, later
possibly live-canary) trading loop against fresh, current-day market data.

Deliberately separate from `python/research/`: everything under
`research/` is backtest/experiment code whose job is to produce and log
*evidence about* a strategy (walk-forward runs, holdout confirmations,
eligibility checks) -- it must never be the thing actually driving a
day-to-day production decision. `python/live/` is the opposite: it holds
no research/selection logic of its own, only wiring that takes an
already-validated (see CLAUDE.md's "Paper Trading Policy Exception")
strategy and runs it once against today's real data. See
`.planning/paper-trading-b-signal-runner.md` for the full reasoning
behind this package boundary (Paper-trading bridge, Task B).

Python still never places a live order directly (CLAUDE.md's
Non-negotiable Rules) -- modules here only ever produce a signal *file*
for a separate Java process to pick up through the Java Risk Gateway.
"""
