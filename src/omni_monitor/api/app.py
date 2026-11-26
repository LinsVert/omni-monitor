"""FastAPI REST server providing APK & management APIs over MonitorEngine and Storage."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from omni_monitor import __version__
from omni_monitor.config import Settings
from omni_monitor.engine import MonitorEngine
from omni_monitor.models import (
    AlertRecord,
    DeviceRegisterRequest,
    MonitorTask,
    TaskCreate,
    TaskUpdate,
    utc_now,
)
from omni_monitor.notifiers.base import BaseNotifier
from omni_monitor.notifiers.fcm import FcmNotifier
from omni_monitor.registry import CollectorRegistry, registry as default_registry
from omni_monitor.scheduler import MonitorScheduler
from omni_monitor.seed import seed_default_tasks
from omni_monitor.storage import Storage

logger = logging.getLogger("omni_monitor.api")


class TestPushRequest(BaseModel):
    title: str = "Omni-Monitor 测试推送"
    summary: str = "如果你看到这条通知，说明 Google FCM 链路已经打通！"
    severity: str = "normal"


class SimulateAlertRequest(BaseModel):
    scenario: str = "qdii_diff"  # "qdii_diff" | "price_chart"


def create_app(
    settings: Settings | None = None,
    *,
    storage: Storage | None = None,
    collector_registry: CollectorRegistry | None = None,
    notifier: BaseNotifier | None = None,
) -> FastAPI:
    cfg = settings or Settings()
    reg = collector_registry or default_registry
    reg.discover_plugins()

    store = storage or Storage(cfg.db_path)
    notif = notifier or FcmNotifier(cfg.fcm_credentials_path)
    engine = MonitorEngine(store, reg, notif, cfg)
    scheduler = MonitorScheduler(store, engine, tick_seconds=cfg.scheduler_tick_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if cfg.seed_defaults:
            seeded = seed_default_tasks(store)
            if seeded:
                logger.info("Seeded %d default monitor tasks", seeded)
        if cfg.scheduler_enabled:
            scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()
            if storage is None:
                store.close()

    app = FastAPI(
        title="Omni-Monitor API",
        version=__version__,
        description="Pluggable multi-scenario monitoring backend with SQLite & Google FCM",
        lifespan=lifespan,
    )
    app.state.storage = store
    app.state.engine = engine
    app.state.scheduler = scheduler
    app.state.registry = reg
    app.state.notifier = notif

    def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
        if cfg.api_key and x_api_key != cfg.api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    # ------------------------------------------------------------------
    # Health & Plugins
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "fcm_mode": "live" if isinstance(notif, FcmNotifier) and notif.is_live_fcm else "dry_run",
            "registered_plugins": len(reg.list_metadata()),
            "registered_devices": len(store.list_device_tokens()),
            "tasks_count": len(store.list_tasks()),
        }

    @app.get("/api/plugins", dependencies=[Depends(verify_api_key)])
    async def list_plugins() -> list[dict[str, Any]]:
        return reg.list_metadata()

    # ------------------------------------------------------------------
    # Tasks CRUD & Manual Trigger
    # ------------------------------------------------------------------

    @app.get("/api/tasks", dependencies=[Depends(verify_api_key)])
    async def list_tasks() -> list[dict[str, Any]]:
        out = []
        for task in store.list_tasks():
            state = store.get_task_state(task.task_id)
            out.append({**task.model_dump(mode="json"), "state": state.model_dump(mode="json")})
        return out

    @app.post("/api/tasks", status_code=201, dependencies=[Depends(verify_api_key)])
    async def create_task(body: TaskCreate) -> dict[str, Any]:
        try:
            collector = reg.get(body.plugin_type)
            validated_cfg = collector.parse_config(body.plugin_config)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid plugin_config: {exc}") from exc

        task_id = body.task_id or f"{body.plugin_type}_{uuid.uuid4().hex[:8]}"
        if store.get_task(task_id) is not None:
            raise HTTPException(status_code=409, detail=f"Task {task_id!r} already exists")

        now = utc_now()
        task = MonitorTask(
            task_id=task_id,
            name=body.name,
            enabled=body.enabled,
            plugin_type=body.plugin_type,
            interval_seconds=body.interval_seconds,
            plugin_config=validated_cfg.model_dump(),
            rule=body.rule,
            alert_policy=body.alert_policy,
            created_at=now,
            updated_at=now,
        )
        store.upsert_task(task)
        return task.model_dump(mode="json")

    @app.get("/api/tasks/{task_id}", dependencies=[Depends(verify_api_key)])
    async def get_task(task_id: str) -> dict[str, Any]:
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        state = store.get_task_state(task_id)
        return {**task.model_dump(mode="json"), "state": state.model_dump(mode="json")}

    @app.patch("/api/tasks/{task_id}", dependencies=[Depends(verify_api_key)])
    async def update_task(task_id: str, body: TaskUpdate) -> dict[str, Any]:
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        updates = body.model_dump(exclude_unset=True)
        if "plugin_config" in updates:
            try:
                collector = reg.get(task.plugin_type)
                updates["plugin_config"] = collector.parse_config(updates["plugin_config"]).model_dump()
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"Invalid plugin_config: {exc}") from exc

        updated = task.model_copy(update={**updates, "updated_at": utc_now()})
        store.upsert_task(updated)
        return updated.model_dump(mode="json")

    @app.delete("/api/tasks/{task_id}", dependencies=[Depends(verify_api_key)])
    async def delete_task(task_id: str) -> dict[str, Any]:
        if not store.delete_task(task_id):
            raise HTTPException(status_code=404, detail="Task not found")
        return {"deleted": True, "task_id": task_id}

    @app.post("/api/tasks/{task_id}/run", dependencies=[Depends(verify_api_key)])
    async def run_task_now(task_id: str) -> dict[str, Any]:
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found")
        outcome = await engine.run_task_by_id(task_id, force=True)
        return {
            "task_id": outcome.task_id,
            "ok": outcome.ok,
            "skipped": outcome.skipped,
            "alert_id": outcome.alert_id,
            "error": outcome.error,
            "observation": outcome.observation_summary,
        }

    # ------------------------------------------------------------------
    # Live Snapshot / QDII Board & Metric History
    # ------------------------------------------------------------------

    @app.get("/api/tasks/{task_id}/latest", dependencies=[Depends(verify_api_key)])
    async def get_latest_observation(
        task_id: str,
        q: str | None = Query(default=None, description="Keyword search across collection items"),
        category: str | None = Query(default=None, description="Filter by limit_category"),
        currency: str | None = Query(default=None, description="Filter by currency (CNY/USD/HKD)"),
        sort_by: str | None = Query(default=None, description="Sort items by field (e.g. daily_limit)"),
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> dict[str, Any]:
        obs = store.get_latest_observation(task_id)
        if not obs:
            raise HTTPException(status_code=404, detail="No observation recorded yet")
        payload = obs.model_dump(mode="json")

        if obs.items is not None:
            items_list = list(obs.items.values())
            if q:
                q_up = q.upper()
                items_list = [
                    it
                    for it in items_list
                    if q_up in f"{it.get('code', '')} {it.get('name', '')} {it.get('title', '')}".upper()
                ]
            if category:
                items_list = [it for it in items_list if it.get("limit_category") == category]
            if currency:
                items_list = [it for it in items_list if it.get("currency") == currency]
            if sort_by == "daily_limit":
                items_list.sort(
                    key=lambda it: (
                        it.get("daily_limit") is None,
                        it.get("daily_limit") if isinstance(it.get("daily_limit"), (int, float)) else 1e18,
                    )
                )
            payload["total_items"] = len(obs.items)
            payload["filtered_items_count"] = len(items_list)
            payload["items_list"] = items_list[:limit]
            # Omit raw map when returning filtered list to keep response lean for mobile
            payload.pop("items", None)
        return payload

    @app.get("/api/tasks/{task_id}/history", dependencies=[Depends(verify_api_key)])
    async def get_task_history(
        task_id: str, limit: int = Query(default=100, ge=1, le=1000)
    ) -> list[dict[str, Any]]:
        if store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return store.get_metric_history(task_id, limit=limit)

    # ------------------------------------------------------------------
    # Alerts History & Detail (Core APK Endpoints)
    # ------------------------------------------------------------------

    @app.get("/api/alerts", dependencies=[Depends(verify_api_key)])
    async def list_alerts(
        task_id: str | None = None,
        unread_only: bool = False,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict[str, Any]]:
        alerts = store.list_alerts(
            task_id=task_id, unread_only=unread_only, limit=limit, offset=offset
        )
        # Compact list representation for APK feed (full detail_blocks loaded on click)
        return [
            {
                "alert_id": a.alert_id,
                "task_id": a.task_id,
                "task_name": a.task_name,
                "plugin_type": a.plugin_type,
                "severity": a.severity,
                "title": a.title,
                "summary": a.summary,
                "triggered_at": a.triggered_at.isoformat(),
                "is_read": a.is_read,
                "push_status": a.push_status,
            }
            for a in alerts
        ]

    @app.get("/api/alerts/{alert_id}", dependencies=[Depends(verify_api_key)])
    async def get_alert_detail(alert_id: str) -> dict[str, Any]:
        alert = store.get_alert(alert_id)
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        return alert.model_dump(mode="json")

    @app.post("/api/alerts/{alert_id}/read", dependencies=[Depends(verify_api_key)])
    async def mark_alert_read(alert_id: str, is_read: bool = True) -> dict[str, Any]:
        if not store.mark_alert_read(alert_id, is_read=is_read):
            raise HTTPException(status_code=404, detail="Alert not found")
        return {"alert_id": alert_id, "is_read": is_read}

    # ------------------------------------------------------------------
    # FCM Device Token Registration & End-to-End Test Push
    # ------------------------------------------------------------------

    @app.post("/api/devices/fcm", dependencies=[Depends(verify_api_key)])
    async def register_fcm_device(body: DeviceRegisterRequest) -> dict[str, Any]:
        device = store.upsert_device_token(
            token=body.token, platform=body.platform, device_name=body.device_name
        )
        return device.model_dump(mode="json")

    @app.get("/api/devices", dependencies=[Depends(verify_api_key)])
    async def list_devices() -> list[dict[str, Any]]:
        return [d.model_dump(mode="json") for d in store.list_device_tokens()]

    @app.post("/api/devices/test-push", dependencies=[Depends(verify_api_key)])
    async def send_test_push(body: TestPushRequest) -> dict[str, Any]:
        alert = AlertRecord(
            alert_id=f"test_{uuid.uuid4().hex[:10]}",
            task_id="_system_test",
            task_name="系统测试",
            plugin_type="system",
            severity="normal",
            title=body.title,
            summary=body.summary,
            detail_blocks=[
                {
                    "type": "kv_grid",
                    "items": [
                        {"label": "消息类型", "value": "FCM 连通性测试"},
                        {"label": "触发时间", "value": utc_now().isoformat()},
                    ],
                }
            ],
        )
        saved = await engine._emit_alert(alert)
        return saved.model_dump(mode="json")

    @app.post("/api/devices/simulate-alert", dependencies=[Depends(verify_api_key)])
    async def send_simulated_alert(body: SimulateAlertRequest) -> dict[str, Any]:
        now = utc_now()
        if body.scenario == "price_chart":
            from datetime import timedelta

            base_prices = [
                3588.0, 3574.5, 3562.0, 3549.2, 3555.8, 3538.0,
                3521.4, 3504.0, 3488.5, 3476.2, 3461.0, 3448.5,
                3435.0, 3422.8, 3411.5, 3403.2, 3392.4,
            ]
            points = [
                [(now - timedelta(hours=len(base_prices) - 1 - i)).isoformat(), p]
                for i, p in enumerate(base_prices)
            ]
            alert = AlertRecord(
                alert_id=f"sim_gold_{uuid.uuid4().hex[:8]}",
                task_id="gold_xau_usd",
                task_name="伦敦现货黄金 (XAU/USD)",
                plugin_type="gold",
                severity="high",
                title="[模拟] 伦敦现货黄金跌破 $3400",
                summary="当前 $3392.40 (-5.45%)，已触发 <= $3400 抄底预警",
                detail_blocks=[
                    {
                        "type": "kv_grid",
                        "items": [
                            {"label": "标的", "value": "伦敦现货黄金 XAU/USD"},
                            {"label": "当前价格", "value": "$3392.40 USD/oz"},
                            {"label": "触发条件", "value": "value <= 3400"},
                            {"label": "折合人民币", "value": "约 728.60 元/克"},
                        ],
                    },
                    {
                        "type": "line_chart",
                        "title": "伦敦现货黄金 近 17 小时走势（模拟）",
                        "unit": "USD/oz",
                        "target_line": 3400.0,
                        "points": points,
                    },
                    {
                        "type": "actions",
                        "links": [
                            {
                                "label": "在 TradingView 查看 XAU/USD",
                                "url": "https://www.tradingview.com/symbols/XAUUSD/",
                            }
                        ],
                    },
                ],
                triggered_at=now,
            )
        else:
            alert = AlertRecord(
                alert_id=f"sim_qdii_{uuid.uuid4().hex[:8]}",
                task_id="qdii_all_limits",
                task_name="全市场 QDII 限购变动监控",
                plugin_type="qdii_limits",
                severity="high",
                title="⭐ [模拟] QDII 限购变动：3 只基金额度调整（含 2 只自选）",
                summary="易方达纳指LOF(暂停申购→限额1000元)；华宝纳指精选(限额200元→限额1000元)",
                detail_blocks=[
                    {
                        "type": "kv_grid",
                        "items": [
                            {"label": "变动数量", "value": "3 项"},
                            {"label": "命中自选", "value": "2 项"},
                            {"label": "当前监控池", "value": "540 只场外人民币 QDII"},
                            {"label": "休市噪音过滤", "value": "已自动屏蔽"},
                        ],
                    },
                    {
                        "type": "diff_list",
                        "title": "详细变动清单（可点击查看公告/详情）",
                        "items": [
                            {
                                "code": "161130",
                                "name": "易方达纳斯达克100ETF联接(QDII-LOF)A",
                                "event": "REOPENED",
                                "before_text": "暂停申购",
                                "after_text": "限额 1000元",
                                "is_watchlist": True,
                                "url": "https://fund.eastmoney.com/161130.html",
                            },
                            {
                                "code": "017436",
                                "name": "华宝纳斯达克精选股票发起式(QDII)A",
                                "event": "LIMIT_UP",
                                "before_text": "限额 200元",
                                "after_text": "限额 1000元",
                                "is_watchlist": True,
                                "url": "https://pdf.dfcfw.com/pdf/H2_AN202611071831507683_1.pdf",
                            },
                            {
                                "code": "000614",
                                "name": "华安德国(DAX)联接(QDII)A",
                                "event": "LIMIT_DOWN",
                                "before_text": "限额 1000元",
                                "after_text": "限额 100元",
                                "is_watchlist": False,
                                "url": "https://fund.eastmoney.com/000614.html",
                            },
                        ],
                    },
                    {
                        "type": "markdown",
                        "title": "模拟验证说明",
                        "content": "该推送用于验收 Android 详情页对 diff_list（放宽标绿、收紧标红、⭐自选置顶、点击跳转官方页面/PDF）的原生渲染效果。",
                    },
                    {
                        "type": "actions",
                        "links": [
                            {
                                "label": "打开天天基金申购状态一览表",
                                "url": "https://fund.eastmoney.com/Fund_sgzt_bzdm.html",
                            }
                        ],
                    },
                ],
                triggered_at=now,
            )

        saved = await engine._emit_alert(alert)
        return saved.model_dump(mode="json")

    return app
