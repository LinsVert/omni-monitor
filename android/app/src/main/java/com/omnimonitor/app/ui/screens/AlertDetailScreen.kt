package com.omnimonitor.app.ui.screens

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.omnimonitor.app.data.AlertDetail
import com.omnimonitor.app.data.DetailBlock
import com.omnimonitor.app.data.DiffItem
import com.omnimonitor.app.data.OmniApiClient
import com.omnimonitor.app.ui.theme.AmberWarn
import com.omnimonitor.app.ui.theme.GreenPositive
import com.omnimonitor.app.ui.theme.RedAlert
import com.omnimonitor.app.ui.theme.SkyAccent

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AlertDetailScreen(
    alertId: String,
    apiClient: OmniApiClient,
    onBack: () -> Unit,
) {
    var detail by remember(alertId) { mutableStateOf<AlertDetail?>(null) }
    var loading by remember(alertId) { mutableStateOf(true) }
    var error by remember(alertId) { mutableStateOf<String?>(null) }
    val context = LocalContext.current

    fun openUrl(url: String) {
        runCatching {
            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        }
    }

    LaunchedEffect(alertId) {
        loading = true
        error = null
        try {
            val loaded = apiClient.getAlertDetail(alertId)
            detail = loaded
            if (!loaded.isRead) {
                runCatching { apiClient.markAlertRead(alertId) }
            }
        } catch (e: Exception) {
            error = e.message ?: "加载详情失败"
        } finally {
            loading = false
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("推送详情", fontWeight = FontWeight.SemiBold) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
            )
        },
    ) { innerPadding ->
        when {
            loading -> {
                Box(
                    Modifier
                        .fillMaxSize()
                        .padding(innerPadding),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator()
                }
            }

            error != null -> {
                Box(
                    Modifier
                        .fillMaxSize()
                        .padding(innerPadding)
                        .padding(24.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(error.orEmpty(), color = MaterialTheme.colorScheme.error)
                }
            }

            detail != null -> {
                val alert = detail!!
                val blocks = remember(alert) { alert.parsedBlocks() }
                LazyColumn(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(innerPadding),
                    contentPadding = PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    item {
                        Card(
                            shape = RoundedCornerShape(16.dp),
                            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                        ) {
                            Column(Modifier.padding(16.dp)) {
                                Text(
                                    text = alert.title,
                                    style = MaterialTheme.typography.titleMedium,
                                    fontWeight = FontWeight.Bold,
                                )
                                Spacer(Modifier.height(6.dp))
                                Text(
                                    text = alert.summary,
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                Spacer(Modifier.height(10.dp))
                                Text(
                                    text = "${alert.taskName}  ·  ${formatTimestamp(alert.triggeredAt)}  ·  推送状态: ${alert.pushStatus}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }

                    blocks.forEachIndexed { idx, block ->
                        item(key = "block_$idx") {
                            when (block) {
                                is DetailBlock.KvGrid -> KvGridCard(block)
                                is DetailBlock.LineChart -> LineChartCard(block)
                                is DetailBlock.DiffList -> DiffListCard(block, onOpenUrl = ::openUrl)
                                is DetailBlock.Markdown -> MarkdownCard(block)
                                is DetailBlock.Actions -> ActionsCard(block, onOpenUrl = ::openUrl)
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun KvGridCard(block: DetailBlock.KvGrid) {
    Card(
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            block.items.chunked(2).forEach { pair ->
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    pair.forEach { kv ->
                        Surface(
                            modifier = Modifier.weight(1f),
                            shape = RoundedCornerShape(10.dp),
                            color = MaterialTheme.colorScheme.surfaceVariant,
                        ) {
                            Column(Modifier.padding(10.dp)) {
                                Text(
                                    text = kv.label,
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                Spacer(Modifier.height(2.dp))
                                Text(
                                    text = kv.value,
                                    style = MaterialTheme.typography.bodyMedium,
                                    fontWeight = FontWeight.SemiBold,
                                )
                            }
                        }
                    }
                    if (pair.size == 1) {
                        Spacer(Modifier.weight(1f))
                    }
                }
            }
        }
    }
}

@Composable
private fun LineChartCard(block: DetailBlock.LineChart) {
    if (block.points.size < 2) return
    val values = block.points.map { it.second }
    val allNums = if (block.targetLine != null) values + block.targetLine else values
    val minV = allNums.min()
    val maxV = allNums.max()
    val span = (maxV - minV).takeIf { it > 1e-9 } ?: 1.0

    Card(
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    text = block.title,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    text = "高 ${"%.2f".format(maxV)} / 低 ${"%.2f".format(minV)} ${block.unit}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.height(12.dp))

            Canvas(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(160.dp),
            ) {
                val padTop = 10f
                val padBottom = 10f
                val drawH = size.height - padTop - padBottom
                val stepX = size.width / (values.size - 1).coerceAtLeast(1)

                fun yFor(v: Double): Float {
                    val norm = ((v - minV) / span).toFloat()
                    return padTop + drawH * (1f - norm)
                }

                // Target threshold dashed line
                block.targetLine?.let { target ->
                    val ty = yFor(target)
                    drawLine(
                        color = AmberWarn,
                        start = Offset(0f, ty),
                        end = Offset(size.width, ty),
                        strokeWidth = 3f,
                        pathEffect = PathEffect.dashPathEffect(floatArrayOf(12f, 8f)),
                    )
                }

                val linePath = Path()
                val fillPath = Path()
                values.forEachIndexed { i, v ->
                    val x = i * stepX
                    val y = yFor(v)
                    if (i == 0) {
                        linePath.moveTo(x, y)
                        fillPath.moveTo(x, size.height)
                        fillPath.lineTo(x, y)
                    } else {
                        linePath.lineTo(x, y)
                        fillPath.lineTo(x, y)
                    }
                }
                fillPath.lineTo(size.width, size.height)
                fillPath.close()

                drawPath(
                    path = fillPath,
                    brush = Brush.verticalGradient(
                        colors = listOf(SkyAccent.copy(alpha = 0.25f), SkyAccent.copy(alpha = 0.0f)),
                    ),
                )
                drawPath(
                    path = linePath,
                    color = SkyAccent,
                    style = Stroke(width = 5f, cap = StrokeCap.Round),
                )
            }

            if (block.targetLine != null) {
                Spacer(Modifier.height(6.dp))
                Text(
                    text = "虚线为目标阈值：${block.targetLine} ${block.unit}",
                    style = MaterialTheme.typography.labelSmall,
                    color = AmberWarn,
                )
            }
        }
    }
}

@Composable
private fun DiffListCard(block: DetailBlock.DiffList, onOpenUrl: (String) -> Unit) {
    Card(
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(
                text = block.title,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(10.dp))
            block.items.forEachIndexed { index, item ->
                if (index > 0) {
                    HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                }
                DiffItemRow(item = item, onOpenUrl = onOpenUrl)
            }
        }
    }
}

@Composable
private fun DiffItemRow(item: DiffItem, onOpenUrl: (String) -> Unit) {
    val badgeColor = when (item.event) {
        "REOPENED", "LIMIT_UP", "ADDED" -> GreenPositive
        "QUOTA_SUSPENDED", "LIMIT_DOWN" -> RedAlert
        else -> SkyAccent
    }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .then(if (!item.url.isNullOrBlank()) Modifier.clickable { onOpenUrl(item.url) } else Modifier),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (item.isWatchlist) {
                Text("⭐ ", style = MaterialTheme.typography.labelMedium)
            }
            Text(
                text = "${item.code} ${item.name}".trim(),
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(8.dp))
            Surface(
                color = badgeColor.copy(alpha = 0.14f),
                shape = RoundedCornerShape(6.dp),
            ) {
                Text(
                    text = item.event,
                    style = MaterialTheme.typography.labelSmall,
                    color = badgeColor,
                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                )
            }
        }
        Spacer(Modifier.height(4.dp))
        Text(
            text = "${item.beforeText}  →  ${item.afterText}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun MarkdownCard(block: DetailBlock.Markdown) {
    Card(
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(
                text = block.title,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                text = block.content,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun ActionsCard(block: DetailBlock.Actions, onOpenUrl: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        block.links.forEach { link ->
            OutlinedButton(
                onClick = { onOpenUrl(link.url) },
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(12.dp),
            ) {
                Text(link.label)
                Spacer(Modifier.width(6.dp))
                Icon(Icons.AutoMirrored.Filled.OpenInNew, contentDescription = null)
            }
        }
    }
}
