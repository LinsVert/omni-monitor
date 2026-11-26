package com.omnimonitor.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

@Serializable
data class HealthResponse(
    val status: String = "",
    val version: String = "",
    @SerialName("fcm_mode") val fcmMode: String = "dry_run",
    @SerialName("registered_plugins") val registeredPlugins: Int = 0,
    @SerialName("registered_devices") val registeredDevices: Int = 0,
    @SerialName("tasks_count") val tasksCount: Int = 0,
)

@Serializable
data class AlertSummary(
    @SerialName("alert_id") val alertId: String,
    @SerialName("task_id") val taskId: String,
    @SerialName("task_name") val taskName: String,
    @SerialName("plugin_type") val pluginType: String,
    val severity: String = "normal",
    val title: String,
    val summary: String,
    @SerialName("triggered_at") val triggeredAt: String,
    @SerialName("is_read") val isRead: Boolean = false,
    @SerialName("push_status") val pushStatus: String = "pending",
)

@Serializable
data class AlertDetail(
    @SerialName("alert_id") val alertId: String,
    @SerialName("task_id") val taskId: String,
    @SerialName("task_name") val taskName: String,
    @SerialName("plugin_type") val pluginType: String,
    val severity: String = "normal",
    val title: String,
    val summary: String,
    @SerialName("detail_blocks") val detailBlocks: List<JsonObject> = emptyList(),
    @SerialName("triggered_at") val triggeredAt: String,
    @SerialName("is_read") val isRead: Boolean = false,
    @SerialName("push_status") val pushStatus: String = "pending",
) {
    fun parsedBlocks(): List<DetailBlock> = detailBlocks.mapNotNull { parseDetailBlock(it) }
}

sealed interface DetailBlock {
    data class KvGrid(val items: List<KvItem>) : DetailBlock
    data class LineChart(
        val title: String,
        val unit: String,
        val targetLine: Double?,
        val points: List<Pair<String, Double>>,
    ) : DetailBlock

    data class DiffList(val title: String, val items: List<DiffItem>) : DetailBlock
    data class Markdown(val title: String, val content: String) : DetailBlock
    data class Actions(val links: List<ActionLink>) : DetailBlock
}

data class KvItem(val label: String, val value: String)

data class DiffItem(
    val code: String,
    val name: String,
    val event: String,
    val beforeText: String,
    val afterText: String,
    val isWatchlist: Boolean,
    val url: String?,
)

data class ActionLink(val label: String, val url: String)

