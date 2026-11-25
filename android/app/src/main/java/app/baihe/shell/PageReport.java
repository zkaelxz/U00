package app.baihe.shell;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.Base64;

/**
 * What a "Send a report" on a Quick read page carries. The PC's answer for a page (its text
 * regions and translations) is kept next to the redrawn picture while the reader is open, so a
 * bad page can be reproduced without asking for the chapter again.
 */
final class PageReport {
    /** A page with more boxes than this is cut, so one report stays well inside the PC's size limit. */
    static final int MAX_REGIONS = 200;
    static final int MAX_NOTE_CHARS = 1000;

    private PageReport() {}

    /** The sidecar text saved beside a page picture; the redrawn image itself is never copied into it. */
    static String meta(JSONObject page, String imageUrl, String pageUrl, String chapter) {
        JSONObject out = new JSONObject();
        try {
            JSONArray regions = page.optJSONArray("regions");
            JSONArray kept = new JSONArray();
            for (int i = 0; regions != null && i < Math.min(regions.length(), MAX_REGIONS); i++) {
                kept.put(regions.get(i));
            }
            out.put("regions", kept);
            out.put("notes", page.optJSONArray("notes") == null ? new JSONArray() : page.optJSONArray("notes"));
            out.put("width", page.optInt("width", 0));
            out.put("height", page.optInt("height", 0));
            out.put("image_url", ServerAddress.publicSourceUrl(imageUrl));
            out.put("page_url", ServerAddress.publicSourceUrl(pageUrl));
            out.put("chapter", chapter == null ? "" : chapter);
        } catch (JSONException e) {
            return "{}";
        }
        return out.toString();
    }

    /** The request body for the PC: the saved sidecar, the picture as base64 and the reader's note. */
    static JSONObject body(String meta, byte[] jpeg, String note) throws JSONException {
        JSONObject body;
        try {
            body = new JSONObject(meta == null || meta.isEmpty() ? "{}" : meta);
        } catch (JSONException e) {
            body = new JSONObject();
        }
        String text = note == null ? "" : note.trim();
        if (text.length() > MAX_NOTE_CHARS) text = text.substring(0, MAX_NOTE_CHARS);
        body.put("note", text);
        body.put("image", "data:image/jpeg;base64," + Base64.getEncoder().encodeToString(jpeg));
        return body;
    }
}
