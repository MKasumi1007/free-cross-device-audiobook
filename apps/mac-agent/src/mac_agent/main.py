from __future__ import annotations

import uvicorn

from .app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=17832, log_level="info")


if __name__ == "__main__":
    main()
