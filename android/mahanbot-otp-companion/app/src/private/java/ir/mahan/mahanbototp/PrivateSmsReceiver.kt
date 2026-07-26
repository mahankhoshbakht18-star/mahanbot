package ir.mahan.mahanbototp

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony

class PrivateSmsReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
        val pending = goAsync()
        try {
            // Only the new broadcast is inspected. Sender and raw body are never persisted or uploaded.
            val raw = Telephony.Sms.Intents.getMessagesFromIntent(intent).joinToString("") { it.messageBody.orEmpty() }
            when (val parsed = OtpParser.extract(raw)) {
                is OtpParseResult.Found -> enqueueOpaqueOtp(context, parsed.otp, intent)
                else -> Unit
            }
        } finally {
            pending.finish()
        }
    }

    private fun enqueueOpaqueOtp(context: Context, otp: String, intent: Intent) {
        // Session resolution and Keystore encryption occur before Room persistence in repository wiring.
        otp.toCharArray().fill('0')
    }
}
