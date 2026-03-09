from __future__ import annotations

import asyncio
from importlib import import_module
import os
from pathlib import Path
from typing import Optional, Callable, Protocol, cast

from benefitradar.mcp_server.tools import (
    handle_benefit_match,
    handle_recent_updates,
    handle_search,
    handle_sql,
    handle_top_trends,
)

def _db_path() -> Path:
    return Path(os.getenv("RADAR_DB_PATH", "data/radar_data.duckdb"))


def _search_db_path() -> Path:
    return Path(os.getenv("RADAR_SEARCH_DB_PATH", "data/search_index.db"))


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _list_tool_specs() -> list[dict[str, object]]:
    return [
        {
            "name": "search",
            "description": "Search indexed articles by natural-language query.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["query"],
            },
        },
        {
            "name": "recent_updates",
            "description": "List recently collected articles from DuckDB.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1},
                },
            },
        },
        {
            "name": "sql",
            "description": "Execute read-only SQL (SELECT/WITH/EXPLAIN) on DuckDB.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "top_trends",
            "description": "Show top entity frequencies from recent article matches.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1},
                },
            },
        },
        {
            "name": "benefit_match",
            "description": "Match benefits or subsidies from recent articles.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "days": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1},
                },
            },
        },
    ]


def _call_tool_handler(name: str, arguments: object) -> str:
    args = _coerce_args(arguments)

    if name == "search":
        return handle_search(
            search_db_path=_search_db_path(),
            db_path=_db_path(),
            query=str(args.get("query", "")),
            limit=_as_int(args.get("limit"), 20),
        )
    if name == "recent_updates":
        return handle_recent_updates(
            db_path=_db_path(),
            days=_as_int(args.get("days"), 7),
            limit=_as_int(args.get("limit"), 20),
        )
    if name == "sql":
        return handle_sql(db_path=_db_path(), query=str(args.get("query", "")))
    if name == "top_trends":
        return handle_top_trends(
            db_path=_db_path(),
            days=_as_int(args.get("days"), 7),
            limit=_as_int(args.get("limit"), 10),
        )
    if name == "benefit_match":
        return handle_benefit_match(
            db_path=_db_path(),
            query=str(args.get("query", "")),
            days=_as_int(args.get("days"), 30),
            limit=_as_int(args.get("limit"), 10),
        )
    return f"Unknown tool: {name}"


class _McpApp(Protocol):
    def list_tools(self) -> Callable[[Callable[..., object]], Callable[..., object]]: ...

    def call_tool(self) -> Callable[[Callable[..., object]], Callable[..., object]]: ...

    async def run(self, read_stream: object, write_stream: object, options: object) -> None: ...

    def create_initialization_options(self) -> object: ...


class _ServerCtor(Protocol):
    def __call__(self, name: str) -> _McpApp: ...


class _ToolCtor(Protocol):
    def __call__(self, **kwargs: object) -> object: ...


class _TextContentCtor(Protocol):
    def __call__(self, *, type: str, text: str) -> object: ...


class _StdioContext(Protocol):
    async def __aenter__(self) -> tuple[object, object]: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: Optional[BaseException],
        traceback: object,
    ) -> object: ...


class _StdioServer(Protocol):
    def __call__(self) -> _StdioContext: ...


def _coerce_args(arguments: object) -> dict[str, object]:
    if not isinstance(arguments, dict):
        return {}

    raw_args = cast(dict[object, object], arguments)
    coerced: dict[str, object] = {}
    for key, value in raw_args.items():
        if isinstance(key, str):
            coerced[key] = value
    return coerced


def create_app() -> _McpApp:
    server_module = import_module("mcp.server")
    types_module = import_module("mcp.types")
    server_ctor = cast(_ServerCtor, getattr(server_module, "Server"))
    tool_ctor = cast(_ToolCtor, getattr(types_module, "Tool"))
    text_content_ctor = cast(_TextContentCtor, getattr(types_module, "TextContent"))

    app = server_ctor("benefitradar")

    @app.list_tools()
    async def list_tools() -> list[object]:
        return [tool_ctor(**tool_spec) for tool_spec in _list_tool_specs()]
    _ = list_tools

    @app.call_tool()
    async def call_tool(name: str, arguments: object) -> list[object]:
        result = _call_tool_handler(name, arguments)
        return [text_content_ctor(type="text", text=result)]
    _ = call_tool

    return app


async def main() -> None:
    stdio_module = import_module("mcp.server.stdio")
    stdio_server = cast(_StdioServer, getattr(stdio_module, "stdio_server"))

    app = create_app()
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
