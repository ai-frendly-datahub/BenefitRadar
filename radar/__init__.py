from __future__ import annotations

import sys
from importlib import import_module

_MODULE_ALIASES = {
    "analyzer": "radar_core.analyzer",
    "collector": "benefitradar.collector",
    "exceptions": "radar_core.exceptions",
    "models": "radar_core.models",
    "nl_query": "radar_core.nl_query",
    "search_index": "benefitradar.search_index",
    "storage": "benefitradar.storage",
}

for _module_name, _target in _MODULE_ALIASES.items():
    sys.modules[f"{__name__}.{_module_name}"] = import_module(_target)


RadarStorage = import_module("benefitradar.storage").RadarStorage


__all__ = ["RadarStorage"]
