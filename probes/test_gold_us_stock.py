"""
Live probe script to benchmark zero-API-key, stable public data sources for:
  A. Gold prices (Spot XAU/USD, Spot CNY/g, COMEX GC=F, Shanghai AU9999, 24/7 PAXGUSDT)
  B. US Stocks   (NVDA, AAPL, MSFT, TSLA, QQQ — single & batch, regular + extended hours)

Usage:
    uv run probes/test_gold_us_stock.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TROY_OZ_TO_GRAM = 31.1034768

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


def run_probe(name: str, fn) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        data = fn()
        elapsed = time.perf_counter() - t0
        print(f"  [OK]   {name:<36} ({elapsed:.2f}s)")
        return {"source": name, "ok": True, "elapsed_s": round(elapsed, 3), "data": data}
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        print(f"  [FAIL] {name:<36} ({elapsed:.2f}s) -> {exc!r}")
        return {"source": name, "ok": False, "elapsed_s": round(elapsed, 3), "error": repr(exc)}


# ---------------------------------------------------------------------------
# GOLD SOURCES
# ---------------------------------------------------------------------------


def probe_goldprice_org(client: httpx.Client) -> dict[str, Any]:
    """Real-time Spot Gold & Silver in USD and CNY (zero API key)."""
    resp = client.get(
        "https://data-asg.goldprice.org/dbXRates/USD,CNY",
        headers={**BROWSER_HEADERS, "Referer": "https://goldprice.org/"},
    )
    resp.raise_for_status()
    payload = resp.json()
    out: dict[str, Any] = {"ts": payload.get("ts"), "date": payload.get("date")}
    for item in payload.get("items", []):
        curr = item["curr"]
        xau = item["xauPrice"]
        out[curr] = {
            "xau_per_oz": xau,
            "xau_per_g": round(xau / TROY_OZ_TO_GRAM, 2),
            "chg_pct": item.get("pcXau"),
            "xag_per_oz": item.get("xagPrice"),
        }
    return out


def probe_yahoo_gold(client: httpx.Client) -> dict[str, Any]:
    """Yahoo Finance v8 Chart for COMEX Gold Futures (GC=F) + intraday points."""
    resp = client.get(
        "https://query1.finance.yahoo.com/v8/finance/chart/GC=F",
        params={"interval": "15m", "range": "1d"},
        headers=BROWSER_HEADERS,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    meta = result["meta"]
    closes = [
        c for c in (result.get("indicators", {}).get("quote", [{}])[0].get("close") or []) if c is not None
    ]
    return {
        "symbol": meta.get("symbol"),
        "price": meta.get("regularMarketPrice"),
        "prev_close": meta.get("chartPreviousClose"),
        "day_high": meta.get("regularMarketDayHigh"),
        "day_low": meta.get("regularMarketDayLow"),
        "currency": meta.get("currency"),
        "intraday_points": len(closes),
    }


def probe_binance_paxg(client: httpx.Client) -> dict[str, Any]:
    """24/7 gold-backed token PAXGUSDT (never closes on weekends/holidays)."""
    resp = client.get(
        "https://api.binance.com/api/v3/ticker/24hr",
        params={"symbol": "PAXGUSDT"},
        headers=BROWSER_HEADERS,
    )
    resp.raise_for_status()
    d = resp.json()
    return {
        "symbol": d["symbol"],
        "price": float(d["lastPrice"]),
        "change_pct_24h": float(d["priceChangePercent"]),
        "high_24h": float(d["highPrice"]),
        "low_24h": float(d["lowPrice"]),
    }


def probe_eastmoney_gold_batch(client: httpx.Client) -> dict[str, Any]:
    """EastMoney batch quote: Shanghai AU9999 + COMEX Gold + London Spot XAU in 1 request."""
    # 118.AU9999 = 上海黄金交易所现货Au99.99, 101.GC00Y = COMEX黄金主连, 122.XAU = 伦敦金现
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": "f12,f14,f2,f3,f4,f15,f16,f18",
        "secids": "118.AU9999,101.GC00Y,122.XAU",
    }
    resp = client.get(url, params=params, headers=BROWSER_HEADERS)
    resp.raise_for_status()
    diff = resp.json().get("data", {}).get("diff") or []
    return {
        row["f12"]: {
            "name": row.get("f14"),
            "price": row.get("f2"),
            "change_pct": row.get("f3"),
            "high": row.get("f15"),
            "low": row.get("f16"),
            "prev_close": row.get("f18"),
        }
        for row in diff
    }


# ---------------------------------------------------------------------------
# US STOCK SOURCES
# ---------------------------------------------------------------------------

TEST_SYMBOLS = ["NVDA", "AAPL", "MSFT", "TSLA", "QQQ"]


def probe_yahoo_us_stock(client: httpx.Client) -> dict[str, Any]:
    """Yahoo Finance v8 chart for NVDA (regular + pre/post + intraday series)."""
    resp = client.get(
        "https://query1.finance.yahoo.com/v8/finance/chart/NVDA",
        params={"interval": "5m", "range": "1d", "includePrePost": "true"},
        headers=BROWSER_HEADERS,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    meta = result["meta"]
    ts_list = result.get("timestamp") or []
    return {
        "symbol": meta.get("symbol"),
        "price": meta.get("regularMarketPrice"),
        "prev_close": meta.get("chartPreviousClose"),
        "day_high": meta.get("regularMarketDayHigh"),
        "day_low": meta.get("regularMarketDayLow"),
        "exchange": meta.get("exchangeName"),
        "points_count": len(ts_list),
    }


def probe_nasdaq_official(client: httpx.Client) -> dict[str, Any]:
    """Nasdaq official public quote API (includes extended hours, zero API key)."""
    resp = client.get(
        "https://api.nasdaq.com/api/quote/NVDA/info",
        params={"assetclass": "stocks"},
        headers={
            **BROWSER_HEADERS,
            "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/",
        },
    )
    resp.raise_for_status()
    d = resp.json().get("data") or {}
    primary = d.get("primaryData") or {}
    secondary = d.get("secondaryData") or {}
    return {
        "symbol": d.get("symbol"),
        "market_status": d.get("marketStatus"),
        "primary_price": primary.get("lastSalePrice"),
        "primary_pct": primary.get("percentageChange"),
        "extended_price": secondary.get("lastSalePrice"),
        "extended_pct": secondary.get("percentageChange"),
    }


def probe_tencent_us_batch(client: httpx.Client) -> dict[str, Any]:
    """Tencent Finance batch US stocks (1 HTTP call for N tickers, ultra fast)."""
    query = ",".join(f"us{s}" for s in TEST_SYMBOLS)
    resp = client.get(f"https://qt.gtimg.cn/q={query}", headers=BROWSER_HEADERS)
    resp.raise_for_status()
    out: dict[str, Any] = {}
    for line in resp.text.strip().splitlines():
        if "=" not in line or "~" not in line:
            continue
        payload = line.split("=", 1)[1].strip().strip('";')
        parts = payload.split("~")
        if len(parts) > 35:
            sym = parts[2].split(".")[0]
            out[sym] = {
                "name": parts[1],
                "price": float(parts[3]) if parts[3] else None,
                "prev_close": float(parts[4]) if parts[4] else None,
                "change_pct": float(parts[32]) if parts[32] else None,
                "high": float(parts[33]) if parts[33] else None,
                "low": float(parts[34]) if parts[34] else None,
                "quote_time": parts[30],
            }
    return out


def probe_eastmoney_us_batch(client: httpx.Client) -> dict[str, Any]:
    """EastMoney batch quote for US stocks (105=NASDAQ, 106=NYSE, 107=AMEX/ETF)."""
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": "f12,f14,f2,f3,f4,f15,f16,f18",
        "secids": "105.NVDA,105.AAPL,105.MSFT,105.TSLA,105.QQQ,107.SPY",
    }
    resp = client.get(url, params=params, headers=BROWSER_HEADERS)
    resp.raise_for_status()
    diff = resp.json().get("data", {}).get("diff") or []
    return {
        row["f12"]: {
            "name": row.get("f14"),
            "price": row.get("f2"),
            "change_pct": row.get("f3"),
            "prev_close": row.get("f18"),
        }
        for row in diff
    }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, list[dict[str, Any]]] = {"gold": [], "us_stock": []}

    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        print("=" * 74)
        print("[A] Probing Gold Price Sources (Zero API Key)")
        report["gold"].append(run_probe("goldprice.org (Spot USD & CNY/g)", lambda: probe_goldprice_org(client)))
        report["gold"].append(run_probe("Yahoo Finance (COMEX GC=F)", lambda: probe_yahoo_gold(client)))
        report["gold"].append(run_probe("Binance (PAXGUSDT 24/7)", lambda: probe_binance_paxg(client)))
        report["gold"].append(run_probe("EastMoney Batch (AU9999+GC+XAU)", lambda: probe_eastmoney_gold_batch(client)))

        print("\n" + "=" * 74)
        print("[B] Probing US Stock Sources (Zero API Key)")
        report["us_stock"].append(run_probe("Yahoo Finance v8 Chart (NVDA)", lambda: probe_yahoo_us_stock(client)))
        report["us_stock"].append(run_probe("Nasdaq Official API (NVDA)", lambda: probe_nasdaq_official(client)))
        report["us_stock"].append(run_probe("Tencent Batch (5 US Tickers)", lambda: probe_tencent_us_batch(client)))
        report["us_stock"].append(run_probe("EastMoney Batch (6 US Tickers)", lambda: probe_eastmoney_us_batch(client)))

    print("\n" + "=" * 74)
    print("Sample Parsed Values:")
    for section in ("gold", "us_stock"):
        print(f"\n--- {section.upper()} ---")
        for item in report[section]:
            if item["ok"]:
                print(f"  {item['source']}:")
                print(f"    {json.dumps(item['data'], ensure_ascii=False)}")

    out_path = DATA_DIR / "gold_us_stock_probe.json"
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nSaved probe report -> {out_path}")


if __name__ == "__main__":
    main()
