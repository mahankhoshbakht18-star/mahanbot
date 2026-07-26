package ir.mahan.mahanbototp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class OtpParserTest {
    @Test fun `normalizes Persian Arabic and Latin digits`() {
        assertEquals("123 456 789", OtpParser.normalize("۱۲۳ ٤٥٦ 789"))
    }
    @Test fun `normalizes Arabic letters and spacing`() {
        assertEquals("کد تایید", OtpParser.normalize("كد\u200Cتاييد"))
    }
    @Test fun `extracts four six and eight digit otp`() {
        for (code in listOf("1234", "123456", "12345678")) {
            assertEquals(OtpParseResult.Found(code), OtpParser.extract("کد تأیید سامانه ازدواج $code"))
        }
    }
    @Test fun `rejects three and nine digits`() {
        assertTrue(OtpParser.extract("کد تایید سامانه ازدواج 123") is OtpParseResult.Irrelevant)
        assertTrue(OtpParser.extract("کد تایید سامانه ازدواج 123456789") is OtpParseResult.Irrelevant)
    }
    @Test fun `rejects irrelevant message`() {
        assertTrue(OtpParser.extract("خرید شما با شماره 123456 انجام شد") is OtpParseResult.Irrelevant)
    }
}
