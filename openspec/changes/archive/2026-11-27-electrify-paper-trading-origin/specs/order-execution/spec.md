# Spec Delta

## Purpose

删掉一句与实现相反的运行方式断言。撮合器的契约是「纯算术、无第三方引擎、状态不在计算器里」，
不是「必须跑在线程/线程池上」——后者写进规格会把一个性能选择变成合规判据。

## MODIFIED Requirements

### Requirement: Stateless fill calculator

The system SHALL compute order fills with a stateless, pure-arithmetic fill calculator
that takes one quote and one order (and, for stop-type orders, the order's trigger state)
and returns the fill outcome, with no third-party matching engine and no per-order engine
construction. Account state (positions, balances) SHALL NOT live in the calculator; it
SHALL live in the database, so accounts are isolated by `account_id`. Because the calculator
is pure arithmetic over a handful of `Decimal` values, it runs synchronously in the caller;
the system SHALL NOT be required to offload it to a worker, and SHALL NOT pay a thread or
process hand-off for it.

Fill semantics SHALL be: a market buy fills at the `ask`; a market sell fills at the `bid`;
a limit buy fills at the `ask` when its limit is at or above the `ask`, otherwise it rests;
a limit sell fills at the `bid` when its limit is at or below the `bid`, otherwise it rests;
a stop sell triggers when the quote's `bid` is at or below its `stop_price` and fills at the
`stop_price`; a stop buy triggers when the quote's `ask` is at or above its `stop_price` and
fills at the `stop_price`; a stop-limit order first applies the stop trigger rule, and once
triggered follows the corresponding limit rule. The calculator SHALL NOT model queueing,
partial fills, slippage, or price movement.

#### Scenario: Calculator computes a fill price

- **WHEN** a market buy for a symbol with a current quote is placed
- **THEN** the calculator returns the fill price (the ask) without touching account balances

#### Scenario: No third-party matching engine is required

- **WHEN** an order is filled
- **THEN** the fill is computed in-process by the service's own calculator, with no
  third-party trading framework imported or invoked

#### Scenario: 计算器同步完成，不付线程交接成本

- **WHEN** 一次下单经过撮合
- **THEN** 成交结果是同一次调用内算出的，不跨线程、不跨进程、不等待 future

#### Scenario: Resting limit consumes no state

- **WHEN** a limit order does not cross the quote
- **THEN** the calculator reports a resting outcome with zero filled quantity and creates
  no position

#### Scenario: Stop sell triggers at the stop price

- **WHEN** a sell stop with `stop_price = s` is evaluated against a quote whose `bid ≤ s`
- **THEN** the fill price is `s`, computed without touching account balances

#### Scenario: Stop not yet triggered stays resting

- **WHEN** a sell stop with `stop_price = s` is evaluated against a quote whose `bid > s`
- **THEN** the calculator reports a not-triggered outcome with zero filled quantity

#### Scenario: Stop-limit falls back to the limit rule after trigger

- **WHEN** a sell stop-limit with `stop_price = s` and `limit_price = l ≤ s` is evaluated
  against a quote whose `bid ≤ s`
- **THEN** the trigger condition is met and the subsequent fill follows the sell-limit rule
  against the same quote (fills at `bid` when `bid ≤ l`, otherwise rests as triggered-limit)
