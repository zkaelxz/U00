package app.baihe.shell;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Base64;
import java.util.List;

/**
 * Reads the pages of a no-save bridge answer for Quick read. Nothing here touches the phone's
 * storage; the reader writes the images it gets.
 */
final class QuickRead {
    private static final String JPEG_PREFIX = "data:image/jpeg;base64,";

    private QuickRead() {}

    /**
     * The JPEG bytes of each page the server typeset, in page order. A page with no typeset
     * image (nothing to translate, or the redraw failed) is left out and counted by the caller
     * from the difference, because there is no plain-boxes view to fall back to here.
     */
    static List<byte[]> typesetPages(JSONObject answer) {
        List<byte[]> out = new ArrayList<>();
        JSONArray pages = answer.optJSONArray("pages");
        if (pages == null) return out;
        for (int i = 0; i < pages.length(); i++) {
            JSONObject page = pages.optJSONObject(i);
            byte[] jpeg = page == null ? null : decode(page.optString("typeset_image", ""));
            if (jpeg != null) out.add(jpeg);
        }
        return out;
    }

    /**
     * The picture the phone itself sent under `key`, shown for a page the PC returned without a
     * redraw (no text to translate, or the redraw failed), so the chapter has no holes.
     */
    static byte[] original(List<PageImage> batch, String key) {
        if (key == null || key.isEmpty()) return null;
        for (PageImage image : batch) {
            if (!key.equals(image.key) || image.base64.isEmpty()) continue;
            try {
                byte[] bytes = Base64.getMimeDecoder().decode(image.base64);
                return bytes.length == 0 ? null : bytes;
            } catch (IllegalArgumentException e) {
                return null;
            }
        }
        return null;
    }

    /** The address the phone grabbed the picture under `key` from, for a report; empty when unknown. */
    static String sourceUrl(List<PageImage> batch, String key) {
        if (key == null || key.isEmpty()) return "";
        for (PageImage image : batch) if (key.equals(image.key)) return image.url;
        return "";
    }

    /** Only the server's own jpeg data: URL is accepted; anything else is ignored, never written. */
    static byte[] decode(String dataUrl) {
        if (dataUrl == null || !dataUrl.startsWith(JPEG_PREFIX)) return null;
        try {
            byte[] bytes = Base64.getDecoder().decode(dataUrl.substring(JPEG_PREFIX.length()));
            return bytes.length == 0 ? null : bytes;
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    /**
     * The first warning the PC attached to any page (no translation engine set, a failed
     * translation, a failed redraw), so a page that comes back with nothing to show says why.
     */
    static String firstWarning(JSONObject answer) {
        JSONArray pages = answer.optJSONArray("pages");
        for (int i = 0; pages != null && i < pages.length(); i++) {
            JSONObject page = pages.optJSONObject(i);
            JSONArray notes = page == null ? null : page.optJSONArray("notes");
            for (int j = 0; notes != null && j < notes.length(); j++) {
                JSONArray note = notes.optJSONArray(j);
                if (note == null || note.length() < 2 || !"warning".equals(note.optString(0))) continue;
                String text = note.optString(note.length() - 1, "");
                if (!text.isEmpty()) return text.length() > 200 ? text.substring(0, 200) : text;
            }
        }
        return "";
    }

    static String summary(int shown, int received, String firstError) {
        StringBuilder out = new StringBuilder();
        if (shown == 0) out.append("Nothing could be translated and redrawn on this page");
        else out.append("Opened ").append(shown).append(" of ").append(received).append(" pages");
        if (firstError != null && !firstError.isEmpty()) out.append(". ").append(firstError);
        return out.toString();
    }
}
