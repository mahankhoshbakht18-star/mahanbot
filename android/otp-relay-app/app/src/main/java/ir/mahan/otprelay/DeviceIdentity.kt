package ir.mahan.otprelay

import android.content.Context
import java.security.MessageDigest
import java.util.UUID

object DeviceIdentity {
    private const val PREFS = "mahan_otp_relay_identity"
    private const val KEY_DEVICE_ID = "device_id"

    fun getOrCreate(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        prefs.getString(KEY_DEVICE_ID, null)?.takeIf { it.isNotBlank() }?.let { return it }
        val created = "android-${UUID.randomUUID()}"
        prefs.edit().putString(KEY_DEVICE_ID, created).commit()
        return created
    }

    fun messageId(sender: String, body: String, receivedAt: Long, slotIndex: Int?): String {
        val source = "$sender|$body|$receivedAt|${slotIndex ?: -1}"
        val bytes = MessageDigest.getInstance("SHA-256").digest(source.toByteArray(Charsets.UTF_8))
        return "mahan-${bytes.joinToString("") { "%02x".format(it) }.take(32)}"
    }
}
