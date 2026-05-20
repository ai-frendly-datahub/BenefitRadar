from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from benefitradar.mcp_server import server


class FakeTool:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakeTextContent:
    def __init__(self, *, type: str, text: str) -> None:
        self.type = type
        self.text = text


class FakeMcpApp:
    def __init__(self, name: str) -> None:
        self.name = name
        self.list_tools_func = None
        self.call_tool_func = None
        self.run_calls: list[tuple[object, object, object]] = []

    def list_tools(self):
        def decorator(func):
            self.list_tools_func = func
            return func

        return decorator

    def call_tool(self):
        def decorator(func):
            self.call_tool_func = func
            return func

        return decorator

    def create_initialization_options(self) -> dict[str, str]:
        return {"app": self.name}

    async def run(self, read_stream: object, write_stream: object, options: object) -> None:
        self.run_calls.append((read_stream, write_stream, options))


class FakeStdioContext:
    async def __aenter__(self) -> tuple[str, str]:
        return ("read-stream", "write-stream")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        return None


def _fake_importer(created_apps: list[FakeMcpApp]):
    def server_ctor(name: str) -> FakeMcpApp:
        app = FakeMcpApp(name)
        created_apps.append(app)
        return app

    def fake_import_module(module_name: str) -> SimpleNamespace:
        if module_name == "mcp.server":
            return SimpleNamespace(Server=server_ctor)
        if module_name == "mcp.types":
            return SimpleNamespace(Tool=FakeTool, TextContent=FakeTextContent)
        if module_name == "mcp.server.stdio":
            return SimpleNamespace(stdio_server=FakeStdioContext)
        raise AssertionError(module_name)

    return fake_import_module


def test_argument_helpers_handle_env_defaults_and_untrusted_input(
    monkeypatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "radar.duckdb"
    search_db_path = tmp_path / "search.db"
    monkeypatch.setenv("RADAR_DB_PATH", str(db_path))
    monkeypatch.setenv("RADAR_SEARCH_DB_PATH", str(search_db_path))

    assert server._db_path() == db_path
    assert server._search_db_path() == search_db_path
    assert server._as_int(7, 20) == 7
    assert server._as_int("8", 20) == 8
    assert server._as_int("bad", 20) == 20
    assert server._as_int(True, 20) == 20
    assert server._as_int(None, 20) == 20
    assert server._coerce_args({"query": "housing", 1: "ignored"}) == {"query": "housing"}
    assert server._coerce_args(["not", "a", "mapping"]) == {}


def test_list_tool_specs_exposes_expected_tools() -> None:
    specs = server._list_tool_specs()
    specs_by_name = {str(spec["name"]): spec for spec in specs}

    assert list(specs_by_name) == [
        "search",
        "recent_updates",
        "sql",
        "top_trends",
        "benefit_match",
        "quality_report",
    ]
    assert specs_by_name["search"]["inputSchema"] == {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["query"],
    }
    assert specs_by_name["sql"]["description"].startswith("Execute read-only SQL")


def test_call_tool_handler_routes_all_tools(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "radar.duckdb"
    search_db_path = tmp_path / "search.db"
    monkeypatch.setenv("RADAR_DB_PATH", str(db_path))
    monkeypatch.setenv("RADAR_SEARCH_DB_PATH", str(search_db_path))
    calls: list[tuple[str, dict[str, object]]] = []

    def make_handler(tool_name: str):
        def handler(**kwargs: object) -> str:
            calls.append((tool_name, kwargs))
            return tool_name

        return handler

    monkeypatch.setattr(server, "handle_search", make_handler("search"))
    monkeypatch.setattr(server, "handle_recent_updates", make_handler("recent_updates"))
    monkeypatch.setattr(server, "handle_sql", make_handler("sql"))
    monkeypatch.setattr(server, "handle_top_trends", make_handler("top_trends"))
    monkeypatch.setattr(server, "handle_benefit_match", make_handler("benefit_match"))
    monkeypatch.setattr(server, "handle_quality_report", make_handler("quality_report"))

    assert server._call_tool_handler("search", {"query": "housing", "limit": "3"}) == "search"
    assert server._call_tool_handler("recent_updates", {"days": "bad", "limit": True}) == (
        "recent_updates"
    )
    assert server._call_tool_handler("sql", {"query": "SELECT 1"}) == "sql"
    assert server._call_tool_handler("top_trends", {"days": 14, "limit": "2"}) == "top_trends"
    assert (
        server._call_tool_handler(
            "benefit_match",
            {"query": "youth", "days": "31", "limit": 4},
        )
        == "benefit_match"
    )
    assert server._call_tool_handler("quality_report", {"category": "benefit"}) == "quality_report"
    assert server._call_tool_handler("unknown", {"query": "x"}) == "Unknown tool: unknown"

    assert calls == [
        (
            "search",
            {
                "search_db_path": search_db_path,
                "db_path": db_path,
                "query": "housing",
                "limit": 3,
            },
        ),
        ("recent_updates", {"db_path": db_path, "days": 7, "limit": 20}),
        ("sql", {"db_path": db_path, "query": "SELECT 1"}),
        ("top_trends", {"db_path": db_path, "days": 14, "limit": 2}),
        ("benefit_match", {"db_path": db_path, "query": "youth", "days": 31, "limit": 4}),
        ("quality_report", {"db_path": db_path, "category": "benefit", "days": 30, "limit": 500}),
    ]


def test_create_app_registers_tool_callbacks(monkeypatch) -> None:
    created_apps: list[FakeMcpApp] = []
    monkeypatch.setattr(server, "import_module", _fake_importer(created_apps))
    monkeypatch.setattr(
        server,
        "_call_tool_handler",
        lambda name, arguments: f"{name}:{arguments}",
    )

    app = server.create_app()

    assert isinstance(app, FakeMcpApp)
    assert app.name == "benefitradar"
    assert app.list_tools_func is not None
    assert app.call_tool_func is not None
    tools = asyncio.run(app.list_tools_func())
    contents = asyncio.run(app.call_tool_func("search", {"query": "welfare"}))

    assert [tool.kwargs["name"] for tool in tools] == [
        "search",
        "recent_updates",
        "sql",
        "top_trends",
        "benefit_match",
        "quality_report",
    ]
    assert len(created_apps) == 1
    assert isinstance(contents[0], FakeTextContent)
    assert contents[0].type == "text"
    assert contents[0].text == "search:{'query': 'welfare'}"


def test_main_runs_app_over_stdio(monkeypatch) -> None:
    created_apps: list[FakeMcpApp] = []
    monkeypatch.setattr(server, "import_module", _fake_importer(created_apps))

    asyncio.run(server.main())

    assert len(created_apps) == 1
    assert created_apps[0].run_calls == [
        ("read-stream", "write-stream", {"app": "benefitradar"}),
    ]


def test_legacy_mcp_server_wrapper_exports_packaged_entrypoints() -> None:
    import mcp_server.server as legacy_server

    assert legacy_server.create_app is server.create_app
    assert legacy_server.main is server.main
