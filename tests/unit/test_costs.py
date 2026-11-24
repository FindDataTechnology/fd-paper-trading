"""Cost arithmetic boundaries (change paper-fees-and-t1-settlement 1.2).

These pin the three properties the spec makes load-bearing: the stamp tax is
sell-side only, slippage moves the *price* (not a second cash charge), and
``enabled=False`` is bit-identical to the pre-cost path.
"""

from decimal import Decimal

from fd_paper_trading.execution.costs import CostModel, apply_costs

ENABLED = CostModel(enabled=True)


def test_buy_charges_commission_only():
    fill = apply_costs(side="BUY", quantity=Decimal(100), price=Decimal("10"), model=ENABLED)
    assert fill.stamp_tax == 0
    assert fill.commission > 0
    # cash out = notional + commission, and notional already carries slippage
    assert fill.cash_delta == -(fill.notional + fill.commission)


def test_sell_charges_commission_and_stamp_tax():
    fill = apply_costs(side="SELL", quantity=Decimal(100), price=Decimal("10"), model=ENABLED)
    assert fill.stamp_tax > 0
    assert fill.commission > 0
    assert fill.cash_delta == fill.notional - fill.commission - fill.stamp_tax


def test_slippage_is_in_the_price_not_a_second_charge():
    fill = apply_costs(side="BUY", quantity=Decimal(100), price=Decimal("10"), model=ENABLED)
    assert fill.effective_price > Decimal("10")
    # notional is qty * effective (slippage already inside), not qty*price + slippage
    assert fill.notional == Decimal(100) * fill.effective_price


def test_zero_slippage_leaves_price_unchanged():
    model = CostModel(enabled=True, slippage_bps=Decimal(0),
                      commission_rate=Decimal(0), stamp_tax_rate=Decimal(0))
    fill = apply_costs(side="BUY", quantity=Decimal(100), price=Decimal("10"), model=model)
    assert fill.effective_price == Decimal("10")
    assert fill.cash_delta == Decimal("-1000")


def test_disabled_is_bit_identical_to_no_costs():
    fill = apply_costs(side="BUY", quantity=Decimal(100), price=Decimal("10"), model=CostModel())
    assert fill.effective_price == Decimal("10")
    assert fill.commission == fill.stamp_tax == fill.slippage_cost == 0
    assert fill.cash_delta == Decimal("-1000")


def test_sell_slippage_lowers_the_fill_price():
    fill = apply_costs(side="SELL", quantity=Decimal(10), price=Decimal("10"), model=ENABLED)
    assert fill.effective_price < Decimal("10")
