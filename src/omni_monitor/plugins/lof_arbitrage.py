"""LOF tractor arbitrage collector: fuses internal LOF premium feed with live EastMoney daily limits."""

from __future__ import annotations

import os
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field

from omni_monitor.models import Observation
from omni_monitor.plugins.qdii_limits import (
    EM_HEADERS,
    _parse_eastmoney_re_data,
    _parse_float,
    normalize_limit_status,
)
from omni_monitor.registry import BaseCollector, register_collector

CN_TZ = ZoneInfo("Asia/Shanghai")


class LofArbitrageConfig(BaseModel):
    lof_api_url: str = Field(
        default="",
        description="LOF 行情接口地址；留空则自动读取环境变量 OMNI_LOF_API_URL（推荐 Docker 内网直连）",
    )
    min_premium_pct: float = Field(default=3.5, description="最低溢价率门槛 (%)")
    min_daily_limit: float = Field(default=100.0, description="单户最低可申购日限额 (元)")
    min_scale_cny: float = Field(
        default=10_000_000.0, description="最低场内规模门槛 (元，过滤僵尸迷你基金)"
    )
    szse_16_only: bool = Field(
        default=True, description="仅监控深市 16xxxx（支持一拖六拖拉机）"
    )
    friction_pct: float = Field(
        default=0.25, description="1 折申购 + 免五卖出综合摩擦成本 (%)"
    )
    max_nav_age_days: int = Field(
        default=4,
        ge=0,
        description="允许的最大净值滞后自然日数（过滤周净值/长期停更假溢价，0 表示不过滤）",
    )
    trading_window_only: bool = Field(
        default=True, description="开启后仅在工作日 14:30-14:55 尾盘自动推送窗口执行"
    )


def is_nav_fresh(nav_time: str, max_age_days: int, today: date | None = None) -> bool:
    """Return False if nav_time (YYYY-MM-DD...) is older than max_age_days."""
    if max_age_days <= 0:
        return True
    raw = nav_time.strip()
    if len(raw) < 10:
        return False
    try:
        nav_d = date.fromisoformat(raw[:10])
    except ValueError:
        return False
    ref = today or datetime.now(CN_TZ).date()
    return (ref - nav_d).days <= max_age_days


