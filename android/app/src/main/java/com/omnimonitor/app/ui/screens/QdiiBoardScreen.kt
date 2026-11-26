package com.omnimonitor.app.ui.screens

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AssistChip
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
import androidx.compose.material3.Surface
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.omnimonitor.app.data.LatestQdiiResponse
import com.omnimonitor.app.data.OmniApiClient
import com.omnimonitor.app.data.QdiiItemDto
import com.omnimonitor.app.ui.theme.AmberWarn
import com.omnimonitor.app.ui.theme.GreenPositive
import com.omnimonitor.app.ui.theme.RedAlert
import com.omnimonitor.app.ui.theme.SkyAccent
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.text.DecimalFormat

private val CATEGORY_FILTERS = listOf(
    null to "全部",
    "LIMITED" to "限大额",
    "UNLIMITED" to "不限额",
    "QUOTA_SUSPENDED" to "额度暂停",
    "HOLIDAY_SUSPENDED" to "休市暂停",
)

private val QUICK_KEYWORDS = listOf("纳斯达克", "标普500", "日经", "黄金", "德国", "印度")

private val NUM_FMT = DecimalFormat("#,##0.##")

private fun formatYuan(amount: Double): String {
    return when {
        amount >= 100_000_000.0 -> "${NUM_FMT.format(amount / 100_000_000.0)} 亿元"
        amount >= 10_000.0 -> "${NUM_FMT.format(amount / 10_000.0)} 万元"
        else -> "${NUM_FMT.format(amount)} 元"
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun QdiiBoardScreen(apiClient: OmniApiClient) {
    var query by remember { mutableStateOf("") }
    var selectedCategory by remember { mutableStateOf<String?>(null) }
    var board by remember { mutableStateOf<LatestQdiiResponse?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    fun fetchBoard() {
        scope.launch {
            loading = true
            error = null
            try {
                board = apiClient.getQdiiBoard(
                    query = query.trim().ifBlank { null },
                    category = selectedCategory,
                    sortBy = "daily_limit",
                    limit = 250,
                )
            } catch (e: Exception) {
                error = e.message ?: "加载 QDII 限购看板失败"
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(query, selectedCategory) {
        delay(220)
        fetchBoard()
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("QDII 限购看板", fontWeight = FontWeight.SemiBold)
                        board?.let {
                            Text(
                                text = "匹配 ${it.filteredItemsCount} / 共 ${it.totalItems} 只 · 更新于 ${formatTimestamp(it.timestamp)}",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                },
                actions = {
                    IconButton(onClick = { fetchBoard() }) {
                        Icon(Icons.Default.Refresh, contentDescription = "刷新")
                    }
                },
            )
        },
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
        ) {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                placeholder = { Text("搜代码或名称，如 纳斯达克 / 标普 / 017436") },
                leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
                trailingIcon = {
                    if (query.isNotEmpty()) {
                        IconButton(onClick = { query = "" }) {
                            Icon(Icons.Default.Clear, contentDescription = "清空")
                        }
                    }
                },
                singleLine = true,
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 4.dp),
            )

            // Quick topic filter chips
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp, vertical = 2.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                QUICK_KEYWORDS.forEach { kw ->
                    AssistChip(
                        onClick = { query = if (query == kw) "" else kw },
                        label = { Text(kw, style = MaterialTheme.typography.labelSmall) },
                    )
                }
            }

            // Category chips with live counts
            val counts = board?.categoryCounts.orEmpty()
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp, vertical = 2.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                CATEGORY_FILTERS.forEach { (cat, label) ->
                    val countSuffix = if (cat == null) {
                        board?.let { " (${it.totalItems})" }.orEmpty()
                    } else {
                        counts[cat]?.let { " ($it)" }.orEmpty()
                    }
                    FilterChip(
                        selected = selectedCategory == cat,
                        onClick = { selectedCategory = cat },
                        label = { Text("$label$countSuffix") },
                    )
                }
            }

            when {
                loading && board == null -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator()
                    }
                }

                error != null && board == null -> {
                    Box(
                        Modifier
                            .fillMaxSize()
                            .padding(24.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text(error.orEmpty(), color = MaterialTheme.colorScheme.error)
                            Spacer(Modifier.height(8.dp))
                            Text(
                                "提示：请先在「任务」页运行一次 qdii_all_limits 建立快照",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            TextButton(onClick = { fetchBoard() }) { Text("重试") }
                        }
                    }
                }

                else -> {
                    val items = board?.itemsList.orEmpty()
                    if (items.isEmpty()) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Text("当前筛选条件下暂无基金", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    } else {
                        LazyColumn(
                            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            items(items, key = { it.code }) { item ->
                                QdiiFundRow(item)
                            }
                        }
                    }
                }
            }
        }
    }
}

