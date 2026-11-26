package com.omnimonitor.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.omnimonitor.app.data.AppSettings
import com.omnimonitor.app.data.OmniApiClient
import com.omnimonitor.app.data.SettingsStore
import com.omnimonitor.app.fcm.FcmSyncResult
import com.omnimonitor.app.fcm.FcmTokenManager
import com.omnimonitor.app.ui.theme.AmberWarn
import com.omnimonitor.app.ui.theme.GreenPositive
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    settingsStore: SettingsStore,
    apiClient: OmniApiClient,
    onOpenAlert: (String) -> Unit,
) {
    val saved by settingsStore.settingsFlow.collectAsState(initial = AppSettings())
    var urlInput by remember(saved.serverUrl) { mutableStateOf(saved.serverUrl) }
    var apiKeyInput by remember(saved.apiKey) { mutableStateOf(saved.apiKey) }
    var statusText by remember { mutableStateOf<String?>(null) }
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val firebaseReady = remember { FcmTokenManager.isFirebaseConfigured(context) }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(title = { Text("服务端与 FCM 设置", fontWeight = FontWeight.SemiBold) })
        },
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Card(
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("监控服务端地址", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    OutlinedTextField(
                        value = urlInput,
                        onValueChange = { urlInput = it },
                        label = { Text("Server Base URL") },
                        placeholder = { Text("http://192.168.1.10:8080 或 https://your-vps") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(
                        value = apiKeyInput,
                        onValueChange = { apiKeyInput = it },
                        label = { Text("X-API-Key (可选)") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Button(
                            onClick = {
                                scope.launch {
                                    settingsStore.saveServerConfig(urlInput, apiKeyInput)
                                    snackbarHostState.showSnackbar("配置已保存")
                                }
                            },
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("保存配置")
                        }
                        FilledTonalButton(
                            onClick = {
                                scope.launch {
                                    settingsStore.saveServerConfig(urlInput, apiKeyInput)
                                    try {
                                        val h = apiClient.getHealth()
                                        statusText = "连接正常 v${h.version} · 插件 ${h.registeredPlugins} · 任务 ${h.tasksCount} · 服务端FCM=${h.fcmMode}"
                                    } catch (e: Exception) {
                                        statusText = "连接失败: ${e.message}"
                                    }
                                }
                            },
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("测试连接")
                        }
                    }
                    if (!statusText.isNullOrBlank()) {
                        Text(
                            text = statusText.orEmpty(),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            Card(
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Google FCM 推送通道", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
                    Text(
                        text = if (firebaseReady) {
                            "状态：已加载 google-services.json（真实 FCM 可用）"
                        } else {
                            "状态：尚未放入 android/app/google-services.json（将文件放入后重新打包即可自动启用真 FCM）"
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = if (firebaseReady) GreenPositive else AmberWarn,
                    )
                    if (saved.fcmToken.isNotBlank()) {
                        Text(
                            text = "Token: ${saved.fcmToken.take(36)}...",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        FilledTonalButton(
                            onClick = {
                                scope.launch {
                                    when (val res = FcmTokenManager.syncTokenToServer(context, settingsStore, apiClient)) {
                                        is FcmSyncResult.Registered ->
                                            snackbarHostState.showSnackbar("FCM Token 已注册到服务端")
                                        FcmSyncResult.FirebaseNotConfigured ->
                                            snackbarHostState.showSnackbar("请先配置 google-services.json")
                                        is FcmSyncResult.Failed ->
                                            snackbarHostState.showSnackbar("注册失败: ${res.message}")
                                    }
                                }
                            },
                            modifier = Modifier.weight(1f),
                        ) {
                            Text("同步 FCM Token")
                        }

                        if (saved.fcmToken.isNotBlank()) {
                            OutlinedButton(
                                onClick = {
                                    clipboard.setText(AnnotatedString(saved.fcmToken))
                                    scope.launch { snackbarHostState.showSnackbar("Token 已复制") }
                                },
                                modifier = Modifier.weight(1f),
                            ) {
                                Text("复制 Token")
                            }
                        }
                    }

                    Spacer(Modifier.height(2.dp))
                    Button(
                        onClick = {
                            scope.launch {
                                try {
                                    val alert = apiClient.sendTestPush()
                                    snackbarHostState.showSnackbar("测试推送已生成 (${alert.pushStatus})")
                                    onOpenAlert(alert.alertId)
                                } catch (e: Exception) {
                                    snackbarHostState.showSnackbar("测试推送失败: ${e.message}")
                                }
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("发送一条测试推送并打开详情页")
                    }
                }
            }
        }
    }
}
