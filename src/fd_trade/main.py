"""Uvicorn entry point: ``uv run fd-trade`` or ``uv run python -m fd_trade.main``."""

import uvicorn

from .api.app import create_app

app = create_app()


def main() -> None:
    uvicorn.run("fd_trade.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
