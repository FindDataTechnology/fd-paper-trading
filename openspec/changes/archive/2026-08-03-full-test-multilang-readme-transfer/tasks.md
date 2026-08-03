## 1. Run Full Test Suite

- [x] 1.1 Run backend tests: `uv run pytest` and capture results
- [x] 1.2 Fix any backend test failures discovered in 1.1
- [x] 1.3 Run frontend component tests: `cd web && npx vitest run` and capture results
- [x] 1.4 Fix any frontend component test failures discovered in 1.3
- [x] 1.5 Run frontend e2e tests: `cd web && npx playwright test` (or note as skipped if browser unavailable)

## 2. Multi-Language README

- [x] 2.1 Rewrite `README.md` in English with language switcher banner, covering: overview, features, tech stack, quick start, architecture, commands, contributing
- [x] 2.2 Create `README_zh.md` as a Chinese translation with identical language switcher banner and code blocks
- [x] 2.3 Verify both files render correctly on GitHub (language switcher links work, code blocks intact)

## 3. Transfer to GitHub Organization

- [x] 3.1 Verify user has admin permissions on the `finddatatechnology` GitHub org
- [x] 3.2 Transfer the repo via `gh repo transfer finddatatechnology/fd-trade` (or GitHub UI)
- [x] 3.3 Update local remote: `git remote set-url origin git@github.com:finddatatechnology/fd-trade.git`
- [x] 3.4 Verify the transfer succeeded by checking `gh repo view` and pushing to the new remote
