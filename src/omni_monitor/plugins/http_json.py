"""Generic zero-code HTTP JSON collector for monitoring arbitrary public APIs."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from omni_monitor.models import Observation
from omni_monitor.registry import BaseCollector, register_collector


def _extract_path(obj: Any, path: str) -> Any:
    curr = obj
    for part in path.split("."):
        if curr is None:
            return None
        if isinstance(curr, list):
            curr = curr[int(part)]
        elif isinstance(curr, dict):
            curr = curr.get(part)
        else:
            return None
    return curr


class HttpJsonConfig(BaseModel):
    url: str = Field(..., description="目标 JSON 接口 URL")
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    value_path: str | None = Field(
        default=None,
        description="数值指标提取路径（如 'data.price' 或 'items.0.xauPrice'）",
    )
    state_path: str | None = Field(
        default=None,
        description="状态文本提取路径（如 'status'）",
    )
    unit: str = ""


@register_collector
class HttpJsonCollector(BaseCollector):
    plugin_type = "http_json"
    display_name = "通用 JSON 接口监控"
    description = "无代码监控任意公开 JSON 接口，支持按路径提取数值、状态或内容指纹变化"
    data_shape = "scalar"
    config_schema = HttpJsonConfig

    async def fetch(
        self, task_id: str, config: BaseModel, client: httpx.AsyncClient
    ) -> Observation:
        assert isinstance(config, HttpJsonConfig)
        resp = await client.request(
            config.method,
            config.url,
            headers=config.headers or None,
            params=config.params or None,
        )
        resp.raise_for_status()
        payload = resp.json()

        value: float | None = None
        if config.value_path:
            raw_v = _extract_path(payload, config.value_path)
            if raw_v is not None:
                value = float(raw_v)

        state: str | None = None
        if config.state_path:
            raw_s = _extract_path(payload, config.state_path)
            if raw_s is not None:
                state = str(raw_s)

        fp = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]

        display = [{"label": "接口地址", "value": config.url}]
        if value is not None:
            display.append({"label": "当前值", "value": f"{value:g} {config.unit}".strip()})
        if state is not None:
            display.append({"label": "当前状态", "value": state})

        return Observation(
            task_id=task_id,
            value=value,
            unit=config.unit,
            state=state,
            fingerprint=fp,
            metrics={"value": value, "state": state},
            display_fields=display,
            source_name="HTTP JSON",
            source_url=config.url,
        )
