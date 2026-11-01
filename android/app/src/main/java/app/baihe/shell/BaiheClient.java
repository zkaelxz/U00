package app.baihe.shell;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * Calls the device-token routes of the household Baihe server (/api/ext-bridge/*): the same
 * ones the browser extension uses. The token goes only in the Authorization header, to the
 * saved https origin, and a redirect is never followed.
 */
final class BaiheClient {
    /** A bridge call can queue behind a long run; the extension waits the same 4.5 minutes. */
    private static final int READ_TIMEOUT_MS = (int) (4.5 * 60 * 1000);
    private static final int CONNECT_TIMEOUT_MS = 15_000;
    private static final int MAX_ANSWER_BYTES = 4 * 1024 * 1024;

    /** A refused or failed call; the message is the server's own text or a short fixed one. */
    static final class BridgeException extends Exception {
        final int status;

        BridgeException(int status, String message) {
            super(message);
            this.status = status;
        }
    }

    private final String origin;
    private final String token;

    BaiheClient(String origin, String token) {
        this.origin = origin;
        this.token = token;
    }

    JSONObject health() throws BridgeException {
        return call("GET", "/health", null);
    }

    /** The title for this page (drama_id and title), found or made by the server's Auto title logic. */
    JSONObject resolveTitle(String pageUrl, List<String> titles) throws BridgeException {
        try {
            JSONObject body = new JSONObject();
            body.put("url", ServerAddress.publicSourceUrl(pageUrl));
            body.put("titles", new JSONArray(titles));
            body.put("kind", "comic");
            body.put("chapter_links", new JSONArray());
            JSONObject answer = call("POST", "/title", body);
            if (answer.optLong("drama_id", 0) <= 0) throw new BridgeException(0, "Baihe did not return a title.");
            return answer;
        } catch (JSONException e) {
            throw new BridgeException(0, "Could not build the request.");
        }
    }

    JSONObject sendPages(long dramaId, String pageUrl, List<PageImage> batch) throws BridgeException {
        try {
            JSONArray images = new JSONArray();
            for (PageImage image : batch) {
                images.put(new JSONObject()
                        .put("key", image.key)
                        .put("url", image.url.startsWith("http") ? image.url : "")
                        .put("content_type", image.contentType)
                        .put("data", image.base64));
            }
            JSONObject body = new JSONObject()
                    .put("images", images)
                    .put("drama_id", dramaId)
                    .put("source_url", ServerAddress.publicSourceUrl(pageUrl))
                    .put("store", true)
                    .put("filter_pages", true);
            // /page takes exactly one image; /pages runs the page filter, so a lone image uses /page.
            return call("POST", batch.size() == 1 ? "/page" : "/pages", body);
        } catch (JSONException e) {
            throw new BridgeException(0, "Could not build the request.");
        }
    }

    private JSONObject call(String method, String path, JSONObject body) throws BridgeException {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(origin + "/api/ext-bridge" + path).openConnection();
            conn.setRequestMethod(method);
            conn.setInstanceFollowRedirects(false);
            conn.setConnectTimeout(CONNECT_TIMEOUT_MS);
            conn.setReadTimeout(READ_TIMEOUT_MS);
            conn.setRequestProperty("Authorization", "Bearer " + token);
            conn.setRequestProperty("Accept", "application/json");
            if (body != null) {
                byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
                conn.setDoOutput(true);
                conn.setFixedLengthStreamingMode(bytes.length);
                conn.setRequestProperty("Content-Type", "application/json");
                try (OutputStream out = conn.getOutputStream()) {
                    out.write(bytes);
                }
            }
            int status = conn.getResponseCode();
            InputStream stream = status < 400 ? conn.getInputStream() : conn.getErrorStream();
            String text = stream == null ? "" : readCapped(stream);
            if (status >= 300) throw new BridgeException(status, refusalText(status, text));
            try {
                return new JSONObject(text);
            } catch (JSONException e) {
                throw new BridgeException(status, "Baihe sent an answer this app could not read.");
            }
        } catch (BridgeException e) {
            throw e;
        } catch (IOException e) {
            throw new BridgeException(0, "Could not reach Baihe.");
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private static String readCapped(InputStream in) throws IOException {
        try (InputStream stream = in) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int n;
            while ((n = stream.read(buffer)) > 0) {
                if (out.size() + n > MAX_ANSWER_BYTES) throw new IOException("answer too large");
                out.write(buffer, 0, n);
            }
            return out.toString("UTF-8");
        }
    }

    /** The server's own {"error": text} when it sent one (never the raw body), else a fixed line. */
    static String refusalText(int status, String body) {
        try {
            String error = new JSONObject(body).optString("error", "");
            if (!error.isEmpty()) return error.length() > 300 ? error.substring(0, 300) : error;
        } catch (JSONException ignored) {
            // Fall through to the fixed text.
        }
        return status == 401 ? "Baihe did not accept the token." : "Baihe refused the request (" + status + ").";
    }
}
