package ir.mahan.otprelay

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

class PendingOtpStore(context: Context) {
    data class PendingOtp(
        val messageId: String,
        val nationalId: String,
        val otp: String,
        val receivedAtSeconds: Double,
        val attempts: Int = 0,
        val nextAttemptAtMillis: Long = 0L,
    )

    private val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    @Synchronized
    fun enqueue(item: PendingOtp) {
        val current = readAll().toMutableList()
        if (current.none { it.messageId == item.messageId }) {
            current += item
            writeAll(current.takeLast(MAX_QUEUE_SIZE))
        }
    }

    @Synchronized
    fun due(now: Long = System.currentTimeMillis()): List<PendingOtp> = readAll()
        .filter { it.nextAttemptAtMillis <= now }

    @Synchronized
    fun remove(messageId: String) {
        writeAll(readAll().filterNot { it.messageId == messageId })
    }

    @Synchronized
    fun reschedule(item: PendingOtp, retryAfterMillis: Long) {
        val updated = readAll().map {
            if (it.messageId == item.messageId) {
                it.copy(
                    attempts = it.attempts + 1,
                    nextAttemptAtMillis = System.currentTimeMillis() + retryAfterMillis,
                )
            } else {
                it
            }
        }
        writeAll(updated)
    }

    @Synchronized
    fun size(): Int = readAll().size

    private fun readAll(): List<PendingOtp> {
        val raw = prefs.getString(KEY_QUEUE, "[]").orEmpty()
        return runCatching {
            val array = JSONArray(raw)
            buildList {
                for (index in 0 until array.length()) {
                    val item = array.getJSONObject(index)
                    add(
                        PendingOtp(
                            messageId = item.getString("message_id"),
                            nationalId = item.getString("national_id"),
                            otp = item.getString("otp"),
                            receivedAtSeconds = item.getDouble("received_at"),
                            attempts = item.optInt("attempts", 0),
                            nextAttemptAtMillis = item.optLong("next_attempt_at", 0L),
                        ),
                    )
                }
            }
        }.getOrDefault(emptyList())
    }

    private fun writeAll(items: List<PendingOtp>) {
        val array = JSONArray()
        items.forEach { item ->
            array.put(
                JSONObject()
                    .put("message_id", item.messageId)
                    .put("national_id", item.nationalId)
                    .put("otp", item.otp)
                    .put("received_at", item.receivedAtSeconds)
                    .put("attempts", item.attempts)
                    .put("next_attempt_at", item.nextAttemptAtMillis),
            )
        }
        prefs.edit().putString(KEY_QUEUE, array.toString()).commit()
    }

    companion object {
        private const val PREFS = "mahan_otp_pending_queue"
        private const val KEY_QUEUE = "queue"
        private const val MAX_QUEUE_SIZE = 30
    }
}
