"""Canonical domain models shared by collectors, rule engine, storage, and API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Observation(BaseModel):
    """Unified output emitted by every collector plugin across all scenarios."""

    task_id: str
    timestamp: datetime = Field(default_factory=utc_now)

    # Shape 1: Scalar / numeric time-series (gold, US stock, FX)
    value: float | None = None
    unit: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)

    # Shape 2: Single-entity state / content hash (status, web diff)
    state: str | None = None
    fingerprint: str | None = None

    # Shape 3: Keyed entity collection (full QDII universe, announcement feed, multi-item inventory)
    items: dict[str, dict[str, Any]] | None = None

    # Optional presentation hints for server-driven APK detail cards
    display_fields: list[dict[str, str]] = Field(default_factory=list)
    source_url: str | None = None
    source_name: str = ""


class RuleConfig(BaseModel):
    """Declarative evaluation rule stored per MonitorTask."""

    type: Literal[
        "threshold",
        "cross",
        "delta",
        "state_change",
        "expr",
        "collection_diff",
        "new_items",
    ]

    # Scalar / expression rule parameters
    field: str = "value"
    op: Literal["gt", "gte", "lt", "lte", "between", "outside", "cross_above", "cross_below"] | None = None
    target: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    window_seconds: int | None = None
    change_pct: float | None = None  # e.g. -3.0 means <= -3%, +5.0 means >= +5%
    expression: str | None = None

    # Collection / announcement rule parameters
    watch_fields: list[str] = Field(default_factory=list)
    ignore_categories: list[str] = Field(
        default_factory=lambda: ["HOLIDAY_SUSPENDED", "NO_EM_LIMIT_DATA", "OTHER"]
    )
    only_events: list[str] = Field(default_factory=list)
    filter_keywords: list[str] = Field(default_factory=list)
    filter_currency: list[str] = Field(default_factory=list)
    watchlist_codes: list[str] = Field(default_factory=list)
    min_retention_ratio: float = 0.80


class AlertPolicy(BaseModel):
    """Anti-spam and re-arm policy controlling when a matched rule emits a push."""

    mode: Literal["cooldown", "hysteresis", "once"] = "cooldown"
    cooldown_seconds: int = 3600
    rearm_value: float | None = None
    severity: Literal["low", "normal", "high"] = "normal"


class TaskCreate(BaseModel):
    task_id: str | None = None
    name: str
    enabled: bool = True
    plugin_type: str
    interval_seconds: int = Field(default=300, ge=10)
    plugin_config: dict[str, Any] = Field(default_factory=dict)
    rule: RuleConfig
    alert_policy: AlertPolicy = Field(default_factory=AlertPolicy)


class TaskUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, ge=10)
    plugin_config: dict[str, Any] | None = None
    rule: RuleConfig | None = None
    alert_policy: AlertPolicy | None = None


class MonitorTask(BaseModel):
    task_id: str
    name: str
    enabled: bool = True
    plugin_type: str
    interval_seconds: int = 300
    plugin_config: dict[str, Any] = Field(default_factory=dict)
    rule: RuleConfig
    alert_policy: AlertPolicy = Field(default_factory=AlertPolicy)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TaskState(BaseModel):
    task_id: str
    status: Literal["IDLE", "TRIGGERED"] = "IDLE"
    last_triggered_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    meta_alerted: bool = False


class AlertRecord(BaseModel):
    """Persisted push alert with server-driven detail_blocks for APK rendering."""

    alert_id: str
    task_id: str
    task_name: str
    plugin_type: str
    severity: Literal["low", "normal", "high"] = "normal"
    title: str
    summary: str
    detail_blocks: list[dict[str, Any]] = Field(default_factory=list)
    triggered_at: datetime = Field(default_factory=utc_now)
    is_read: bool = False
    push_status: str = "pending"


class DeviceRegisterRequest(BaseModel):
    token: str = Field(min_length=10)
    platform: Literal["android", "ios", "web"] = "android"
    device_name: str = ""


class DeviceToken(BaseModel):
    token: str
    platform: str = "android"
    device_name: str = ""
    updated_at: datetime = Field(default_factory=utc_now)
