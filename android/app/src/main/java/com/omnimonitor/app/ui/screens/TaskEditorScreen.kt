package com.omnimonitor.app.ui.screens

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.omnimonitor.app.data.AlertPolicyDto
import com.omnimonitor.app.data.OmniApiClient
import com.omnimonitor.app.data.RuleSummaryDto
import com.omnimonitor.app.data.TaskCreateBody
import com.omnimonitor.app.data.TaskUpdateBody
import com.omnimonitor.app.ui.theme.RedAlert
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

private val PLUGIN_OPTIONS = listOf(
    "gold" to "黄金价格",
    "us_stock" to "美股行情",
    "qdii_limits" to "QDII 限购",
    "qdii_announcements" to "QDII 公告",
    "http_json" to "HTTP JSON",
)

private val GOLD_INSTRUMENTS = listOf(
    "XAU" to "伦敦现货(USD)",
    "AU9999" to "上海金(CNY/g)",
    "GC00Y" to "COMEX期货",
    "PAXG" to "PAXG(24/7)",
)

private val SCALAR_OPS = listOf(
    "lte" to "≤ 跌破",
    "gte" to "≥ 涨破",
    "cross_below" to "下穿",
    "cross_above" to "上穿",
)

private val POLICY_MODES = listOf(
    "hysteresis" to "回差防抖",
    "cooldown" to "冷却防抖",
    "once" to "仅触发一次",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TaskEditorScreen(
    taskId: String?,
    apiClient: OmniApiClient,
    onBack: () -> Unit,
    onSaved: () -> Unit,
) {
    val isEdit = !taskId.isNullOrBlank()
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    var loading by remember { mutableStateOf(isEdit) }
    var saving by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }

    // Common task fields
    var pluginType by remember { mutableStateOf("us_stock") }
    var name by remember { mutableStateOf("") }
    var enabled by remember { mutableStateOf(true) }
    var intervalSec by remember { mutableStateOf("180") }

    // Plugin-specific fields
    var goldInstrument by remember { mutableStateOf("XAU") }
    var stockSymbol by remember { mutableStateOf("NVDA") }
    var stockMarketHoursOnly by remember { mutableStateOf(true) }
    var qdiiKeywords by remember { mutableStateOf("纳斯达克,标普500") }
    var qdiiWatchlist by remember { mutableStateOf("016452,017436,161130,270042,040046") }
    var qdiiCnyOnly by remember { mutableStateOf(true) }
    var httpUrl by remember { mutableStateOf("") }
    var httpValuePath by remember { mutableStateOf("data.price") }
    var httpUnit by remember { mutableStateOf("") }

    // Rule fields (scalar)
    var scalarOp by remember { mutableStateOf("lte") }
    var targetValue by remember { mutableStateOf("") }
    var useCustomExpr by remember { mutableStateOf(false) }
    var customExpr by remember { mutableStateOf("") }

    // Alert policy fields
    var policyMode by remember { mutableStateOf("hysteresis") }
    var cooldownSec by remember { mutableStateOf("3600") }
    var rearmValue by remember { mutableStateOf("") }
    var severity by remember { mutableStateOf("normal") }

    LaunchedEffect(taskId) {
        if (!isEdit) return@LaunchedEffect
        loading = true
        try {
            val existing = apiClient.getTask(taskId!!)
            pluginType = existing.pluginType
            name = existing.name
            enabled = existing.enabled
            intervalSec = existing.intervalSeconds.toString()

            val cfg = existing.pluginConfig
            when (existing.pluginType) {
                "gold" -> {
                    goldInstrument = cfg["instrument"]?.jsonPrimitive?.contentOrNull ?: "XAU"
                }

                "us_stock" -> {
                    stockSymbol = cfg["symbol"]?.jsonPrimitive?.contentOrNull ?: ""
                    stockMarketHoursOnly = cfg["market_hours_only"]?.jsonPrimitive?.booleanOrNull ?: true
                }

                "qdii_limits", "qdii_announcements" -> {
                    val kws = (cfg["keywords"] as? JsonArray)?.mapNotNull { it.jsonPrimitive.contentOrNull }.orEmpty()
                    qdiiKeywords = kws.joinToString(",")
                    val currs = (cfg["currencies"] as? JsonArray)?.mapNotNull { it.jsonPrimitive.contentOrNull }.orEmpty()
                    qdiiCnyOnly = currs.isEmpty() || "CNY" in currs
                    qdiiWatchlist = existing.rule.watchlistCodes.joinToString(",")
                }

                "http_json" -> {
                    httpUrl = cfg["url"]?.jsonPrimitive?.contentOrNull ?: ""
                    httpValuePath = cfg["value_path"]?.jsonPrimitive?.contentOrNull ?: ""
                    httpUnit = cfg["unit"]?.jsonPrimitive?.contentOrNull ?: ""
                }
            }

            if (existing.rule.type == "expr" && !existing.rule.expression.isNullOrBlank()) {
                useCustomExpr = true
                customExpr = existing.rule.expression
            } else {
                useCustomExpr = false
                scalarOp = existing.rule.op ?: "lte"
                targetValue = existing.rule.target?.toString() ?: ""
            }

            policyMode = existing.alertPolicy.mode
            cooldownSec = existing.alertPolicy.cooldownSeconds.toString()
            rearmValue = existing.alertPolicy.rearmValue?.toString() ?: ""
            severity = existing.alertPolicy.severity
        } catch (e: Exception) {
            snackbarHostState.showSnackbar("加载任务失败: ${e.message}")
        } finally {
            loading = false
        }
    }

    fun parseCsv(raw: String): List<String> =
        raw.split(",", "，", " ", "\n").map { it.trim() }.filter { it.isNotEmpty() }

    fun buildPluginConfig(): JsonObject = when (pluginType) {
        "gold" -> buildJsonObject {
            put("instrument", goldInstrument)
        }

        "us_stock" -> buildJsonObject {
            put("symbol", stockSymbol.trim().uppercase())
            put("market_hours_only", stockMarketHoursOnly)
            put("include_extended_hours", true)
        }

        "qdii_limits" -> buildJsonObject {
            put("keywords", JsonArray(parseCsv(qdiiKeywords).map { JsonPrimitive(it) }))
            put(
                "currencies",
                JsonArray(if (qdiiCnyOnly) listOf(JsonPrimitive("CNY")) else emptyList()),
            )
            put("exclude_exchange_etf", true)
        }

        "qdii_announcements" -> buildJsonObject {
            put("codes", JsonArray(emptyList()))
            put("keywords", JsonArray(parseCsv(qdiiKeywords).map { JsonPrimitive(it) }))
            put("lookback_days", 30)
            put("fetch_body", true)
        }

        else -> buildJsonObject {
            put("url", httpUrl.trim())
            put("method", "GET")
            if (httpValuePath.isNotBlank()) put("value_path", httpValuePath.trim())
            put("unit", httpUnit.trim())
        }
    }

    fun buildRule(): RuleSummaryDto = when (pluginType) {
        "qdii_limits" -> RuleSummaryDto(
            type = "collection_diff",
            watchFields = listOf("limit_category", "daily_limit"),
            ignoreCategories = listOf("HOLIDAY_SUSPENDED", "NO_EM_LIMIT_DATA", "OTHER"),
            onlyEvents = listOf("REOPENED", "LIMIT_UP", "LIMIT_DOWN", "QUOTA_SUSPENDED", "ADDED"),
            watchlistCodes = parseCsv(qdiiWatchlist),
        )

        "qdii_announcements" -> RuleSummaryDto(
            type = "new_items",
            watchlistCodes = parseCsv(qdiiWatchlist),
        )

        else -> {
            if (useCustomExpr && customExpr.isNotBlank()) {
                RuleSummaryDto(type = "expr", expression = customExpr.trim())
            } else {
                val t = targetValue.trim().toDoubleOrNull()
                    ?: throw IllegalArgumentException("请输入有效的数字目标阈值")
                val rType = if (scalarOp.startsWith("cross_")) "cross" else "threshold"
                RuleSummaryDto(type = rType, field = "value", op = scalarOp, target = t)
            }
        }
    }

    fun save() {
        scope.launch {
            saving = true
            try {
                val interval = intervalSec.trim().toIntOrNull()?.coerceAtLeast(10) ?: 300
                val cd = cooldownSec.trim().toIntOrNull()?.coerceAtLeast(0) ?: 3600
                val rearm = rearmValue.trim().toDoubleOrNull()
                val finalName = name.trim().ifBlank {
                    when (pluginType) {
                        "gold" -> "黄金 $goldInstrument 监控"
                        "us_stock" -> "${stockSymbol.trim().uppercase()} 价格监控"
                        "qdii_limits" -> "QDII 限购监控"
                        "qdii_announcements" -> "QDII 限购公告预警"
                        else -> "HTTP JSON 监控"
                    }
                }
                val pluginCfg = buildPluginConfig()
                val rule = buildRule()
                val policy = AlertPolicyDto(
                    mode = if (pluginType in listOf("qdii_limits", "qdii_announcements")) "cooldown" else policyMode,
                    cooldownSeconds = cd,
                    rearmValue = if (policyMode == "hysteresis") rearm else null,
                    severity = severity,
                )

                if (isEdit) {
                    apiClient.updateTask(
                        taskId!!,
                        TaskUpdateBody(
                            name = finalName,
                            enabled = enabled,
                            intervalSeconds = interval,
                            pluginConfig = pluginCfg,
                            rule = rule,
                            alertPolicy = policy,
                        ),
                    )
                } else {
                    apiClient.createTask(
                        TaskCreateBody(
                            name = finalName,
                            enabled = enabled,
                            pluginType = pluginType,
                            intervalSeconds = interval,
                            pluginConfig = pluginCfg,
                            rule = rule,
                            alertPolicy = policy,
                        ),
                    )
                }
                onSaved()
            } catch (e: Exception) {
                snackbarHostState.showSnackbar("保存失败: ${e.message}")
            } finally {
                saving = false
            }
        }
    }

    if (confirmDelete && isEdit) {
        AlertDialog(
            onDismissRequest = { confirmDelete = false },
            title = { Text("删除监控任务") },
            text = { Text("确认删除「$name」吗？删除后将停止该任务监控。") },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmDelete = false
                        scope.launch {
                            try {
                                apiClient.deleteTask(taskId!!)
                                onSaved()
                            } catch (e: Exception) {
                                snackbarHostState.showSnackbar("删除失败: ${e.message}")
                            }
                        }
                    },
                ) {
                    Text("确认删除", color = RedAlert)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmDelete = false }) { Text("取消") }
            },
        )
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = { Text(if (isEdit) "编辑监控任务" else "新建监控任务", fontWeight = FontWeight.SemiBold) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                actions = {
                    if (isEdit) {
                        IconButton(onClick = { confirmDelete = true }) {
                            Icon(Icons.Default.Delete, contentDescription = "删除", tint = RedAlert)
                        }
                    }
                },
            )
        },
    ) { innerPadding ->
        if (loading) {
            Box(Modifier.fillMaxSize().padding(innerPadding), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            return@Scaffold
        }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            // 1. Plugin Type Selector
            if (!isEdit) {
                Card(
                    shape = RoundedCornerShape(14.dp),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                ) {
                    Column(Modifier.padding(14.dp)) {
                        Text("选择监控插件", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(8.dp))
                        Row(
                            modifier = Modifier.horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            PLUGIN_OPTIONS.forEach { (type, label) ->
                                FilterChip(
                                    selected = pluginType == type,
                                    onClick = { pluginType = type },
                                    label = { Text(label) },
                                )
                            }
                        }
                    }
                }
            }

            // 2. Basic Info + Plugin-Specific Config
            Card(
                shape = RoundedCornerShape(14.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("监控标的与采集参数", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)

                    OutlinedTextField(
                        value = name,
                        onValueChange = { name = it },
                        label = { Text("任务名称（留空自动命名）") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )

                    when (pluginType) {
                        "gold" -> {
                            Row(
                                modifier = Modifier.horizontalScroll(rememberScrollState()),
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                GOLD_INSTRUMENTS.forEach { (inst, label) ->
                                    FilterChip(
                                        selected = goldInstrument == inst,
                                        onClick = { goldInstrument = inst },
                                        label = { Text(label) },
                                    )
                                }
                            }
                        }

                        "us_stock" -> {
                            OutlinedTextField(
                                value = stockSymbol,
                                onValueChange = { stockSymbol = it.uppercase() },
                                label = { Text("美股代码（如 NVDA / AAPL / QQQ）") },
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text("仅美东交易日（含盘前盘后）采集", style = MaterialTheme.typography.bodyMedium)
                                Switch(
                                    checked = stockMarketHoursOnly,
                                    onCheckedChange = { stockMarketHoursOnly = it },
                                )
                            }
                        }

                        "qdii_limits", "qdii_announcements" -> {
                            OutlinedTextField(
                                value = qdiiKeywords,
                                onValueChange = { qdiiKeywords = it },
                                label = { Text("基金名称关键词（逗号分隔，留空=全市场）") },
                                placeholder = { Text("纳斯达克,标普500,日经") },
                                modifier = Modifier.fillMaxWidth(),
                            )
                            OutlinedTextField(
                                value = qdiiWatchlist,
                                onValueChange = { qdiiWatchlist = it },
                                label = { Text("⭐ 重点自选基金代码（命中时高优先推送）") },
                                placeholder = { Text("016452,017436,161130") },
                                modifier = Modifier.fillMaxWidth(),
                            )
                            if (pluginType == "qdii_limits") {
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Text("仅监控人民币份额 (CNY)", style = MaterialTheme.typography.bodyMedium)
                                    Switch(
                                        checked = qdiiCnyOnly,
                                        onCheckedChange = { qdiiCnyOnly = it },
                                    )
                                }
                            }
                        }

                        "http_json" -> {
                            OutlinedTextField(
                                value = httpUrl,
                                onValueChange = { httpUrl = it },
                                label = { Text("JSON API URL") },
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            OutlinedTextField(
                                value = httpValuePath,
                                onValueChange = { httpValuePath = it },
                                label = { Text("数值提取路径（如 data.price）") },
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            OutlinedTextField(
                                value = httpUnit,
                                onValueChange = { httpUnit = it },
                                label = { Text("显示单位（可选，如 USD / 元）") },
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }

                    OutlinedTextField(
                        value = intervalSec,
                        onValueChange = { intervalSec = it.filter(Char::isDigit) },
                        label = { Text("采集间隔（秒，最小 10）") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }

            // 3. Scalar Trigger Condition
            if (pluginType !in listOf("qdii_limits", "qdii_announcements")) {
                Card(
                    shape = RoundedCornerShape(14.dp),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                ) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text("触发条件", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                            FilterChip(
                                selected = useCustomExpr,
                                onClick = { useCustomExpr = !useCustomExpr },
                                label = { Text("高级表达式") },
                            )
                        }

                        if (useCustomExpr) {
                            OutlinedTextField(
                                value = customExpr,
                                onValueChange = { customExpr = it },
                                label = { Text("安全表达式（如 value <= 135 or metrics.change_pct <= -3）") },
                                modifier = Modifier.fillMaxWidth(),
                            )
                        } else {
                            Row(
                                modifier = Modifier.horizontalScroll(rememberScrollState()),
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                SCALAR_OPS.forEach { (op, label) ->
                                    FilterChip(
                                        selected = scalarOp == op,
                                        onClick = { scalarOp = op },
                                        label = { Text(label) },
                                    )
                                }
                            }
                            OutlinedTextField(
                                value = targetValue,
                                onValueChange = { targetValue = it },
                                label = { Text("目标价格 / 阈值") },
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }
                }
            }

            // 4. Alert Policy & Priority
            Card(
                shape = RoundedCornerShape(14.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            ) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("推送防抖与优先级", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)

                    if (pluginType !in listOf("qdii_limits", "qdii_announcements")) {
                        Row(
                            modifier = Modifier.horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            POLICY_MODES.forEach { (m, label) ->
                                FilterChip(
                                    selected = policyMode == m,
                                    onClick = { policyMode = m },
                                    label = { Text(label) },
                                )
                            }
                        }
                        if (policyMode == "hysteresis") {
                            OutlinedTextField(
                                value = rearmValue,
                                onValueChange = { rearmValue = it },
                                label = { Text("回差重置价（可选，如跌破 3400 后回升到 3450 再重新布防）") },
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }

                    OutlinedTextField(
                        value = cooldownSec,
                        onValueChange = { cooldownSec = it.filter(Char::isDigit) },
                        label = { Text("冷却时间（秒）") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )

                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        listOf("normal" to "常规优先级", "high" to "高优先级提醒").forEach { (sev, label) ->
                            FilterChip(
                                selected = severity == sev,
                                onClick = { severity = sev },
                                label = { Text(label) },
                            )
                        }
                    }
                }
            }

            Button(
                onClick = { save() },
                enabled = !saving,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp),
                shape = RoundedCornerShape(12.dp),
            ) {
                Text(if (saving) "保存中..." else if (isEdit) "保存修改" else "创建监控任务")
            }
            Spacer(Modifier.height(16.dp))
        }
    }
}
