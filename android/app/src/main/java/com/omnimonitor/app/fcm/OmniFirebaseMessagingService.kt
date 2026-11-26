package com.omnimonitor.app.fcm

import android.os.Build
import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.omnimonitor.app.OmniMonitorApp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class OmniFirebaseMessagingService : FirebaseMessagingService() {
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        Log.i(TAG, "Received refreshed FCM token")
        val app = application as? OmniMonitorApp ?: return
        serviceScope.launch {
            try {
                app.settingsStore.saveFcmToken(token)
                val deviceName = "${Build.MANUFACTURER} ${Build.MODEL}".trim()
                app.apiClient.registerFcmToken(token, deviceName)
            } catch (e: Exception) {
                Log.w(TAG, "Deferred FCM token upload failed: ${e.message}")
            }
        }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        super.onMessageReceived(message)
        val data = message.data
        val alertId = data[NotificationHelper.EXTRA_ALERT_ID].orEmpty()
        val title = data["title"] ?: message.notification?.title ?: "Omni Monitor 提醒"
        val summary = data["summary"] ?: message.notification?.body ?: "点击查看推送详情"
        val severity = data["severity"] ?: "normal"

        NotificationHelper.showAlertNotification(
            context = this,
            alertId = alertId.ifBlank { System.currentTimeMillis().toString() },
            title = title,
            summary = summary,
            severity = severity,
        )
    }

    companion object {
        private const val TAG = "OmniFcmService"
    }
}
