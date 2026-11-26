# Omni-Monitor（全场景插件化监控系统）

[English](README.md) | **简体中文**

一套轻量、可扩展的个人多场景资产与事件监控系统：**Python (FastAPI + SQLite WAL)** 插件化后端 + **原生 Kotlin (Jetpack Compose + Material 3)** Android 客户端 + **Google FCM** 系统级推送（无需 App 后台保活）。

---

## 核心特性

* **三态统一观测模型**：同一套引擎兼容 **标量数值**（黄金/美股）、**单对象状态**（接口/网页变更）、**集合快照 Diff**（全市场 QDII 限购、公告流、LOF 溢价列表）。
* **零代码/低代码扩展**：新增监控源只需在 `src/omni_monitor/plugins/` 下新建一个文件并加 `@register_collector`，自动接入调度、防抖状态机、SQLite 持久化、FCM 推送与 App 详情页渲染。
* **服务端驱动详情页（Server-Driven UI）**：后端统一下发 `kv_grid`、`line_chart`（原生 Compose `Canvas` 折线图 + 目标虚线）、`diff_list`（变动清单 + ⭐自选置顶 + 公告 PDF 直链）、`markdown`、`actions` 块，新增插件无需重发 APK。
* **防骚扰与自愈机制**：支持冷却（`cooldown`）、迟滞回差重置（`hysteresis`）、一次性触发（`once`）、集合快照防抽风保护（`min_retention_ratio`）以及数据源连续失败元告警。
* **真机 Google FCM 推送**：Android High Priority 推送，App 退后台或进程被杀仍可由 GMS 拉起通知栏，点击通知直达对应 `alert_id` 详情页。

---

## 内置监控插件

| `plugin_type` | 数据形态 | 主/备数据源（均免 API Key） | 核心能力 |
| :--- | :--- | :--- | :--- |
| `qdii_limits` | `collection` | 东财 `fundcode_search.js` + `Fund_JJJZ_Data.aspx?t=8` + 移动端 `MAXSG` | 枚举全市场 700+ QDII/海外基金，单次批量抓申购状态与单日限额；自动区分**海外节假日休市**与**真实额度停购**，休市日仍补全常驻限额 |
| `qdii_announcements` | `collection` | 东财 F10 `JJGG?type=5` + `np-cnotice-stock` 正文直出 | T-1 场外 QDII 限购/暂停/恢复公告提前预警，抽取生效日期、限额与官方 PDF 直链 |
| `lof_arbitrage` | `scalar` + `items` | 内部 LOF 溢价源（`OMNI_LOF_API_URL`）+ 东财实时限额 | 工作日尾盘 `14:30~14:55` 筛选深市 `16xxxx` 可拖拉机套利 LOF，过滤旧净值假溢价并估算一拖六收益 |
| `gold` | `scalar` | 东财批量（`AU9999` / `GC00Y` / `XAU`）+ `goldprice.org` + Binance `PAXGUSDT` | 覆盖上海金（元/克）、伦敦现货金、COMEX 期货及 7×24 小时 `PAXG` |
| `us_stock` | `scalar` | 腾讯财经单次批量主源（`qt.gtimg.cn`）+ Nasdaq 官方 API 备源 | N 只美股任务合并为 1 次 HTTP 批量拉取；支持美东交易时段（含盘前盘后）过滤 |
| `http_json` | `scalar` / `state` | 任意公开 JSON 接口 | 零代码监控任意 JSON API（按 `data.price` 等路径提取数值或监控内容指纹变化） |

---

## 快速启动（本地开发）

```bash
# 1. 安装依赖（推荐使用 uv）
uv sync --extra dev

# 2. 运行单元与接口集成测试
uv run pytest -v

# 3. 手动执行一轮全量实时采集测试
uv run omni-monitor run-once --force

# 4. 启动 FastAPI 服务 + 后台调度器（Swagger 文档：http://127.0.0.1:8080/docs）
uv run omni-monitor serve
```

---

## Docker Compose 生产部署（支持 x86_64 / ARM64）

```bash
# 1. 克隆仓库并准备环境配置
git clone https://github.com/LinsVert/omni-monitor.git
cd omni-monitor
cp .env.example .env

# 2. 编辑 .env：
#    - 设置强随机 OMNI_API_KEY（公网部署强烈建议开启）
#    - 设置 OMNI_FCM_CREDENTIALS_PATH=secrets/firebase-sa.json
#    - 如需联动 LOF 溢价服务，设置 OMNI_LOF_API_URL=http://lof-monitor:8000/api/funds
#    将 Firebase Console 导出的 Service Account 私钥放至 secrets/firebase-sa.json

# 3. 构建并启动（SQLite 数据持久化在 ./data，私钥只读挂载在 ./secrets）
docker compose up -d --build

# 4. 后续一行无痛热更新（保留数据与密钥）
git pull --ff-only && docker compose up -d --build
```

> **安全提示**：`docker-compose.yml` 默认仅绑定 `127.0.0.1:8088:8080`，建议配合 Nginx / Caddy 反代启用 HTTPS；`.env`、`secrets/`、`docker-compose.override.yml`、`android/app/google-services.json` 均已加入 `.gitignore`，切勿提交至公开仓库。

---

## Android 客户端（Kotlin + Jetpack Compose）

客户端源码位于 `android/`，内置 4 大模块：
1. **推送记录 & 通用详情页**：未读筛选、点击通知栏直达详情、原生 `Canvas` 折线图、QDII/LOF 变动卡片与 PDF/行情跳转。
2. **QDII 限购实时看板**：支持关键词搜索、快捷主题标签（纳斯达克/标普500/日经/黄金等）、分类计数筛选，直观展示具体限购金额（含休市常驻限额）。
3. **可视化任务管理**：直接在手机端新建、编辑、启停、删除监控任务或点击「立即抓取」。
4. **服务端与 FCM 设置**：配置 HTTPS 域名与 `X-API-Key`、查看/同步 FCM Token、发送连通性测试推送。

### 打包步骤

1. 在 Firebase Console 创建 Android 应用（包名：`com.omnimonitor.app`），下载 `google-services.json` 放到 `android/app/google-services.json`。
2. 编译 Debug APK（检测到 `google-services.json` 后会自动启用 `com.google.gms.google-services` 插件）：
   ```bash
   cd android
   ./gradlew assembleDebug
   ```
3. 产物路径：`android/app/build/outputs/apk/debug/app-debug.apk`。

---

## 核心 API 列表

> 若服务端设置了 `OMNI_API_KEY`，除 `/health` 外所有接口均需在请求头携带 `X-API-Key`。

* `GET /health` — 服务状态、FCM 模式（`live` / `dry_run`）、已注册插件与设备数
* `GET /api/plugins` — 已注册插件列表与参数 JSON Schema
* `GET / POST / PATCH / DELETE /api/tasks` — 监控任务增删改查
* `POST /api/tasks/{task_id}/run` — 手动立即触发一次采集
* `GET /api/tasks/{task_id}/latest` — 最新观测快照 / QDII 实时看板（支持 `q`、`category`、`currency`、`sort_by` 参数）
* `GET /api/tasks/{task_id}/history` — 标量历史走势点位
* `GET /api/alerts` & `GET /api/alerts/{alert_id}` & `POST /api/alerts/{alert_id}/read` — 推送记录流、详情块与已读标记
* `POST /api/devices/fcm` & `POST /api/devices/test-push` & `POST /api/devices/simulate-alert` — FCM Token 注册与联调测试推送
