package com.omnimonitor.app.ui.screens

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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
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
import androidx.compose.ui.unit.dp
import com.omnimonitor.app.data.OmniApiClient
import com.omnimonitor.app.data.TaskDto
import com.omnimonitor.app.ui.theme.GreenPositive
import com.omnimonitor.app.ui.theme.RedAlert
import com.omnimonitor.app.ui.theme.SkyAccent
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TasksScreen(
    apiClient: OmniApiClient,
    onCreateTask: () -> Unit,
    onEditTask: (String) -> Unit,
) {
    var tasks by remember { mutableStateOf<List<TaskDto>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var runningTaskId by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    fun reload() {
        scope.launch {
            loading = true
            error = null
            try {
                tasks = apiClient.listTasks()
            } catch (e: Exception) {
                error = e.message ?: "加载任务列表失败"
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) { reload() }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        floatingActionButton = {
            FloatingActionButton(onClick = onCreateTask) {
                Icon(Icons.Default.Add, contentDescription = "新建任务")
            }
        },
        topBar = {
            TopAppBar(
                title = { Text("监控任务", fontWeight = FontWeight.SemiBold) },
                actions = {
                    IconButton(onClick = { reload() }) {
                        Icon(Icons.Default.Refresh, contentDescription = "刷新")
                    }
                },
            )
        },
    ) { innerPadding ->
        when {
            loading && tasks.isEmpty() -> {
                Box(
                    Modifier
                        .fillMaxSize()
                        .padding(innerPadding),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator()
                }
            }

            error != null && tasks.isEmpty() -> {
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

            else -> {
                LazyColumn(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(innerPadding),
                    contentPadding = PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    items(tasks, key = { it.taskId }) { task ->
                        TaskCard(
                            task = task,
                            isRunning = runningTaskId == task.taskId,
                            onEdit = { onEditTask(task.taskId) },
                            onToggleEnabled = { enabled ->
                                scope.launch {
                                    try {
                                        apiClient.setTaskEnabled(task.taskId, enabled)
                                        reload()
                                    } catch (e: Exception) {
                                        snackbarHostState.showSnackbar("更新失败: ${e.message}")
                                    }
                                }
                            },
                            onRunNow = {
                                scope.launch {
                                    runningTaskId = task.taskId
                                    try {
                                        val res = apiClient.runTaskNow(task.taskId)
                                        val msg = when {
                                            !res.ok -> "采集失败: ${res.error}"
                                            res.alertId != null -> "采集成功，已触发推送 (${res.alertId})"
                                            else -> "采集成功，当前未触发告警"
                                        }
                                        snackbarHostState.showSnackbar(msg)
                                        reload()
                                    } catch (e: Exception) {
                                        snackbarHostState.showSnackbar("执行异常: ${e.message}")
                                    } finally {
                                        runningTaskId = null
                                    }
                                }
                            },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun TaskCard(
    task: TaskDto,
    isRunning: Boolean,
    onEdit: () -> Unit,
    onToggleEnabled: (Boolean) -> Unit,
    onRunNow: () -> Unit,
) {
    val hasErrors = task.state.consecutiveFailures > 0
    Card(
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(
                        text = task.name,
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(Modifier.height(4.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Surface(
                            color = SkyAccent.copy(alpha = 0.12f),
                            shape = RoundedCornerShape(6.dp),
                        ) {
                            Text(
                                text = pluginDisplayName(task.pluginType),
                                style = MaterialTheme.typography.labelSmall,
                                color = SkyAccent,
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                            )
                        }
                        Surface(
                            color = (if (hasErrors) RedAlert else GreenPositive).copy(alpha = 0.12f),
                            shape = RoundedCornerShape(6.dp),
                        ) {
                            Text(
                                text = if (hasErrors) "连续失败 ${task.state.consecutiveFailures} 次" else "运行正常",
                                style = MaterialTheme.typography.labelSmall,
                                color = if (hasErrors) RedAlert else GreenPositive,
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                            )
                        }
                    }
                }
                Switch(
                    checked = task.enabled,
                    onCheckedChange = onToggleEnabled,
                )
            }

            Spacer(Modifier.height(8.dp))
            Text(
                text = "频率: 每 ${task.intervalSeconds}s · 规则: ${ruleTypeDisplayName(task.rule.type)}" +
                    (task.state.lastSuccessAt?.let { " · 上次成功: ${formatTimestamp(it)}" } ?: ""),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            if (!task.state.lastError.isNullOrBlank()) {
                Spacer(Modifier.height(4.dp))
                Text(
                    text = "最近错误: ${task.state.lastError}",
                    style = MaterialTheme.typography.labelSmall,
                    color = RedAlert,
                )
            }

            Spacer(Modifier.height(10.dp))
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
                modifier = Modifier.fillMaxWidth(),
            ) {
                OutlinedButton(
                    onClick = onEdit,
                    shape = RoundedCornerShape(10.dp),
                ) {
                    Icon(Icons.Default.Edit, contentDescription = null, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("编辑")
                }
                FilledTonalButton(
                    onClick = onRunNow,
                    enabled = !isRunning,
                    shape = RoundedCornerShape(10.dp),
                ) {
                    if (isRunning) {
                        CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                        Spacer(Modifier.width(6.dp))
                        Text("采集中...")
                    } else {
                        Icon(Icons.Default.PlayArrow, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("立即抓取")
                    }
                }
            }
        }
    }
}
