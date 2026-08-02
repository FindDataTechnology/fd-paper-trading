## Context

The fd-trade project is a bootstrapped, functional multi-tenant stock paper-trading service. Before transferring it to the `finddatatechnology` GitHub organization, three things need to happen: verify all tests pass, produce a multi-language README for the org's audience, and execute the transfer. These are largely independent operational tasks that share a common "get the repo ready" goal.

## Goals / Non-Goals

**Goals:**
- Run the full test suite (backend + frontend) and fix any failures.
- Produce a multi-language README (English + Chinese at minimum) with a language switcher.
- Transfer the repo to the `finddatatechnology` GitHub org and update the local remote.

**Non-Goals:**
- Adding new features or changing the trading logic.
- Translating into more than two languages (English and Chinese) — additional languages can be added later.
- Setting up CI/CD secrets or org-level settings on the new org — that's a separate ops task.

## Decisions

### 1. Multi-language README: separate files vs. inline

**Decision**: Use separate `README.md` (English, default) + `README_zh.md` (Chinese) with a language-switcher banner at the top of each file linking to the other.

**Rationale**: Separate files are simpler to maintain, render correctly on GitHub without JavaScript, and are the convention for multi-language repos on GitHub. Inline language switching (single file with collapsible sections) is harder to scan and GitHub renders markdown without JS.

**Alternatives considered**:
- Single file with `<details>` blocks per language — clunky, hard to navigate.
- i18n framework (e.g. vue-i18n) — overkill for a README, adds build step.

### 2. Test run scope

**Decision**: Run `uv run pytest` for the backend and `cd web && npm test` (Vitest) for the frontend component tests. The Playwright e2e test will be run if a browser is available; otherwise it will be noted as skipped.

**Rationale**: This covers all test layers defined in the test-suite spec. Fixing failures is in scope; if failures are deep (e.g. Nautilus API changes), they'll be addressed in a follow-up change.

### 3. GitHub transfer approach

**Decision**: Use `gh repo transfer finddatatechnology/fd-trade` (or the GitHub UI API) to transfer. Then update the local remote with `git remote set-url origin git@github.com:finddatatechnology/fd-trade.git`.

**Rationale**: The `gh` CLI is the simplest path if the user has org admin rights. If not, the transfer must be initiated from the GitHub UI by an org owner.

## Risks / Trade-offs

- **[Test failures block transfer]** → Run tests first, fix what's broken, then transfer. If fixes are large, split into a separate change.
- **[Translation quality]** → Use a reasonable Chinese translation; native speaker review is a follow-up.
- **[Transfer requires org permissions]** → If the user doesn't have admin on `finddatatechnology`, the transfer will fail. Verify permissions before attempting.
- **[Post-transfer remote URL]** → GitHub sets up a redirect from the old URL, but the local remote should be updated explicitly to avoid confusion.
