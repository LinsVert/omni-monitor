"""Starter monitor tasks seeded on first run so the system is immediately useful."""

from __future__ import annotations

from omni_monitor.models import AlertPolicy, MonitorTask, RuleConfig
from omni_monitor.storage import Storage


def seed_default_tasks(storage: Storage) -> int:
    """Insert default starter tasks only if the database currently has zero tasks."""
    if storage.list_tasks():
        return 0

    defaults = [
        MonitorTask(
            task_id="qdii_all_limits",
            name="全市场 QDII 限购变动监控",
            enabled=True,
            plugin_type="qdii_limits",
            interval_seconds=1800,
            plugin_config={
                "keywords": [],
                "currencies": ["CNY"],
                "exclude_exchange_etf": True,
            },
            rule=RuleConfig(
                type="collection_diff",
                watch_fields=["limit_category", "daily_limit"],
                ignore_categories=["HOLIDAY_SUSPENDED", "NO_EM_LIMIT_DATA", "OTHER"],
                only_events=["REOPENED", "LIMIT_UP", "LIMIT_DOWN", "QUOTA_SUSPENDED", "ADDED"],
                watchlist_codes=[
                    "016452",  # 南方纳斯达克100A
                    "017436",  # 华宝纳斯达克精选A
                    "019441",  # 万家纳斯达克100A
                    "161130",  # 易方达纳斯达克100(LOF)A
                    "270042",  # 广发纳斯达克100A
                    "040046",  # 华安纳斯达克100A
                ],
                min_retention_ratio=0.80,
            ),
            alert_policy=AlertPolicy(mode="cooldown", cooldown_seconds=300, severity="normal"),
        ),
        MonitorTask(
            task_id="qdii_nasdaq_announcements",
            name="纳指/标普 QDII 限购公告提前预警",
            enabled=True,
            plugin_type="qdii_announcements",
            interval_seconds=1800,
            plugin_config={
                "codes": [],
                "keywords": ["纳斯达克", "纳指", "标普500"],
                "lookback_days": 30,
                "fetch_body": True,
            },
            rule=RuleConfig(
                type="new_items",
                watchlist_codes=["016452", "017436", "161130", "270042", "040046"],
            ),
            alert_policy=AlertPolicy(mode="cooldown", cooldown_seconds=60, severity="high"),
        ),
        MonitorTask(
            task_id="gold_xau_usd",
            name="伦敦现货黄金 (XAU/USD)",
            enabled=True,
            plugin_type="gold",
            interval_seconds=300,
            plugin_config={"instrument": "XAU"},
            rule=RuleConfig(type="threshold", field="value", op="lte", target=3400.0),
            alert_policy=AlertPolicy(
                mode="hysteresis", rearm_value=3450.0, cooldown_seconds=3600, severity="normal"
            ),
        ),
        MonitorTask(
            task_id="gold_au9999",
            name="上海金 Au99.99 (CNY/g)",
            enabled=True,
            plugin_type="gold",
            interval_seconds=300,
            plugin_config={"instrument": "AU9999"},
            rule=RuleConfig(type="threshold", field="value", op="lte", target=740.0),
            alert_policy=AlertPolicy(
                mode="hysteresis", rearm_value=750.0, cooldown_seconds=3600, severity="normal"
            ),
        ),
        MonitorTask(
            task_id="us_nvda",
            name="英伟达 (NVDA) 抄底监控",
            enabled=True,
            plugin_type="us_stock",
            interval_seconds=120,
            plugin_config={
                "symbol": "NVDA",
                "market_hours_only": True,
                "include_extended_hours": True,
            },
            rule=RuleConfig(type="threshold", field="value", op="lte", target=135.0),
            alert_policy=AlertPolicy(
                mode="hysteresis", rearm_value=142.0, cooldown_seconds=3600, severity="high"
            ),
        ),
        MonitorTask(
            task_id="us_qqq",
            name="纳指100 ETF (QQQ) 波动监控",
            enabled=True,
            plugin_type="us_stock",
            interval_seconds=120,
            plugin_config={
                "symbol": "QQQ",
                "market_hours_only": True,
                "include_extended_hours": True,
            },
            rule=RuleConfig(type="expr", expression="metrics.change_pct is not None and metrics.change_pct <= -2.5"),
            alert_policy=AlertPolicy(mode="cooldown", cooldown_seconds=7200, severity="normal"),
        ),
        MonitorTask(
            task_id="lof_tractor_arbitrage",
            name="LOF 拖拉机尾盘套利预警",
            enabled=True,
            plugin_type="lof_arbitrage",
            interval_seconds=300,
            plugin_config={
                "lof_api_url": "",
                "min_premium_pct": 3.5,
                "min_daily_limit": 100.0,
                "min_scale_cny": 10000000.0,
                "szse_16_only": True,
                "friction_pct": 0.25,
                "trading_window_only": True,
            },
            rule=RuleConfig(type="threshold", field="value", op="gte", target=1.0),
            alert_policy=AlertPolicy(mode="cooldown", cooldown_seconds=36000, severity="high"),
        ),
    ]

    for task in defaults:
        storage.upsert_task(task)
    return len(defaults)
