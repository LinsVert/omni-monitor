"""Gold price collector: goldprice.org + EastMoney batch (5s fast-fail) + Binance PAXG 24/7."""

from __future__ import annotations

import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from omni_monitor.models import Observation
from omni_monitor.registry import BaseCollector, register_collector

TROY_OZ_TO_GRAM = 31.1034768
EM_CIRCUIT_BREAKER_SECONDS = 300.0  # Skip EastMoney push2 for 5 min after timeout/502

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

_EM_SECIDS = {
    "AU9999": ("118.AU9999", "CNY/g", "上海金 Au99.99"),
    "GC00Y": ("101.GC00Y", "USD/oz", "COMEX 黄金期货"),
    "XAU": ("122.XAU", "USD/oz", "伦敦现货黄金 XAU/USD"),
}

_em_blocked_until: float = 0.0


class GoldConfig(BaseModel):
    instrument: Literal["XAU", "GC00Y", "AU9999", "PAXG"] = Field(
        default="XAU",
        description="XAU=伦敦现货金(USD/oz), GC00Y=COMEX期货(USD/oz), AU9999=上海金(CNY/g), PAXG=24/7黄金代币(USDT)",
    )


@register_collector
class GoldCollector(BaseCollector):
    plugin_type = "gold"
    display_name = "黄金价格监控"
    description = "支持伦敦现货金(XAU)、COMEX期货(GC00Y)、上海金(AU9999)、24/7 PAXG，内置快速熔断与主备数据源切换"
    data_shape = "scalar"
    config_schema = GoldConfig

    async def _fetch_eastmoney(
        self, instruments: set[str], client: httpx.AsyncClient
    ) -> dict[str, dict[str, Any]]:
        global _em_blocked_until
        if time.monotonic() < _em_blocked_until:
            return {}
        secids = ",".join(_EM_SECIDS[i][0] for i in instruments if i in _EM_SECIDS)
        if not secids:
            return {}
        try:
            resp = await client.get(
                "https://push2.eastmoney.com/api/qt/ulist.np/get",
                params={
                    "fltt": "2",
                    "invt": "2",
                    "fields": "f12,f14,f2,f3,f4,f15,f16,f18",
                    "secids": secids,
                },
                headers=BROWSER_HEADERS,
                timeout=5.0,  # Fast-fail so overseas stalls never burn the batch timeout budget
            )
            resp.raise_for_status()
            diff = resp.json().get("data", {}).get("diff") or []
            out: dict[str, dict[str, Any]] = {}
            for row in diff:
                code = row.get("f12")
                price = row.get("f2")
                if code and isinstance(price, (int, float)):
                    out[code] = {
                        "price": float(price),
                        "change_pct": row.get("f3"),
                        "change": row.get("f4"),
                        "high": row.get("f15"),
                        "low": row.get("f16"),
                        "prev_close": row.get("f18"),
                        "source": "EastMoney",
                    }
            if not out:
                _em_blocked_until = time.monotonic() + EM_CIRCUIT_BREAKER_SECONDS
            return out
        except Exception:
            _em_blocked_until = time.monotonic() + EM_CIRCUIT_BREAKER_SECONDS
            return {}

    async def _fetch_goldprice_org(
        self, client: httpx.AsyncClient
    ) -> dict[str, dict[str, Any]]:
        resp = await client.get(
            "https://data-asg.goldprice.org/dbXRates/USD,CNY",
            headers={**BROWSER_HEADERS, "Referer": "https://goldprice.org/"},
            timeout=8.0,
        )
        resp.raise_for_status()
        out: dict[str, dict[str, Any]] = {}
        for item in resp.json().get("items", []):
            curr = item.get("curr")
            xau = item.get("xauPrice")
            if curr == "USD" and isinstance(xau, (int, float)):
                out["XAU"] = {
                    "price": round(float(xau), 2),
                    "change_pct": item.get("pcXau"),
                    "source": "goldprice.org",
                }
            elif curr == "CNY" and isinstance(xau, (int, float)):
                out["AU9999"] = {
                    "price": round(float(xau) / TROY_OZ_TO_GRAM, 2),
                    "change_pct": item.get("pcXau"),
                    "source": "goldprice.org (折算CNY/g)",
                }
        return out

    async def _fetch_paxg(self, client: httpx.AsyncClient) -> dict[str, Any]:
        resp = await client.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbol": "PAXGUSDT"},
            headers=BROWSER_HEADERS,
            timeout=6.0,
        )
        resp.raise_for_status()
        d = resp.json()
        return {
            "price": float(d["lastPrice"]),
            "change_pct": float(d["priceChangePercent"]),
            "high": float(d["highPrice"]),
            "low": float(d["lowPrice"]),
            "source": "Binance PAXGUSDT",
        }

    def _to_obs(self, task_id: str, inst: str, quote: dict[str, Any]) -> Observation:
        unit = _EM_SECIDS[inst][1] if inst in _EM_SECIDS else "USDT/oz"
        label = _EM_SECIDS[inst][2] if inst in _EM_SECIDS else "PAXG/USDT (24/7)"
        price = float(quote["price"])
        chg = quote.get("change_pct")
        display = [
            {"label": "标的", "value": label},
            {"label": "当前价格", "value": f"{price:.2f} {unit}"},
        ]
        if isinstance(chg, (int, float)):
            display.append({"label": "涨跌幅", "value": f"{chg:+.2f}%"})
        if isinstance(quote.get("high"), (int, float)) and isinstance(quote.get("low"), (int, float)):
            display.append({"label": "日内高/低", "value": f"{quote['high']} / {quote['low']}"})

        return Observation(
            task_id=task_id,
            value=price,
            unit=unit,
            metrics={
                "instrument": inst,
                "price": price,
                "change_pct": chg,
                "high": quote.get("high"),
                "low": quote.get("low"),
                "prev_close": quote.get("prev_close"),
            },
            display_fields=display,
            source_name=str(quote.get("source", "")),
            source_url="https://www.tradingview.com/symbols/XAUUSD/" if inst != "AU9999" else "https://quote.eastmoney.com/globalfuture/AU9999.html",
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
        needed = {cfg.instrument for _, cfg in tasks if isinstance(cfg, GoldConfig)}
        quotes: dict[str, dict[str, Any]] = {}

        # 1. Try EastMoney first ONLY for domestic AU9999 / COMEX GC00Y (with 5s fast-fail + 5m circuit breaker)
        em_targets = needed & {"AU9999", "GC00Y"}
        if em_targets:
            quotes.update(await self._fetch_eastmoney(em_targets, client))

        # 2. Use goldprice.org as primary for XAU and instant fallback for AU9999
        if ("XAU" in needed) or ("AU9999" in needed and "AU9999" not in quotes):
            try:
                gp = await self._fetch_goldprice_org(client)
                for k in ("XAU", "AU9999"):
                    if k in needed and k not in quotes and k in gp:
                        quotes[k] = gp[k]
            except Exception:
                pass

        # 3. Secondary EastMoney attempt for XAU if goldprice.org failed
        if "XAU" in needed and "XAU" not in quotes:
            quotes.update(await self._fetch_eastmoney({"XAU"}, client))

        # 4. Binance PAXGUSDT for PAXG or last-resort 24/7 XAU fallback
        if "PAXG" in needed or ("XAU" in needed and "XAU" not in quotes):
            try:
                paxg = await self._fetch_paxg(client)
                quotes["PAXG"] = paxg
                if "XAU" not in quotes:
                    quotes["XAU"] = {**paxg, "source": "Binance PAXGUSDT (fallback)"}
            except Exception:
                pass

        results: dict[str, Observation | Exception] = {}
        for tid, cfg in tasks:
            assert isinstance(cfg, GoldConfig)
            q = quotes.get(cfg.instrument)
            if q is None:
                results[tid] = RuntimeError(f"All gold data sources failed for {cfg.instrument}")
            else:
                results[tid] = self._to_obs(tid, cfg.instrument, q)
        return results
