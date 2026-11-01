package app.baihe.shell;

import android.webkit.JavascriptInterface;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * The only object the browsed page's JavaScript can reach (as `Baihe`). It accepts images
 * only while a grab is running and only with that grab's secret (a page can read the
 * secret, so it is no more than a time window). Every method returns
 * nothing, and the token and server address are not reachable from here.
 */
final class GrabBridge {
    interface Listener {
        void onBegin(List<String> titles, int count);

        void onDone(List<PageImage> images, int unreadable, boolean truncated);
    }

    /** Past these the grab stops collecting and sends what it has. */
    static final int MAX_IMAGES = 300;
    static final long MAX_TOTAL_BASE64_CHARS = 24L * 1024 * 1024;

    private final Listener listener;
    private String nonce;
    private List<PageImage> images = new ArrayList<>();
    private long totalChars;
    private int unreadable;
    private boolean truncated;
    private boolean begun;

    GrabBridge(Listener listener) {
        this.listener = listener;
    }

    synchronized void start(String newNonce) {
        nonce = newNonce;
        images = new ArrayList<>();
        totalChars = 0;
        unreadable = 0;
        truncated = false;
        begun = false;
    }

    synchronized void cancel() {
        nonce = null;
    }

    private boolean accepts(String given) {
        return nonce != null && given != null && nonce.equals(given);
    }

    @JavascriptInterface
    public void begin(String given, String json) {
        synchronized (this) {
            if (!accepts(given)) return;
            begun = true;
        }
        try {
            JSONObject info = new JSONObject(json);
            List<String> titles = new ArrayList<>();
            JSONArray array = info.optJSONArray("titles");
            for (int i = 0; array != null && i < array.length() && i < 12; i++) {
                String title = array.optString(i, "");
                if (!title.isEmpty()) titles.add(title.length() > 300 ? title.substring(0, 300) : title);
            }
            listener.onBegin(titles, info.optInt("count", 0));
        } catch (Exception e) {
            synchronized (this) {
                nonce = null;
            }
        }
    }

    @JavascriptInterface
    public synchronized void image(String given, String key, String url, String type, String base64) {
        if (!accepts(given) || !begun || base64 == null || base64.isEmpty()) return;
        if (Batches.tooLarge(base64)) {
            unreadable++;
            return;
        }
        if (images.size() >= MAX_IMAGES || totalChars + base64.length() > MAX_TOTAL_BASE64_CHARS) {
            truncated = true;
            return;
        }
        String safeType = type != null && type.matches("image/[A-Za-z0-9.+-]{1,20}") ? type : "image/jpeg";
        images.add(new PageImage(key, url == null ? "" : url, safeType, base64));
        totalChars += base64.length();
    }

    @JavascriptInterface
    public synchronized void skipped(String given, String key) {
        if (accepts(given)) unreadable++;
    }

    @JavascriptInterface
    public void done(String given) {
        List<PageImage> collected;
        int bad;
        boolean cut;
        synchronized (this) {
            if (!accepts(given) || !begun) return;
            nonce = null;
            collected = images;
            bad = unreadable;
            cut = truncated;
            images = new ArrayList<>();
        }
        listener.onDone(collected, bad, cut);
    }
}
