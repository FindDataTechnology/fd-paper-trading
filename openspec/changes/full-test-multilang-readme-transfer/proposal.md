## Why

The project is bootstrapped and functional but needs three things before it can be handed off to the `finddatatechnology` GitHub organization: (1) a full test run to confirm everything passes, (2) a multi-language README so the org's international audience can understand the project, and (3) the actual transfer to the org. Doing these together ensures the repo is verified, documented, and in the right place in one shot.

## What Changes

- Run the full test suite (`uv run pytest`) and fix any failures discovered.
- Expand the existing `README.md` into a multi-language version (at minimum English + Chinese, matching the project's audience).
- Transfer the repository to the GitHub organization `finddatatechnology` (requires org owner permissions and configuring the remote).

## Capabilities

### New Capabilities
- `multi-lang-readme`: Multi-language README with language switcher, covering project overview, setup, architecture, and usage.

### Modified Capabilities
- `test-suite`: Run the full test suite to verify all tests pass; fix any failures found during the run.

## Impact

- **Tests**: All test files in `tests/` will be executed; failures may require fixes in `src/fd_trade/` or `tests/`.
- **Documentation**: `README.md` will be rewritten with multi-language support (inline language switcher or separate `README_<lang>.md` files).
- **Git remote**: The remote URL will change from the current user repo to `finddatatechnology/fd-trade`. Requires `gh` CLI or GitHub UI for the transfer, then `git remote set-url`.
- **CI/CD**: Any GitHub Actions workflows in `.github/` may need path or secret updates after transfer.
