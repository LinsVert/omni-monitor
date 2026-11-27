"""Unit tests for scalar rules, collection diff (QDII holiday/quota), new_items, and state machine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from omni_monitor.models import AlertPolicy, MonitorTask, Observation, RuleConfig, TaskState
from omni_monitor.rules import apply_alert_policy, evaluate_rule, safe_eval_expr


def _ts(offset_sec: int = 0) -> datetime:
    return datetime(2026, 11, 26, 10, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_sec)


def test_safe_eval_expr_blocks_unsafe_code() -> None:
    assert safe_eval_expr("value <= 120 and metrics.change_pct < -3", {"value": 115, "metrics": {"change_pct": -4.2}})
    with pytest.raises(ValueError):
        safe_eval_expr("__import__('os').system('echo hi')", {})
    with pytest.raises(ValueError):
        safe_eval_expr("metrics.__class__", {"metrics": {}})


def test_threshold_and_hysteresis_rearm() -> None:
    task = MonitorTask(
        task_id="gold_test",
        name="Gold",
        plugin_type="gold",
        rule=RuleConfig(type="threshold", field="value", op="gte", target=3600.0),
        alert_policy=AlertPolicy(mode="hysteresis", rearm_value=3550.0),
    )
    state = TaskState(task_id="gold_test")

    # 1. Crosses above 3600 -> emits alert
    r1 = evaluate_rule(task, Observation(task_id="gold_test", value=3610.0, unit="USD"), None)
    send1, state, disable1 = apply_alert_policy(task.alert_policy, state, r1, _ts(0), rule=task.rule)
    assert send1 is True
    assert disable1 is False
    assert state.status == "TRIGGERED"

    # 2. Still above 3600 -> suppressed
    r2 = evaluate_rule(task, Observation(task_id="gold_test", value=3620.0, unit="USD"), None)
    send2, state, _ = apply_alert_policy(task.alert_policy, state, r2, _ts(60), rule=task.rule)
    assert send2 is False
    assert state.status == "TRIGGERED"

    # 3. Drops to 3580 (below 3600, but NOT yet <= rearm 3550) -> stays TRIGGERED!
    r3 = evaluate_rule(task, Observation(task_id="gold_test", value=3580.0, unit="USD"), None)
    send3, state, _ = apply_alert_policy(task.alert_policy, state, r3, _ts(120), rule=task.rule)
    assert send3 is False
    assert state.status == "TRIGGERED"

    # 4. Oscillates back to 3605 -> still suppressed because never re-armed
    r4 = evaluate_rule(task, Observation(task_id="gold_test", value=3605.0, unit="USD"), None)
    send4, state, _ = apply_alert_policy(task.alert_policy, state, r4, _ts(180), rule=task.rule)
    assert send4 is False

    # 5. Drops to 3540 (<= 3550) -> re-arms to IDLE
    r5 = evaluate_rule(task, Observation(task_id="gold_test", value=3540.0, unit="USD"), None)
    send5, state, _ = apply_alert_policy(task.alert_policy, state, r5, _ts(240), rule=task.rule)
    assert send5 is False
    assert state.status == "IDLE"

    # 6. Rallies back to 3602 -> triggers again!
    r6 = evaluate_rule(task, Observation(task_id="gold_test", value=3602.0, unit="USD"), None)
    send6, state, _ = apply_alert_policy(task.alert_policy, state, r6, _ts(300), rule=task.rule)
    assert send6 is True


def test_once_policy_disables_task() -> None:
    task = MonitorTask(
        task_id="nvda_once",
        name="NVDA Dip",
        plugin_type="us_stock",
        rule=RuleConfig(type="threshold", field="value", op="lte", target=140.0),
        alert_policy=AlertPolicy(mode="once"),
    )
    res = evaluate_rule(task, Observation(task_id="nvda_once", value=138.0, unit="USD"), None)
    send, _, disable = apply_alert_policy(task.alert_policy, TaskState(task_id="nvda_once"), res, _ts(0), rule=task.rule)
    assert send is True
    assert disable is True


def test_delta_window_rule() -> None:
    task = MonitorTask(
        task_id="qqq_drop",
        name="QQQ",
        plugin_type="us_stock",
        rule=RuleConfig(type="delta", field="value", window_seconds=3600, change_pct=-3.0),
    )
    history = [
        {"timestamp": _ts(-1800).isoformat(), "value": 600.0, "metrics": {}},
        {"timestamp": _ts(-600).isoformat(), "value": 590.0, "metrics": {}},
    ]
    curr = Observation(task_id="qqq_drop", timestamp=_ts(0), value=580.0, unit="USD")
    res = evaluate_rule(task, curr, None, history=history)
    assert res.matched is True
    assert any(b["type"] == "line_chart" for b in res.detail_blocks)


def test_qdii_collection_diff_filters_holidays_and_detects_quota_changes() -> None:
    task = MonitorTask(
        task_id="qdii_limits",
        name="QDII 限购",
        plugin_type="qdii_limits",
        rule=RuleConfig(
            type="collection_diff",
            watch_fields=["limit_category", "daily_limit"],
            ignore_categories=["HOLIDAY_SUSPENDED", "NO_EM_LIMIT_DATA", "OTHER"],
            watchlist_codes=["016452"],
        ),
    )

    prev = Observation(
        task_id="qdii_limits",
        items={
            # Watchlist fund reopens from quota halt -> 1000元 (MUST ALERT, HIGH severity!)
            "016452": {"name": "南方纳斯达克100A", "limit_category": "QUOTA_SUSPENDED", "daily_limit": 0.0, "currency": "CNY"},
            # Fund enters Thanksgiving holiday pause (MUST BE IGNORED!)
            "017436": {"name": "华宝纳斯达克精选A", "limit_category": "LIMITED", "daily_limit": 200.0, "currency": "CNY"},
            # Fund tightens limit 1000 -> 100 (MUST ALERT!)
            "000614": {"name": "华安德国DAX联接A", "limit_category": "LIMITED", "daily_limit": 1000.0, "currency": "CNY"},
        },
    )
    curr = Observation(
        task_id="qdii_limits",
        items={
            "016452": {"name": "南方纳斯达克100A", "limit_category": "LIMITED", "daily_limit": 1000.0, "currency": "CNY"},
            "017436": {"name": "华宝纳斯达克精选A", "limit_category": "HOLIDAY_SUSPENDED", "daily_limit": 0.0, "currency": "CNY"},
            "000614": {"name": "华安德国DAX联接A", "limit_category": "LIMITED", "daily_limit": 100.0, "currency": "CNY"},
        },
    )

    res = evaluate_rule(task, curr, prev)
    assert res.matched is True
    assert res.severity == "high"  # upgraded due to watchlist hit
    diff_block = next(b for b in res.detail_blocks if b["type"] == "diff_list")
    codes = [item["code"] for item in diff_block["items"]]
    assert codes == ["016452", "000614"]  # watchlist first; holiday-suspended 017436 filtered out!
    assert diff_block["items"][0]["event"] == "REOPENED"
    assert diff_block["items"][1]["event"] == "LIMIT_DOWN"


def test_new_items_announcement_baseline_then_alert() -> None:
    task = MonitorTask(
        task_id="qdii_ann",
        name="纳指公告",
        plugin_type="qdii_announcements",
        rule=RuleConfig(type="new_items", watchlist_codes=["016452"]),
    )
    obs1 = Observation(
        task_id="qdii_ann",
        items={"AN001": {"code": "016452", "fund_name": "南方纳指A", "title": "旧公告"}},
    )
    # First run builds baseline without alert
    assert evaluate_rule(task, obs1, None).matched is False

    obs2 = Observation(
        task_id="qdii_ann",
        items={
            "AN001": {"code": "016452", "fund_name": "南方纳指A", "title": "旧公告"},
            "AN002": {
                "code": "016452",
                "fund_name": "南方纳指A",
                "title": "关于恢复大额申购的公告",
                "action": "REOPENED",
                "effective_date": "2026年11月27日",
                "limit_summary": "1000元",
                "pdf_url": "https://pdf.dfcfw.com/pdf/H2_AN002_1.pdf",
            },
        },
    )
    res = evaluate_rule(task, obs2, obs1)
    assert res.matched is True
    assert res.severity == "high"
    assert any(b["type"] == "actions" for b in res.detail_blocks)


def test_lof_arbitrage_scalar_with_items_emits_diff_list() -> None:
    task = MonitorTask(
        task_id="lof_arb",
        name="LOF 拖拉机套利",
        plugin_type="lof_arbitrage",
        rule=RuleConfig(type="threshold", field="value", op="gte", target=1.0),
        alert_policy=AlertPolicy(mode="cooldown", cooldown_seconds=36000, severity="high"),
    )
    obs = Observation(
        task_id="lof_arb",
        value=1.0,
        unit="只",
        metrics={"top_summary": "161128 标普信息科技LOF(+6.2%, 100元)"},
        items={
            "161128": {
                "code": "161128",
                "name": "标普信息科技LOF",
                "event": "溢价+6.20%",
                "before_text": "限额 100元 × 6户 · 净值日 2026-11-25",
                "after_text": "估赚约 +35.7元 (6户共600元)",
                "is_watchlist": True,
                "url": "https://fund.eastmoney.com/161128.html",
            }
        },
    )
    res = evaluate_rule(task, obs, None)
    assert res.matched is True
    assert "161128" in res.summary
    assert any(b["type"] == "diff_list" for b in res.detail_blocks)


def test_lof_is_nav_fresh_filters_stale_weekly_nav() -> None:
    from datetime import date
    from omni_monitor.plugins.lof_arbitrage import is_nav_fresh

    today = date(2026, 11, 26)
    assert is_nav_fresh("2026-11-25", max_age_days=4, today=today) is True
    assert is_nav_fresh("2026-11-24", max_age_days=4, today=today) is True
    assert is_nav_fresh("2026-11-20", max_age_days=4, today=today) is False
    assert is_nav_fresh("2026-11-20", max_age_days=0, today=today) is True


def test_csrc_table_limit_regex_extracts_50_yuan() -> None:
    from omni_monitor.plugins.qdii_announcements import _extract_fields

    body = (
        "调整大额定期定额投资起始日 2026年11月30日 "
        "限制申购金额（单位：人民币元） 50.00 "
        "限制定期定额投资金额（单位：人民币元） 50.00"
    )
    out = _extract_fields(
        "关于大成纳斯达克100交易型开放式指数证券投资基金联接基金(QDII)调整大额申购的公告",
        body,
    )
    assert out["effective_date"] == "2026年11月30日"
    assert out["limit_summary"] == "限额 50元"


def test_collection_diff_added_respects_ignore_categories() -> None:
    task = MonitorTask(
        task_id="qdii_limits",
        name="QDII",
        plugin_type="qdii_limits",
        rule=RuleConfig(
            type="collection_diff",
            ignore_categories=["HOLIDAY_SUSPENDED", "OTHER"],
            only_events=["ADDED", "LIMIT_UP"],
        ),
    )
    prev = Observation(task_id="qdii_limits", items={"016452": {"name": "南方纳指", "limit_category": "LIMITED", "daily_limit": 10.0}})
    curr = Observation(
        task_id="qdii_limits",
        items={
            "016452": {"name": "南方纳指", "limit_category": "LIMITED", "daily_limit": 10.0},
            "159329": {"name": "沙特ETF南方", "limit_category": "HOLIDAY_SUSPENDED", "daily_limit": 0.0},
        },
    )
    assert evaluate_rule(task, curr, prev).matched is False


def test_new_items_permanent_seen_keys_prevent_duplicate_push_after_timeout_drop() -> None:
    task = MonitorTask(
        task_id="qdii_ann",
        name="公告",
        plugin_type="qdii_announcements",
        rule=RuleConfig(type="new_items"),
    )
    # Previous run lost AN001 due to upstream timeout (prev.items is empty),
    # but seen_keys remembers AN001 was already alerted -> MUST NOT alert again!
    prev_dropped = Observation(task_id="qdii_ann", items={})
    curr_recovered = Observation(
        task_id="qdii_ann",
        items={"AN001": {"code": "000834", "fund_name": "大成纳指A", "title": "调整大额申购"}},
    )
    res = evaluate_rule(task, curr_recovered, prev_dropped, seen_keys={"AN001"})
    assert res.matched is False
