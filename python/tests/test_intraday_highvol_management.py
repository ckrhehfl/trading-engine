from fractions import Fraction

from research.intraday_highvol_management import paired_management

from test_intraday_highvol_labels import at, row, session


def compare(value, forecast, *, cutoff=51):
    return paired_management(value, at(18), Fraction(100), Fraction(98), cost_bps=33,
                             label_cutoff=at(cutoff), hold_gain_at=forecast)


def test_same_lot_reduction_then_exit_charges_paid_entry_cost_once():
    value = session(row(23, (104, 104, 100, 101)), row(28, (106, 106, 100, 101)),
                    row(48, (99, 101, 98, 100)))
    clocks = []

    def forecast(clock):
        clocks.append(clock)
        return 0.0

    pair = compare(value, forecast)
    assert clocks == [at(22), at(27)]
    assert [update.action for update in pair.updates] == ["reduce_half", "exit"]
    assert pair.fixed.original_quantity == pair.managed.original_quantity
    assert pair.fixed.initial_cost == pair.managed.initial_cost
    q = pair.fixed.original_quantity
    assert [leg.quantity for leg in pair.managed.legs] == [q // 2, q - q // 2]
    assert pair.managed.gross_return == Fraction((q // 2) * 4 + (q - q // 2) * 6, q * 100)
    assert pair.managed.gross_return - pair.managed.net_return == Fraction(33, 10_000)
    assert pair.fixed.gross_return - pair.fixed.net_return == Fraction(33, 10_000)


def test_known_structure_precedes_model_and_missing_execution_preserves_inventory():
    value = session(row(19, (100, 101, 98, 98)))
    value.minutes.pop(at(23))

    def forecast(clock):
        raise AssertionError(f"unexpected model call {clock}")

    pair = compare(value, forecast)
    assert pair.updates[0].reason == "structural_close_failure"
    assert pair.managed.unresolved_quantity == pair.managed.original_quantity
    assert pair.managed.net_return is None
    assert pair.fixed.status == "closed"


def test_cutoff_blocks_later_holding_inputs():
    def forecast(clock):
        raise AssertionError(f"future model call {clock}")

    pair = compare(session(), forecast, cutoff=21)
    assert all(update.close is None and update.action == "hold" for update in pair.updates)
    assert pair.managed.net_return is None
