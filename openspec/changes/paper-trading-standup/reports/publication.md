# Publication record (task 5.5/5.6)

- Repo: https://github.com/FindDataTechnology/fd-paper-trading (public, 2026-10-14)
- Line mark trio per public-repo-surface: description carries `[wire]`, topics include
  `wire`, both READMEs carry the /products/wire banner and cross-link.
- Full-history gitleaks scan before push: no leaks found (10 commits, ~2.4 MB).
- CI workflow (.github/workflows/ci.yml) is present; GHA visibility now public —
  free-tier minutes apply to public repos at no cost.

## Recorded debt (not resolved here)

- **Org consolidation is undecided** (workspace PRODUCT.md). Public fd-* repos are split
  today: fd-open-data-mcp / fd-industry-data / fd-daas-mcp live under FindDataTechnology,
  fd-daas-mcp is duplicated under FindDataOfficial, and fd-text-core's public mirror sits
  on a personal account (scs001) from the GHA-billing-block episode. This repo chose
  FindDataTechnology (the majority home). Any future consolidation move is a separate,
  workspace-level decision.
- The three existing public repos (fd-open-data-mcp, fd-text-core, fd-industry-data)
  declare MIT in pyproject but ship no LICENSE file — not actually open source in the
  legal sense. This repo ships the file; backfilling the others is separate debt.
