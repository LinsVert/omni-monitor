"""Integration tests for MonitorEngine (batching, sanity check, meta-alerts) and FastAPI APK endpoints."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from omni_monitor.api.app import create_app
from omni_monitor.config import Settings
from omni_monitor.engine import MonitorEngine
from omni_monitor.models import AlertPolicy, AlertRecord, MonitorTask, Observation, RuleConfig
from omni_monitor.notifiers.base import BaseNotifier, PushResult
from omni_monitor.registry import BaseCollector, CollectorRegistry
from omni_monitor.storage import Storage


class MemoryNotifier(BaseNotifier):
    def __init__(self, invalid_tokens: list[str] | None = None) -> None:
        self.sent: list[tuple[AlertRecord, list[str]]] = []
        self.invalid_tokens = invalid_tokens or []

    async def send(self, alert: AlertRecord, tokens: list[str]) -> PushResult:
        self.sent.append((alert, list(tokens)))
        return PushResult(
            status="sent",
            success_count=len(tokens),
            invalid_tokens=list(self.invalid_tokens),
        )


class DummyConfig(BaseModel):
    mode: str = "normal"


class FakeCollectionCollector(BaseCollector):
    plugin_type = "fake_qdii"
    display_name = "Fake QDII"
    data_shape = "collection"
    config_schema = DummyConfig

    def __init__(self) -> None:
        self.snapshots: list[dict[str, dict[str, Any]]] = []
        self.fail_next = False

    async def fetch(
        self, task_id: str, config: BaseModel, client: httpx.AsyncClient
    ) -> Observation:
        if self.fail_next:
            raise RuntimeError("Upstream 503")
        items = self.snapshots.pop(0)
        return Observation(task_id=task_id, items=items, source_name="FakeEM")


@pytest.mark.asyncio
async def test_engine_sanity_guard_and_meta_failure_alert(tmp_path) -> None:
    storage = Storage(tmp_path / "test.db")
    reg = CollectorRegistry()
    collector = FakeCollectionCollector()
    reg._plugins["fake_qdii"] = collector

    notifier = MemoryNotifier(invalid_tokens=["stale_fcm_token_00001"])
    settings = Settings(db_path=tmp_path / "test.db", failure_alert_threshold=2)
    engine = MonitorEngine(storage, reg, notifier, settings)

    storage.upsert_device_token("valid_fcm_token_00001", "android", "Pixel 9")
    storage.upsert_device_token("stale_fcm_token_00001", "android", "Old Phone")

    task = MonitorTask(
        task_id="t_qdii",
        name="QDII Test",
        plugin_type="fake_qdii",
        rule=RuleConfig(type="collection_diff", min_retention_ratio=0.80),
        alert_policy=AlertPolicy(mode="cooldown", cooldown_seconds=0),
    )
    storage.upsert_task(task)

    # 1. Baseline snapshot with 25 items
    base_items = {
        f"00{i:04d}": {"name": f"QDII-{i}", "limit_category": "LIMITED", "daily_limit": 100.0}
        for i in range(25)
    }
    collector.snapshots.append(base_items)
    o1 = await engine.run_task_by_id("t_qdii")
    assert o1.ok is True
    assert o1.alert_id is None

    # 2. Glitch snapshot drops from 25 -> 3 items (triggers sanity check failure #1, does NOT overwrite snapshot)
    collector.snapshots.append({k: v for k, v in list(base_items.items())[:3]})
    o2 = await engine.run_task_by_id("t_qdii")
    assert o2.ok is False
    assert "sanity check" in (o2.error or "").lower()
    assert len((storage.get_latest_observation("t_qdii").items or {})) == 25  # untouched!

    # 3. Failure #2 hits threshold=2 -> emits system health meta-alert AND prunes stale token
    collector.fail_next = True
    o3 = await engine.run_task_by_id("t_qdii")
    assert o3.ok is False
    assert o3.alert_id is not None
    assert len(notifier.sent) == 1
    assert "监控源异常" in notifier.sent[0][0].title
    remaining_tokens = [d.token for d in storage.list_device_tokens()]
    assert remaining_tokens == ["valid_fcm_token_00001"]

    # 4. Recovery with real limit increase -> resets consecutive_failures & sends real diff alert
    collector.fail_next = False
    notifier.invalid_tokens = []
    updated_items = {k: dict(v) for k, v in base_items.items()}
    updated_items["000001"]["daily_limit"] = 1000.0
    collector.snapshots.append(updated_items)
    o4 = await engine.run_task_by_id("t_qdii")
    assert o4.ok is True
    assert o4.alert_id is not None
    assert storage.get_task_state("t_qdii").consecutive_failures == 0
    assert len(notifier.sent) == 2

    storage.close()


def test_fastapi_apk_endpoints_end_to_end(tmp_path) -> None:
    settings = Settings(
        db_path=tmp_path / "api.db",
        scheduler_enabled=False,
        seed_defaults=True,
        api_key="secret-key",
    )
    storage = Storage(settings.db_path)
    notifier = MemoryNotifier()
    app = create_app(settings, storage=storage, notifier=notifier)
    headers = {"X-API-Key": "secret-key"}

    with TestClient(app) as client:
        # Health works without auth
        r_health = client.get("/health")
        assert r_health.status_code == 200
        assert r_health.json()["registered_plugins"] >= 5

        # Protected endpoint rejects missing key
        assert client.get("/api/tasks").status_code == 401

        # Plugins expose JSON Schema
        plugins = client.get("/api/plugins", headers=headers).json()
        plugin_types = {p["plugin_type"] for p in plugins}
        assert {"gold", "us_stock", "qdii_limits", "qdii_announcements", "http_json"} <= plugin_types

        # Seeded tasks exist
        tasks = client.get("/api/tasks", headers=headers).json()
        assert len(tasks) >= 5

        # Register APK FCM device token & trigger test push
        r_dev = client.post(
            "/api/devices/fcm",
            headers=headers,
            json={"token": "fcm_android_pixel_test_token_12345", "platform": "android", "device_name": "Pixel 9"},
        )
        assert r_dev.status_code == 200

        r_push = client.post("/api/devices/test-push", headers=headers, json={})
        assert r_push.status_code == 200
        alert_id = r_push.json()["alert_id"]

        # APK fetches alert history list -> taps into alert detail -> marks read
        alerts = client.get("/api/alerts", headers=headers).json()
        assert any(a["alert_id"] == alert_id for a in alerts)

        detail = client.get(f"/api/alerts/{alert_id}", headers=headers).json()
        assert detail["alert_id"] == alert_id
        assert len(detail["detail_blocks"]) >= 1

        r_read = client.post(f"/api/alerts/{alert_id}/read", headers=headers)
        assert r_read.status_code == 200
        assert client.get(f"/api/alerts/{alert_id}", headers=headers).json()["is_read"] is True

        # Seed a QDII snapshot and test live board query filtering (?q=纳斯达克&category=LIMITED)
        storage.save_observation(
            Observation(
                task_id="qdii_all_limits",
                items={
                    "017436": {"code": "017436", "name": "华宝纳斯达克精选A", "currency": "CNY", "limit_category": "LIMITED", "daily_limit": 200.0},
                    "019441": {"code": "019441", "name": "万家纳斯达克100A", "currency": "CNY", "limit_category": "LIMITED", "daily_limit": 5.0},
                    "000614": {"code": "000614", "name": "华安德国DAX联接A", "currency": "CNY", "limit_category": "LIMITED", "daily_limit": 1000.0},
                },
            )
        )
        board = client.get(
            "/api/tasks/qdii_all_limits/latest?q=纳斯达克&category=LIMITED&sort_by=daily_limit",
            headers=headers,
        ).json()
        assert board["filtered_items_count"] == 2
        assert [i["code"] for i in board["items_list"]] == ["019441", "017436"]

    storage.close()
