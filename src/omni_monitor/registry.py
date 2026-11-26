"""Collector plugin interface, global registry, and automatic module discovery."""

from __future__ import annotations

import asyncio
import importlib
import pkgutil
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from omni_monitor.models import Observation


class BaseCollector(ABC):
    """Base contract for every monitoring data-source plugin."""

    plugin_type: str = ""
    display_name: str = ""
    description: str = ""
    data_shape: Literal["scalar", "state", "collection"] = "scalar"
    config_schema: type[BaseModel] = BaseModel

    def parse_config(self, raw: dict[str, Any]) -> BaseModel:
        return self.config_schema.model_validate(raw)

    def should_run(self, now: datetime, config: BaseModel) -> bool:
        """Override to restrict execution to specific trading sessions/windows."""
        return True

    @abstractmethod
    async def fetch(
        self, task_id: str, config: BaseModel, client: httpx.AsyncClient
    ) -> Observation:
        """Fetch a single task's observation."""

    async def fetch_batch(
        self,
        tasks: list[tuple[str, BaseModel]],
        client: httpx.AsyncClient,
    ) -> dict[str, Observation | Exception]:
        """
        Batch fetch multiple tasks sharing this plugin.
        Override in plugins that support multi-ticker batch APIs (e.g. us_stock, gold)
        to collapse N tasks into 1 HTTP request.
        """
        results = await asyncio.gather(
            *(self.fetch(tid, cfg, client) for tid, cfg in tasks),
            return_exceptions=True,
        )
        return {
            tid: res for (tid, _), res in zip(tasks, results)
        }


class CollectorRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, BaseCollector] = {}

    def register(self, collector_cls: type[BaseCollector]) -> type[BaseCollector]:
        instance = collector_cls()
        if not instance.plugin_type:
            raise ValueError(f"{collector_cls.__name__} must define plugin_type")
        self._plugins[instance.plugin_type] = instance
        return collector_cls

    def get(self, plugin_type: str) -> BaseCollector:
        if plugin_type not in self._plugins:
            raise KeyError(f"Unknown plugin_type: {plugin_type!r}")
        return self._plugins[plugin_type]

    def list_metadata(self) -> list[dict[str, Any]]:
        return [
            {
                "plugin_type": p.plugin_type,
                "display_name": p.display_name,
                "description": p.description,
                "data_shape": p.data_shape,
                "config_schema": p.config_schema.model_json_schema(),
            }
            for p in self._plugins.values()
        ]

    def discover_plugins(self, package_name: str = "omni_monitor.plugins") -> None:
        pkg = importlib.import_module(package_name)
        for mod_info in pkgutil.iter_modules(pkg.__path__, f"{package_name}."):
            importlib.import_module(mod_info.name)


registry = CollectorRegistry()


def register_collector(cls: type[BaseCollector]) -> type[BaseCollector]:
    """Class decorator to register a collector plugin automatically on import."""
    return registry.register(cls)
