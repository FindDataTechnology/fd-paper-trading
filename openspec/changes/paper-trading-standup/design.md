## Context

See `proposal.md` for motivation. The constraints that shape this design:

- The repository has never been pushed (`git ls-remote origin` → *Repository not found*), so
  every identifier is still free. Three commits, one branch, no LICENSE.
- 87 tracked files mention the old identifiers; 24 of those are archived change records.
- `nautilus_trader` occupies 444 MB of a 782 MB virtualenv. Its only uses in `src/` are
  `Instrument` type annotations and `TestInstrumentProvider`; `scripts/spike_nautilus_fill.py`
  is its only substantive consumer. Fills, backtests, and the sandbox are already pure Python.
- The MCP server reads one `TRADE_API_TOKEN` at import time and sends it for every call,
  while the REST layer already resolves per-user `fdt_` tokens. The gap is forwarding, not
  capability.
- The roster of live concept families in the catalog: index (`IDX_*`), crypto (`CRYPTO_*`),
  and fund NAV (`nav.*`). The stock `price.*` family is deprecated by a 2026-10-07 curation
  decision that this change does not reopen.
- The registry gateway strips `Authorization` and `X-Authorization` on egress for every
  upstream except one hardcoded internal server, so a token cannot survive a call routed
  through it.
- `mcp-service-deploy` names `fd-infra-deploy`'s production manifest tree as the single
  source of truth for a service's manifests; the newest service (`fd-text-core`) instead
  keeps manifests in its own repository, so the two patterns are both in use.

## Goals / Non-Goals

**Goals:**

- A service that can be deployed, sold, and consumed independently, with no build-time or
  runtime coupling to 柏讯 or any other line.
- A conversational client can open an account, inspect positions and history, and trade —
  as the correct end user, with no cross-account exposure.
- Real market data behind the trading loop again, without reopening the price-data
  curation decision.

**Non-Goals:**

- Real-money or broker-connected trading. The service stays paper-only; that boundary is
  what keeps compliance questions out of scope.
- The 壹座 pack (skill text, suite manifest, persona). It edits the paas repository, which
  belongs to a separate change in its own session.
- A public subdomain. Mesh + gateway access is sufficient to operate; a domain is a
  branding step to take when the service is actually marketed.
- Consolidating the workspace's repository organizations. The upstream docs record that
  choice as undecided; this change picks the majority practice and records the debt.

## Decisions

### D1: Remove `nautilus_trader`; own the instrument type

Introduce an owned `Instrument` value type (symbol, display name, venue, price precision,
quantity precision) and delete the dependency plus the spike script.

*Alternatives considered:* keep Nautilus for a future live-trading node (rejected — that
node does not exist, real trading needs a broker integration and licensing, and carrying
444 MB to reserve the option is a bad trade); keep it only in tests (rejected — it would
stay in the dependency graph and the image).

*Consequence:* `get_instrument` remains an existence-and-metadata check; the fill calculator
never needed more.

### D2: Concept family is configuration, defaulting to a live family

The fd-open-data provider resolves `(concept code, entity type, frequency)` from
configuration, defaulting to a family that is live in the catalog. A deprecated concept
yields a data gap, not an error.

*Alternatives considered:* register new stock price concepts (rejected — violates the
2026-10-07 decision); hardcode a different family (rejected — the same rot would recur);
build a file-upload ingestion path now (rejected — it is a real feature, not a
configuration option, and it belongs in a later change).

*Open:* which family ships as the default demo family — see Open Questions.

### D3: Identity delegation by forwarding the inbound credential

The MCP server forwards the inbound `Authorization` to the REST API and falls back to the
operator-configured token only when no inbound credential is present. The credential is
read from the raw request headers.

*Alternatives considered:* give each deployment its own accounts (rejected — it defeats a
single shared instance and multiplies operations); the registry's egress credential vault
(rejected — disabled in this deployment and requires a different storage backend); the
gateway's identity-forwarding header (rejected — it carries no secret, so any caller who can
reach the service could impersonate); one shared service credential (rejected — that is the
bug being fixed).

*Consequence, recorded explicitly:* because the gateway strips the credential, a client
consuming this service for per-user identity must reach the service directly over the mesh,
as the market-face server already does. The service is still registered in the gateway for
discoverability, but that path carries no end-user identity. The registry-side
scope/billing/audit gates are therefore not in this service's path — acceptable because the
service owns its own accounts, quotas, and ledger. The registry ledger must record this
honestly.

