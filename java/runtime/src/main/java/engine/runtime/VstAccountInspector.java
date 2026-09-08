package engine.runtime;

import engine.exchange.BalanceSnapshot;
import engine.exchange.BingXAdapter;
import engine.exchange.PositionSnapshot;
import java.math.BigDecimal;
import java.util.List;

/**
 * Read-only diagnostic: print the real VST account's balance and open
 * positions, and place no order of any kind.
 *
 * <p>Written on 2026-09-08, when {@code daily-tsmom-ensemble} produced this
 * project's first real signal, the {@code bingx-vst} loop submitted a real
 * order for it, and the order stuck at {@code PARTIALLY_FILLED} with the
 * executor reporting a quantity mismatch against the venue. The kill switch
 * tripped correctly and the loop stopped, but nothing in this codebase could
 * answer the first question an operator has: <em>what does the account
 * actually hold right now?</em> The dashboard shows the balance
 * {@link VstPreflight} logged at the last <em>startup</em>, which is stale by
 * construction.
 *
 * <h2>Why this is not a Risk Gateway bypass</h2>
 *
 * <p>CLAUDE.md's rule is that {@code ExchangeAdapter} may only be invoked from
 * OMS-mediated flows, and it exists to stop a hand-built order reaching a
 * venue. This class calls exactly two methods -- {@link
 * BingXAdapter#getBalance()} and {@link BingXAdapter#getPositions()} -- both of
 * which are HTTP GETs that cannot create, modify or cancel anything. It
 * follows the same read-only precedent {@link VstPreflight} already sets by
 * calling both at startup.
 *
 * <p>It deliberately does <em>not</em> expose {@code submitOrder},
 * {@code cancelOrder}, {@code setLeverage} or {@code setPositionMode}. A
 * diagnostic that could also act is not a diagnostic.
 *
 * <h2>Host</h2>
 *
 * <p>Reuses {@link PaperTradingApp}'s hardcoded VST host constant rather than
 * taking one, so there is no configuration surface here either -- the same
 * reasoning as {@code BINGX_VST_BASE_URL} itself.
 *
 * <pre>{@code
 * BINGX_API_KEY=... BINGX_API_SECRET=... \
 *   java -cp <classpath> engine.runtime.VstAccountInspector
 * }</pre>
 */
public final class VstAccountInspector {

    private VstAccountInspector() {}

    public static void main(String[] args) {
        String apiKey = System.getenv(PaperTradingApp.ENV_BINGX_API_KEY);
        String apiSecret = System.getenv(PaperTradingApp.ENV_BINGX_API_SECRET);
        if (apiKey == null || apiKey.isBlank() || apiSecret == null || apiSecret.isBlank()) {
            System.err.println(
                    "export "
                            + PaperTradingApp.ENV_BINGX_API_KEY
                            + " and "
                            + PaperTradingApp.ENV_BINGX_API_SECRET
                            + " first. This tool never reads .env.");
            System.exit(2);
            return;
        }

        BingXAdapter adapter =
                new BingXAdapter(apiKey, apiSecret, PaperTradingApp.vstBaseUrl());

        BalanceSnapshot balance = adapter.getBalance();
        System.out.println("asset=" + balance.asset());
        System.out.println("balance=" + balance.balance());
        System.out.println("equity=" + balance.equity());
        System.out.println("availableMargin=" + balance.availableMargin());
        System.out.println("usedMargin=" + balance.usedMargin());
        System.out.println("unrealizedProfit=" + balance.unrealizedProfit());

        List<PositionSnapshot> positions = adapter.getPositions();
        System.out.println();
        System.out.println("positions=" + positions.size());
        for (PositionSnapshot p : positions) {
            System.out.println(
                    "  symbol=" + p.symbol()
                            + " side=" + p.positionSide()
                            + " amt=" + p.positionAmt()
                            + " avgPrice=" + p.avgPrice()
                            + " leverage=" + p.leverage()
                            + " unrealizedProfit=" + p.unrealizedProfit()
                            + " liquidationPrice=" + p.liquidationPrice());
        }

        // The number the 2026-09-08 incident turns on: the venue's own filled
        // quantity against what this project approved. Printed as a total so
        // an operator can compare it with the approved quantity in the log
        // without doing the arithmetic by hand.
        BigDecimal net = BigDecimal.ZERO;
        for (PositionSnapshot p : positions) {
            if (p.positionAmt() != null) {
                net = net.add(p.positionAmt().abs());
            }
        }
        System.out.println();
        System.out.println("total absolute position size = " + net);
    }
}
