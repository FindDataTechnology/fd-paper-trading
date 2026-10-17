## MODIFIED Requirements

### Requirement: Backtest failure is explicit

If bar loading fails (no data for symbol/window), the window is only **partially** covered (missing intervals that are not confirmed non-trading days), or a bar evaluation fails (sandbox timeout/crash), the run SHALL transition to `FAILED` with a human-readable `error` message; partial equity points MAY be retained. A `FAILED` run SHALL NOT appear as `COMPLETED`.

"Partially covered" SHALL mean: the data provider reported intervals that are absent **and** not confirmed to hold no data upstream. Intervals confirmed as non-trading (market holidays, probed and empty) SHALL NOT cause failure — a window whose only absences are market closures is complete for every day the series trades.

When the provider cannot report coverage, the window SHALL NOT be judged incomplete on that basis; the run records that coverage was not checked (see the backtest-data-coverage capability).

#### Scenario: No data fails the run
- **WHEN** a backtest is requested for a symbol with no bars in the window
- **THEN** the run transitions to `FAILED` with an error naming the symbol

#### Scenario: Partial coverage fails the run
- **WHEN** the provider reports missing intervals that were not confirmed non-trading
- **THEN** the run transitions to `FAILED` with an error naming the symbol and at least one missing interval

#### Scenario: Holiday-only gaps do not fail the run
- **WHEN** the only absences in the window are intervals confirmed to hold no data upstream
- **THEN** the run completes normally

#### Scenario: Sandbox crash fails the run
- **WHEN** a code-track strategy's sandboxed evaluation crashes on a bar
- **THEN** the run transitions to `FAILED` with an error identifying the failing bar
