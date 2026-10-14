# Proposal: paper-trading-standup

## Why

`fd-paper-trading` is a working multi-tenant paper-trading service, but it was never published
(its GitHub remote does not exist, there is no LICENSE, nothing is pushed) and three of
its load-bearing assumptions have rotted:

1. **Its market-data path is dead by policy.** The `fd-open-data` provider reads the
   stock `price.close` concept, which the 2026-10-07 curation decision keeps deprecated
   (price data is fetched client-side; the server catalog curates public data only).
2. **It cannot be used by several people at once.** The MCP server sends one static
   `PAPER_TRADING_API_TOKEN` for every call, so on a shared platform every caller acts as the
   same user.
3. **It carries a 444 MB dependency it no longer uses.** `nautilus_trader` is the single
   largest thing in the environment, yet fills, backtests and the sandbox are already
   pure Python; Nautilus survives only as type annotations and an existence check.

Meanwhile the pieces it needs to become a real product already exist elsewhere in this
workspace: the `mcp-surface-contract` 上牌 contract, the `mcp-service-deploy` deployment
contract, the per-service PostgreSQL convention, and the reference credential-forwarding
pattern in `facet-ops-mcp`.

Because nothing is published yet, every name below is still free to choose. After the
first release they become runtime contracts that cannot change.

## What Changes

- **Rename (BREAKING).** Repo/directory `fd-paper-trading` → `fd-paper-trading`; import package
  `fd_paper_trading` → `fd_paper_trading`; MCP server name, console script, and default database
  name follow. The `fdt_` API-token prefix stays (existing tokens depend on it for
  dispatch, and it reads as find**d**ata **t**rade).
- **Drop `nautilus_trader`.** Introduce an owned `Instrument` value type; delete the
  dependency (≈444 MB) and the spike script that is its only real consumer.
- **Market data becomes family-configurable.** The provider resolves its concept family
  from configuration instead of a hardcoded `price.close`/`stock`/`daily` triple, so live
  concept families (index, fund NAV) serve real data today. Stock `price.*` MUST stay
  deprecated — this change does not reopen that decision.
- **MCP surface grows the tools a conversational client needs**: `create_account`,
  `list_positions`, `get_bars`, `search_instruments`. Deposit/reset stay REST-only.
- **MCP identity is delegated.** The server forwards the caller's inbound `Authorization`
  and only falls back to the configured token when no header is present, so one instance
  can serve many users without cross-account access.
- **Deployment artifacts exist.** Dockerfile, ArgoCD-managed manifests in
  `fd-infra-deploy` (the single source of truth per `mcp-service-deploy`), NodePort
  31894, a dedicated PostgreSQL instance, and the quota bump that a new service needs.
- **上牌.** `tool-surface.json` + `selfcheck_surface` so the server can be referenced by
  packs and driven by agents.
- **Package surface.** MIT license declaration plus the `LICENSE` file the three existing
  public repos are missing; public repo; `README.zh-CN.md` naming per
  `public-repo-surface`.
- **Remove the CopilotKit runtime.** The bundled chat runtime duplicates what the 壹座
  pack provides; the SPA stays.

## Capabilities

### New Capabilities

- `service-identity`: the naming contract for a published paper-trading service — repo,
  import package, MCP server name, console script, database name, and the frozen token
  prefix.
- `service-deployment`: how the service is deployed, configured, and released — image
  pipeline, manifest ownership, PostgreSQL instance, NodePort ledger, resource quota,
  and the deploy-time environment contract.
- `mcp-tool-surface`: the complete MCP tool surface, caller-identity delegation on
  inbound requests, and the 上牌 manifest/selfcheck obligations.

### Modified Capabilities

- `market-data`: the provider abstraction drops its Nautilus-typed instrument in favour
  of an owned value type, and gains configuration-driven concept-family resolution
  instead of a hardcoded deprecated concept.
- `order-execution`: the fill calculator requirement no longer names Nautilus's matching
  engine — the shipped calculator is the stateless pure-Python one, and the requirement
  is restated to match what the system actually does.
- `test-suite`: the unit-test requirement names the shipped fill calculator and drops the
  Nautilus deprecation-warning clause that only existed because of that dependency.

## Impact

- **Code**: 87 files mention the old identifiers (24 of them inside `openspec/changes/archive/`,
  which is historical record and is not rewritten). Largest blocks: `openspec/` (31),
  `tests/` (25), `web/` (13), `src/` (9).
- **Runtime contracts**: `fd_paper_trading` import path and the `fd-paper-trading` MCP
  server name become permanent once published — the tool namespace
  (`mcp__fd-paper-trading__*`) is what packs and skills reference.
- **Dependencies**: `nautilus_trader` removed; `fd-open-data-mcp` moves from an editable
  sibling path to a normal versioned dependency (PyPI 0.5.37), which also removes the
  last absolute local path from `pyproject.toml`.
- **Environment**: `.venv` shrinks from ≈782 MB to ≈338 MB before the SPA is built.
- **External systems**: registry gateway (`MCP-REGISTRY.md` ledger row + registration),
  `fd-infra-deploy` (`PORTS.md` row, manifest tree, `compute-quota`), ArgoCD application.
- **Non-goals**: this change does not touch 柏讯's code, does not reopen the price-data
  curation decision, does not add a public subdomain, and does not perform the 壹座 pack
  work (that is a follow-up change, since it edits the paas repository).
