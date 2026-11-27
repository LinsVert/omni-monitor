"""QDII T-1 limit-change announcement collector using EastMoney F10 JJGG + full-text API."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, Field

from omni_monitor.models import Observation
from omni_monitor.plugins.qdii_limits import get_qdii_universe
from omni_monitor.registry import BaseCollector, register_collector

EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://fundf10.eastmoney.com/",
}


def is_quota_announcement(title: str) -> bool:
    if any(k in title for k in ("节假日", "休市", "非港股通", "境外主要")):
        return False
    if "暂停申购" in title and "赎回" in title and "大额" not in title and "限额" not in title:
        return False
    return any(
        k in title for k in ("大额", "限额", "限制申购", "暂停申购", "恢复申购", "调整申购")
    )


def _format_num_yuan(raw_num: str, unit: str = "") -> str:
    try:
        val = float(raw_num.replace(",", ""))
        num_str = f"{val:g}"
    except ValueError:
        num_str = raw_num
    return f"限额 {num_str}{unit}元"


def _extract_fields(title: str, content: str) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", content)
    if "恢复大额申购" in title or "恢复申购" in title or ("取消" in title and "限制" in title):
        action = "REOPENED"
    elif "暂停申购" in title and "大额" not in title:
        action = "QUOTA_SUSPENDED"
    elif any(k in title for k in ("调整大额", "限制金额", "金额上限", "暂停大额")):
        action = "LIMIT_ADJUSTED"
    else:
        action = "NOTICE"

    eff_date = None
    for pat in (
        r"(?:暂停|限制|调整|恢复)[^\u3002]{0,25}起始日[^\d]{0,12}(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)",
        r"自\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*(?:起|开始)",
    ):
        m = re.search(pat, text)
        if m:
            eff_date = re.sub(r"\s+", "", m.group(1))
            break

    limit_summary = ""
    # Pattern 1: Standard CSRC table format: "限制申购金额（单位：人民币元） 50.00" or "（单位：万元） 1.00"
    table_m = re.search(
        r"限制(?:大额)?(?:申购|定期定额投资)金额[（(][^）)]*?单位[：:]\s*(?:人民币)?(万|亿)?元[）)]\s*([\d,]+(?:\.\d+)?)",
        text,
    )
    if table_m:
        limit_summary = _format_num_yuan(table_m.group(2), table_m.group(1) or "")
    else:
        # Pattern 2: Prose format: "调整为5元", "限制申购金额为100万元", "单日累计不超过200元"
        for pat in (
            r"限制[^\u3002]{0,30}?金额(?:（[^）]*）|\([^)]*\))?[^\d]{0,12}([\d,]+(?:\.\d+)?)\s*(万|亿)?\s*元",
            r"(?:调整为|上限为|不超过|限额为|设定为)\s*(?:人民币\s*)?([\d,]+(?:\.\d+)?)\s*(万|亿)?\s*元",
        ):
            m = re.search(pat, text)
            if m:
                limit_summary = _format_num_yuan(m.group(1), m.group(2) or "")
                break

    if not limit_summary:
        limit_summary = {
            "REOPENED": "恢复申购/取消限额",
            "QUOTA_SUSPENDED": "暂停申购",
            "LIMIT_ADJUSTED": "调整大额限额(见原文)",
        }.get(action, "限购变动公告")

    snippet_m = re.search(
        r"([^。]{0,50}(?:限制申购金额|调整为|单日单个基金账户|暂停申购起始日)[^。]{0,110}。?)",
        text,
    )
    snippet = snippet_m.group(1).strip() if snippet_m else text[:180]
    return {
        "action": action,
        "effective_date": eff_date,
        "limit_summary": limit_summary,
        "snippet": snippet,
    }


class QdiiAnnouncementsConfig(BaseModel):
    codes: list[str] = Field(
        default_factory=list,
        description="指定监控的基金代码；若留空则根据 keywords 自动选取代表性场外人民币 QDII",
    )
    keywords: list[str] = Field(
        default_factory=lambda: ["纳斯达克", "纳指", "标普500"],
        description="当 codes 为空时，按关键词自动筛选监控的 QDII 家族",
    )
    lookback_days: int = Field(default=30, ge=1, le=365)
    fetch_body: bool = Field(
        default=True,
        description="拉取公告纯文本并抽取生效日期与限购金额",
    )


@register_collector
class QdiiAnnouncementsCollector(BaseCollector):
    plugin_type = "qdii_announcements"
    display_name = "QDII 限购/暂停公告提前预警"
    description = "监听东财 F10 场外基金公告，过滤节假日噪音，自动抽取生效日与限额并提供官方 PDF 链接"
    data_shape = "collection"
    config_schema = QdiiAnnouncementsConfig

    async def _resolve_target_funds(
        self, cfg: QdiiAnnouncementsConfig, client: httpx.AsyncClient
    ) -> list[tuple[str, str]]:
        universe = await get_qdii_universe(client)
        if cfg.codes:
            return [(c, universe.get(c, {}).get("name", c)) for c in cfg.codes]

        # Pick 1 representative CNY OTC share per fund family to avoid duplicate announcements
        seen_families: set[str] = set()
        targets: list[tuple[str, str]] = []
        for code in sorted(universe.keys()):
            info = universe[code]
            if info["currency"] != "CNY":
                continue
            name = info["name"]
            if code.startswith(("159", "513")) and "联接" not in name:
                continue
            if cfg.keywords and not any(kw.upper() in name.upper() for kw in cfg.keywords):
                continue
            family_key = re.sub(r"[A-Za-z0-9()（）\-]+", "", name)[:8]
            if family_key in seen_families:
                continue
            seen_families.add(family_key)
            targets.append((code, name))
        return targets[:40]

    async def fetch(
        self, task_id: str, config: BaseModel, client: httpx.AsyncClient
    ) -> Observation:
        assert isinstance(config, QdiiAnnouncementsConfig)
        targets = await self._resolve_target_funds(config, client)
        cutoff = (datetime.now() - timedelta(days=config.lookback_days)).strftime("%Y-%m-%d")
        sem = asyncio.Semaphore(12)

        async def _check_one(code: str, fund_name: str) -> list[dict[str, Any]]:
            async with sem:
                resp = await client.get(
                    "https://api.fund.eastmoney.com/f10/JJGG",
                    params={"fundcode": code, "pageIndex": 1, "pageSize": 15, "type": 5},
                    headers=EM_HEADERS,
                    timeout=8.0,
                )
                resp.raise_for_status()
                anns = resp.json().get("Data") or []

            matched: list[dict[str, Any]] = []
            for ann in anns:
                title = str(ann.get("TITLE") or "")
                pub_date = str(ann.get("PUBLISHDATE") or "")[:10]
                if pub_date < cutoff or not is_quota_announcement(title):
                    continue
                art_code = str(ann.get("ID") or "")
                body = ""
                if config.fetch_body and art_code:
                    try:
                        async with sem:
                            c_resp = await client.get(
                                "https://np-cnotice-stock.eastmoney.com/api/content/ann",
                                params={"art_code": art_code, "client_source": "web", "page_index": 1},
                                headers=EM_HEADERS,
                                timeout=8.0,
                            )
                            c_resp.raise_for_status()
                            body = (c_resp.json().get("data") or {}).get("notice_content") or ""
                    except Exception:
                        body = ""
                extracted = _extract_fields(title, body)
                matched.append(
                    {
                        "art_code": art_code,
                        "code": code,
                        "fund_name": fund_name,
                        "name": fund_name,
                        "title": title,
                        "publish_date": pub_date,
                        "pdf_url": f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf" if art_code else None,
                        **extracted,
                    }
                )
            return matched

        batches = await asyncio.gather(
            *(_check_one(c, n) for c, n in targets), return_exceptions=True
        )
        items: dict[str, dict[str, Any]] = {}
        for batch in batches:
            if isinstance(batch, list):
                for entry in batch:
                    key = entry["art_code"] or f"{entry['code']}_{entry['publish_date']}"
                    items[key] = entry

        return Observation(
            task_id=task_id,
            items=items,
            metrics={"watched_families": len(targets), "announcements_count": len(items)},
            source_name="EastMoney F10 JJGG",
        )
