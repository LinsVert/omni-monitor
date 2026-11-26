package com.omnimonitor.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ShowChart
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.core.content.ContextCompat
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.omnimonitor.app.fcm.FcmTokenManager
import com.omnimonitor.app.fcm.NotificationHelper
import com.omnimonitor.app.ui.screens.AlertDetailScreen
import com.omnimonitor.app.ui.screens.AlertsScreen
import com.omnimonitor.app.ui.screens.QdiiBoardScreen
import com.omnimonitor.app.ui.screens.SettingsScreen
import com.omnimonitor.app.ui.screens.TaskEditorScreen
import com.omnimonitor.app.ui.screens.TasksScreen
import com.omnimonitor.app.ui.theme.OmniMonitorTheme
import kotlinx.coroutines.flow.MutableStateFlow

private data class BottomTab(
    val route: String,
    val label: String,
    val icon: ImageVector,
)

private val TABS = listOf(
    BottomTab("alerts", "推送", Icons.Default.Notifications),
    BottomTab("qdii", "QDII看板", Icons.AutoMirrored.Filled.ShowChart),
    BottomTab("tasks", "任务", Icons.Default.Tune),
    BottomTab("settings", "设置", Icons.Default.Settings),
)

class MainActivity : ComponentActivity() {
    private val pendingAlertId = MutableStateFlow<String?>(null)

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { /* Permission result handled automatically by NotificationManager */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        extractAlertIntent(intent)
        requestNotificationPermissionIfNeeded()

        val app = application as OmniMonitorApp
        setContent {
            OmniMonitorTheme {
                OmniMonitorRoot(
                    app = app,
                    pendingAlertFlow = pendingAlertId,
                    onConsumePendingAlert = { pendingAlertId.value = null },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        extractAlertIntent(intent)
    }

    private fun extractAlertIntent(intent: Intent?) {
        val alertId = intent?.getStringExtra(NotificationHelper.EXTRA_ALERT_ID)
        if (!alertId.isNullOrBlank()) {
            pendingAlertId.value = alertId
        }
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) {
                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }
}

@Composable
private fun OmniMonitorRoot(
    app: OmniMonitorApp,
    pendingAlertFlow: MutableStateFlow<String?>,
    onConsumePendingAlert: () -> Unit,
) {
    val navController = rememberNavController()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route
    val pendingAlertId by pendingAlertFlow.collectAsState()

    // Best-effort FCM registration on launch (no-op if google-services.json is not yet added)
    LaunchedEffect(Unit) {
        FcmTokenManager.syncTokenToServer(app, app.settingsStore, app.apiClient)
    }

    // Deep-link straight to Alert Detail when user taps an FCM notification
    LaunchedEffect(pendingAlertId) {
        val targetId = pendingAlertId ?: return@LaunchedEffect
        navController.navigate("alert/$targetId")
        onConsumePendingAlert()
    }

    val showBottomBar = TABS.any { it.route == currentRoute }

    Scaffold(
        bottomBar = {
            if (showBottomBar) {
                NavigationBar {
                    TABS.forEach { tab ->
                        NavigationBarItem(
                            selected = currentRoute == tab.route,
                            onClick = {
                                navController.navigate(tab.route) {
                                    popUpTo(navController.graph.findStartDestination().id) {
                                        saveState = true
                                    }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            },
                            icon = { Icon(tab.icon, contentDescription = tab.label) },
                            label = { Text(tab.label) },
                        )
                    }
                }
            }
        },
    ) { innerPadding ->
        NavHost(
            navController = navController,
            startDestination = "alerts",
            modifier = Modifier.padding(innerPadding),
        ) {
            composable("alerts") {
                AlertsScreen(
                    apiClient = app.apiClient,
                    onOpenAlert = { id -> navController.navigate("alert/$id") },
                )
            }
            composable("qdii") {
                QdiiBoardScreen(apiClient = app.apiClient)
            }
            composable("tasks") {
                TasksScreen(
                    apiClient = app.apiClient,
                    onCreateTask = { navController.navigate("task/new") },
                    onEditTask = { id -> navController.navigate("task/edit/$id") },
                )
            }
            composable("task/new") {
                TaskEditorScreen(
                    taskId = null,
                    apiClient = app.apiClient,
                    onBack = { navController.popBackStack() },
                    onSaved = { navController.popBackStack() },
                )
            }
            composable(
                route = "task/edit/{taskId}",
                arguments = listOf(navArgument("taskId") { type = NavType.StringType }),
            ) { entry ->
                val tid = entry.arguments?.getString("taskId")
                TaskEditorScreen(
                    taskId = tid,
                    apiClient = app.apiClient,
                    onBack = { navController.popBackStack() },
                    onSaved = { navController.popBackStack() },
                )
            }
            composable("settings") {
                SettingsScreen(
                    settingsStore = app.settingsStore,
                    apiClient = app.apiClient,
                    onOpenAlert = { id -> navController.navigate("alert/$id") },
                )
            }
            composable(
                route = "alert/{alertId}",
                arguments = listOf(navArgument("alertId") { type = NavType.StringType }),
            ) { entry ->
                val alertId = entry.arguments?.getString("alertId").orEmpty()
                AlertDetailScreen(
                    alertId = alertId,
                    apiClient = app.apiClient,
                    onBack = { navController.popBackStack() },
                )
            }
        }
    }
}
