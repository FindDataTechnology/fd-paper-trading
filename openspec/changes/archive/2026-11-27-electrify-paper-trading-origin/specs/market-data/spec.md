# Spec Delta

## Purpose

让 provider 清单与实际可配置集合一致：消息里说的「三实现」在配置面上只有一个可选，
另外两个是代码里保留、当前无法被 `MARKET_DATA_SOURCE_ID` 选中的类。规格不该让读的人以为
它们随时可切换。

## MODIFIED Requirements

### Requirement: Polygon provider (real previous-close)

The `PolygonMarketDataProvider` class SHALL remain in the codebase and SHALL, when
constructed directly, derive a quote from Polygon's previous-close aggregate (free tier):
the last close is the reference price, and a small spread (5 bps each side, `$0.01` floor)
is synthesized around it to produce `bid`/`ask`. Quotes SHALL be cached per symbol for a TTL
(default 300s). The provider SHALL return `None` when there is no API key, on HTTP error, on
a non-200 response (rate-limited / not entitled / unknown symbol), or when no close is
present. Instruments SHALL come from the service's own instrument value type.

Because provider selection resolves exclusively through the price-source registry's declared
`access.kind` (see "Provider is swappable"), and no registered source declares a Polygon-kind
access, this class SHALL NOT be selectable through configuration today. Its presence is
library code, not a supported deployment option; the system SHALL NOT present it as one to
operators or to the product surface.

#### Scenario: No API key returns no quote

- **WHEN** the Polygon provider is configured with an empty API key
- **THEN** `get_quote` returns `None`

#### Scenario: Synthesized spread around previous close

- **WHEN** the Polygon provider fetches a previous close of `c`
- **THEN** the returned `bid < c < ask` and `bid != ask`

#### Scenario: 配置选不到 Polygon

- **WHEN** 运维只通过 `MARKET_DATA_SOURCE_ID` 选择行情源，且注册表里没有任何源声明 Polygon 访问类型
- **THEN** 不存在任何配置组合会选中 `PolygonMarketDataProvider`
- **AND THEN** 该事实对运维可见（文档与 provider 清单不把 Polygon 列为可选项）
