package ir.mahanvip.smsforwardmanager.mahanbot

import android.content.BroadcastReceiver
import android.content.Context
import android.util.Log

/**
 * Hook this into the existing SMS BroadcastReceiver after Android has delivered
 * a new message to the app.
 *
 * localEventSeed should be a local stable identifier such as a hash of the PDU
 * bytes. The seed is hashed again and is never sent as-is. Do not pass the SMS
 * body, OTP, national ID, or full sender number as senderHint.
 */
object MahanBotSmsNotifyHook {
    private const val TAG = "MahanBotSmsNotify"

    fun onSmsArrived(
        context: Context,
        receiver: BroadcastReceiver,
        receivedAtMillis: Long,
        localEventSeed: String,
        senderHint: String? = null,
    ) {
        val pendingResult = receiver.goAsync()
        MahanBotNotifyClient.notifyArrival(
            context = context,
            receivedAtMillis = receivedAtMillis,
            localEventSeed = localEventSeed,
            senderHint = senderHint,
        ) { result ->
            when (result) {
                is MahanBotNotifyResult.Success -> {
                    Log.i(TAG, "Arrival signal delivered; duplicate=${result.duplicate}")
                }
                is MahanBotNotifyResult.Skipped -> {
                    Log.d(TAG, "Arrival signal skipped: ${result.reason}")
                }
                is MahanBotNotifyResult.Failure -> {
                    Log.w(TAG, "Arrival signal failed: ${result.message}")
                }
            }
            pendingResult.finish()
        }
    }
}
