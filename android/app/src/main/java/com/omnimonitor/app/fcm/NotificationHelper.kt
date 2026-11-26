package com.omnimonitor.app.fcm

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.omnimonitor.app.MainActivity
import com.omnimonitor.app.R

object NotificationHelper {
    const val CHANNEL_DEFAULT = "omni_alerts_default"
    const val CHANNEL_HIGH = "omni_alerts_high"
    const val EXTRA_ALERT_ID = "alert_id"

    fun ensureChannels(context: Context) {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        val defaultChannel = NotificationChannel(
            CHANNEL_DEFAULT,
            "常规监控推送",
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = "价格变动、QDII 额度调整等常规监控提醒"
        }

        val highChannel = NotificationChannel(
            CHANNEL_HIGH,
            "高优先级紧急告警",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "自选标的触发、QDII 重新开门、数据源异常告警"
            enableVibration(true)
        }

        manager.createNotificationChannel(defaultChannel)
        manager.createNotificationChannel(highChannel)
    }

    fun showAlertNotification(
        context: Context,
        alertId: String,
        title: String,
        summary: String,
        severity: String,
    ) {
        ensureChannels(context)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) return
        }

        val intent = Intent(context, MainActivity::class.java).apply {
            action = "OPEN_ALERT_DETAIL"
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra(EXTRA_ALERT_ID, alertId)
        }
        val requestCode = alertId.hashCode()
        val pendingIntent = PendingIntent.getActivity(
            context,
            requestCode,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val isHigh = severity.equals("high", ignoreCase = true)
        val channelId = if (isHigh) CHANNEL_HIGH else CHANNEL_DEFAULT
        val priority = if (isHigh) NotificationCompat.PRIORITY_HIGH else NotificationCompat.PRIORITY_DEFAULT

        val notification = NotificationCompat.Builder(context, channelId)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(summary)
            .setStyle(NotificationCompat.BigTextStyle().bigText(summary))
            .setPriority(priority)
            .setAutoCancel(true)
            .setContentIntent(pendingIntent)
            .build()

        NotificationManagerCompat.from(context).notify(requestCode, notification)
    }
}
