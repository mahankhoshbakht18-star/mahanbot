package ir.mahanvip.smsforwardmanager.mahanbot

import android.content.Context
import java.util.UUID

/**
 * Local settings for the MahanBot arrival-notification bridge.
 *
 * The device key is stored in private SharedPreferences. Production builds
 * should migrate it to Android Keystore-backed encrypted storage.
 */
data class MahanBotBridgeConfig(
    val enabled: Boolean,
    val baseUrl: String,
    val deviceId: String,
    val deviceKey: String,
) {
    fun normalizedBaseUrl(): String = baseUrl.trim().trimEnd('/')

    fun isUsable(): Boolean =
        enabled &&
            normalizedBaseUrl().startsWith("http") &&
            deviceId.isNotBlank() &&
            deviceKey.length >= 24
}

object MahanBotBridgeSettings {
    private const val PREFS = "mahanbot_sms_notify_bridge"
    private const val KEY_ENABLED = "enabled"
    private const val KEY_BASE_URL = "base_url"
    private const val KEY_DEVICE_ID = "device_id"
    private const val KEY_DEVICE_KEY = "device_key"

    fun load(context: Context): MahanBotBridgeConfig {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        var deviceId = prefs.getString(KEY_DEVICE_ID, null).orEmpty().trim()
        if (deviceId.isBlank()) {
            deviceId = "android-${UUID.randomUUID()}"
            prefs.edit().putString(KEY_DEVICE_ID, deviceId).apply()
        }
        return MahanBotBridgeConfig(
            enabled = prefs.getBoolean(KEY_ENABLED, false),
            baseUrl = prefs.getString(KEY_BASE_URL, "").orEmpty(),
            deviceId = deviceId,
            deviceKey = prefs.getString(KEY_DEVICE_KEY, "").orEmpty(),
        )
    }

    fun save(context: Context, config: MahanBotBridgeConfig) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_ENABLED, config.enabled)
            .putString(KEY_BASE_URL, config.normalizedBaseUrl())
            .putString(KEY_DEVICE_ID, config.deviceId.trim())
            .putString(KEY_DEVICE_KEY, config.deviceKey.trim())
            .apply()
    }

    fun disable(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_ENABLED, false)
            .apply()
    }
}