### D4: Deployment manifests in `fd-infra-deploy`

Follow the written contract and place the workload manifests in the infrastructure
repository's production tree.

*Alternatives considered:* follow `fd-text-core` and keep manifests in the service
repository (rejected as the default — the written contract exists precisely so a service's
deployment cannot live only in the service repository; but the contract-vs-precedent
divergence is recorded as debt rather than silently inherited).

### D5: Dedicated PostgreSQL on a shared host

Follow the per-service convention (as the lex and wire lines do): a dedicated database
instance on a cheap host, reached over the mesh, with its connection string supplied
through a cluster secret. No shared database is available in the cluster to reuse.

*Alternatives considered:* an in-cluster database StatefulSet (rejected — it exists once,
namespace-scoped, and would introduce a new form); sharing another line's instance
(rejected — cross-line coupling, which this change exists to avoid).

### D6: NodePort and quota

Allocate the next free adjacent NodePort and register it in the ledger before the manifest.
Raise the namespace quota in the same change if the workload does not fit; the quota's
history shows five prior raises driven by exactly this situation.

### D7: Keep the SPA, remove the bundled chat runtime

The SPA (desktop workspace plus mobile shell) stays: it provides what a conversational
client cannot (charts, click-to-trade, mobile), and it is the surface a customer sees when
the service is sold standalone. The bundled CopilotKit runtime is removed: it is a second,
drifting copy of the assistant instructions that the platform pack will own.

### D8: License file is added, not just declared

Declare MIT in package metadata *and* ship a `LICENSE` file. The three existing public
repositories declare a license without shipping the file, which leaves them not actually
open-source in the legal sense; that is recorded as debt rather than copied.

### D9: Rename mechanics

`git mv` for the package directory plus a codemod over the 63 live files; archived change
records are left untouched as historical evidence. Two spots need manual attention rather
than blind substitution: the sandbox worker's `python -m` module path (a string, not an
import) and the MCP server name (which changes the tool namespace).

## Risks / Trade-offs

- **The sandbox module path is a string.** A missed substitution degrades silently: a
  backtest fails loudly, but a live paper run skips the bar without surfacing an error. →
  Mitigated by the existing test that spawns a real subprocess; that test is the gate, and
  it must be green before and after.
- **Quota exhaustion blocks rollout.** → Raise the quota in the same change; the namespace
  history shows this is the recurring failure mode.
- **The chosen concept family could later be deprecated.** → Family is configuration, so the
  fix is a config value, and a deprecated concept already degrades to a data gap.
- **Removing Nautilus changes instrument identity used by tests.** → The rename and the
  removal land on one branch gated by the full test pyramid.
- **Publishing the repository exposes history.** → A full-history secret scan runs before the
  first push; the working tree currently shows no credentials and `.env` was never committed.
- **Mesh-direct consumption bypasses registry gates.** → Accepted deliberately; the registry
  ledger records the consumption path, and the service's own account model is the authority.

## Migration Plan

1. Rename and move on a branch; run the three CI gates (backend pytest, frontend Vitest,
   Playwright e2e) until green.
2. Remove Nautilus; re-run the gates, watching the sandbox subprocess test specifically.
3. Add the market-data family configuration and the four new tools; re-run gates.
4. Secret-scan the full history; publish the repository; create the remote.
5. Build the image; register the port in the ledger; apply manifests; create the secret.
6. Register with the gateway; add the ledger row; run the surface selfcheck.
7. Rollback: before publish, nothing is at risk (no remote, no deployment). After publish,
   the image tag pins the running version, so a rollback is a manifest revert.

## Open Questions

- **Which concept family ships as the default demo family.** The families differ in fitness:
  the crypto family is the only one with full OHLCV and round-the-clock bars (so it is the
  only one that can demonstrate intraday trading); the index family supports technical
  analysis but cannot be traded directly; fund NAV is a single daily series suited to
  accumulation-style demos. The choice is a configuration value and therefore does not
  change the specs, the approach, or the tasks — but the externally visible framing of a
  crypto paper-trading product is a business decision, so the shipped default stays the
  neutral combination (index + fund NAV) and the crypto family is available by
  configuration until that decision is made explicitly.
- **Whether and when to publish to the package index.** The repository being public is
  required for "others can adopt it independently"; publishing a distribution is an
  additional, separable step.
