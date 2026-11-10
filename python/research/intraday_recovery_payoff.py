"""Pure EK path labels, EJ60 payoff and matched holding management.

Forecasts are caller-supplied observations, never fitted here. Percentage
values use percentage points (``1`` means one percent). Path labels use the
frozen maximum purchase price; executable payoff uses the modeled entry open.
The nominal round-trip cost is prepaid once on entry, so a remaining-gross
forecast is compared with zero without subtracting that sunk cost again.
"""

from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from research import intraday_candidate_laggard_execution as trade
from research.intraday_candidate_exit import _minute
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs, _session


TARGET = Fraction(5033, 5000)
UPDATE_OFFSETS = (77, 92, 107)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"EK payoff: {message}")


def _reference_bar(session: SessionInputs, at: datetime) -> tuple[Fraction, ...] | None:
    """Independent quote validation; neither the producer nor EJ quote helper."""
    row = session.minutes.get(at)
    if not isinstance(row, Minute) or row.symbol != session.symbol or row.start_at != at:
        return None
    if row.price_state != "observed" or row.available_at != at + 3 * MINUTE:
        return None
    if type(row.ohlc) is not tuple or len(row.ohlc) != 4:
        return None
    if any(not isinstance(p, Decimal) or not p.is_finite() for p in row.ohlc):
        return None
    op, high, low, close = tuple(Fraction(p) for p in row.ohlc)
    return (op, high, low, close) if 0 < low <= min(op, close) <= max(op, close) <= high else None


def evaluate(session: SessionInputs, limit: Fraction, stop: Fraction) -> dict:
    """Label the first causal close event and retain the exact EJ60 baseline."""
    baseline = trade.execute_opening_hold60(
        session.symbol, session.opening + 62 * MINUTE, limit, stop, session)
    path = {"class": None, "reason": "entry_not_filled", "at": None}
    if baseline["entry_at"] is not None:
        for offset in range(63, 120):
            start = session.opening + offset * MINUTE
            available = (start + 3 * MINUTE).isoformat()
            bar = _minute(session, start)
            if bar is None:
                path = {"class": None, "reason": "first_close_unknown", "at": available}
                break
            close = Fraction(bar[3])
            if close >= limit * TARGET:
                path = {"class": 0, "reason": "target_first", "at": available}
                break
            if close <= stop:
                path = {"class": 1, "reason": "invalidation_first", "at": available}
                break
        else:
            path = {"class": 2, "reason": "timeout", "at": (session.opening + 122 * MINUTE).isoformat()}
    elif baseline["status"] == "unresolved":
        path["reason"] = "entry_unknown"
    gross_pct = (str(Fraction(baseline["gross"]) / Fraction(baseline["entry_notional"]) * 100)
                 if baseline["status"] == "closed" else None)
    return {"baseline": baseline, "path": path, "gross_pct": gross_pct}


def reference_evaluate(session: SessionInputs, limit: Fraction, stop: Fraction) -> dict:
    """Reconstruct labels and payoff without the producer's path or quotes."""
    arm = trade.reference_opening_hold60(
        session.symbol, session.opening + 62 * MINUTE, limit, stop, session)
    label, reason, at = None, "entry_not_filled", None
    if arm["entry_at"] is not None:
        clock = session.opening + 66 * MINUTE
        last = session.opening + 122 * MINUTE
        while clock <= last:
            prices = _reference_bar(session, clock - 3 * MINUTE)
            at = clock.isoformat()
            if prices is None:
                reason = "first_close_unknown"
                break
            if prices[-1] >= limit * Fraction(10066, 10000):
                label, reason = 0, "target_first"
                break
            if prices[-1] <= stop:
                label, reason = 1, "invalidation_first"
                break
            clock += MINUTE
        else:
            label, reason = 2, "timeout"
    elif arm["status"] == "unresolved":
        reason = "entry_unknown"
    pct = None
    if arm["status"] == "closed":
        pct = str((Fraction(arm["exit_price"]) / Fraction(arm["entry_price"]) - 1) * 100)
    return {"baseline": arm, "path": {"class": label, "reason": reason, "at": at}, "gross_pct": pct}


def _updates(session: SessionInputs, updates: list[dict]) -> dict[datetime, Fraction | None]:
    _require(type(updates) is list, "updates must be a list")
    parsed = {}
    allowed = {session.opening + offset * MINUTE for offset in UPDATE_OFFSETS}
    previous = None
    for update in updates:
        _require(type(update) is dict and set(update) == {
            "decision_at", "available_at", "expected_remaining_gross_pct"}, "forecast schema differs")
        _require(type(update["decision_at"]) is str and type(update["available_at"]) is str,
                 "forecast clocks must be stamped")
        try:
            decision = datetime.fromisoformat(update["decision_at"])
            available = datetime.fromisoformat(update["available_at"])
        except ValueError as exc:
            raise ValueError("EK payoff: invalid forecast clock") from exc
        _require(decision.utcoffset() is not None and available.utcoffset() is not None,
                 "aware forecast clocks required")
        _require(decision in allowed and available <= decision and available >= session.opening,
                 "future or off-schedule forecast")
        _require(decision not in parsed and (previous is None or previous < decision),
                 "duplicate or unordered forecast")
        value = update["expected_remaining_gross_pct"]
        _require(value is None or type(value) is str, "exact forecast string required")
        try:
            parsed[decision] = None if value is None else Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError("EK payoff: invalid exact forecast") from exc
        previous = decision
    return parsed


