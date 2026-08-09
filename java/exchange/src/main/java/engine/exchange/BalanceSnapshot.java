package engine.exchange;

import java.math.BigDecimal;

/**
 * Account balance as reported by {@code GET /openApi/swap/v3/user/balance}.
 *
 * <p>{@code asset} is the per-asset ticker BingX's real response carries
 * (confirmed against a live VST call — see CLAUDE.md's "Verified —
 * authenticated, VST key" section: {@code [{"userId","asset","balance",
 * "equity",...}]}) -- {@code "VST"} for a demo account, {@code "USDT"} for
 * production. Added specifically so a caller (see {@code
 * engine.runtime.VstPreflight}) can fail closed unless a real balance call's
 * {@code asset} is exactly {@code "VST"} before ever trading against it,
 * rather than trusting the base URL alone to guarantee that.
 */
public record BalanceSnapshot(
        BigDecimal balance,
        BigDecimal equity,
        BigDecimal availableMargin,
        BigDecimal usedMargin,
        BigDecimal unrealizedProfit,
        String asset) {}
