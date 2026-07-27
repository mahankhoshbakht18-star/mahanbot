package ir.mahan.otprelay

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

object RelayApi {
    data class Result(
        val success: Boolean,
        val retryable: Boolean,
        val code: Int,
        val waiting: Boolean? = null,
        val detail: String = "",
    )

    fun checkStatus(deviceId: String, nationalId: String): Result {
        // The lightweight FastAPI health endpoint does not require a national ID.
        // Keep the argument in the public contract so the current UI needs no migration.
        nationalId.length
        return request(
            url = URL(RelayPreferences.STATUS_ENDPOINT),
            method = "GET",
            deviceId = deviceId,
            idempotencyKey = null,
            body = null,
        )
    }

    fun sendOtp(deviceId: String, item: PendingOtpStore.PendingOtp): Result {
        val payload = JSONObject()
            .put("nid", item.nationalId)
            .put("code", item.otp)
            .toString()

        return request(
            url = URL(RelayPreferences.OTP_ENDPOINT),
            method = "POST",
            deviceId = deviceId,
            idempotencyKey = item.messageId,
            body = payload,
        )
    }

    private fun request(
        url: URL,
        method: String,
        deviceId: String,
        idempotencyKey: String?,
        body: String?,
    ): Result {
        var connection: HttpURLConnection? = null
        return try {
            connection = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = method
                connectTimeout = 15_000
                readTimeout = 20_000
                useCaches = false
                doInput = true
                setRequestProperty("Accept", "application/json")
                setRequestProperty("Cache-Control", "no-store")
                setRequestProperty("User-Agent", "MahanOtpRelay/2.8.1")
                setRequestProperty("X-DEVICE-ID", deviceId)
                if (!idempotencyKey.isNullOrBlank()) {
                    setRequestProperty("Idempotency-Key", idempotencyKey)
                }
                if (body != null) {
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json; charset=utf-8")
                }
            }

            if (body != null) {
                connection.outputStream.use { output ->
                    output.write(body.toByteArray(Charsets.UTF_8))
                }
            }

            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val responseBody = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            val json = runCatching { JSONObject(responseBody) }.getOrNull()

            Result(
                success = code in 200..299,
                retryable = code == 408 || code == 425 || code == 429 || code >= 500,
                code = code,
                waiting = json?.takeIf { it.has("waiting") }?.optBoolean("waiting"),
                detail = json?.optString("detail").orEmpty(),
            )
        } catch (error: IOException) {
            Result(false, true, -1, detail = error.javaClass.simpleName)
        } catch (error: Exception) {
            Result(false, false, -2, detail = error.javaClass.simpleName)
        } finally {
            connection?.disconnect()
        }
    }
}
