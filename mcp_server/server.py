from __future__ import annotations

from benefitradar.mcp_server.server import create_app, main

__all__ = ["create_app", "main"]


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
