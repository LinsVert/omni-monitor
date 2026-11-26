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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
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
import androidx.compose.ui.text.font.FontWeight
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

private val CATEGORY_FILTERS = listOf(
    null to "全部",
    "LIMITED" to "限大额",
    "UNLIMITED" to "无限额",
    "QUOTA_SUSPENDED" to "额度暂停",
    "HOLIDAY_SUSPENDED" to "休市暂停",
)

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
                    limit = 200,
                )
            } catch (e: Exception) {
                error = e.message ?: "加载 QDII 限购看板失败"
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(query, selectedCategory) {
        delay(250)
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
                                text = "显示 ${it.filteredItemsCount} / 共 ${it.totalItems} 只 · ${formatTimestamp(it.timestamp)}",
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
                singleLine = true,
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 6.dp),
            )

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                CATEGORY_FILTERS.forEach { (cat, label) ->
                    FilterChip(
                        selected = selectedCategory == cat,
                        onClick = { selectedCategory = cat },
                        label = { Text(label) },
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
                    LazyColumn(
                        contentPadding = PaddingValues(16.dp),
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

@Composable
private fun QdiiFundRow(item: QdiiItemDto) {
    val (badgeText, badgeColor) = when (item.limitCategory) {
        "UNLIMITED" -> "无限额" to GreenPositive
        "LIMITED" -> {
            val lim = item.dailyLimit
            val label = when {
                lim == null -> "限大额"
                lim >= 10000 -> "限 ${"%.0f".format(lim / 10000)}万"
                else -> "限 ${"%.0f".format(lim)}元"
            }
            label to SkyAccent
        }
        "QUOTA_SUSPENDED" -> "额度暂停" to RedAlert
        "HOLIDAY_SUSPENDED" -> "休市暂停" to AmberWarn
        else -> (item.purchaseStatus.ifBlank { item.limitCategory }) to MaterialTheme.colorScheme.onSurfaceVariant
    }

    Card(
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.5.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
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
            }

            Surface(
                color = badgeColor.copy(alpha = 0.14f),
                shape = RoundedCornerShape(8.dp),
            ) {
                Text(
                    text = badgeText,
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = badgeColor,
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 5.dp),
                )
            }
        }
    }
}
