package ir.mahan.mahanbototp

data class OtpCandidate(val value: String, val position: Int)
sealed interface OtpParseResult {
    data class Found(val otp: String) : OtpParseResult
    data class Ambiguous(val maskedCandidates: List<String>) : OtpParseResult
    data object Irrelevant : OtpParseResult
}

object OtpParser {
    private val phrases = listOf("کد تایید مربوط به سامانه ازدواج", "سامانه ازدواج", "کد تایید", "کد تأیید")
    private val number = Regex("(?<!\\d)\\d{4,8}(?!\\d)")

    fun normalize(raw: String): String = raw
        .replace(Regex("[\\u0000-\\u001F\\u007F]"), " ")
        .replace('ي', 'ی').replace('ك', 'ک').replace('\u200C', ' ')
        .map { c -> when (c) {
            in '۰'..'۹' -> ('0'.code + c.code - '۰'.code).toChar()
            in '٠'..'٩' -> ('0'.code + c.code - '٠'.code).toChar()
            else -> c
        }}.joinToString("").replace(Regex("\\s+"), " ").trim()

    fun extract(raw: String, configuredPhrases: List<String> = phrases): OtpParseResult {
        val text = normalize(raw)
        val phrasePositions = configuredPhrases.map { text.indexOf(normalize(it)) }.filter { it >= 0 }
        if (phrasePositions.isEmpty()) return OtpParseResult.Irrelevant
        val candidates = number.findAll(text).map { OtpCandidate(it.value, it.range.first) }.toList()
        if (candidates.isEmpty()) return OtpParseResult.Irrelevant
        val ranked = candidates.sortedBy { candidate -> phrasePositions.minOf { kotlin.math.abs(it - candidate.position) } }
        if (ranked.size > 1) {
            val firstDistance = phrasePositions.minOf { kotlin.math.abs(it - ranked[0].position) }
            val secondDistance = phrasePositions.minOf { kotlin.math.abs(it - ranked[1].position) }
            if (firstDistance == secondDistance) return OtpParseResult.Ambiguous(ranked.map { "*".repeat(it.value.length) })
        }
        return OtpParseResult.Found(ranked.first().value)
    }
}
