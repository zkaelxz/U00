package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public class ProblemReportTest {
    @Test
    public void bodyMarksTheKindDropsTheQueryAndCutsLongText() throws Exception {
        StringBuilder longCrash = new StringBuilder();
        for (int i = 0; i < ProblemReport.MAX_CRASH_CHARS + 50; i++) longCrash.append('x');
        JSONObject body = ProblemReport.body("  froze  ", "Grab", "https://example.com/ch1?token=abc",
                ProblemReport.phoneInfo(12, 34, "Model X"), longCrash.toString(), null);
        assertEquals("app", body.getString("kind"));
        assertEquals("froze", body.getString("note"));
        assertEquals("https://example.com/ch1", body.getString("page_url"));
        assertEquals("Build 12, Android SDK 34, Model X", body.getString("phone"));
        assertEquals(ProblemReport.MAX_CRASH_CHARS, body.getString("crash").length());
        assertFalse(body.has("image"));
    }

    @Test
    public void aScreenshotIsOnlyAddedWhenGiven() throws Exception {
        JSONObject body = ProblemReport.body("", "Grab", "", "", "", new byte[] {1, 2, 3});
        assertTrue(body.getString("image").startsWith("data:image/jpeg;base64,"));
    }
}
