## 1. Move and rename

- [x] 1.1 Move the repository to the workspace as `~/finddata/fd-paper-trading` (`git mv` the directory so history is preserved), and verify `git log --oneline` still shows the three existing commits and `git status` is clean
- [x] 1.2 Rename the importable package directory `src/fd_paper_trading` → `src/fd_paper_trading` with `git mv`, and verify `python -c "import fd_paper_trading"` succeeds after dependency install
- [x] 1.3 Substitute the retired identifiers (`fd_paper_trading`, `fd-paper-trading`, `paper_trading`) across the 63 live files with a codemod, leaving `openspec/changes/archive/` untouched, and verify `grep -rE "fd_paper_trading|fd-paper-trading|paper_trading"` outside the archive returns nothing
- [x] 1.4 Hand-fix the two non-substitutable spots: the sandbox worker's `python -m` module path string and the MCP server name, and verify by running the sandbox subprocess test (`tests/unit/test_sandbox_worker.py`) — it must spawn the worker successfully
- [x] 1.5 Update `pyproject.toml`: distribution name, console script, MIT license declaration, removal of the absolute-path `tool.uv.sources` entry for `fd-open-data-mcp` in favour of a normal versioned dependency; verify `uv sync` resolves with no local path in the lockfile for that package
- [x] 1.6 Update the default database name to `paper_trading` in `config.py` and `.env.example`, and verify the documented default and the code default agree by grep
- [x] 1.7 Rename the MCP client environment variables to `PAPER_TRADING_API_URL` / `PAPER_TRADING_API_TOKEN` and confirm the old names are not read; verify by starting the MCP server with only the old variable set and observing it does not treat it as its credential
- [x] 1.8 Run the three CI gates and require all green: backend `uv run pytest`, frontend `npm test` in `web/`, and Playwright e2e — record the tail of each run in the change report

## 2. Remove the third-party trading framework

- [x] 2.1 Introduce the owned instrument value type (symbol, display name, venue, price precision, quantity precision) and switch the provider interface, providers, and consumers to it; verify `grep -rn "nautilus" src/` returns nothing
- [x] 2.2 Delete `scripts/spike_nautilus_fill.py` and the `nautilus_trader` dependency, then verify `uv sync` succeeds and the virtualenv size drops by roughly 440 MB (`du -sh .venv`)
- [x] 2.3 Verify a clean install needs no third-party trading framework: create a throwaway environment from the lockfile and run the backend test suite in it
- [x] 2.4 Remove the now-obsolete framework deprecation-warning filter from the pytest configuration and verify the suite is still warning-clean

## 3. Market-data concept family as configuration

- [x] 3.1 Replace the hardcoded concept code / entity type / frequency with configuration plus a live-family default; verify a unit test selects a family by configuration and reads only that family's concept
- [x] 3.2 Verify deprecated concepts degrade to a data gap rather than an exception, with a test asserting no data and no raise
- [x] 3.3 Verify deterministic single-concept selection when several concepts match a family, with a test proving only one is read
- [x] 3.4 Document the available live families and the configuration keys in `.env.example` and the README, and verify the documented keys match the code by grep
- [x] 3.5 Point a local run at a live family and verify real bars come back for a known symbol (an end-to-end observation recorded in the change report)

## 4. MCP tool surface

- [x] 4.1 Add the `create_account` tool and verify an agent can open an account for a fresh user who has none, observing the returned account and its default starting balance
- [x] 4.2 Add the `list_positions` tool and verify per-symbol quantity, average cost, and unrealized P&L for an account with an open position
- [x] 4.3 Add the `search_instruments` and `get_bars` tools and verify symbol discovery and a chronological historical series for a known symbol
- [x] 4.4 Implement inbound credential forwarding: read the caller's `Authorization` from the raw request, forward it to the REST API, and fall back to the configured token only when absent; verify by a test asserting two distinct inbound credentials reach the API as two distinct users
- [x] 4.5 Verify the delegation is genuinely per-user by an integration check: create two users, place an order as one, and confirm the other cannot see the account, order, or position
- [x] 4.6 Verify the fail-closed path: a call with no inbound credential and no configured token returns an authorization error rather than reaching the API unauthenticated
- [x] 4.7 Verify the stdio path still works with only the configured token set
- [x] 4.8 Remove the bundled CopilotKit runtime directory and its CI references, and verify the SPA builds and the frontend gates still pass without it

