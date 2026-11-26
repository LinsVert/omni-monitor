"""US Stock collector: Tencent Finance batch (primary, 1 req for N tickers) + EastMoney fallback."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field, field_validator

from omni_monitor.models import Observation
from omni_monitor.registry import BaseCollector, register_collector

US_EASTERN = ZoneInfo("America/New_York")

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}


class USStockConfig(BaseModel):
    symbol: str = Field(..., description="美股代码，如 NVDA、AAPL、QQQ、SPY")
    market_hours_only: bool = Field(
        default=False,
        description="开启后仅在美东交易日时段内采集，休市期间跳过",
    )
    include_extended_hours: bool = Field(
        default=True,
        description="配合 market_hours_only 使用，覆盖盘前盘后 (04:00-20:00 ET)",
    )

    @field_validator("symbol")
    @classmethod
    def _upper_symbol(cls, v: str) -> str:
        return v.strip().upper()


@register_collector
class USStockCollector(BaseCollector):
    plugin_type = "us_stock"
    display_name = "美股行情监控"
    description = "批量拉取美股/ETF实时行情（腾讯财经单次批量主源 + 东财/Yahoo 备源），支持美东交易时段过滤"
    data_shape = "scalar"
    config_schema = USStockConfig

    def should_run(self, now: datetime, config: BaseModel) -> bool:
        assert isinstance(config, USStockConfig)
        if not config.market_hours_only:
            return True
        et = now.astimezone(US_EASTERN)
        if et.weekday() >= 5:  # Saturday / Sunday
            return False
        t = et.time()
        if config.include_extended_hours:
            return time(4, 0) <= t <= time(20, 5)
        return time(9, 25) <= t <= time(16, 10)

    async def _fetch_tencent_batch(
        self, symbols: list[str], client: httpx.AsyncClient
    ) -> dict[str, dict[str, Any]]:
        query = ",".join(f"us{s}" for s in symbols)
        resp = await client.get(
            f"https://qt.gtimg.cn/q={query}", headers=BROWSER_HEADERS
        )
        resp.raise_for_status()
        out: dict[str, dict[str, Any]] = {}
        for line in resp.text.strip().splitlines():
            if "=" not in line or "~" not in line:
                continue
            payload = line.split("=", 1)[1].strip().strip('";')
            parts = payload.split("~")
            if len(parts) <= 35 or not parts[3]:
                continue
            sym = parts[2].split(".")[0].upper()
            out[sym] = {
                "symbol": sym,
                "name": parts[1],
                "price": float(parts[3]),
                "prev_close": float(parts[4]) if parts[4] else None,
                "open": float(parts[5]) if parts[5] else None,
                "change": float(parts[31]) if parts[31] else None,
                "change_pct": float(parts[32]) if parts[32] else None,
                "high": float(parts[33]) if parts[33] else None,
                "low": float(parts[34]) if parts[34] else None,
                "quote_time": parts[30],
                "source": "Tencent Finance",
            }
        return out

    async def _fetch_nasdaq_single(
        self, symbol: str, client: httpx.AsyncClient
    ) -> dict[str, Any] | None:
        resp = await client.get(
            f"https://api.nasdaq.com/api/quote/{symbol}/info",
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
        raw_price = (primary.get("lastSalePrice") or "").replace("$", "").replace(",", "")
        if not raw_price:
            return None
        raw_pct = (primary.get("percentageChange") or "").replace("%", "").replace("+", "")
        return {
            "symbol": symbol,
            "name": d.get("companyName") or symbol,
            "price": float(raw_price),
            "change_pct": float(raw_pct) if raw_pct else None,
            "market_status": d.get("marketStatus"),
            "source": "Nasdaq Official API (fallback)",
        }

    def _to_obs(self, task_id: str, quote: dict[str, Any]) -> Observation:
        sym = quote["symbol"]
        price = float(quote["price"])
        chg_pct = quote.get("change_pct")
        display = [
            {"label": "标的", "value": f"{sym} ({quote.get('name', sym)})"},
            {"label": "当前价格", "value": f"${price:.2f}"},
        ]
        if isinstance(chg_pct, (int, float)):
            display.append({"label": "当日涨跌", "value": f"{chg_pct:+.2f}%"})
        if isinstance(quote.get("high"), (int, float)) and isinstance(quote.get("low"), (int, float)):
            display.append({"label": "最高 / 最低", "value": f"${quote['high']:.2f} / ${quote['low']:.2f}"})
        if quote.get("quote_time"):
            display.append({"label": "行情时间", "value": str(quote["quote_time"])})

        return Observation(
            task_id=task_id,
            value=price,
            unit="USD",
            metrics={
                "symbol": sym,
                "price": price,
                "change_pct": chg_pct,
                "prev_close": quote.get("prev_close"),
                "high": quote.get("high"),
                "low": quote.get("low"),
            },
            display_fields=display,
            source_name=str(quote.get("source", "")),
            source_url=f"https://www.tradingview.com/symbols/{sym}/",
        )

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
        symbols = sorted({cfg.symbol for _, cfg in tasks if isinstance(cfg, USStockConfig)})
        quotes: dict[str, dict[str, Any]] = {}
        try:
            quotes = await self._fetch_tencent_batch(symbols, client)
        except Exception:
            quotes = {}

        # Fallback per missing symbol via Nasdaq official API
        for sym in symbols:
            if sym not in quotes:
                try:
                    fb = await self._fetch_nasdaq_single(sym, client)
                    if fb:
                        quotes[sym] = fb
                except Exception:
                    pass

        results: dict[str, Observation | Exception] = {}
        for tid, cfg in tasks:
            assert isinstance(cfg, USStockConfig)
            q = quotes.get(cfg.symbol)
            if q is None:
                results[tid] = RuntimeError(f"Failed to fetch US stock quote for {cfg.symbol}")
            else:
                results[tid] = self._to_obs(tid, q)
        return results
