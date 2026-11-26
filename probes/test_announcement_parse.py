"""
Probe EastMoney F10 announcement full-text API and test structured extraction
of effective date, action type, and exact daily limit amount from real Nasdaq QDII notices.

Usage:
    uv run probes/test_announcement_parse.py
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://fundf10.eastmoney.com/",
}

SAMPLE_CODES = [
    "017436",  # 华宝纳斯达克精选股票发起式(QDII)A
    "019441",  # 万家纳斯达克100指数发起式(QDII)A
    "014978",  # 华安纳斯达克100ETF联接(QDII)C
    "019736",  # 宝盈纳斯达克100指数发起(QDII)A
    "017894",  # 汇添富纳斯达克生物科技联接(QDII)A
    "019524",  # 华泰柏瑞纳斯达克100联接(QDII)A
]


def is_limit_announcement(title: str) -> bool:
    if any(k in title for k in ("节假日", "休市", "非港股通", "境外主要")):
        return False
    if "暂停申购" in title and "赎回" in title and "大额" not in title and "限额" not in title:
        return False
    return any(
        k in title for k in ("大额", "限额", "限制申购", "暂停申购", "恢复申购", "调整申购")
    )


def fetch_latest_limit_ann(client: httpx.Client, code: str) -> dict[str, Any] | None:
    resp = client.get(
        "https://api.fund.eastmoney.com/f10/JJGG",
        params={"fundcode": code, "pageIndex": 1, "pageSize": 20, "type": 5},
        headers=EM_HEADERS,
    )
    resp.raise_for_status()
    for item in resp.json().get("Data") or []:
        if is_limit_announcement(item.get("TITLE", "")):
            return item
    return None


def fetch_announcement_content(client: httpx.Client, art_code: str) -> dict[str, Any]:
    """Fetch plain-text announcement body directly from EastMoney notice content API."""
    url = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
    params = {
        "art_code": art_code,
        "client_source": "web",
        "page_index": 1,
    }
    resp = client.get(url, params=params, headers=EM_HEADERS)
    resp.raise_for_status()
    return resp.json().get("data") or {}


def _to_yuan(num_str: str, unit: str) -> float:
    val = float(num_str.replace(",", ""))
    if unit == "万":
        return val * 10_000
    if unit == "亿":
        return val * 100_000_000
    return val


def extract_limit_fields(title: str, content: str) -> dict[str, Any]:
    """Heuristic regex extractor for effective date, action, and daily purchase cap."""
    text = re.sub(r"\s+", " ", content)

    # 1. Action classification
    if "恢复大额申购" in title or "恢复申购" in title or "取消" in title and "限制" in title:
        action = "RESUME_OR_UNLIMIT"
    elif "暂停申购" in title and "大额" not in title:
        action = "SUSPEND_PURCHASE"
    elif any(k in title for k in ("调整大额", "限制金额", "金额上限", "暂停大额")):
        action = "ADJUST_OR_CAP_LIMIT"
    else:
        action = "OTHER"

    # 2. Effective date (look for patterns near 起始日 / 自...起)
    eff_date = None
    date_patterns = [
        r"(?:暂停|限制|调整|恢复)[^\u3002]{0,20}起始日[^\d]{0,10}(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)",
        r"自\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*(?:起|开始)",
        r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*(?:起|开始)",
    ]
    for pat in date_patterns:
        m = re.search(pat, text)
        if m:
            eff_date = re.sub(r"\s+", "", m.group(1))
            break

    # 3. Daily limit amount (CNY & USD separately)
    cny_amounts: list[float] = []
    usd_amounts: list[float] = []

    # Match structured table or prose mentions of daily limit
    cny_patterns = [
        r"限制[^\u3002\n]{0,35}?金额(?:（[^）]*）|\([^)]*\))?[^\d]{0,15}([\d,]+(?:\.\d+)?)\s*(万|亿)?\s*元",
        r"调整为\s*(?:人民币\s*)?([\d,]+(?:\.\d+)?)\s*(万|亿)?\s*元",
        r"(?:不超过|上限为|限制在|限额为|设定为|均为)\s*(?:人民币\s*)?([\d,]+(?:\.\d+)?)\s*(万|亿)?\s*元",
        r"人民币[^\u3002\n]{0,25}?([\d,]+(?:\.\d+)?)\s*(万|亿)?\s*元",
        r"单日[^\u3002\n]{0,35}?([\d,]+(?:\.\d+)?)\s*(万|亿)?\s*元",
    ]
    for pat in cny_patterns:
        for m in re.finditer(pat, text):
            amt = _to_yuan(m.group(1), m.group(2) or "")
            if amt > 0 and amt not in cny_amounts:
                cny_amounts.append(amt)

    usd_patterns = [
        r"美元[^\u3002\n]{0,25}?([\d,]+(?:\.\d+)?)\s*(万)?\s*美元",
        r"([\d,]+(?:\.\d+)?)\s*(万)?\s*美元",
    ]
    for pat in usd_patterns:
        for m in re.finditer(pat, text):
            amt = _to_yuan(m.group(1), m.group(2) or "")
            if amt > 0 and amt not in usd_amounts:
                usd_amounts.append(amt)

    # Extract a short relevant snippet around 限制/调整/暂停
    snippet_match = re.search(
        r"([^。]{0,60}(?:限制申购金额|调整为|单日单个基金账户|暂停申购起始日)[^。]{0,100}。?)",
        text,
    )
    snippet = snippet_match.group(1).strip() if snippet_match else text[:180]

    return {
        "action": action,
        "effective_date": eff_date,
        "cny_limits_found": cny_amounts[:5],
        "usd_limits_found": usd_amounts[:5],
        "snippet": snippet,
    }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    t0 = time.perf_counter()

    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        for code in SAMPLE_CODES:
            ann = fetch_latest_limit_ann(client, code)
            if not ann:
                print(f"[{code}] No limit announcement found.")
                continue

            art_code = ann["ID"]
            title = ann["TITLE"]
            pub_date = (ann.get("PUBLISHDATE") or "")[:10]

            detail = fetch_announcement_content(client, art_code)
            body = detail.get("notice_content") or ""
            pdf_url = f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf"

            extracted = extract_limit_fields(title, body)
            row = {
                "code": code,
                "art_code": art_code,
                "publish_date": pub_date,
                "title": title,
                "body_len": len(body),
                "pdf_url": pdf_url,
                **extracted,
            }
            results.append(row)

            print("=" * 78)
            print(f"[{code}] ({pub_date}) {title}")
            print(f"  art_code       : {art_code} (正文长度: {len(body)} chars)")
            print(f"  action         : {extracted['action']}")
            print(f"  effective_date : {extracted['effective_date']}")
            print(f"  cny_limits     : {extracted['cny_limits_found']}")
            print(f"  usd_limits     : {extracted['usd_limits_found']}")
            print(f"  snippet        : {extracted['snippet'][:140]}")
            print(f"  pdf_url        : {pdf_url}")

    elapsed = time.perf_counter() - t0
    out_path = DATA_DIR / "announcement_parse_samples.json"
    out_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n" + "=" * 78)
    print(f"Parsed {len(results)} announcements in {elapsed:.2f}s -> {out_path}")


if __name__ == "__main__":
    main()
