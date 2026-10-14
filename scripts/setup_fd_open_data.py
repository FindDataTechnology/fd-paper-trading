#!/usr/bin/env python
"""Bootstrap the fd-open-data ontology used by the fd-open-data market-data provider.

Runs the library's own bootstrap pipeline in order, then resolves the app's
curated symbol universe to ontology entity IDs and prints a coverage report:

    migrate            -> create all ontology tables (idempotent)
    import-catalog     -> import fd-* provider registries (needs the sibling
                          fd-akshare checkout's registry.db; skipped with a
                          warning when absent)
    consume-concepts   -> indicator_defs -> concepts
    propose-bindings   -> column -> concept bindings
    seed-entities      -> per-source stock/country identifiers

The ontology database is configured via ``FD_OPEN_DATA_MCP_DATABASE_URL``
(see the library's own docs); it defaults to a local sqlite file.

Usage:
    uv run python scripts/setup_fd_open_data.py
"""

from __future__ import annotations

import sys


def main() -> int:
    from fd_open_data_mcp.db import get_database
    from fd_open_data_mcp.migrate import migrate
    from fd_open_data_mcp.semantic.concepts import consume_indicator_defs
    from fd_open_data_mcp.semantic.bindings import propose_bindings
    from fd_open_data_mcp.entities.resolver import (
        seed_country_identifiers,
        seed_stock_identifiers,
    )

    print("== migrate ==")
    r = migrate()
    print(f"  {r['table_count']} tables at {r['database_url']}")

    print("== import-catalog ==")
    try:
        from fd_open_data_mcp.catalog.importer import import_all

        for result in import_all():
            print(f"  {result}")
    except Exception as exc:  # noqa: BLE001 - missing fd-akshare registry is expected in some setups
        print(f"  WARNING: catalog import failed ({exc}).")
        print("  The akshare catalog comes from the sibling fd-akshare registry.db;")
        print("  without it there are no functions/bindings and reads will return")
        print("  no data. Point the library at a populated registry and re-run.")

    session = get_database().get_session()
    try:
        print("== consume-concepts ==")
        print(f"  {consume_indicator_defs(session)}")
        print("== propose-bindings ==")
        print(f"  {propose_bindings(session)}")
        print("== seed-entities ==")
        print(f"  stocks={seed_stock_identifiers(session)}")
        try:
            print(f"  countries={seed_country_identifiers(session)}")
        except Exception as exc:  # noqa: BLE001 - country taxonomy needs a sync pg driver + remote DB
            print(f"  WARNING: country seeding skipped ({exc}).")
            print("  Not needed for stock market data; the country taxonomy lives")
            print("  on the remote ontology Postgres (see fd-open-data-mcp docs).")

        # Resolve the app's curated symbol universe to entity IDs and report
        # coverage; unresolved symbols will return no bars/quotes until the
        # ontology seeds them (e.g. via migrate-astock-daily or entity import).
        from sqlalchemy import select

        from fd_open_data_mcp.models import Entity
        from fd_trade.marketdata.simulated import _INSTRUMENT_UNIVERSE

        print("== curated universe resolution ==")
        resolved = 0
        for symbol, name in _INSTRUMENT_UNIVERSE:
            entity = session.execute(
                select(Entity).where(
                    Entity.entity_type == "stock", Entity.code == symbol.upper()
                )
            ).scalars().first()
            if entity is not None:
                resolved += 1
                print(f"  OK    {symbol:<6} -> entity {entity.id} ({name})")
            else:
                print(f"  MISS  {symbol:<6} ({name})")
        print(f"  {resolved}/{len(_INSTRUMENT_UNIVERSE)} symbols resolved")
    finally:
        session.close()

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
