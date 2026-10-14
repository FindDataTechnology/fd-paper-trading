"""Uvicorn entry point: ``uv run fd-paper-trading`` or ``uv run python -m fd_paper_trading.main``."""

import uvicorn

from .api.app import create_app

app = create_app()


def main() -> None:
    uvicorn.run("fd_paper_trading.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
