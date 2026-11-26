package com.omnimonitor.app

import android.app.Application
import com.omnimonitor.app.data.OmniApiClient
import com.omnimonitor.app.data.SettingsStore
import com.omnimonitor.app.fcm.NotificationHelper

class OmniMonitorApp : Application() {
    lateinit var settingsStore: SettingsStore
        private set
    lateinit var apiClient: OmniApiClient
        private set

    override fun onCreate() {
        super.onCreate()
        NotificationHelper.ensureChannels(this)
        settingsStore = SettingsStore(this)
        apiClient = OmniApiClient(settingsStore)
    }
}
