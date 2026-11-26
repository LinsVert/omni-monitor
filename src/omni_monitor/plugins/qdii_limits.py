"""China QDII full-universe purchase limit collector (EastMoney static universe + batch limit table)."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from omni_monitor.models import Observation
from omni_monitor.registry import BaseCollector, register_collector

EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://fund.eastmoney.com/Fund_sgzt_bzdm.html",
}

UNLIMITED_THRESHOLD = 800_000_000.0
VALID_EM_BUY_CODES = {str(i) for i in range(1, 11)}
UNIVERSE_CACHE_TTL_SECONDS = 6 * 3600


def infer_currency(name: str) -> str:
    if "美元" in name and "美元债" not in name:
        return "USD"
    if any(k in name for k in ("港元", "港币")):
        return "HKD"
    return "CNY"


def _parse_float(val: str) -> float | None:
    val = val.strip()
    if not val or val in {"---", "--"}:
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _parse_eastmoney_re_data(text: str) -> dict[str, Any]:
    match = re.search(r"var\s+reData\s*=\s*(\{.*\})\s*;?", text, re.DOTALL)
    if not match:
        raise ValueError(f"Unexpected Fund_JJJZ_Data payload: {text[:160]!r}")
    raw_obj = match.group(1)
    json_like = re.sub(r"([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)", r'\1"\2"\3', raw_obj)
    return json.loads(json_like)


def normalize_limit_status(
    purchase_status: str,
    redeem_status: str,
    raw_limit: float | None,
    is_buy_code: str,
) -> tuple[str, float | None]:
    p_status = purchase_status.strip()
    r_status = redeem_status.strip()

    if any(k in p_status for k in ("暂停申购", "停止申购")):
        if any(k in r_status for k in ("暂停赎回", "停止赎回")):
            return "HOLIDAY_SUSPENDED", 0.0
        return "QUOTA_SUSPENDED", 0.0

    if any(k in p_status for k in ("封闭期", "认购期", "场内", "暂停交易")) or p_status == "":
        return "OTHER", None

    if is_buy_code not in VALID_EM_BUY_CODES:
        return "NO_EM_LIMIT_DATA", None

    if raw_limit is None or raw_limit < 0:
        if "开放" in p_status:
            return "UNLIMITED", None
        return "UNKNOWN", None

    if raw_limit >= UNLIMITED_THRESHOLD:
        return "UNLIMITED", None
    if raw_limit == 0:
        return "QUOTA_SUSPENDED", 0.0
    return "LIMITED", raw_limit


# Module-level cache shared by qdii_limits & qdii_announcements
_UNIVERSE_CACHE: tuple[float, dict[str, dict[str, str]]] = (0.0, {})


async def get_qdii_universe(client: httpx.AsyncClient) -> dict[str, dict[str, str]]:
    """Fetch & cache full market QDII / overseas fund directory from EastMoney."""
    global _UNIVERSE_CACHE
    cached_at, cached_map = _UNIVERSE_CACHE
    if cached_map and (time.monotonic() - cached_at) < UNIVERSE_CACHE_TTL_SECONDS:
        return cached_map

    resp = await client.get(
        "https://fund.eastmoney.com/js/fundcode_search.js", headers=EM_HEADERS
    )
    resp.raise_for_status()
    match = re.search(r"var\s+r\s*=\s*(\[.*\])\s*;?", resp.text, re.DOTALL)
    if not match:
        raise ValueError("Failed to parse fundcode_search.js")

    qdii_map: dict[str, dict[str, str]] = {}
    for row in json.loads(match.group(1)):
        if len(row) < 4:
            continue
        code, abbr, name, fund_type = row[0], row[1], row[2], row[3]
        if "QDII" in fund_type.upper() or "QDII" in name.upper() or "海外" in fund_type:
            qdii_map[code] = {
                "code": code,
                "abbr": abbr,
                "name": name,
                "fund_type": fund_type,
                "currency": infer_currency(name),
            }

    if len(qdii_map) < 200:
        raise RuntimeError(f"Sanity check failed: QDII universe unexpectedly small ({len(qdii_map)})")
    _UNIVERSE_CACHE = (time.monotonic(), qdii_map)
    return qdii_map


class QdiiLimitsConfig(BaseModel):
    keywords: list[str] = Field(
        default_factory=list,
        description="按基金名/类型关键词过滤（如 ['纳斯达克','纳指','标普500']）；留空则枚举全市场 QDII",
    )
    currencies: list[str] = Field(
        default_factory=list,
        description="按币种过滤（如 ['CNY']）；留空则含人民币/美元/港元全部份额",
    )
    exclude_exchange_etf: bool = Field(
        default=False,
        description="是否排除纯场内交易 ETF（仅保留可场外申购的联接/LOF/指数基金）",
    )


@register_collector
class QdiiLimitsCollector(BaseCollector):
    plugin_type = "qdii_limits"
    display_name = "QDII 全市场限购枚举监控"
    description = "自动枚举全市场 700+ QDII/海外基金，单次批量获取申购状态与单日限额，自动区分节假日休市与额度限购"
    data_shape = "collection"
    config_schema = QdiiLimitsConfig

    async def _fetch_all_qdii_rows(
        self, client: httpx.AsyncClient
    ) -> dict[str, dict[str, Any]]:
        universe = await get_qdii_universe(client)
        all_rows: list[list[str]] = []
        page = 1
        page_size = 30000

        while True:
            resp = await client.get(
                "https://fund.eastmoney.com/Data/Fund_JJJZ_Data.aspx",
                params={
                    "t": "8",
                    "page": f"{page},{page_size}",
                    "js": "reData",
                    "sort": "fcode,asc",
                },
                headers=EM_HEADERS,
            )
            resp.raise_for_status()
            parsed = _parse_eastmoney_re_data(resp.text)
            rows = parsed.get("datas", [])
            total = int(parsed.get("record", len(rows)))
            all_rows.extend(rows)
            if not rows or len(all_rows) >= total:
                break
            page_size = len(rows)
            page += 1
            if page > 100:
                break

        items: dict[str, dict[str, Any]] = {}
        for row in all_rows:
            if len(row) < 13:
                continue
            code = row[0].strip()
            name = row[1].strip()
            fund_type = row[2].strip()
            if (
                code not in universe
                and "QDII" not in fund_type.upper()
                and "QDII" not in name.upper()
                and "海外" not in fund_type
            ):
                continue

            nav = _parse_float(row[3])
            nav_date = row[4].strip()
            purchase_status = row[5].strip()
            redeem_status = row[6].strip()
            min_purchase = _parse_float(row[8])
            raw_limit = _parse_float(row[9])
            is_buy_code = row[11].strip()
            fee_rate = row[12].strip()

            limit_category, daily_limit = normalize_limit_status(
                purchase_status, redeem_status, raw_limit, is_buy_code
            )
            items[code] = {
                "code": code,
                "name": name,
                "fund_type": fund_type or universe.get(code, {}).get("fund_type", ""),
                "currency": infer_currency(name),
                "nav": nav,
                "nav_date": nav_date,
                "purchase_status": purchase_status,
                "redeem_status": redeem_status,
                "min_purchase": min_purchase,
                "daily_limit": daily_limit,
                "limit_category": limit_category,
                "fee_rate": fee_rate,
                "is_buy_code": is_buy_code,
            }

        if len(items) < 200:
            raise RuntimeError(f"Sanity check failed: matched QDII rows unexpectedly low ({len(items)})")
        return items

    def _filter_items(
        self, all_items: dict[str, dict[str, Any]], cfg: QdiiLimitsConfig
    ) -> dict[str, dict[str, Any]]:
        filtered: dict[str, dict[str, Any]] = {}
        for code, item in all_items.items():
            if cfg.currencies and item["currency"] not in cfg.currencies:
                continue
            if cfg.exclude_exchange_etf and item["purchase_status"] == "场内交易":
                continue
            if cfg.keywords:
                haystack = f"{item['name']} {item['fund_type']}".upper()
                if not any(kw.upper() in haystack for kw in cfg.keywords):
                    continue
            filtered[code] = item
        return filtered

    async def fetch(
        self, task_id: str, config: BaseModel, client: httpx.AsyncClient
    ) -> Observation:
        batch = await self.fetch_batch([(task_id, config)], client)
        res = batch[task_id]
        if isinstance(res, Exception):
            raise res
        return res

    async def fetch_batch(
        self,
        tasks: list[tuple[str, BaseModel]],
        client: httpx.AsyncClient,
    ) -> dict[str, Observation | Exception]:
        """Multiple QDII limit tasks share ONE single upstream fetch."""
        try:
            all_items = await self._fetch_all_qdii_rows(client)
        except Exception as exc:
            return {tid: exc for tid, _ in tasks}

        results: dict[str, Observation | Exception] = {}
        for tid, cfg in tasks:
            assert isinstance(cfg, QdiiLimitsConfig)
            subset = self._filter_items(all_items, cfg)
            results[tid] = Observation(
                task_id=tid,
                items=subset,
                metrics={"total_count": len(subset)},
                source_name="EastMoney Fund_JJJZ_Data",
                source_url="https://fund.eastmoney.com/Fund_sgzt_bzdm.html",
            )
        return results
