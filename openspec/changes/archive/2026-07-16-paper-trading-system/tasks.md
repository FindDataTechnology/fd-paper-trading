## 1. Spec Artifacts

- [x] 1.1 Write `proposal.md` (why, what changes, capabilities, impact) framing this as a spec-only formalization of the as-built system
- [x] 1.2 Write `design.md` recording the as-built architecture and decisions (D1 stateless calculator, D2 Postgres isolation, D3 swappable provider, D4 auth, D5 API, D6 off-loop execution)
- [x] 1.3 Write `specs/user-auth/spec.md` (registration, login, JWT, API tokens, bearer auth)
- [x] 1.4 Write `specs/paper-accounts/spec.md` (ownership, creation, summary, deposit/withdrawal, reset, isolation, reads)
- [x] 1.5 Write `specs/market-data/spec.md` (provider interface, simulated, Polygon, instrument resolution, swappability)
- [x] 1.6 Write `specs/order-execution/spec.md` (stateless calculator, market/limit fills, buying power, rejections, cancellation, ledgering, isolation)
- [x] 1.7 Write `specs/trading-api/spec.md` (versioning, standardized errors, auth, order/account/token endpoints)

## 2. Verify Specs Match As-Built Code

- [x] 2.1 Cross-check each `user-auth` scenario against `src/fd_trade/auth/` and `tests/test_auth.py` (codes: `email_exists`, `invalid_credentials`, `user_inactive`, `unauthorized`; `fdt_` prefix; revoke leaves JWT valid) - VERIFIED: all codes, `fdt_` prefix, and revoke-leaves-JWT-valid match
- [x] 2.2 Cross-check `paper-accounts` against `src/fd_trade/accounts/service.py` + `tests/test_accounts.py` (default 100000, signed deposit, `invalid_amount`, reset at cost basis, cross-user `404`) - VERIFIED
- [x] 2.3 Cross-check `market-data` against `src/fd_trade/marketdata/{provider,simulated,polygon}.py` (5bps spread + $0.01 floor, 300s cache, `None` on no-key/error/non-200) - VERIFIED
- [x] 2.4 Cross-check `order-execution` against `src/fd_trade/execution/{service,fill_engine}.py` + `tests/test_orders.py` (buy@ask, sell@bid, resting vs crossing limit, `insufficient_buying_power`, `not_cancellable`, signed-quantity ledger) - VERIFIED
- [x] 2.5 Cross-check `trading-api` against `src/fd_trade/api/{app,errors,schemas,routes/}.py` (`/api/v1` prefix, `202` on `POST /orders`, `validation_error` with `fields`, status-to-code map, `/health`, `/docs`) - VERIFIED
- [x] 2.6 Resolve any spec-vs-code discrepancies found in 2.1-2.5 (update the spec to match code, since this change captures as-built behavior) - RESOLVED: the 5 in-scope specs match the `paper-trading-api` baseline. Discovered the as-built repo also contains realtime WS (`/ws`), `/api/v1/instruments/*`, and `web/` (owned by the in-flight `trading-web-ui` change). Fixed `design.md`/`proposal.md`, which had falsely marked WebSocket as deferred, and documented the scope boundary. Specs left unchanged per the scoping decision.

## 3. Validate & Consolidate

- [x] 3.1 Run `openspec validate paper-trading-system` and fix any reported issues - PASS ("Change 'paper-trading-system' is valid")
- [x] 3.2 Confirm no `src/fd_trade/` files were modified by this change (spec-only) - CONFIRMED: all writes under `openspec/changes/paper-trading-system/`; `src/` untouched
- [ ] 3.3 Consolidate the five capability specs into `openspec/specs/{user-auth,paper-accounts,market-data,order-execution,trading-api}/spec.md` (the specs/ baseline this change establishes) - PERFORMED BY `/opsx:archive`: consolidation into `openspec/specs/` is the archive step (`openspec archive` applies the ADDED-Requirements deltas to the empty `openspec/specs/` and moves the change to `archive/`). Left for an explicit `/opsx:archive` invocation because it is a hard-to-reverse lifecycle action.
