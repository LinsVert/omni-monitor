package com.omnimonitor.app.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

class OmniApiClient(private val settingsStore: SettingsStore) {
    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        coerceInputValues = true
        encodeDefaults = true
    }

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .build()

    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()

    private suspend fun buildUrl(path: String, query: Map<String, String?> = emptyMap()): String {
        val base = settingsStore.current().serverUrl.trimEnd('/')
        val parsed = "$base$path".toHttpUrlOrNull()
            ?: throw IllegalArgumentException("无效的服务器地址: $base")
        val builder = parsed.newBuilder()
        query.forEach { (k, v) ->
            if (!v.isNullOrBlank()) builder.addQueryParameter(k, v)
        }
        return builder.build().toString()
    }

    private suspend fun execute(requestBuilder: Request.Builder): String = withContext(Dispatchers.IO) {
        val apiKey = settingsStore.current().apiKey
        if (apiKey.isNotBlank()) {
            requestBuilder.header("X-API-Key", apiKey)
        }
        httpClient.newCall(requestBuilder.build()).execute().use { resp ->
            val body = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                throw IOException("HTTP ${resp.code}: ${body.take(200)}")
            }
            body
        }
    }

    suspend fun getHealth(): HealthResponse {
        val url = buildUrl("/health")
        val text = execute(Request.Builder().url(url).get())
        return json.decodeFromString(text)
    }

    suspend fun listAlerts(unreadOnly: Boolean = false, limit: Int = 60): List<AlertSummary> {
        val url = buildUrl(
            "/api/alerts",
            mapOf("unread_only" to unreadOnly.toString(), "limit" to limit.toString()),
        )
        val text = execute(Request.Builder().url(url).get())
        return json.decodeFromString(text)
    }

    suspend fun getAlertDetail(alertId: String): AlertDetail {
        val url = buildUrl("/api/alerts/$alertId")
        val text = execute(Request.Builder().url(url).get())
        return json.decodeFromString(text)
    }

    suspend fun markAlertRead(alertId: String) {
        val url = buildUrl("/api/alerts/$alertId/read")
        execute(Request.Builder().url(url).post("{}".toRequestBody(jsonMediaType)))
    }

    suspend fun listTasks(): List<TaskDto> {
        val url = buildUrl("/api/tasks")
        val text = execute(Request.Builder().url(url).get())
        return json.decodeFromString(text)
    }

    suspend fun getTask(taskId: String): TaskDto {
        val url = buildUrl("/api/tasks/$taskId")
        val text = execute(Request.Builder().url(url).get())
        return json.decodeFromString(text)
    }

    suspend fun createTask(body: TaskCreateBody): TaskDto {
        val url = buildUrl("/api/tasks")
        val payload = json.encodeToString(body)
        val text = execute(Request.Builder().url(url).post(payload.toRequestBody(jsonMediaType)))
        return json.decodeFromString(text)
    }

    suspend fun updateTask(taskId: String, body: TaskUpdateBody): TaskDto {
        val url = buildUrl("/api/tasks/$taskId")
        val payload = json.encodeToString(body)
        val text = execute(Request.Builder().url(url).patch(payload.toRequestBody(jsonMediaType)))
        return json.decodeFromString(text)
    }

    suspend fun deleteTask(taskId: String) {
        val url = buildUrl("/api/tasks/$taskId")
        execute(Request.Builder().url(url).delete())
    }

    suspend fun setTaskEnabled(taskId: String, enabled: Boolean) {
        val url = buildUrl("/api/tasks/$taskId")
        val payload = json.encodeToString(TaskPatchBody(enabled = enabled))
        execute(Request.Builder().url(url).patch(payload.toRequestBody(jsonMediaType)))
    }

    suspend fun runTaskNow(taskId: String): TaskRunResponse {
        val url = buildUrl("/api/tasks/$taskId/run")
        val text = execute(Request.Builder().url(url).post("{}".toRequestBody(jsonMediaType)))
        return json.decodeFromString(text)
    }

    suspend fun getQdiiBoard(
        taskId: String = "qdii_all_limits",
        query: String? = null,
        category: String? = null,
        sortBy: String? = "daily_limit",
        limit: Int = 200,
    ): LatestQdiiResponse {
        val url = buildUrl(
            "/api/tasks/$taskId/latest",
            mapOf(
                "q" to query,
                "category" to category,
                "sort_by" to sortBy,
                "limit" to limit.toString(),
            ),
        )
        val text = execute(Request.Builder().url(url).get())
        return json.decodeFromString(text)
    }

    suspend fun registerFcmToken(token: String, deviceName: String) {
        val url = buildUrl("/api/devices/fcm")
        val payload = json.encodeToString(
            DeviceRegisterBody(token = token, platform = "android", deviceName = deviceName),
        )
        execute(Request.Builder().url(url).post(payload.toRequestBody(jsonMediaType)))
    }

    suspend fun sendTestPush(): AlertDetail {
        val url = buildUrl("/api/devices/test-push")
        val payload = json.encodeToString(TestPushBody())
        val text = execute(Request.Builder().url(url).post(payload.toRequestBody(jsonMediaType)))
        return json.decodeFromString(text)
    }
}