private fun parseDetailBlock(obj: JsonObject): DetailBlock? {
    val type = obj["type"]?.jsonPrimitive?.contentOrNull ?: return null
    return when (type) {
        "kv_grid" -> {
            val items = (obj["items"] as? JsonArray)?.mapNotNull { el ->
                val item = el.jsonObject
                val label = item["label"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
                val value = item["value"]?.jsonPrimitive?.contentOrNull ?: "-"
                KvItem(label, value)
            }.orEmpty()
            DetailBlock.KvGrid(items)
        }

        "line_chart" -> {
            val title = obj["title"]?.jsonPrimitive?.contentOrNull ?: "走势图"
            val unit = obj["unit"]?.jsonPrimitive?.contentOrNull ?: ""
            val targetLine = obj["target_line"]?.jsonPrimitive?.doubleOrNull
            val points = (obj["points"] as? JsonArray)?.mapNotNull { pt ->
                val arr = pt as? JsonArray ?: return@mapNotNull null
                if (arr.size < 2) return@mapNotNull null
                val ts = arr[0].jsonPrimitive.contentOrNull ?: ""
                val v = arr[1].jsonPrimitive.doubleOrNull ?: return@mapNotNull null
                ts to v
            }.orEmpty()
            DetailBlock.LineChart(title, unit, targetLine, points)
        }

        "diff_list" -> {
            val title = obj["title"]?.jsonPrimitive?.contentOrNull ?: "变动清单"
            val items = (obj["items"] as? JsonArray)?.mapNotNull { el ->
                val item = el.jsonObject
                DiffItem(
                    code = item["code"]?.jsonPrimitive?.contentOrNull ?: "",
                    name = item["name"]?.jsonPrimitive?.contentOrNull ?: "",
                    event = item["event"]?.jsonPrimitive?.contentOrNull ?: "CHANGED",
                    beforeText = item["before_text"]?.jsonPrimitive?.contentOrNull ?: "",
                    afterText = item["after_text"]?.jsonPrimitive?.contentOrNull ?: "",
                    isWatchlist = item["is_watchlist"]?.jsonPrimitive?.booleanOrNull ?: false,
                    url = item["url"]?.jsonPrimitive?.contentOrNull,
                )
            }.orEmpty()
            DetailBlock.DiffList(title, items)
        }

        "markdown" -> {
            val title = obj["title"]?.jsonPrimitive?.contentOrNull ?: "详情"
            val content = obj["content"]?.jsonPrimitive?.contentOrNull ?: ""
            DetailBlock.Markdown(title, content)
        }

        "actions" -> {
            val links = (obj["links"] as? JsonArray)?.mapNotNull { el ->
                val item = el.jsonObject
                val label = item["label"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
                val url = item["url"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null
                ActionLink(label, url)
            }.orEmpty()
            DetailBlock.Actions(links)
        }

        else -> null
    }
}

@Serializable
data class TaskStateDto(
    val status: String = "IDLE",
    @SerialName("last_triggered_at") val lastTriggeredAt: String? = null,
    @SerialName("last_checked_at") val lastCheckedAt: String? = null,
    @SerialName("last_success_at") val lastSuccessAt: String? = null,
    @SerialName("consecutive_failures") val consecutiveFailures: Int = 0,
    @SerialName("last_error") val lastError: String? = null,
)

@Serializable
data class RuleSummaryDto(
    val type: String = "threshold",
    val field: String = "value",
    val op: String? = null,
    val target: Double? = null,
    @SerialName("min_value") val minValue: Double? = null,
    @SerialName("max_value") val maxValue: Double? = null,
    @SerialName("window_seconds") val windowSeconds: Int? = null,
    @SerialName("change_pct") val changePct: Double? = null,
    val expression: String? = null,
    @SerialName("watch_fields") val watchFields: List<String> = emptyList(),
    @SerialName("ignore_categories") val ignoreCategories: List<String> = listOf("HOLIDAY_SUSPENDED", "NO_EM_LIMIT_DATA", "OTHER"),
    @SerialName("only_events") val onlyEvents: List<String> = emptyList(),
    @SerialName("filter_keywords") val filterKeywords: List<String> = emptyList(),
    @SerialName("filter_currency") val filterCurrency: List<String> = emptyList(),
    @SerialName("watchlist_codes") val watchlistCodes: List<String> = emptyList(),
)

@Serializable
data class AlertPolicyDto(
    val mode: String = "cooldown",
    @SerialName("cooldown_seconds") val cooldownSeconds: Int = 3600,
    @SerialName("rearm_value") val rearmValue: Double? = null,
    val severity: String = "normal",
)

@Serializable
data class TaskDto(
    @SerialName("task_id") val taskId: String,
    val name: String,
    val enabled: Boolean = true,
    @SerialName("plugin_type") val pluginType: String,
    @SerialName("interval_seconds") val intervalSeconds: Int = 300,
    @SerialName("plugin_config") val pluginConfig: JsonObject = JsonObject(emptyMap()),
    val rule: RuleSummaryDto = RuleSummaryDto(),
    @SerialName("alert_policy") val alertPolicy: AlertPolicyDto = AlertPolicyDto(),
    val state: TaskStateDto = TaskStateDto(),
)

@Serializable
data class TaskCreateBody(
    val name: String,
    val enabled: Boolean = true,
    @SerialName("plugin_type") val pluginType: String,
    @SerialName("interval_seconds") val intervalSeconds: Int = 300,
    @SerialName("plugin_config") val pluginConfig: JsonObject,
    val rule: RuleSummaryDto,
    @SerialName("alert_policy") val alertPolicy: AlertPolicyDto,
)

@Serializable
data class TaskUpdateBody(
    val name: String,
    val enabled: Boolean,
    @SerialName("interval_seconds") val intervalSeconds: Int,
    @SerialName("plugin_config") val pluginConfig: JsonObject,
    val rule: RuleSummaryDto,
    @SerialName("alert_policy") val alertPolicy: AlertPolicyDto,
)

@Serializable
data class TaskRunResponse(
    @SerialName("task_id") val taskId: String,
    val ok: Boolean,
    val skipped: Boolean = false,
    @SerialName("alert_id") val alertId: String? = null,
    val error: String? = null,
)

@Serializable
data class QdiiItemDto(
    val code: String = "",
    val name: String = "",
    @SerialName("fund_type") val fundType: String = "",
    val currency: String = "CNY",
    val nav: Double? = null,
    @SerialName("nav_date") val navDate: String = "",
    @SerialName("purchase_status") val purchaseStatus: String = "",
    @SerialName("redeem_status") val redeemStatus: String = "",
    @SerialName("daily_limit") val dailyLimit: Double? = null,
    @SerialName("limit_category") val limitCategory: String = "",
    @SerialName("fee_rate") val feeRate: String = "",
)

@Serializable
data class LatestQdiiResponse(
    @SerialName("task_id") val taskId: String = "",
    val timestamp: String = "",
    @SerialName("total_items") val totalItems: Int = 0,
    @SerialName("filtered_items_count") val filteredItemsCount: Int = 0,
    @SerialName("items_list") val itemsList: List<QdiiItemDto> = emptyList(),
)

@Serializable
data class DeviceRegisterBody(
    val token: String,
    val platform: String = "android",
    @SerialName("device_name") val deviceName: String = "",
)

@Serializable
data class TaskPatchBody(
    val enabled: Boolean,
)

@Serializable
data class TestPushBody(
    val title: String = "Omni-Monitor 测试推送",
    val summary: String = "FCM 通道与详情页联调测试成功！",
)
