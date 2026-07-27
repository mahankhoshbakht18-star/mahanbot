package ir.mahan.otprelay

import android.content.Context

class RelayPreferences(context: Context) {
    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    var enabled: Boolean
        get() = prefs.getBoolean(KEY_ENABLED, false)
        set(value) = prefs.edit().putBoolean(KEY_ENABLED, value).apply()

    var consentAccepted: Boolean
        get() = prefs.getBoolean(KEY_CONSENT, false)
        set(value) = prefs.edit().putBoolean(KEY_CONSENT, value).apply()

    var nationalIdSim1: String
        get() = prefs.getString(KEY_NID_SIM1, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_NID_SIM1, normalizeDigits(value)).apply()

    var nationalIdSim2: String
        get() = prefs.getString(KEY_NID_SIM2, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_NID_SIM2, normalizeDigits(value)).apply()

    var keyword: String
        get() = prefs.getString(KEY_KEYWORD, DEFAULT_KEYWORD).orEmpty()
        set(value) = prefs.edit().putString(KEY_KEYWORD, value.trim()).apply()

    var lastStatus: String
        get() = prefs.getString(KEY_LAST_STATUS, "تنظیمات را تکمیل کنید").orEmpty()
        set(value) = prefs.edit()
            .putString(KEY_LAST_STATUS, value)
            .putLong(KEY_LAST_STATUS_AT, System.currentTimeMillis())
            .apply()

    val lastStatusAt: Long
        get() = prefs.getLong(KEY_LAST_STATUS_AT, 0L)

    fun nationalIdForSlot(slotIndex: Int?): String? {
        val sim1 = validNationalId(nationalIdSim1)
        val sim2 = validNationalId(nationalIdSim2)
        return when (slotIndex) {
            1 -> sim2 ?: sim1
            else -> sim1 ?: sim2
        }
    }

    fun configuredNationalIds(): List<String> = listOf(nationalIdSim1, nationalIdSim2)
        .mapNotNull(::validNationalId)
        .distinct()

    fun isReady(): Boolean = enabled && consentAccepted && configuredNationalIds().isNotEmpty()

    companion object {
        const val SERVER_BASE_URL = "https://otp.mahanvip.ir"
        const val OTP_ENDPOINT = "$SERVER_BASE_URL/receive_sms"
        const val STATUS_ENDPOINT = "$SERVER_BASE_URL/health"
        const val DEFAULT_KEYWORD = "سامانه ازدواج"

        private const val PREFS_NAME = "mahan_otp_relay"
        private const val KEY_ENABLED = "enabled"
        private const val KEY_CONSENT = "consent"
        private const val KEY_NID_SIM1 = "national_id_sim1"
        private const val KEY_NID_SIM2 = "national_id_sim2"
        private const val KEY_KEYWORD = "keyword"
        private const val KEY_LAST_STATUS = "last_status"
        private const val KEY_LAST_STATUS_AT = "last_status_at"

        fun normalizeDigits(value: String): String = buildString(value.length) {
            value.forEach { char ->
                append(
                    when (char) {
                        in '۰'..'۹' -> '0' + (char.code - '۰'.code)
                        in '٠'..'٩' -> '0' + (char.code - '٠'.code)
                        else -> char
                    },
                )
            }
        }.filter(Char::isDigit)

        fun validNationalId(value: String): String? = normalizeDigits(value)
            .takeIf { it.length == 10 }
    }
}