private data class LimitDisplay(
    val primaryValue: String,
    val statusLabel: String,
    val accent: Color,
)

private fun resolveLimitDisplay(item: QdiiItemDto, mutedColor: Color): LimitDisplay {
    return when (item.limitCategory) {
        "LIMITED" -> {
            val lim = item.dailyLimit
            LimitDisplay(
                primaryValue = if (lim != null) formatYuan(lim) else "限大额",
                statusLabel = "单日限购",
                accent = SkyAccent,
            )
        }

        "UNLIMITED" -> LimitDisplay(
            primaryValue = "不限额",
            statusLabel = "开放申购",
            accent = GreenPositive,
        )

        "HOLIDAY_SUSPENDED" -> {
            val standing = item.standingLimit
            val primary = when {
                standing != null && standing > 0 -> formatYuan(standing)
                item.standingUnlimited -> "不限额"
                else -> "休市中"
            }
            val sub = if ((standing != null && standing > 0) || item.standingUnlimited) {
                "休市·常驻限额"
            } else {
                "今日休市暂停"
            }
            LimitDisplay(primaryValue = primary, statusLabel = sub, accent = AmberWarn)
        }

        "QUOTA_SUSPENDED" -> {
            val standing = item.standingLimit
            val sub = if (standing != null && standing > 0) {
                "暂停(原限${formatYuan(standing)})"
            } else {
                "额度暂停申购"
            }
            LimitDisplay(primaryValue = "0 元", statusLabel = sub, accent = RedAlert)
        }

        "NO_EM_LIMIT_DATA" -> LimitDisplay(
            primaryValue = "--",
            statusLabel = "非东财代销",
            accent = mutedColor,
        )

        else -> LimitDisplay(
            primaryValue = "--",
            statusLabel = item.purchaseStatus.ifBlank { item.limitCategory },
            accent = mutedColor,
        )
    }
}

@Composable
private fun QdiiFundRow(item: QdiiItemDto) {
    val display = resolveLimitDisplay(item, MaterialTheme.colorScheme.onSurfaceVariant)

    Card(
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.8.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 11.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = item.name,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    text = buildString {
                        append(item.code)
                        append(" · ")
                        append(item.currency)
                        if (item.nav != null) {
                            append(" · 净值 ${"%.4f".format(item.nav)}")
                        }
                        if (item.navDate.isNotBlank()) {
                            append(" (${item.navDate})")
                        }
                    },
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (item.minPurchase != null && item.minPurchase > 0) {
                    Spacer(Modifier.height(2.dp))
                    Text(
                        text = "起购 ${formatYuan(item.minPurchase)}" +
                            if (item.feeRate.isNotBlank() && item.feeRate != "--") " · 费率 ${item.feeRate}" else "",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            Spacer(Modifier.width(10.dp))

            // Prominent numeric limit column on the right
            Surface(
                color = display.accent.copy(alpha = 0.12f),
                shape = RoundedCornerShape(10.dp),
            ) {
                Column(
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                    horizontalAlignment = Alignment.End,
                ) {
                    Text(
                        text = display.primaryValue,
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                        color = display.accent,
                        textAlign = TextAlign.End,
                    )
                    Text(
                        text = display.statusLabel,
                        style = MaterialTheme.typography.labelSmall,
                        color = display.accent.copy(alpha = 0.85f),
                        textAlign = TextAlign.End,
                    )
                }
            }
        }
    }
}
