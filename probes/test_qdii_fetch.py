"""
Live probe script to test and verify stable Chinese QDII data sources:
1. EastMoney fundcode_search.js       -> Full QDII universe enumeration
2. EastMoney Fund_JJJZ_Data.aspx?t=8  -> Purchase status & daily purchase limit (日累计限定金额)
3. CNINFO hisAnnouncement/query       -> Recent QDII limit-change announcements (T-1 early warning)

Usage:
    uv run probes/test_qdii_fetch.py
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://fund.eastmoney.com/Fund_sgzt_bzdm.html",
}

UNLIMITED_THRESHOLD = 800_000_000.0  # Mirrors EastMoney sgzt_new_js MoneyChange logic


def fetch_qdii_universe(client: httpx.Client) -> dict[str, dict[str, str]]:
    """Source 1: Enumerate all QDII funds from EastMoney static fundcode_search.js."""
    url = "https://fund.eastmoney.com/js/fundcode_search.js"
    t0 = time.perf_counter()
    resp = client.get(url, headers=DEFAULT_HEADERS)
    resp.raise_for_status()
    elapsed = time.perf_counter() - t0

    # Format: var r = [["000001","HXCZHH","华夏成长混合","混合型-灵活","HUAXIACHENGZHANGHUNHE"], ...];
    match = re.search(r"var\s+r\s*=\s*(\[.*\])\s*;?", resp.text, re.DOTALL)
    if not match:
        raise ValueError("Failed to parse fundcode_search.js payload")

    all_funds: list[list[str]] = json.loads(match.group(1))
    qdii_map: dict[str, dict[str, str]] = {}
    type_counter: Counter[str] = Counter()

    for row in all_funds:
        if len(row) < 4:
            continue
        code, abbr, name, fund_type = row[0], row[1], row[2], row[3]
        if (
            "QDII" in fund_type.upper()
            or "QDII" in name.upper()
            or "海外" in fund_type
        ):
            qdii_map[code] = {
                "code": code,
                "abbr": abbr,
                "name": name,
                "fund_type": fund_type,
            }
            type_counter[fund_type] += 1

    print("=" * 72)
    print(f"[Source 1] EastMoney fundcode_search.js  ({elapsed:.2f}s)")
    print(f"  Total public funds : {len(all_funds)}")
    print(f"  QDII universe      : {len(qdii_map)}")
    print("  Breakdown by type  :")
    for ftype, cnt in type_counter.most_common():
        print(f"    - {ftype}: {cnt}")
    return qdii_map


def _parse_eastmoney_re_data(text: str) -> dict[str, Any]:
    """Parse `var reData={datas:[...],record:"...",pages:"...",curpage:"..."};` safely."""
    match = re.search(r"var\s+reData\s*=\s*(\{.*\})\s*;?", text, re.DOTALL)
    if not match:
        raise ValueError(f"Unexpected Fund_JJJZ_Data response prefix: {text[:200]!r}")
    raw_obj = match.group(1)
    # EastMoney uses unquoted JS object keys like {datas:[...],record:"123",...}
    json_like = re.sub(r"([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)", r'\1"\2"\3', raw_obj)
    return json.loads(json_like)


def _parse_float(val: str) -> float | None:
    val = val.strip()
    if not val or val in {"---", "--"}:
        return None
    try:
        return float(val)
    except ValueError:
        return None


VALID_EM_BUY_CODES = {str(i) for i in range(1, 11)}


def _infer_currency(name: str) -> str:
    """Avoid false positives like '美元债' (CNY fund investing in USD bonds)."""
    if "美元" in name and "美元债" not in name:
        return "USD"
    if any(k in name for k in ("港元", "港币")):
        return "HKD"
    return "CNY"


def _normalize_limit(
    purchase_status: str,
    redeem_status: str,
    raw_limit: float | None,
    is_buy_code: str,
) -> tuple[str, float | None]:
    """
    Distinguish clearly between:
      - HOLIDAY_SUSPENDED : both purchase & redeem suspended (typically overseas market holiday)
      - QUOTA_SUSPENDED   : purchase suspended while redeem is still open (true QDII quota halt!)
      - LIMITED           : open with explicit daily cap (e.g. 5, 10, 100, 1000)
      - UNLIMITED         : open without practical daily cap
      - NO_EM_LIMIT_DATA  : open/limited on market, but not distributed on EastMoney (is_buy_code empty)
      - OTHER             : 认购期 / 封闭期 / 场内交易 / empty
    """
    p_status = purchase_status.strip()
    r_status = redeem_status.strip()

    if any(k in p_status for k in ("暂停申购", "停止申购")):
        if any(k in r_status for k in ("暂停赎回", "停止赎回")):
            return "HOLIDAY_SUSPENDED", 0.0
        return "QUOTA_SUSPENDED", 0.0

    if any(k in p_status for k in ("封闭期", "认购期", "场内", "暂停交易")) or p_status == "":
        return "OTHER", None

    # EastMoney leaves is_buy_code empty and daily_limit=0 for shares it doesn't distribute (e.g. USD/I/F)
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


def fetch_qdii_purchase_limits(
    client: httpx.Client, qdii_universe: dict[str, dict[str, str]]
) -> dict[str, dict[str, Any]]:
    """Source 2: Fetch full purchase status & daily limits from EastMoney Fund_JJJZ_Data.aspx?t=8."""
    base_url = "https://fund.eastmoney.com/Data/Fund_JJJZ_Data.aspx"
    t0 = time.perf_counter()

    # Probe whether large page size works in 1 shot, fall back to pagination if server caps page size
    all_rows: list[list[str]] = []
    page = 1
    page_size = 30000
    total_records = 0
    requests_made = 0

    while True:
        params = {
            "t": "8",
            "page": f"{page},{page_size}",
            "js": "reData",
            "sort": "fcode,asc",
        }
        resp = client.get(base_url, params=params, headers=DEFAULT_HEADERS)
        resp.raise_for_status()
        requests_made += 1
        parsed = _parse_eastmoney_re_data(resp.text)

        rows = parsed.get("datas", [])
        total_records = int(parsed.get("record", len(rows)))
        all_rows.extend(rows)

        if not rows or len(all_rows) >= total_records:
            break
        # Server capped page size; continue with actual returned size
        page_size = len(rows)
        page += 1
        if page > 100:
            break

    elapsed = time.perf_counter() - t0

    qdii_items: dict[str, dict[str, Any]] = {}
    for row in all_rows:
        if len(row) < 13:
            continue
        code = row[0].strip()
        name = row[1].strip()
        fund_type = row[2].strip()

        if code not in qdii_universe and "QDII" not in fund_type.upper() and "QDII" not in name.upper():
            continue

        nav = _parse_float(row[3])
        nav_date = row[4].strip()
        purchase_status = row[5].strip()
        redeem_status = row[6].strip()
        next_open_date = row[7].strip()
        min_purchase = _parse_float(row[8])
        raw_daily_limit = _parse_float(row[9])
        is_buy_code = row[11].strip()
        fee_rate = row[12].strip()

        limit_category, daily_limit = _normalize_limit(
            purchase_status, redeem_status, raw_daily_limit, is_buy_code
        )

        currency = _infer_currency(name)
        qdii_items[code] = {
            "code": code,
            "name": name,
            "fund_type": fund_type or qdii_universe.get(code, {}).get("fund_type", ""),
            "currency": currency,
            "nav": nav,
            "nav_date": nav_date,
            "purchase_status": purchase_status,
            "redeem_status": redeem_status,
            "next_open_date": next_open_date,
            "min_purchase": min_purchase,
            "daily_limit": daily_limit,
            "raw_daily_limit": raw_daily_limit,
            "limit_category": limit_category,
            "fee_rate": fee_rate,
            "is_buy_code": is_buy_code,
        }

    status_counter = Counter(item["purchase_status"] for item in qdii_items.values())
    cat_counter = Counter(item["limit_category"] for item in qdii_items.values())

    print("\n" + "=" * 72)
    print(
        f"[Source 2] EastMoney Fund_JJJZ_Data.aspx?t=8  "
        f"({elapsed:.2f}s, {requests_made} HTTP req)"
    )
    print(f"  Total market rows  : {len(all_rows)} / record={total_records}")
    print(f"  Matched QDII funds : {len(qdii_items)} (universe={len(qdii_universe)})")
    print("  By limit category  :")
    for cat, cnt in cat_counter.most_common():
        print(f"    - {cat:<10}: {cnt}")
    print("  By raw purchase_status :")
    for st, cnt in status_counter.most_common():
        print(f"    - {st or '<empty>'}: {cnt}")

    # Show sample: truly quota-suspended + tightly limited CNY QDIIs
    quota_halted = [i for i in qdii_items.values() if i["limit_category"] == "QUOTA_SUSPENDED"]
    limited = sorted(
        [i for i in qdii_items.values() if i["limit_category"] == "LIMITED"],
        key=lambda x: x["daily_limit"] if x["daily_limit"] is not None else 1e12,
    )

    print(f"\n  [Quota-Suspended] 暂停申购但开放赎回 ({len(quota_halted)} total):")
    for item in quota_halted[:10]:
        print(f"    {item['code']} | {item['purchase_status']} / {item['redeem_status']} | {item['name']}")

    print(f"\n  [Tightly Limited] 限大额 QDII ({len(limited)} total, lowest 12):")
    for item in limited[:12]:
        print(
            f"    {item['code']} | 限额: {item['daily_limit']:g}元 ({item['currency']}) | {item['name']}"
        )

    return qdii_items


def fetch_cninfo_qdii_announcements(
    client: httpx.Client, qdii_codes: set[str], days: int = 14
) -> list[dict[str, Any]]:
    """Source 3: Query CNINFO recent limit-change announcements & filter by QDII code pool."""
    url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
    headers = {
        "User-Agent": DEFAULT_HEADERS["User-Agent"],
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "http://www.cninfo.com.cn",
        "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
        "X-Requested-With": "XMLHttpRequest",
    }

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    se_date = f"{start_dt:%Y-%m-%d}~{end_dt:%Y-%m-%d}"

    keywords = ["大额申购", "申购限额", "暂停申购", "恢复申购"]
    matched: dict[str, dict[str, Any]] = {}
    t0 = time.perf_counter()

    for kw in keywords:
        form = {
            "pageNum": "1",
            "pageSize": "50",
            "column": "fund",
            "tabName": "fulltext",
            "plate": "",
            "stock": "",
            "searchkey": kw,
            "secid": "",
            "category": "",
            "trade": "",
            "seDate": se_date,
            "sortName": "",
            "sortType": "",
            "isHLtitle": "false",
        }
        resp = client.post(url, data=form, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        anns = payload.get("announcements") or []
        for ann in anns:
            sec_code = str(ann.get("secCode") or "").strip()
            title = str(ann.get("announcementTitle") or "").strip()
            ann_id = str(ann.get("announcementId") or "")
            if sec_code in qdii_codes or "QDII" in title.upper():
                is_holiday = any(
                    k in title for k in ("节假日", "休市", "非港股通", "境外主要市场")
                )
                ts_ms = ann.get("announcementTime")
                pub_time = (
                    datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M")
                    if isinstance(ts_ms, (int, float))
                    else str(ts_ms)
                )
                matched[ann_id] = {
                    "announcement_id": ann_id,
                    "code": sec_code,
                    "sec_name": ann.get("secName", ""),
                    "title": title,
                    "is_holiday_notice": is_holiday,
                    "publish_time": pub_time,
                    "pdf_url": f"http://static.cninfo.com.cn/{ann.get('adjunctUrl', '')}",
                }

    elapsed = time.perf_counter() - t0
    results = sorted(matched.values(), key=lambda x: x["publish_time"], reverse=True)
    quota_anns = [a for a in results if not a["is_holiday_notice"]]
    holiday_anns = [a for a in results if a["is_holiday_notice"]]

    print("\n" + "=" * 72)
    print(f"[Source 3] CNINFO Fund Announcements ({se_date}, {elapsed:.2f}s)")
    print(
        f"  Matched QDII announcements : {len(results)} "
        f"(quota/limit={len(quota_anns)}, holiday={len(holiday_anns)})"
    )
    for ann in quota_anns[:12]:
        print(f"    [LIMIT] [{ann['publish_time']}] {ann['code']} {ann['title']}")
    if not quota_anns:
        for ann in holiday_anns[:5]:
            print(f"    [HOLIDAY] [{ann['publish_time']}] {ann['code']} {ann['title']}")

    return results


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        qdii_universe = fetch_qdii_universe(client)
        qdii_items = fetch_qdii_purchase_limits(client, qdii_universe)

        # Save snapshot for inspection / future diff tests
        snapshot_path = DATA_DIR / "qdii_snapshot.json"
        snapshot = {
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "universe_count": len(qdii_universe),
            "items_count": len(qdii_items),
            "items": qdii_items,
        }
        snapshot_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nSaved QDII snapshot -> {snapshot_path}")

        try:
            announcements = fetch_cninfo_qdii_announcements(
                client, set(qdii_universe.keys()), days=14
            )
            ann_path = DATA_DIR / "qdii_announcements.json"
            ann_path.write_text(
                json.dumps(announcements, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"Saved CNINFO announcements -> {ann_path}")
        except Exception as exc:
            print(f"\n[Source 3] CNINFO probe failed: {exc!r}")


if __name__ == "__main__":
    main()
