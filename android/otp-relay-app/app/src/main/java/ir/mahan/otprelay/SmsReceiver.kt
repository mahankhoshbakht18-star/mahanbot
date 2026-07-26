package ir.mahan.otprelay

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony

class SmsReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
        val preferences = RelayPreferences(context)
        if (!preferences.isReady()) return

        val messages = Telephony.Sms.Intents.getMessagesFromIntent(intent)
        if (messages.isEmpty()) return
        val sender = messages.firstOrNull()?.originatingAddress.orEmpty()
        val body = messages.joinToString(separator = "") { it.messageBody.orEmpty() }
        if (!OtpExtractor.matchesKeyword(body, preferences.keyword)) return
        val otp = OtpExtractor.extract(body) ?: return
        val slotIndex = resolveSlotIndex(intent)
        val nationalId = preferences.nationalIdForSlot(slotIndex) ?: return
        val receivedAt = messages.minOfOrNull { it.timestampMillis } ?: System.currentTimeMillis()
        val messageId = DeviceIdentity.messageId(sender, body, receivedAt, slotIndex)

        PendingOtpStore(context).enqueue(
            PendingOtpStore.PendingOtp(
                messageId = messageId,
                nationalId = nationalId,
                otp = otp,
                receivedAtSeconds = receivedAt / 1000.0,
            ),
        )
        preferences.lastStatus = "پیامک دریافت شد؛ در حال ارسال امن به سرور"
        RelayForegroundService.wake(context)
    }

    private fun resolveSlotIndex(intent: Intent): Int? {
        val extras = intent.extras ?: return null
        val keys = listOf("slot", "slot_id", "phone", "simSlotIndex")
        keys.forEach { key ->
            if (extras.containsKey(key)) {
                val value = extras.get(key)
                when (value) {
                    is Int -> if (value >= 0) return value
                    is Long -> if (value >= 0) return value.toInt()
                }
            }
        }
        return null
    }
}
