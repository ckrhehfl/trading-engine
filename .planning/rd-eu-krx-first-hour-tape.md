# EU first-hour KRX tape and quote quality

2026-11-19. The operator approved the proposed local order-free observation
of Samsung Electronics (005930) and SK hynix (000660). ET completed two
initial fits and four independent policy comparisons after PR #471. The
related read-only local inventory found no synchronized cash-equity tape and
top-of-book data in the admitted prior evaluation archive. Minute volume
alone cannot distinguish which trades were reported as sells during a pullback.

## One observation and its limits

Capture only the KRX public trade and quote channels, `H0STCNT0` and
`H0STASP0`, for those two codes on Friday 2026-11-20. Use 09:00 inclusive to
10:00 exclusive Asia/Seoul. The one-time local launcher is eligible between
08:58 inclusive and 09:00 exclusive to connect and subscribe. Record setup
and connection metadata, and admit market rows only in the one-hour window.
No delayed start, date substitution or second-day acquisition is authorized.
KRX's special opening notice applies to November 19 only. This observation is
exploratory data-quality evidence. No return calculation, strategy comparison,
simulated broker fill, live decision, order, or promotion is part of it.

Use the existing local KIS market application pair with HTTPS certificate
verification for `/oauth2/Approval`. The documented real market-data
WebSocket endpoint is `ws://ops.koreainvestment.com:21000`. Only its temporary
approval key is needed on the socket. Send exactly the four market
subscriptions over one connection. A single reconnect after a network failure
may reuse the in-memory approval key and reissue the same four subscriptions.
Do not request another key, retry a subscription rejection, follow redirected
destinations or use a proxy. Never put authentication values, requests,
unfiltered control messages, account identifiers or API error bodies in the
journal or diagnostic log. Existing market collection and credential files
remain outside this task's write scope.

Use the official [KIS API service](https://apiportal.koreainvestment.com/apiservice),
[public market WebSocket example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_stock/domestic_stock_functions_ws.py),
[approval-key request example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/kis_auth.py)
and the dated [KRX schedule notice](https://kind.krx.co.kr/external/2026/11/05/000141/20261105000216/99303.htm).

The exclusive private journal carries a start, connection/acknowledgement and
gap notices, receipt timestamps, data-only frames and terminal status. Use the
observed Korean exchange times as a distinct clock. Persist the connection and
arrival order. Missing, incomplete, invalid, duplicate, out-of-order, stale or
unsynchronized fields remain visible in quality statistics. A reconnect or a
minute without support is not repaired with a prior-day or later message.
The journal has a 512 MiB ceiling. A local one-shot task limits elapsed
runtime, including login and readback, to 67 minutes. Keep all raw
market bytes and output receipts outside Git in the private local WSL path.

## Independent quality result

An offline reader reconstructs the observed scope, channel announcements,
source and receive-time order, regular-session prices/quantities, directional
classification, minute support and preceding quote matches. Report directional
codes as provider semantics and leave unknown codes unresolved. Check the signed
sizes against cumulative volume only where the source and connection permit the
comparison. Use earlier receipt and exchange-time constraints before assigning
trade-to-quote support. Preserve the denominator and report suspect same-second
ordering and any disconnection or incomplete terminal. The quality report
records supported counts rather than simulated execution.

Initial eligibility requires all four market acknowledgements before opening,
the registered scope and complete failure-free terminal records, at least
54 of 60 covered minutes with both valid trade and quote data per code, at
least 95% classified directional trades per code, and at least 90% of those
classified trades with a matching recent preceding quote. For this initial
gate the quote exchange second must strictly precede the trade by no more than
three seconds, and the quote must have been received before the trade within
that same maximum lag. Clock regressions, invalid trade/quote fields, missing
cumulative checks and size discrepancies fail the quality gate. Report
numerators, denominators and degraded runs separately. The conservative gate
is for this data-readiness exercise, not a strategy entry threshold.

Before registration, test synthetic full-width frames, both approved channels,
wrong topic/symbol/scope, missing support, time boundaries, network gaps and
one-shot termination with no reachable order route. Require separate source
and data-integrity review, required CI and normal merge. Freeze the reviewed
source hashes in the local invocation receipt and verify them before launch.
The one-shot schedule and aggregate journal are separate receipts. A verified
schedule alone never establishes actual capture or data quality.
