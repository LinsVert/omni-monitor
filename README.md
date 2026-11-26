# omni-monitor

**English** | [简体中文](README.zh-CN.md)

Pluggable multi-scenario monitoring backend with **SQLite (WAL)**, **FastAPI**, **universal scalar + collection diff rule engine**, and **Google FCM push** for your native Kotlin Android APK.

## Built-in Collector Plugins

| `plugin_type` | Data Shape | Primary / Fallback Sources | Key Capabilities |
| :--- | :--- | :--- | :--- |
| `qdii_limits` | `collection` | EastMoney `fundcode_search.js` + `Fund_JJJZ_Data.aspx?t=8` + Mobile `MAXSG` | Enumerates 740+ QDII/overseas funds in 1 batch call; distinguishes holiday pauses (`暂停申购+暂停赎回`) from real quota halts (`暂停申购+开放赎回`) and enriches standing limits on holidays |
| `qdii_announcements` | `collection` | EastMoney F10 `JJGG?type=5` + `np-cnotice-stock` full text | T-1 early warning for OTC QDII limit-change notices with effective date & official PDF link |
| `lof_arbitrage` | `scalar` + `items` | Companion project [fund_lof](https://github.com/LinsVert/fund_lof) (`OMNI_LOF_API_URL`) + EastMoney live limits | Scans SZSE `16xxxx` LOFs during closing window (`14:30-14:55` CST), filters stale NAVs, and estimates 6-account tractor arbitrage profit |
| `gold` | `scalar` | EastMoney Batch (`AU9999`/`GC00Y`/`XAU`) + `goldprice.org` + Binance `PAXGUSDT` | Domestic CNY/g, spot USD/oz, COMEX futures, and 24/7 gold token |
| `us_stock` | `scalar` | Tencent Finance Batch (`qt.gtimg.cn`) + Nasdaq Official API | Collapses N ticker tasks into 1 HTTP request; optional US Eastern trading-hours filter |
| `http_json` | `scalar`/`state` | Any public JSON URL | Zero-code monitoring via dot-path (`data.price`, `items.0.xauPrice`) or content hash |

## Quick Start

```bash
# 1. Install dependencies
uv sync --extra dev

# 2. Run unit & API integration tests
uv run pytest

# 3. Run one-shot live sweep across all seeded tasks (QDII, Gold, US Stocks)
uv run omni-monitor run-once --force

# 4. Start the FastAPI server + background scheduler (http://127.0.0.1:8080/docs)
uv run omni-monitor serve
```

## Docker Compose Deployment

```bash
# 1. Clone repo & prepare config
git clone https://github.com/LinsVert/omni-monitor.git
cd omni-monitor
cp .env.example .env
# Edit .env: set a strong OMNI_API_KEY and OMNI_FCM_CREDENTIALS_PATH=secrets/firebase-sa.json
# Optional: deploy https://github.com/LinsVert/fund_lof and set OMNI_LOF_API_URL for lof_arbitrage
# Place your Firebase Service Account JSON at secrets/firebase-sa.json

# 2. Build & start (single worker, SQLite persisted in ./data)
docker compose up -d --build

# 3. Future updates
git pull && docker compose up -d --build
```

## Google FCM Setup

1. Create a Firebase project & Android app (`google-services.json` goes into your APK).
2. Download a **Firebase Service Account JSON** key to the server (e.g. `secrets/firebase-sa.json`).
3. Set `OMNI_FCM_CREDENTIALS_PATH=secrets/firebase-sa.json` in `.env`.
4. Have your APK `POST /api/devices/fcm` with its FCM Registration Token on launch and in `onNewToken`.
5. Call `POST /api/devices/test-push` to verify end-to-end delivery.

## Core APK API Endpoints

* `GET /health` — Service status, FCM mode (`live` / `dry_run`), counts
* `GET /api/plugins` — Registered plugins + Pydantic JSON Schema (for dynamic UI forms)
* `GET / POST / PATCH / DELETE /api/tasks` — Manage monitoring tasks
* `POST /api/tasks/{task_id}/run` — Trigger immediate collection
* `GET /api/tasks/{task_id}/latest?q=纳斯达克&category=LIMITED&sort_by=daily_limit` — Latest observation / **Live QDII Limit Board**
* `GET /api/tasks/{task_id}/history` — Time-series points for charts
* `GET /api/alerts` — Push notification history feed
* `GET /api/alerts/{alert_id}` — Full detail view with server-driven `detail_blocks` (`kv_grid`, `line_chart`, `diff_list`, `markdown`, `actions`)
* `POST /api/alerts/{alert_id}/read` — Mark alert read
* `POST /api/devices/fcm` — Register / refresh Android FCM token
