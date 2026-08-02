## ADDED Requirements

### Requirement: Full test suite execution and verification

The system SHALL execute the complete test suite (backend unit tests, backend e2e tests, frontend component tests, and frontend e2e tests if a browser is available) and all tests SHALL pass. Any test failures discovered during execution SHALL be investigated and fixed before the change is considered complete.

#### Scenario: Backend tests pass
- **WHEN** `uv run pytest` is executed from the project root
- **THEN** all tests in `tests/unit/`, `tests/e2e/`, and `tests/` pass with zero failures

#### Scenario: Frontend component tests pass
- **WHEN** `npm test` (or `npx vitest run`) is executed from the `web/` directory
- **THEN** all Vitest component tests pass with zero failures

#### Scenario: Frontend e2e tests pass or are explicitly skipped
- **WHEN** `npx playwright test` is executed from the `web/` directory
- **THEN** either all Playwright tests pass, or any skipped tests are documented with a reason (e.g. browser not available in CI)
