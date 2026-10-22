package ir.mahan.otprelay

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OtpExtractorTest {
    @Test
    fun extractsPersianOtp() {
        assertEquals("697945", OtpExtractor.extract("کد تایید مربوط به سامانه ازدواج: ۶۹۷۹۴۵"))
    }

    @Test
    fun keywordMatchingNormalizesPersianCharacters() {
        assertTrue(OtpExtractor.matchesKeyword("سامانه ازدواج: 123456", "سامانه ازدواج"))
        assertFalse(OtpExtractor.matchesKeyword("پیام تبلیغاتی 123456", "سامانه ازدواج"))
    }
}
