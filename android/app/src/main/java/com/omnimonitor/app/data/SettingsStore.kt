package com.omnimonitor.app.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "omni_monitor_prefs")

data class AppSettings(
    val serverUrl: String = DEFAULT_SERVER_URL,
    val apiKey: String = "",
    val fcmToken: String = "",
) {
    companion object {
        // Works out-of-the-box on physical phones with `adb reverse tcp:8080 tcp:8080`;
        // change in Settings to LAN IP or VPS HTTPS URL for wireless/production use.
        const val DEFAULT_SERVER_URL = "http://127.0.0.1:8080"
    }
}

class SettingsStore(private val context: Context) {
    private val keyServerUrl = stringPreferencesKey("server_url")
    private val keyApiKey = stringPreferencesKey("api_key")
    private val keyFcmToken = stringPreferencesKey("fcm_token")

    val settingsFlow: Flow<AppSettings> = context.dataStore.data.map { prefs ->
        AppSettings(
            serverUrl = (prefs[keyServerUrl] ?: AppSettings.DEFAULT_SERVER_URL).trimEnd('/'),
            apiKey = prefs[keyApiKey] ?: "",
            fcmToken = prefs[keyFcmToken] ?: "",
        )
    }

    suspend fun current(): AppSettings = settingsFlow.first()

    suspend fun saveServerConfig(serverUrl: String, apiKey: String) {
        val cleaned = serverUrl.trim().trimEnd('/').ifBlank { AppSettings.DEFAULT_SERVER_URL }
        context.dataStore.edit { prefs ->
            prefs[keyServerUrl] = cleaned
            prefs[keyApiKey] = apiKey.trim()
        }
    }

    suspend fun saveFcmToken(token: String) {
        context.dataStore.edit { prefs ->
            prefs[keyFcmToken] = token.trim()
        }
    }
}
