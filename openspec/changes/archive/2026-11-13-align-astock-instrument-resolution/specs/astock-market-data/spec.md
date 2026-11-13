## MODIFIED Requirements

### Requirement: Instrument search resolves A-share entities

`search_instruments(query)` SHALL resolve A-share entities by code (e.g. `600036`,
`000333`) and by Chinese name; `get_instrument(symbol)` SHALL return the service's own
instrument value (symbol, display name, venue `XSHG`/`XSHE`, price/quantity precision) for
**syntactically valid codes of the supported A-share markets**. Instrument resolution for
this capability is syntactic by design — the interface is synchronous while the store is
asynchronous, and the A-share catalog is the exchange's own code space rather than a curated
list — so a syntactically valid but unknown code also yields an instrument value. Order
acceptance therefore SHALL NOT rely on `get_instrument` alone: the order entry SHALL refuse
the symbol with a named rejection (`unknown_instrument` when resolution itself fails,
`no_quote` when the symbol cannot be priced), which is the precondition that actually
prevents an uninstrumented symbol from being traded.

#### Scenario: Search by code and by name

- **WHEN** the search query is `600036` or `招商银行`
- **THEN** the matching A-share entity is returned with its code, name, and venue

#### Scenario: Unknown symbol stays unknown

- **WHEN** `get_instrument` is asked for a code the store does not carry, or an order is placed for it
- **THEN** order acceptance for that symbol is refused with a named rejection (`no_quote` for a syntactically valid but unpriceable code, `unknown_instrument` when resolution fails), even though `get_instrument` may resolve the code syntactically
