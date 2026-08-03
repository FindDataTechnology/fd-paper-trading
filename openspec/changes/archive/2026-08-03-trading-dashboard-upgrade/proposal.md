## Why

The current web UI requires users to navigate to an instrument page to place orders and doesn't provide a watchlist or quick position management — it feels like an API explorer, not a trading app. Users need a professional trading workspace where they can monitor multiple symbols, see live prices, and execute trades quickly without excessive navigation.

## What Changes

**New:** Transform the Dashboard into a full-featured trading workspace with:
- Watchlist component showing live quotes for tracked symbols
- Persistent order panel updated via click-to-select symbols
- Position table with close/reduce action buttons
- Daily P&L summary across all positions

**Modified:** Instrument pages will remain for chart/historical analysis but become optional "deep dive" destinations rather than required for trading.

## Capabilities

### New Capabilities
- `trading-dashboard`: Centralized trading workspace with watchlist, order panel, and position actions
- `position-actions`: Quick close/reduce functionality on existing positions
- `daily-pnl-tracking`: Calculate and display total daily P&L from realized gains plus unrealized mark-to-market

### Modified Capabilities
- None (existing capabilities preserved)

## Impact

- **Frontend components**: New `Watchlist`, `OrderPanel` components; modified `PositionsTable`, `Dashboard`
- **State management**: Add symbol selection context so order panel updates anywhere in app
- **Real-time streaming**: Leverage existing WebSocket stream for live quotes in watchlist
- **API**: No new backend endpoints needed (positions already have close logic, just exposed via UI)
- **Navigation**: Optional hotkey support (not scope of initial implementation)
