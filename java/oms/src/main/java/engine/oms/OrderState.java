package engine.oms;

public enum OrderState {
    NEW,
    SUBMITTED,
    ACKNOWLEDGED,
    PARTIALLY_FILLED,
    FILLED,
    CANCEL_PENDING,
    CANCELLED,
    REJECTED,
    EXPIRED;

    public boolean isTerminal() {
        return this == FILLED || this == CANCELLED || this == REJECTED || this == EXPIRED;
    }
}
