package com.omnimonitor.app.fcm

import android.content.Context
import android.os.Build
import android.util.Log
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import com.omnimonitor.app.data.OmniApiClient
import com.omnimonitor.app.data.SettingsStore
import kotlinx.coroutines.tasks.await

sealed interface FcmSyncResult {
    data class Registered(val token: String) : FcmSyncResult
    data object FirebaseNotConfigured : FcmSyncResult
    data class Failed(val message: String) : FcmSyncResult
}

object FcmTokenManager {
    private const val TAG = "FcmTokenManager"

    fun isFirebaseConfigured(context: Context): Boolean {
        return try {
            FirebaseApp.getApps(context).isNotEmpty()
        } catch (_: Throwable) {
            false
        }
    }

    suspend fun syncTokenToServer(
        context: Context,
        settingsStore: SettingsStore,
        apiClient: OmniApiClient,
    ): FcmSyncResult {
        if (!isFirebaseConfigured(context)) {
            return FcmSyncResult.FirebaseNotConfigured
        }
        return try {
            val token = FirebaseMessaging.getInstance().token.await()
            settingsStore.saveFcmToken(token)
            val deviceName = "${Build.MANUFACTURER} ${Build.MODEL}".trim()
            apiClient.registerFcmToken(token, deviceName)
            Log.i(TAG, "Registered FCM token for $deviceName")
            FcmSyncResult.Registered(token)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to sync FCM token: ${e.message}")
            FcmSyncResult.Failed(e.message ?: "FCM 注册失败")
        }
    }
}
