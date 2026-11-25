package app.baihe.shell;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class PageTranslateTest {
    @Test
    public void onTranslatesToEnglishFromGoogleOnly() {
        assertTrue(PageTranslate.ON.contains("includedLanguages:'en'"));
        assertTrue(PageTranslate.ON.contains("https://translate.google.com/"));
        assertFalse(PageTranslate.ON.contains("http://"));
    }

    @Test
    public void offRestoresTheOriginalAndClearsTheCookie() {
        assertTrue(PageTranslate.OFF.contains("pick('')"));
        assertTrue(PageTranslate.OFF.contains("googtrans=;expires="));
        assertTrue(PageTranslate.OFF.contains("__baiheTranslate=false"));
    }
}
