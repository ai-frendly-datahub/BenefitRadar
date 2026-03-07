from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, cast

import yaml

from .models import CategoryConfig, EntityDefinition, RadarSettings, Source
from .notifier import NotificationConfig


def _resolve_path(path_value: str, *, project_root: Path) -> Path:
    """Resolve a path from config, treating relative paths as project-root relative."""
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return (project_root / path).resolve()


def _read_yaml_dict(path: Path) -> dict[str, object]:
    raw = cast(object, yaml.safe_load(path.read_text(encoding="utf-8")))
    if isinstance(raw, dict):
        raw_dict = cast(dict[object, object], raw)
        return {str(k): v for k, v in raw_dict.items()}
    return {}


def _string_value(raw: dict[str, object], key: str, default: str) -> str:
    value = raw.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return default


def _dict_items(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    items: list[dict[str, object]] = []
    for item in cast(list[object], value):
        if isinstance(item, dict):
            item_dict = cast(dict[object, object], item)
            items.append({str(k): v for k, v in item_dict.items()})
    return items


_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def load_settings(config_path: Path | None = None) -> RadarSettings:
    """Load global radar settings such as database and report directories."""
    project_root = Path(__file__).resolve().parent.parent
    config_file = config_path or project_root / "config" / "config.yaml"

    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    raw = _read_yaml_dict(config_file)
    db_path = _resolve_path(
        _string_value(raw, "database_path", "data/radar_data.duckdb"), project_root=project_root
    )
    report_dir = _resolve_path(
        _string_value(raw, "report_dir", "reports"), project_root=project_root
    )
    raw_data_dir = _resolve_path(
        _string_value(raw, "raw_data_dir", "data/raw"), project_root=project_root
    )
    search_db_path = _resolve_path(
        _string_value(raw, "search_db_path", "data/search_index.db"), project_root=project_root
    )
    return RadarSettings(
        database_path=db_path,
        report_dir=report_dir,
        raw_data_dir=raw_data_dir,
        search_db_path=search_db_path,
    )


def load_category_config(category_name: str, categories_dir: Path | None = None) -> CategoryConfig:
    """Load a category YAML and parse it into a CategoryConfig object."""
    project_root = Path(__file__).resolve().parent.parent
    base_dir = categories_dir or project_root / "config" / "categories"
    config_file = Path(base_dir) / f"{category_name}.yaml"

    if not config_file.exists():
        raise FileNotFoundError(f"Category config not found: {config_file}")

    raw = _read_yaml_dict(config_file)
    sources = [_parse_source(entry) for entry in _dict_items(raw.get("sources"))]
    entities = [_parse_entity(entry) for entry in _dict_items(raw.get("entities"))]

    display_name = (
        _string_value(raw, "display_name", "")
        or _string_value(raw, "category_name", "")
        or category_name
    )

    return CategoryConfig(
        category_name=_string_value(raw, "category_name", category_name),
        display_name=display_name,
        sources=sources,
        entities=entities,
    )


def load_notification_config(config_path: Path) -> NotificationConfig:
    if not config_path.exists():
        return NotificationConfig(enabled=False, channels=[])

    loaded = cast(object, yaml.safe_load(config_path.read_text(encoding="utf-8")))
    root = cast(dict[str, Any], loaded if isinstance(loaded, dict) else {})
    notifications = cast(dict[str, Any], root.get("notifications", {}))

    channels = notifications.get("channels", [])
    if not isinstance(channels, list):
        channels = []

    return NotificationConfig(
        enabled=bool(notifications.get("enabled", False)),
        channels=[str(channel) for channel in channels],
        email_settings=_resolve_env_refs(cast(dict[str, Any], notifications.get("email", {}))),
        webhook_url=str(_resolve_env_refs(notifications.get("webhook_url", ""))),
        telegram_config=_resolve_env_refs(cast(dict[str, Any], notifications.get("telegram", {}))),
        rules=_resolve_env_refs(cast(dict[str, Any], notifications.get("rules", {}))),
    )


def _resolve_env_refs(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda match: os.environ.get(match.group(1), ""), value)
    if isinstance(value, list):
        return [_resolve_env_refs(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _resolve_env_refs(item) for key, item in value.items()}
    return value


def _parse_source(entry: dict[str, object]) -> Source:
    if not entry:
        raise ValueError("Empty source entry in category config")
    return Source(
        name=_string_value(entry, "name", "Unnamed Source"),
        type=_string_value(entry, "type", "rss"),
        url=_string_value(entry, "url", ""),
    )


def _parse_entity(entry: dict[str, object]) -> EntityDefinition:
    if not entry:
        raise ValueError("Empty entity entry in category config")
    name = _string_value(entry, "name", "entity")
    display_name = _string_value(entry, "display_name", name)
    keywords_raw = entry.get("keywords")
    keywords: list[object]
    if isinstance(keywords_raw, list):
        keywords = []
        for keyword in cast(list[object], keywords_raw):
            keywords.append(keyword)
    elif isinstance(keywords_raw, (tuple, set)):
        keywords = []
        for keyword in cast(list[object], list(keywords_raw)):
            keywords.append(keyword)
    else:
        keywords = []
    keyword_list = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
    return EntityDefinition(name=name, display_name=display_name, keywords=keyword_list)
