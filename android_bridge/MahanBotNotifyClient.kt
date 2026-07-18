package ir.mahanvip.smsforwardmanager.mahanbot

import android.content.Context
import android.os.Handler
import android.os.Looper
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.util.concurrent.Executors

sealed interface MahanBotNotifyResult {
    data class Success(val accepted: Boolean, val duplicate: Boolean) : MahanBotNotifyResult
    data class Skipped(val reason: String) : MahanBotNotifyResult
    data class Failure(val message: String, val httpCode: Int? = null) : MahanBotNotifyResult
}

/**
 * Sends an arrival-only signal to MahanBot.
 *
 * No SMS body, OTP, PIN, national ID, or phone number is accepted by this API.
 */
object MahanBotNotifyClient {
    private val executor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "mahanbot-sms-notify").apply { isDaemon = true }
    }
    private val mainHandler = Handler(Looper.getMainLooper())

    fun notifyArrival(
        context: Context,
        receivedAtMillis: Long,
        localEventSeed: String,
        senderHint: String? = null,
        callback: (MahanBotNotifyResult) -> Unit = {},
    ) {
        val appContext = context.applicationContext
        val config = MahanBotBridgeSettings.load(appContext)
        if (!config.isUsable()) {
            deliver(callback, MahanBotNotifyResult.Skipped("bridge_disabled_or_incomplete"))
            return
        }

        val eventId = opaqueEventId(config.deviceId, receivedAtMillis, localEventSeed)
        executor.execute {
            val result = postJson(
                url = "${config.normalizedBaseUrl()}/api/v1/sms/notify",
                config = config,
                payload = JSONObject()
                    .put("device_id", config.deviceId)
                    .put("message_id", eventId)
                    .put("received_at", receivedAtMillis / 1000.0)
                    .put("source", "android")
                    .apply {
                        val safeHint = senderHint.orEmpty().trim().take(32)
                        if (safeHint.isNotBlank()) put("sender_hint", safeHint)
                    },
            )
            deliver(callback, result)
        }
    }

    fun heartbeat(
        context: Context,
        callback: (MahanBotNotifyResult) -> Unit = {},
    ) {
        val appContext = context.applicationContext
        val config = MahanBotBridgeSettings.load(appContext)
        if (!config.isUsable()) {
            deliver(callback, MahanBotNotifyResult.Skipped("bridge_disabled_or_incomplete"))
            return
        }

        executor.execute {
            val result = postJson(
                url = "${config.normalizedBaseUrl()}/api/v1/sms/notify/heartbeat",
                config = config,
                payload = JSONObject()
                    .put("device_id", config.deviceId)
                    .put("sent_at", System.currentTimeMillis() / 1000.0),
            )
            deliver(callback, result)
        }
    }

    private fun deliver(
        callback: (MahanBotNotifyResult) -> Unit,
        result: MahanBotNotifyResult,
    ) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            callback(result)
        } else {
            mainHandler.post { callback(result) }
        }
    }

    private fun postJson(
        url: String,
        config: MahanBotBridgeConfig,
        payload: JSONObject,
    ): MahanBotNotifyResult {
        var connection: HttpURLConnection? = null
        return try {
            connection = (URL(url).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 8_000
                readTimeout = 8_000
                doOutput = true
                useCaches = false
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
                setRequestProperty("Accept", "application/json")
                setRequestProperty("X-DEVICE-ID", config.deviceId)
                setRequestProperty("X-DEVICE-KEY", config.deviceKey)
            }

            val bytes = payload.toString().toByteArray(StandardCharsets.UTF_8)
            connection.setFixedLengthStreamingMode(bytes.size)
            connection.outputStream.use { it.write(bytes) }

            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val body = stream?.use { input ->
                BufferedReader(InputStreamReader(input, StandardCharsets.UTF_8)).readText()
            }.orEmpty()

            if (code !in 200..299) {
                MahanBotNotifyResult.Failure(
                    message = sanitizeError(body.ifBlank { "HTTP $code" }),
                    httpCode = code,
                )
            } else {
                val response = runCatching { JSONObject(body) }.getOrNull()
                MahanBotNotifyResult.Success(
                    accepted = response?.optBoolean("accepted", true) ?: true,
                    duplicate = response?.optBoolean("duplicate", false) ?: false,
                )
            }
        } catch (error: Exception) {
            MahanBotNotifyResult.Failure(error.javaClass.simpleName + ": " + error.message.orEmpty().take(160))
        } finally {
            connection?.disconnect()
        }
    }

    private fun opaqueEventId(deviceId: String, receivedAtMillis: Long, localEventSeed: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val input = "$deviceId|$receivedAtMillis|${localEventSeed.take(256)}"
        return digest.digest(input.toByteArray(StandardCharsets.UTF_8))
            .joinToString(separator = "") { byte -> "%02x".format(byte) }
    }

    private fun sanitizeError(value: String): String =
        value.replace(Regex("[\\r\\n\\t]+"), " ").trim().take(240)
}
