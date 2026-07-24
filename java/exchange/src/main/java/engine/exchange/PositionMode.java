package engine.exchange;

/**
 * BingX account-wide position mode (see {@code GET/POST
 * /openApi/swap/v1/positionSide/dual}). One-way = one net position per
 * symbol; hedge = simultaneous LONG + SHORT positions. Account-wide, not
 * per-symbol, and cannot change while any position or open order exists.
 */
public enum PositionMode {
    ONE_WAY,
    HEDGE
}
