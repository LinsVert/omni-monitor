"""
Focused probe for all Nasdaq (纳斯达克 / 纳指) QDII funds:
1. Extract & group all Nasdaq QDII funds from data/qdii_snapshot.json
2. Concurrently fetch EastMoney Mobile API (FundMNBasicInformation) for all CNY OTC Nasdaq QDIIs
   to obtain their standing MAXSG (daily purchase limit) — which remains visible even on US holidays!
3. Fetch EastMoney F10 announcement API (api.fund.eastmoney.com/f10/JJGG?type=5) per fund family
   to get the latest non-holiday quota/limit announcement.

Usage:
    uv run probes/test_nasdaq_qdii.py
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SNAPSHOT_PATH = DATA_DIR / "qdii_snapshot.json"

UNLIMITED_THRESHOLD = 800_000_000.0

EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://fundf10.eastmoney.com/",
}


def infer_currency(name: str) -> str:
    if "美元" in name and "美元债" not in name:
        return "USD"
    if any(k in name for k in ("港元", "港币")):
        return "HKD"
    return "CNY"


def load_nasdaq_qdii() -> list[dict[str, Any]]:
    if not SNAPSHOT_PATH.exists():
        raise FileNotFoundError(
            f"{SNAPSHOT_PATH} not found. Run `uv run probes/test_qdii_fetch.py` first."
        )
    data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    items = list(data["items"].values())
    nasdaq = []
    for item in items:
        if any(k in item["name"].upper() for k in ("纳斯达克", "纳指", "NASDAQ")):
            item = dict(item)
            item["currency"] = infer_currency(item["name"])
            nasdaq.append(item)
    nasdaq.sort(key=lambda x: x["code"])
    return nasdaq


def classify_bucket(item: dict[str, Any]) -> str:
    name = item["name"]
    p_status = item["purchase_status"]
    if p_status == "场内交易" or (
        ("ETF" in name.upper() and "联接" not in name)
        and item["code"].startswith(("159", "513"))
    ):
        return "ETF_EXCHANGE"
    if item["currency"] != "CNY":
        return f"OTC_{item['currency']}"
    if "LOF" in name.upper():
        return "OTC_CNY_LOF"
    return "OTC_CNY"


def is_limit_announcement(title: str) -> bool:
    if any(k in title for k in ("节假日", "休市", "非港股通", "境外主要")):
        return False
    if "暂停申购" in title and "赎回" in title and "大额" not in title and "限额" not in title:
        return False
    return any(
        k in title for k in ("大额", "限额", "限制申购", "暂停申购", "恢复申购", "调整申购")
    )


def format_maxsg(val: Any) -> tuple[str, float | None]:
    if val in (None, "", "--", "---"):
        return "未知", None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return str(val), None
    if num >= UNLIMITED_THRESHOLD:
        return "无限额(或暂未设上限)", None
    if num <= 0:
        return "0/不可购", 0.0
    if num >= 10000:
        return f"{num / 10000:g}万元", num
    return f"{num:g}元", num


async def fetch_mobile_basic(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, code: str
) -> dict[str, Any]:
    url = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNBasicInformation"
    params = {
        "FCODE": code,
        "deviceid": "123456",
        "plat": "Android",
        "product": "EFund",
        "version": "6.5.5",
    }
    async with sem:
        resp = await client.get(url, params=params, headers=EM_HEADERS)
        resp.raise_for_status()
        return resp.json().get("Datas") or {}


async def fetch_announcements(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, code: str
) -> list[dict[str, Any]]:
    url = "https://api.fund.eastmoney.com/f10/JJGG"
    params = {"fundcode": code, "pageIndex": 1, "pageSize": 20, "type": 5}
    async with sem:
        resp = await client.get(url, params=params, headers=EM_HEADERS)
        resp.raise_for_status()
        return resp.json().get("Data") or []


async def main() -> None:
    nasdaq = load_nasdaq_qdii()
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in nasdaq:
        buckets[classify_bucket(item)].append(item)

    cny_otc = sorted(buckets["OTC_CNY"] + buckets["OTC_CNY_LOF"], key=lambda x: x["code"])
    etfs = buckets["ETF_EXCHANGE"]
    usd_otc = buckets["OTC_USD"]

    print("=" * 84)
    print(f"All Nasdaq QDII funds enumerated: {len(nasdaq)}")
    print(f"  - 场外人民币 (含LOF): {len(cny_otc)}")
    print(f"  - 场外美元份额      : {len(usd_otc)}")
    print(f"  - 场内纳斯达克 ETF  : {len(etfs)}")

    # Concurrently enrich all CNY OTC Nasdaq funds with Mobile MAXSG + F10 latest limit announcement
    t0 = time.perf_counter()
    sem = asyncio.Semaphore(10)
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        basic_tasks = [fetch_mobile_basic(client, sem, item["code"]) for item in cny_otc]
        ann_tasks = [fetch_announcements(client, sem, item["code"]) for item in cny_otc]
        basic_results, ann_results = await asyncio.gather(
            asyncio.gather(*basic_tasks, return_exceptions=True),
            asyncio.gather(*ann_tasks, return_exceptions=True),
        )
    elapsed = time.perf_counter() - t0
    print(f"\nEnriched {len(cny_otc)} CNY OTC Nasdaq QDIIs (Mobile MAXSG + F10 JJGG) in {elapsed:.2f}s")

    enriched: list[dict[str, Any]] = []
    for item, basic, anns in zip(cny_otc, basic_results, ann_results):
        basic_dict = basic if isinstance(basic, dict) else {}
        anns_list = anns if isinstance(anns, list) else []
        limit_anns = [a for a in anns_list if is_limit_announcement(a.get("TITLE", ""))]
        latest_ann = limit_anns[0] if limit_anns else None

        maxsg_label, maxsg_num = format_maxsg(basic_dict.get("MAXSG"))
        latest_title = latest_ann.get("TITLE", "") if latest_ann else ""
        latest_date = (latest_ann.get("PUBLISHDATE") or "")[:10] if latest_ann else ""

        # Infer likely standing status combining MAXSG + latest non-holiday announcement
        if "暂停申购" in latest_title and "大额" not in latest_title and "恢复" not in latest_title:
            inferred_note = "近期公告暂停申购"
        elif maxsg_num is not None and maxsg_num > 0:
            inferred_note = f"常驻限额 {maxsg_label}"
        else:
            inferred_note = maxsg_label

        enriched.append(
            {
                "code": item["code"],
                "name": item["name"],
                "nav": item["nav"],
                "nav_date": item["nav_date"],
                "today_sgzt": basic_dict.get("SGZT") or item["purchase_status"],
                "today_shzt": basic_dict.get("SHZT") or item["redeem_status"],
                "standing_maxsg": maxsg_num,
                "standing_maxsg_label": maxsg_label,
                "minsg": basic_dict.get("MINSG"),
                "inferred_standing": inferred_note,
                "latest_limit_date": latest_date,
                "latest_limit_title": latest_title,
            }
        )

    # Sort by standing_maxsg ascending (limited first, then paused/unlimited)
    enriched.sort(
        key=lambda x: (
            0 if (x["standing_maxsg"] is not None and x["standing_maxsg"] > 0) else 1,
            x["standing_maxsg"] if x["standing_maxsg"] is not None else 1e12,
            x["code"],
        )
    )

    print("\n--- 场外人民币纳斯达克 QDII 明细 (含常驻限额 MAXSG + 最新限购公告) ---")
    for row in enriched:
        ann_tag = (
            f"[{row['latest_limit_date']}] {row['latest_limit_title'][:32]}"
            if row["latest_limit_title"]
            else "[无近期限额公告]"
        )
        print(
            f"  {row['code']} | {row['inferred_standing']:<16} | MAXSG:{row['standing_maxsg_label']:<10} "
            f"| {row['name']} | {ann_tag}"
        )

    print("\n--- 场内纳斯达克 ETF 全量列表 ---")
    for item in etfs:
        print(f"  {item['code']} | 净值:{item['nav']} ({item['nav_date']}) | {item['name']}")

    out_path = DATA_DIR / "nasdaq_qdii_report.json"
    out_path.write_text(
        json.dumps(
            {
                "total": len(nasdaq),
                "counts": {
                    "cny_otc": len(cny_otc),
                    "usd_otc": len(usd_otc),
                    "etf_exchange": len(etfs),
                },
                "cny_otc_enriched": enriched,
                "etf_exchange": etfs,
                "usd_otc": usd_otc,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nSaved full Nasdaq QDII report -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
