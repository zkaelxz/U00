package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public class PageReportTest {
    private static JSONObject page(int regions) throws Exception {
        JSONArray list = new JSONArray();
        for (int i = 0; i < regions; i++) list.put(new JSONObject().put("source_text", "字").put("translated_text", "x"));
        return new JSONObject().put("regions", list).put("notes", new JSONArray().put("note"))
                .put("width", 800).put("height", 1200).put("typeset_image", "data:image/jpeg;base64,AAAA");
    }

    @Test
    public void metaKeepsRegionsAndDropsTheRedrawnPicture() throws Exception {
        JSONObject meta = new JSONObject(PageReport.meta(page(2), "https://cdn.example.com/a/1.jpg?token=secret",
                "https://example.com/ch1?x=1", "Chapter 1"));
        assertEquals(2, meta.getJSONArray("regions").length());
        assertEquals("https://cdn.example.com/a/1.jpg", meta.getString("image_url"));
        assertEquals("https://example.com/ch1", meta.getString("page_url"));
        assertEquals(800, meta.getInt("width"));
        assertFalse(meta.has("typeset_image"));
    }

    @Test
    public void metaCutsAPageWithTooManyBoxes() throws Exception {
        JSONObject meta = new JSONObject(PageReport.meta(page(PageReport.MAX_REGIONS + 50), "", "", ""));
        assertEquals(PageReport.MAX_REGIONS, meta.getJSONArray("regions").length());
    }

    @Test
    public void bodyCarriesTheNoteAndPictureAndSurvivesMissingMeta() throws Exception {
        JSONObject body = PageReport.body(null, new byte[] {1, 2, 3}, "  bad box  ");
        assertEquals("bad box", body.getString("note"));
        assertTrue(body.getString("image").startsWith("data:image/jpeg;base64,"));
        String longNote = new String(new char[PageReport.MAX_NOTE_CHARS + 5]).replace('\0', 'a');
        assertEquals(PageReport.MAX_NOTE_CHARS, PageReport.body("{}", new byte[] {1}, longNote).getString("note").length());
    }
}
