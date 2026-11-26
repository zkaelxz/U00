package app.baihe.shell;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Base64;

/** What the phone app's "Report a problem" sends: the note, where it was, the build and the kept crash text. */
final class ProblemReport {
    static final int MAX_NOTE_CHARS = 1000;
    /** The PC refuses a crash text over 8000 characters; a trace is cut to this before it is sent. */
    static final int MAX_CRASH_CHARS = 8000;

    private ProblemReport() {}

    /** One line saying which build and phone this is; never a name, number or account. */
    static String phoneInfo(int versionCode, int sdk, String model) {
        return "Build " + versionCode + ", Android SDK " + sdk + (model == null || model.isEmpty() ? "" : ", " + model);
    }

    /**
     * The request body. `kind: app` tells the PC this is not a Quick read page. The page address
     * loses its query (it can hold a login token) before it leaves the phone, and a screenshot is
     * only added when the reader ticked it.
     */
    static JSONObject body(String note, String screen, String pageUrl, String phone, String crash, byte[] jpeg)
            throws JSONException {
        String text = note == null ? "" : note.trim();
        if (text.length() > MAX_NOTE_CHARS) text = text.substring(0, MAX_NOTE_CHARS);
        String trace = crash == null ? "" : crash;
        if (trace.length() > MAX_CRASH_CHARS) trace = trace.substring(0, MAX_CRASH_CHARS);
        JSONObject body = new JSONObject()
                .put("kind", "app")
                .put("note", text)
                .put("screen", screen == null ? "" : screen)
                .put("page_url", ServerAddress.publicSourceUrl(pageUrl))
                .put("phone", phone == null ? "" : phone)
                .put("crash", trace);
        if (jpeg != null && jpeg.length > 0) {
            body.put("image", "data:image/jpeg;base64," + Base64.getEncoder().encodeToString(jpeg));
        }
        return body;
    }
}