def _baseline(session: SessionInputs, baseline: dict, *, reference: bool) -> Fraction:
    """Authenticate the supplied arm against the frozen initial price bounds."""
    _session(session)
    reader = _reference_bar if reference else _minute
    initial = [reader(session, session.opening + offset * MINUTE) for offset in range(45, 60)]
    _require(all(bar is not None for bar in initial), "initial bounds unknown")
    limit, stop = Fraction(initial[-1][3]), min(Fraction(bar[2]) for bar in initial)
    expected = trade.reference_opening_hold60(
        session.symbol, session.opening + 62 * MINUTE, limit, stop, session)
    _require(type(baseline) is dict and baseline == expected, "baseline receipt differs")
    return stop


def _initial_result(baseline: dict) -> dict:
    filled = baseline["entry_at"] is not None
    known_flat = baseline["status"] == "no_fill"
    quantity = baseline["fill"]["quantity"] if filled else (0 if known_flat else None)
    price = Fraction(baseline["entry_price"]) if filled else None
    return {
        "status": "holding" if filled else baseline["status"],
        "reason": "holding" if filled else baseline["reason"],
        "entry_at": baseline["entry_at"], "entry_price": str(price) if filled else None,
        "initial_quantity": quantity, "remaining_quantity": quantity,
        "entry_basis": str(quantity * price) if filled else ("0" if known_flat else None),
        "entry_cost": baseline["fill"]["cost"] if filled else ("0" if known_flat else None),
        "sales": [], "realized_basis": "0", "realized_gross": "0", "realized_cost": "0", "realized_net": "0",
        "gross": "0" if known_flat else None, "cost": "0" if known_flat else None,
        "net": "0" if known_flat else None,
        "unresolved_at": baseline["unknown_at"] if not filled else None,
    }


def _sell(session: SessionInputs, result: dict, request: datetime, quantity: int, reason: str) -> None:
    execution = request + MINUTE
    bar = _minute(session, execution)
    row = {"request_at": request.isoformat(), "execute_at": execution.isoformat(), "at": None,
           "quantity": quantity, "price": None, "reason": reason,
           "basis": None, "gross": None, "cost": None, "net": None}
    result["sales"].append(row)
    if bar is None:
        result.update(status="unresolved", reason="sale_open_unknown", remaining_quantity=None,
                      unresolved_at=execution.isoformat())
        return
    entry, price = Fraction(result["entry_price"]), Fraction(bar[0])
    basis = quantity * entry
    gross = quantity * (price - entry)
    cost = Fraction(result["entry_cost"]) * quantity / result["initial_quantity"]
    row.update(at=execution.isoformat(), price=str(price), basis=str(basis), gross=str(gross),
               cost=str(cost), net=str(gross - cost))
    for key in ("basis", "gross", "cost", "net"):
        result[f"realized_{key}"] = str(Fraction(result[f"realized_{key}"]) + Fraction(row[key]))
    result["remaining_quantity"] -= quantity
    if result["remaining_quantity"] == 0:
        result.update(status="closed", reason=reason,
                      **{key: result[f"realized_{key}"] for key in ("gross", "cost", "net")})


def manage(session: SessionInputs, baseline: dict, updates: list[dict]) -> dict:
    """Hold, reduce once, then exit on two consecutive nonpositive forecasts.

    Update dictionaries require ISO ``decision_at`` and ``available_at`` plus
    an exact-string ``expected_remaining_gross_pct`` or None. Omitted slots
    reset the consecutive count. Structural/time decisions win clock ties.
    Unknown sale opens stop replay permanently, retaining the pending request.
    """
    forecasts = _updates(session, updates)
    stop = _baseline(session, baseline, reference=False)
    result = _initial_result(baseline)
    if result["status"] != "holding":
        return result
    reduced, consecutive = False, 0
    for offset in range(66, 123):
        request = session.opening + offset * MINUTE
        if offset == 122:
            _sell(session, result, request, result["remaining_quantity"], "time")
        else:
            start = request - 3 * MINUTE
            bar = _minute(session, start)
            if bar is None:
                result.update(status="unresolved", reason="first_structural_close_unknown",
                              unresolved_at=start.isoformat())
                break
            if Fraction(bar[3]) <= stop:
                _sell(session, result, request, result["remaining_quantity"], "structural")
            elif offset in UPDATE_OFFSETS:
                forecast = forecasts.get(request)
                consecutive = consecutive + 1 if forecast is not None and forecast <= 0 else 0
                if consecutive >= 2:
                    _sell(session, result, request, result["remaining_quantity"], "forecast_exit")
                elif consecutive and not reduced:
                    reduced = True
                    quantity = result["initial_quantity"] // 2
                    if quantity:
                        _sell(session, result, request, quantity, "forecast_reduce")
        if result["status"] != "holding":
            break
    return result