async def _fetch_live_em_limits(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    """Fetch live intraday purchase/redeem status & exact daily limit for all funds in 1 request."""
    resp = await client.get(
        "https://fund.eastmoney.com/Data/Fund_JJJZ_Data.aspx",
        params={"t": "8", "page": "1,30000", "js": "reData", "sort": "fcode,asc"},
        headers=EM_HEADERS,
    )
    resp.raise_for_status()
    parsed = _parse_eastmoney_re_data(resp.text)
    out: dict[str, dict[str, Any]] = {}
    for row in parsed.get("datas", []):
        if len(row) < 13:
            continue
        code = row[0].strip()
        p_status = row[5].strip()
        r_status = row[6].strip()
        raw_lim = _parse_float(row[9])
        is_buy = row[11].strip()
        cat, lim = normalize_limit_status(p_status, r_status, raw_lim, is_buy)
        out[code] = {
            "limit_category": cat,
            "daily_limit": lim,
            "purchase_status": p_status,
            "redeem_status": r_status,
            "nav_date_em": row[4].strip(),
        }
    return out


def _fmt_limit(cat: str, lim: float | None) -> str:
    if cat == "UNLIMITED":
        return "不限额"
    if lim is None:
        return "-"
    if lim >= 10_000:
        return f"{lim / 10_000:g}万"
    return f"{lim:g}元"


@register_collector
class LofArbitrageCollector(BaseCollector):
    plugin_type = "lof_arbitrage"
    display_name = "LOF 拖拉机套利监控"
    description = "融合 LOF 实时溢价率与东财实时申购限额，在工作日尾盘 14:30-14:55 筛选可申购高溢价套利机会"
    data_shape = "scalar"
    config_schema = LofArbitrageConfig

    def should_run(self, now: datetime, config: BaseModel) -> bool:
        assert isinstance(config, LofArbitrageConfig)
        if not config.trading_window_only:
            return True
        cn = now.astimezone(CN_TZ)
        if cn.weekday() >= 5:
            return False
        t = cn.time()
        return time(14, 30) <= t <= time(14, 55)

    async def fetch(
        self, task_id: str, config: BaseModel, client: httpx.AsyncClient
    ) -> Observation:
        assert isinstance(config, LofArbitrageConfig)
        api_url = config.lof_api_url.strip() or os.environ.get("OMNI_LOF_API_URL", "").strip()
        if not api_url:
            raise RuntimeError("OMNI_LOF_API_URL is not configured")

        # Fetch LOF premiums + live EastMoney purchase limits concurrently
        lof_resp = await client.get(api_url, timeout=12.0)
        lof_resp.raise_for_status()
        lof_rows: list[dict[str, Any]] = lof_resp.json()

        live_limits: dict[str, dict[str, Any]] = {}
        try:
            live_limits = await _fetch_live_em_limits(client)
        except Exception:
            # Fallback to buyStatus/buyLimit from lof-monitor if EastMoney temporarily hiccups
            live_limits = {}

        opportunities: list[dict[str, Any]] = []
        scanned = 0

        for row in lof_rows:
            code = str(row.get("code") or "").strip()
            if not code:
                continue
            if config.szse_16_only and not code.startswith("16"):
                continue
            scanned += 1

            premium = row.get("premium")
            price = row.get("price")
            nav = row.get("nav")
            scale = float(row.get("volume") or 0.0)
            if not isinstance(premium, (int, float)) or not isinstance(price, (int, float)) or not isinstance(nav, (int, float)):
                continue
            if premium < config.min_premium_pct:
                continue
            if scale > 0 and scale < config.min_scale_cny:
                continue
            nav_date = str(row.get("navTime") or "")
            if not is_nav_fresh(nav_date, config.max_nav_age_days):
                continue

            # Resolve real-time purchase limit (prefer live EastMoney over stale 21:00 cache)
            em_info = live_limits.get(code)
            if em_info:
                cat = em_info["limit_category"]
                daily_limit = em_info["daily_limit"]
            else:
                bs = str(row.get("buyStatus") or "")
                bl = row.get("buyLimit")
                if "暂停" in bs:
                    cat, daily_limit = "QUOTA_SUSPENDED", 0.0
                elif "开放" in bs and bl is None:
                    cat, daily_limit = "UNLIMITED", None
                elif isinstance(bl, (int, float)) and bl > 0:
                    cat, daily_limit = "LIMITED", float(bl)
                else:
                    cat, daily_limit = "UNKNOWN", None

            if cat not in ("LIMITED", "UNLIMITED"):
                continue
            if cat == "LIMITED" and (daily_limit is None or daily_limit < config.min_daily_limit):
                continue

            accounts = int(row.get("tractorAccounts") or (6 if code.startswith("16") and code != "161226" else 1))
            net_spread_pct = max(0.0, float(premium) - config.friction_pct)

            if cat == "LIMITED" and daily_limit is not None:
                total_cap = daily_limit * accounts
                est_profit = round(total_cap * net_spread_pct / 100.0, 1)
                profit_text = f"估赚约 +{est_profit:g}元 ({accounts}户共{total_cap:g}元)"
            else:
                # For unlimited funds, show estimate per 10,000 CNY
                est_per_10k = round(10_000.0 * net_spread_pct / 100.0, 1)
                est_profit = est_per_10k
                profit_text = f"每万元估赚 +{est_per_10k:g}元 (不限额)"

            lim_label = _fmt_limit(cat, daily_limit)
            name = str(row.get("name") or code)

            opportunities.append(
                {
                    "code": code,
                    "name": name,
                    "event": f"溢价+{premium:.2f}%",
                    "premium": round(float(premium), 2),
                    "price": price,
                    "nav": nav,
                    "nav_date": nav_date,
                    "limit_category": cat,
                    "daily_limit": daily_limit,
                    "tractor_accounts": accounts,
                    "est_profit_cny": est_profit,
                    "before_text": f"限额 {lim_label} × {accounts}户 · 净值日 {nav_date}",
                    "after_text": profit_text,
                    "is_watchlist": premium >= 5.0,
                    "url": f"https://fund.eastmoney.com/{code}.html",
                }
            )

        opportunities.sort(key=lambda x: (-x["premium"], -x["est_profit_cny"]))
        items_map = {op["code"]: op for op in opportunities}

        top_summary = ""
        if opportunities:
            preview = "；".join(
                f"{op['code']} {op['name']}(+{op['premium']:.1f}%, {_fmt_limit(op['limit_category'], op['daily_limit'])})"
                for op in opportunities[:2]
            )
            top_summary = preview + (f" 等共 {len(opportunities)} 只" if len(opportunities) > 2 else "")

        display = [
            {"label": "可套利数量", "value": f"{len(opportunities)} 只"},
            {"label": "扫描 LOF 数", "value": f"{scanned} 只"},
            {"label": "筛选门槛", "value": f"溢价 ≥ {config.min_premium_pct:g}% · 限额 ≥ {config.min_daily_limit:g}元"},
            {"label": "风控提示", "value": "务必核对净值日期防 T-1 假溢价"},
        ]
        if opportunities:
            display.insert(1, {"label": "最高溢价", "value": f"+{opportunities[0]['premium']:.2f}% ({opportunities[0]['name']})"})

        return Observation(
            task_id=task_id,
            value=float(len(opportunities)),
            unit="只",
            metrics={
                "opportunities_count": len(opportunities),
                "max_premium": opportunities[0]["premium"] if opportunities else 0.0,
                "top_summary": top_summary,
                "scanned_count": scanned,
            },
            items=items_map,
            display_fields=display,
            source_name="LOF Internal Feed + EastMoney Live Limits",
        )
