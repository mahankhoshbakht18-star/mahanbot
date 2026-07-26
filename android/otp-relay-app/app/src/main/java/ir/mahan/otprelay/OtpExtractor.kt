package ir.mahan.otprelay

object OtpExtractor {
    private val otpPattern = Regex("(?<!\\d)[0-9۰-۹٠-٩]{4,8}(?!\\d)")

    fun extract(message: String): String? = otpPattern.find(message)?.value
        ?.let(RelayPreferences::normalizeDigits)
        ?.takeIf { it.length in 4..8 }

    fun matchesKeyword(message: String, keyword: String): Boolean {
        if (keyword.isBlank()) return true
        return normalize(message).contains(normalize(keyword))
    }

    private fun normalize(value: String): String = RelayPreferences.normalizeDigits(value)
        .replace('ي', 'ی')
        .replace('ك', 'ک')
        .replace("‌", " ")
        .lowercase()
        .replace(Regex("\\s+"), " ")
        .trim()
}