## 5. Package surface and publication

- [x] 5.1 Add the `LICENSE` file with MIT text and verify the package metadata and the file agree
- [x] 5.2 Rename the Chinese README to `README.zh-CN.md`, cross-link the pair, and verify both files link to each other
- [x] 5.3 Update the READMEs to describe the service as it now behaves: the renamed identifiers, the concept-family configuration, the new tools, identity delegation, and the paper-only boundary
- [x] 5.4 Run a full-history secret scan (`gitleaks detect` or equivalent) and verify zero findings before any push
- [x] 5.5 Create the remote repository as public under the workspace's majority organization and push; verify `git ls-remote` succeeds and the repository description carries the line mark and topics per the public-surface contract
- [x] 5.6 Record the organization-consolidation debt (the upstream docs leave it undecided; public repositories are currently split) in the change report rather than resolving it here

## 6. Deployment artifacts

- [x] 6.1 Write the `Dockerfile` serving API, WebSocket, and built SPA from one process, and verify by building the image locally and observing the SPA and a REST endpoint respond
- [x] 6.2 Register the NodePort in `fd-infra-deploy`'s port ledger (next free adjacent port, 31894 unless taken) and verify the ledger row exists before any manifest references it
- [x] 6.3 Provision the dedicated PostgreSQL instance on a cheap host following the per-service convention (adjacent port to the existing lex/wire instances), with a documented backup step, and verify connectivity from the cluster mesh
- [x] 6.4 Add the workload manifests to `fd-infra-deploy`'s production manifest tree — Deployment, Service, probes, resource requests/limits, and the secret reference — and verify they fit the namespace quota or raise it in the same commit
- [x] 6.5 Create the cluster secret with the database URL and the service's own configuration, and verify the pod starts and completes an Alembic migration to head
- [x] 6.6 Add the ArgoCD application and verify the service reaches a healthy state and that an out-of-band manifest edit is reverted
- [x] 6.7 Verify end-to-end over the mesh: create a user, open an account, place an order, and read the position back through the deployed instance's API
- [x] 6.8 Verify the deployment works with no public DNS record (mesh callers and the gateway only)

## 7. 上牌 and gateway registration

- [x] 7.1 Write the tool-surface manifest declaring every tool's exposure level, the eligible skill list, the shared-instance hosting form, and the expected tool count
- [x] 7.2 Add the surface selfcheck asserting both directions (manifest ↔ registered tools, commercial set closure) and verify it fails when a tool is added without a manifest update
- [x] 7.3 Register the server with the gateway and PATCH the snapshot so the registered tool count matches the manifest; verify by reading the gateway's stored entry
- [x] 7.4 Add the ledger row in `MCP-REGISTRY.md` recording upstream, tool count, surface status, hosting form, and — honestly — that per-user consumption goes over the mesh because the gateway does not forward caller credentials
- [x] 7.5 Verify an anonymous initialize against the registered path returns the expected authorization response, and that a tools/list on the mesh endpoint returns the manifest's tool set

## 8. Cross-cutting verification

- [x] 8.1 Re-run the full test pyramid on the renamed, framework-free tree and require backend, frontend, and browser suites all green; record the totals
- [x] 8.2 Verify the rename did not regress the silent-degradation path: run a live paper strategy run against the simulated provider and confirm bars are actually evaluated (not skipped) for both the template track and the custom-code sandbox track
- [x] 8.3 Run `openspec validate paper-trading-standup --strict` and fix any reported errors
- [x] 8.4 Backfill the `TBD` Purpose placeholders in the archived specs touched by this change (`market-data`, `order-execution`, `test-suite`) so the strict validation gate is not blocked at archive time
