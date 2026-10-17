# Vendored: fd_quant contract layer

**Do not edit anything in this directory by hand**, except `README.md`.

`fd_quant/contracts/models.py` and `schemas/*.json` are **byte-identical copies**
of their upstream counterparts in the `fd-quant` repository root, pinned by
sha256 in `VENDOR.json`.

## Why a copy instead of a dependency

`fd-quant` has no distribution channel today: it is not published to an index and
is not even a git repository, while this repo installs with `uv sync --frozen`
against a committed lockfile and builds an image that copies only this
repository. A path dependency (`../fd-quant`) cannot resolve in CI or in the
image, so a mirror is the only honest option that keeps the engine's weight rules
identical to the contract's.

This is a **distribution workaround, not a re-implementation**. The rules are
not restated here: the identical file is what runs, so the engine cannot drift
from the contract by transcription error. Switching to a real package dependency
is an explicit deliverable in
`openspec/changes/strategy-weight-mode/tasks.md` (task 1.2).

## Layout is load-bearing

Upstream's `models.py` resolves its JSON Schemas as
`Path(__file__).parents[2] / "schemas"`, so the mirror reproduces upstream's
repository layout exactly: `<vendor>/fd_quant/contracts/models.py` with
`<vendor>/schemas/` as a sibling of the `fd_quant` package. Moving these files
to any other depth passes the checksum and silently breaks `load_schema`; the
drift gate asserts reachability to catch that.

## Commands

```bash
python3 scripts/check_contract_drift.py             # verify pin + upstream drift
python3 scripts/check_contract_drift.py --sync      # adopt upstream (author action)
python3 scripts/check_contract_drift.py --selfcheck # prove the gate can fail
```

Import it as `fd_paper_trading._vendor.fd_quant` (never as top-level `fd_quant`,
so a future real install of the package cannot be shadowed).
