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
import java.util.ArrayList;
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
    /** Room for a few typeset pages (each at most 1.5 MB as base64 text) of a Quick read answer. */
    private static final int MAX_ANSWER_BYTES = 12 * 1024 * 1024;
    private static final int MAX_LINE_BYTES = 6 * 1024 * 1024;

    /** Names this client's page batches so the server can be told to stop working on them. */
    volatile String runId;
    /** Quick read only: the screen width, so the PC need not send back pictures wider than it can show. 0 sends none. */
    volatile int typesetMaxWidth;
    /** How the English lettering looks; null or the default sends nothing, so the PC keeps its own render. */
    volatile TextStyle textStyle;
    /** Quick read only: where the requests' time went. */
    volatile UploadStats stats;
    /** The WebView's user agent, sent with a picture the PC is asked to download. */
    volatile String fetchUserAgent;
    /** Quick read only: read the pages again even when the PC remembers them (a "Redo this page"). */
    volatile boolean redo;
    /** Quick read only: the page's language when its text showed it ("" lets the PC decide). */
    volatile String sourceLanguage = "";
    /** What the page said about its chapter (for the saved pages' chapter label) and its work. */
    volatile GrabBridge.PageMeta pageMeta = GrabBridge.PageMeta.NONE;

    private volatile boolean aborted;
    private final java.util.Set<HttpURLConnection> open = new java.util.HashSet<>();

    /**
     * Cuts every request in flight and refuses new ones, so Cancel does not wait out a batch the
     * PC is still answering (a read can sit for minutes). Call it off the main thread.
     */
    void abort() {
        java.util.List<HttpURLConnection> live;
        synchronized (open) {
            aborted = true;
            live = new java.util.ArrayList<>(open);
        }
        for (HttpURLConnection c : live) c.disconnect();
    }

    private HttpURLConnection track(HttpURLConnection conn) throws BridgeException {
        synchronized (open) {
            if (aborted) throw new BridgeException(0, "Cancelled.");
            open.add(conn);
        }
        return conn;
    }

    /** A refused or failed call; the message is the server's own text or a short fixed one. */
    static final class BridgeException extends Exception {
        final int status;
        /** The connection broke or stalled; asking again may work, unlike a refusal. */
        final boolean dropped;

        BridgeException(int status, String message) {
            this(status, message, false);
        }

        BridgeException(int status, String message, boolean dropped) {
            super(message);
            this.status = status;
            this.dropped = dropped;
        }
    }

    private static final int STREAM_RETRIES = 2;
    private static final long RETRY_WAIT_MS = 3_000;

    /**
     * A proxy in front of the PC answers 502/503/504 when the PC is slow or restarting, and a phone
     * that left the app can come back to a connection Android cut; neither says the pages are bad.
     */
    static boolean worthRetrying(BridgeException e) {
        return e.dropped || e.status == 502 || e.status == 503 || e.status == 504;
    }

    /** `batch` without the pictures whose pages already arrived, so a retry asks only for the rest. */
    static List<PageImage> notDelivered(List<PageImage> batch, java.util.Set<String> delivered) {
        List<PageImage> rest = new java.util.ArrayList<>(batch.size());
        for (PageImage image : batch) if (!delivered.contains(image.key)) rest.add(image);
        return rest;
    }

    /** Clients with a streamed answer open, so coming back to the app can reach them. */
    private static final java.util.Set<BaiheClient> streaming = new java.util.HashSet<>();
    private volatile long lastHeard;
    private volatile boolean skipRetryWait;

    /**
     * Back in the app after time away: a stream that heard nothing since the app left is cut so the
     * batch is asked again at once, instead of waiting out the read timeout on a connection Android
     * may have cut silently. Call off the main thread.
     */
    static void dropQuietStreams(long since) {
        java.util.List<BaiheClient> live;
        synchronized (streaming) {
            live = new java.util.ArrayList<>(streaming);
        }
        for (BaiheClient client : live) client.dropIfQuiet(since);
    }

    private void dropIfQuiet(long since) {
        if (lastHeard >= since || aborted) return;
        skipRetryWait = true;
        java.util.List<HttpURLConnection> live;
        synchronized (open) {
            live = new java.util.ArrayList<>(open);
        }
        for (HttpURLConnection c : live) c.disconnect();
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

    /** The source sites the server can read, from its own source list, as name and front-page address. */
    List<GrabBookmarks.Mark> sites() throws BridgeException {
        List<GrabBookmarks.Mark> out = new ArrayList<>();
        JSONArray sites = call("GET", "/sites", null).optJSONArray("sites");
        for (int i = 0; sites != null && i < sites.length(); i++) {
            JSONObject site = sites.optJSONObject(i);
            String url = site == null ? null : ServerAddress.browsable(site.optString("url"));
            if (url != null) out.add(new GrabBookmarks.Mark(site.optString("name"), url));
        }
        return out;
    }

    /** The title for this page (drama_id and title), found or made by the server's Auto title logic. */
    JSONObject resolveTitle(String pageUrl, List<String> titles, JSONArray chapters) throws BridgeException {
        return resolveTitle(pageUrl, titles, chapters, "comic");
    }

    /** `kind` is "novel" for a text chapter, so it is never filed under a comic of the same name. */
    JSONObject resolveTitle(String pageUrl, List<String> titles, JSONArray chapters, String kind) throws BridgeException {
        try {
            JSONObject body = new JSONObject();
            body.put("url", ServerAddress.publicSourceUrl(pageUrl));
            body.put("titles", new JSONArray(titles));
            body.put("kind", kind);
            body.put("chapter_links", chapters == null ? new JSONArray() : chapters);
            GrabBridge.PageMeta meta = pageMeta;
            if (!meta.author.isEmpty()) body.put("author", meta.author);
            if (!meta.summary.isEmpty()) body.put("summary", meta.summary);
            if (!meta.coverUrl.isEmpty()) body.put("cover_url", meta.coverUrl);
            JSONObject answer = call("POST", "/title", body);
            if (answer.optLong("drama_id", 0) <= 0) throw new BridgeException(0, "Baihe did not return a title.");
            return answer;
        } catch (JSONException e) {
            throw new BridgeException(0, "Could not build the request.");
        }
    }

    /** Where the PC's translating of a title this token saved to has got: state, step and kind. */
    JSONObject titleProgress(long dramaId) throws BridgeException {
        try {
            return call("POST", "/title-progress", new JSONObject().put("drama_id", dramaId));
        } catch (JSONException e) {
            throw new BridgeException(0, "Could not build the request.");
        }
    }

    JSONObject sendPages(long dramaId, String pageUrl, List<PageImage> batch) throws BridgeException {
        return post(pageUrl, batch, dramaId, true);
    }

    /**
     * Quick read: the pages are translated and typeset on the PC and sent back, with no title
     * and nothing saved. The same permissions and spending limit as a saved send apply.
     */
    JSONObject readPages(String pageUrl, List<PageImage> batch) throws BridgeException {
        return post(pageUrl, batch, 0, false);
    }

    /**
     * Quick read of a novel: a part of the chapter's text is translated on the PC and nothing is
     * saved. The same permissions and spending limit as a picture apply.
     */
    String translateText(String text, String source, String target) throws BridgeException {
        try {
            JSONObject answer = call("POST", "/text", new JSONObject()
                    .put("text", text).put("source_language", source).put("target_language", target).put("store", false));
            String translated = answer.optString("translated_text", "");
            if (translated.isEmpty()) {
                JSONArray notes = answer.optJSONArray("notes");
                JSONArray first = notes == null ? null : notes.optJSONArray(0);
                String why = first == null ? "" : first.optString(first.length() - 1, "");
                throw new BridgeException(0, why.isEmpty() ? "Baihe returned no translation." : why);
            }
            return translated;
        } catch (JSONException e) {
            throw new BridgeException(0, "Could not build the request.");
        }
    }

    /** Saves a novel chapter's raw text into a novel title; a repeat of the same chapter adds nothing. */
    JSONObject saveNovelChapter(long dramaId, String pageUrl, String heading, String text) throws BridgeException {
        try {
            return call("POST", "/novel", new JSONObject().put("drama_id", dramaId).put("heading", heading)
                    .put("text", text).put("url", ServerAddress.publicSourceUrl(pageUrl)));
        } catch (JSONException e) {
            throw new BridgeException(0, "Could not build the request.");
        }
    }

    /** Sends a video page's link to Quick video translate on the PC; returns the new title's id. */
    long sendVideo(String link) throws BridgeException {
        try {
            return call("POST", "/video", new JSONObject().put("url", link)).optLong("drama_id", 0);
        } catch (JSONException e) {
            throw new BridgeException(0, "Could not build the request.");
        }
    }

    /** Asks the PC to watch this page's series for new chapters; returns the series title. */
    String follow(String pageUrl) throws BridgeException {
        try {
            return call("POST", "/follow", new JSONObject().put("url", pageUrl)).optString("title", "");
        } catch (JSONException e) {
            throw new BridgeException(0, "Could not build the request.");
        }
    }

    /** Tells the PC which page this account is reading, so its other phones can offer to go on from it. */
    void rememberPage(String pageUrl, String title) throws BridgeException {
        try {
            call("POST", "/last-page", new JSONObject().put("url", pageUrl).put("title", title == null ? "" : title));
        } catch (JSONException e) {
            throw new BridgeException(0, "Could not build the request.");
        }
    }

    /** The page this account last read on each site, most recent first, as {host, url, title}. */
    List<String[]> lastPages() throws BridgeException {
        List<String[]> out = new ArrayList<>();
        JSONArray pages = call("POST", "/last-pages", new JSONObject()).optJSONArray("pages");
        for (int i = 0; pages != null && i < pages.length(); i++) {
            JSONObject page = pages.optJSONObject(i);
            if (page != null) out.add(new String[] {page.optString("host"), page.optString("url"), page.optString("title")});
        }
        return out;
    }

    /** Starts Live captions on a stream page's link, in the stream's spoken language (zh, ja or ko). */
    void startLive(String link, String language) throws BridgeException {
        try {
            call("POST", "/live", new JSONObject().put("url", link).put("source_language", language));
        } catch (JSONException e) {
            throw new BridgeException(0, "Could not build the request.");
        }
    }

    /** Tells the server to drop the pages of this run it has not started; the page in flight finishes. */
    void cancelRun(String id) throws BridgeException {
        try {
            call("POST", "/cancel", new JSONObject().put("run_id", id));
        } catch (JSONException e) {
            throw new BridgeException(0, "Could not build the request.");
        }
    }

    /** The Quick read runs this phone's account still has working on the PC. */
    JSONArray quickReadRuns() throws BridgeException {
        JSONArray runs = call("POST", "/quick-reads", new JSONObject()).optJSONArray("runs");
        return runs == null ? new JSONArray() : runs;
    }

    /** Drops one run (its pages not started are not worked on), or every run when `runId` is null. */
    void dropQuickRead(String runId) throws BridgeException {
        try {
            JSONObject body = new JSONObject();
            if (runId != null) body.put("run_id", runId);
            call("POST", "/quick-reads/drop", body);
        } catch (JSONException e) {
            throw new BridgeException(0, "Could not build the request.");
        }
    }

    /** Sends a Quick read page the reader found wrong, with what the PC read on it, to the PC's reports. */
    void sendReport(JSONObject body) throws BridgeException {
        call("POST", "/report", body);
    }

    /** Hears each finished page as the server sends it, instead of after the whole batch. */
    interface PageListener {
        void onPage(JSONObject page) throws IOException;
    }

    /**
     * Quick read with a streamed answer: `onPage` gets each typeset page as soon as the server
     * finishes it, and the result is the closing summary (failed, skipped and so on). A lone image
     * has no streamed route, so it comes back whole and is handed to `onPage` the same way.
     */
    JSONObject streamPages(String pageUrl, List<PageImage> batch, PageListener onPage) throws BridgeException {
        java.util.Set<String> delivered = new java.util.HashSet<>();
        PageListener tracking = page -> {
            delivered.add(page.optString("key", ""));
            onPage.onPage(page);
        };
        List<PageImage> todo = batch;
        for (int attempt = 0; ; attempt++) {
            try {
                return streamOnce(pageUrl, todo, tracking);
            } catch (BridgeException e) {
                todo = notDelivered(batch, delivered);
                // Every page arrived and only the closing line was lost: the read is complete.
                if (e.dropped && !aborted && todo.isEmpty() && !batch.isEmpty()) return new JSONObject();
                if (attempt >= STREAM_RETRIES || aborted || todo.isEmpty() || !worthRetrying(e)) throw e;
                if (!skipRetryWait) {
                    try {
                        Thread.sleep(RETRY_WAIT_MS * (attempt + 1));
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        throw e;
                    }
                }
                skipRetryWait = false;
                if (aborted) throw e;
            }
        }
    }

    private JSONObject streamOnce(String pageUrl, List<PageImage> batch, PageListener onPage) throws BridgeException {
        try {
            // A lone picture the PC is asked to download goes the streamed route, which takes fetch_url.
            if (batch.size() == 1 && !batch.get(0).fetchFirst) {
                JSONObject answer = readPages(pageUrl, batch);
                JSONArray pages = answer.optJSONArray("pages");
                for (int i = 0; pages != null && i < pages.length(); i++) onPage.onPage(pages.getJSONObject(i));
                answer.remove("pages");
                return answer;
            }
            // The heartbeat keeps the read timeout from ending a wait the PC is still working through.
            JSONObject body = requestBody(pageUrl, batch, 0, false).put("stream", true).put("heartbeat", true);
            return callStream("/pages", body, onPage);
        } catch (JSONException | IOException e) {
            throw new BridgeException(0, "Could not read the pages.");
        }
    }

    private JSONObject callStream(String path, JSONObject body, PageListener onPage) throws BridgeException {
        HttpURLConnection conn = null;
        lastHeard = System.currentTimeMillis();
        synchronized (streaming) { streaming.add(this); }
        try {
            conn = track((HttpURLConnection) new URL(origin + "/api/ext-bridge" + path).openConnection());
            conn.setRequestMethod("POST");
            conn.setInstanceFollowRedirects(false);
            conn.setConnectTimeout(CONNECT_TIMEOUT_MS);
            conn.setReadTimeout(READ_TIMEOUT_MS);
            conn.setRequestProperty("Authorization", "Bearer " + token);
            conn.setRequestProperty("Accept", "application/x-ndjson");
            byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
            conn.setDoOutput(true);
            conn.setFixedLengthStreamingMode(bytes.length);
            conn.setRequestProperty("Content-Type", "application/json");
            long sendStart = System.currentTimeMillis();
            try (OutputStream out = conn.getOutputStream()) {
                out.write(bytes);
            }
            long sent = System.currentTimeMillis();
            int status = conn.getResponseCode();
            UploadStats record = stats;
            if (record != null) record.add(bytes.length, sent - sendStart, System.currentTimeMillis() - sent);
            if (status >= 300) {
                InputStream err = conn.getErrorStream();
                throw new BridgeException(status, refusalText(status, err == null ? "" : readCapped(err)));
            }
            JSONObject done = null;
            try (InputStream in = conn.getInputStream()) {
                String line;
                while ((line = readLine(in)) != null) {
                    lastHeard = System.currentTimeMillis();
                    if (line.isEmpty()) continue;
                    JSONObject item = new JSONObject(line);
                    if (item.has("page")) onPage.onPage(item.getJSONObject("page"));
                    else if (item.has("done")) done = item.getJSONObject("done");
                    else if (item.has("error")) throw new BridgeException(item.optInt("status", 0), item.optString("error"));
                }
            }
            if (done == null) throw new BridgeException(status, "Baihe stopped before it finished the pages.", true);
            return done;
        } catch (BridgeException e) {
            throw e;
        } catch (JSONException e) {
            throw new BridgeException(0, "Baihe sent an answer this app could not read.");
        } catch (IOException e) {
            throw new BridgeException(0, aborted ? "Cancelled." : "Could not reach Baihe.", !aborted);
        } finally {
            synchronized (streaming) { streaming.remove(this); }
            if (conn != null) {
                synchronized (open) { open.remove(conn); }
                conn.disconnect();
            }
        }
    }

    /** One line of the stream; a typeset page is a few MB at most, so a longer line is refused. */
    private static String readLine(InputStream in) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        int b;
        while ((b = in.read()) != -1) {
            if (b == '\n') return out.toString("UTF-8");
            if (out.size() >= MAX_LINE_BYTES) throw new IOException("line too large");
            out.write(b);
        }
        return out.size() == 0 ? null : out.toString("UTF-8");
    }

    private JSONObject post(String pageUrl, List<PageImage> batch, long dramaId, boolean store) throws BridgeException {
        try {
            JSONObject body = requestBody(pageUrl, batch, dramaId, store);
            // /page takes exactly one image; /pages runs the page filter, so a lone image uses /page.
            return call("POST", batch.size() == 1 ? "/page" : "/pages", body);
        } catch (JSONException e) {
            throw new BridgeException(0, "Could not build the request.");
        }
    }

    private JSONObject requestBody(String pageUrl, List<PageImage> batch, long dramaId, boolean store)
            throws JSONException {
        JSONArray images = new JSONArray();
        boolean asksPc = false;
        for (PageImage image : batch) {
            JSONObject entry = new JSONObject()
                    .put("key", image.key)
                    .put("url", image.url.startsWith("http") ? image.url : "")
                    .put("content_type", image.contentType);
            if (image.fetchFirst) {
                entry.put("fetch_url", image.url);
                asksPc = true;
            } else {
                entry.put("data", image.base64);
            }
            images.put(entry);
        }
        JSONObject body = new JSONObject()
                .put("images", images)
                .put("source_url", ServerAddress.publicSourceUrl(pageUrl))
                .put("store", store)
                .put("filter_pages", true);
        if (runId != null) body.put("run_id", runId);
        if (asksPc) {
            body.put("fetch_referer", pageUrl);
            if (fetchUserAgent != null) body.put("fetch_user_agent", fetchUserAgent);
        }
        if (store) {
            body.put("drama_id", dramaId);
            List<String> titles = pageMeta.chapterTitles;
            if (!titles.isEmpty()) body.put("page_titles", new JSONArray(titles));
        } else {
            body.put("typeset", true);
            if (sourceLanguage != null && !sourceLanguage.isEmpty()) body.put("source_language", sourceLanguage);
            if (typesetMaxWidth > 0) body.put("typeset_max_width", typesetMaxWidth);
            TextStyle look = textStyle;
            if (look != null && !look.isDefault()) {
                body.put("typeset_style", new JSONObject().put("min_font", look.minFont())
                        .put("stroke", look.outline).put("font", look.fontName()));
            }
            if (redo) body.put("redo", true);
        }
        return body;
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

    /** Shown for a refused token, because the server's own text ("Authentication required.") says nothing to do. */
    static final String TOKEN_HELP = "Baihe did not accept the token. Make a phone token at the PC in Baihe: "
            + "Settings > Extension tokens per computer, then paste it in Grab > Settings.";

    /**
     * What to show for a refused call: a fixed line for a refused token, else the server's own text.
     * The server sends {"error": "text"} from the bridge and {"error": {"code", "message"}} from the
     * rest of the API; never the raw body.
     */
    static String refusalText(int status, String body) {
        if (status == 401) return TOKEN_HELP;
        try {
            Object error = new JSONObject(body).opt("error");
            String text = error instanceof JSONObject ? ((JSONObject) error).optString("message", "")
                    : error instanceof String ? (String) error : "";
            if (!text.isEmpty()) return text.length() > 300 ? text.substring(0, 300) : text;
        } catch (JSONException ignored) {
            // Fall through to the fixed text.
        }
        return "Baihe refused the request (" + status + ").";
    }
}
