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

    private fun normalize(value: String): String = toLatinDigits(value)
        .replace('ي', 'ی')
        .replace('ك', 'ک')
        .replace("‌", " ")
        .lowercase()
        .replace(Regex("\\s+"), " ")
        .trim()

    private fun toLatinDigits(value: String): String = buildString(value.length) {
        value.forEach { char ->
            append(
                when (char) {
                    in '۰'..'۹' -> '0' + (char.code - '۰'.code)
                    in '٠'..'٩' -> '0' + (char.code - '٠'.code)
                    else -> char
                },
            )
        }
    }
}
