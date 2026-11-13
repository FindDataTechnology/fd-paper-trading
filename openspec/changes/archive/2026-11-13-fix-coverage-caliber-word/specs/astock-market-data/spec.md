## MODIFIED Requirements

### Requirement: Provider declares its adjustment caliber in coverage

The provider SHALL implement `get_bars_with_coverage` with the same contract as the
fd-open-data provider (`missing` / `market_closed` / `fill_failed` / `fingerprint`), and the
coverage result SHALL additionally carry the provider's adjustment caliber **as declared by
the price-source registry** — for the `astock_daily` family the declaration is `qfq`
(前复权; the contract vocabulary is `qfq` / `hfq` / `none` / `not_applicable`). The module
constant SHALL be used only as the fallback when no registry declaration is available, and
SHALL NOT override a declaration. When the caliber cannot be determined, the report SHALL
say so explicitly rather than defaulting silently.

#### Scenario: Coverage reports the adjustment caliber

- **WHEN** a coverage check completes for a symbol
- **THEN** the report includes the caliber declared for the provider's source (`adjusted = "qfq"` for `astock_daily`) so consumers can document the caliber the series was read at
- **AND** that value equals the registry declaration rather than a literal carried by the caller

#### Scenario: Indeterminable caliber is stated, not defaulted

- **WHEN** the provider cannot establish which caliber it read
- **THEN** the coverage report marks the caliber as unknown instead of asserting a value