def verify_management(session: SessionInputs, baseline: dict, updates: list[dict], result: dict) -> None:
    """Audit chronology, unknowns and cash independently of ``manage`` helpers.

    Failure raises ValueError. This does not certify caller forecast provenance
    beyond its stamped availability, or turn nominal event cash into account NAV.
    """
    predictions = _updates(session, updates)
    stop = _baseline(session, baseline, reference=True)
    entry = baseline["entry_at"]
    known_flat = baseline["status"] == "no_fill"
    count = baseline["fill"]["quantity"] if entry is not None else (0 if known_flat else None)
    purchase = Fraction(baseline["entry_price"]) if entry is not None else None
    original_cost = count * purchase * Fraction(33, 10000) if entry is not None else None
    expected = {
        "status": baseline["status"] if entry is None else "holding",
        "reason": baseline["reason"] if entry is None else "holding",
        "entry_at": entry, "entry_price": str(purchase) if entry is not None else None,
        "initial_quantity": count, "remaining_quantity": count,
        "entry_basis": str(count * purchase) if entry is not None else ("0" if known_flat else None),
        "entry_cost": str(original_cost) if entry is not None else ("0" if known_flat else None),
        "sales": [], "realized_basis": "0", "realized_gross": "0", "realized_cost": "0", "realized_net": "0",
        "gross": "0" if known_flat else None, "cost": "0" if known_flat else None,
        "net": "0" if known_flat else None, "unresolved_at": baseline["unknown_at"] if entry is None else None,
    }
    if entry is not None:
        # Construct request events from causal closes and forecast slots first.
        # Price one selected sale at a time; an unknown open terminates the audit.
        reduced, previous_nonpositive = False, False
        sold_quantity, proceeds = 0, Fraction()
        clock, expiry_request = session.opening + 66 * MINUTE, session.opening + 122 * MINUTE
        while clock <= expiry_request:
            sell, reason = 0, None
            if clock == expiry_request:
                sell, reason = count - sold_quantity, "time"
            else:
                quote = _reference_bar(session, clock - 3 * MINUTE)
                if quote is None:
                    expected.update(status="unresolved", reason="first_structural_close_unknown",
                                    unresolved_at=(clock - 3 * MINUTE).isoformat())
                    break
                if quote[-1] <= stop:
                    sell, reason = count - sold_quantity, "structural"
                elif clock - session.opening in tuple(offset * MINUTE for offset in UPDATE_OFFSETS):
                    prediction = predictions.get(clock)
                    nonpositive = prediction is not None and prediction <= 0
                    if nonpositive and previous_nonpositive:
                        sell, reason = count - sold_quantity, "forecast_exit"
                    elif nonpositive and not reduced:
                        reduced = True
                        sell, reason = count // 2, "forecast_reduce"
                    previous_nonpositive = nonpositive
            if sell:
                executed = clock + MINUTE
                sale = _reference_bar(session, executed)
                row = {"request_at": clock.isoformat(), "execute_at": executed.isoformat(), "at": None,
                       "quantity": sell, "price": None, "reason": reason,
                       "basis": None, "gross": None, "cost": None, "net": None}
                expected["sales"].append(row)
                if sale is None:
                    expected.update(status="unresolved", reason="sale_open_unknown", remaining_quantity=None,
                                    unresolved_at=executed.isoformat())
                    break
                basis, cost = sell * purchase, sell * purchase * Fraction(33, 10000)
                gross = sale[0] * sell - basis
                row.update(at=executed.isoformat(), price=str(sale[0]), basis=str(basis), gross=str(gross),
                           cost=str(cost), net=str(gross - cost))
                sold_quantity += sell
                proceeds += sell * sale[0]
                total_basis = sold_quantity * purchase
                total_cost = original_cost * Fraction(sold_quantity, count)
                expected.update(remaining_quantity=count - sold_quantity, realized_basis=str(total_basis),
                                realized_gross=str(proceeds - total_basis), realized_cost=str(total_cost),
                                realized_net=str(proceeds - total_basis - total_cost))
                if sold_quantity == count:
                    expected.update(status="closed", reason=reason, gross=str(proceeds - total_basis),
                                    cost=str(total_cost), net=str(proceeds - total_basis - total_cost))
                    break
            clock += MINUTE
    _require(type(result) is dict and result == expected, "management ledger differs from independent reconstruction")
