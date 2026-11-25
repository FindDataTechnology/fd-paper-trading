# Spec Delta

## Purpose

把仓内这份 spec 的最老一条从「钉死单一源」修正为**价格族**模型——根账
（`finddata/openspec/specs/astock-market-data`）与已上产代码（`_concepts_by_type` /
`_family` / `_family_source`）早已是这个模型，仓内这份是 `2026-11-15-fund-price-channel`
之后没有回填的陈旧副本。其余五条与代码一致，不动。

## MODIFIED Requirements

### Requirement: Astock provider reads the sharded daily table through a pinned federated view

`AstockMarketDataProvider` SHALL serve real A-share daily OHLC bars by reading the
platform's federated view over the sharded store, read-only. The read SHALL resolve each
symbol to an entity of type `stock`, `index`, or `fund` and then query the source
**declared as that type's price family's preferred source** in the price-source registry —
not a single source pinned for the whole provider. A symbol whose entity type has no
declared price family SHALL be reported as a data gap; the provider SHALL NOT borrow
another family's source for it. Within one family the provider SHALL NOT mix rows from
multiple sources in the same (concept, entity, date) resolution, and SHALL treat an
ambiguous multi-source row as a defect to reject, not to average. The provider is read-only:
it SHALL NOT write to any store.

The pinned source SHALL remain authoritative for every entity type its own declaration
covers, so individual-stock reads keep the caliber they had before this requirement existed.

#### Scenario: An exchange-traded fund resolves through the fund family

- **WHEN** bars are requested for `510300` and the store holds a `fund` entity for it with rows in the fund family's source
- **THEN** bars are returned for the window
- **AND THEN** every query the provider issued for those rows filtered on the fund source's `source_used` value
- **AND THEN** the coverage report names that source and the fund family's adjustment, not the stock family's

#### Scenario: The pinned source still governs the family it declares

- **WHEN** a stock symbol is read while the fund family is declared
- **THEN** the provider queries the stock family's declared source
- **AND THEN** the coverage report still carries `adjusted=qfq`

#### Scenario: An undeclared family is a gap, never a substitution

- **WHEN** a symbol resolves to an entity type whose price family has no declared preferred source
- **THEN** the provider returns no bars for it
- **AND THEN** it issues no observation query against any other family's source
- **AND THEN** the coverage report marks the adjustment as `unknown`

#### Scenario: Bars come from the pinned source only

- **WHEN** a symbol's window is read and the federated view holds rows from more than one source
- **THEN** every returned bar originates from the source declared for its entity type's family, and no other source contributes to the series

#### Scenario: Provider is read-only

- **WHEN** the provider serves any read
- **THEN** no INSERT/UPDATE/DELETE is issued against any store it touches
