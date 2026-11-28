package app.baihe.shell;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.net.URI;
import java.util.ArrayList;
import java.util.List;

/** What Grab keeps between runs: the chapters waiting to be grabbed one after another, and what each run ended with. */
final class GrabQueue {
    private GrabQueue() {}

    static final int MAX_QUEUED = 500;
    static final int MAX_HISTORY = 50;
    static final int MAX_CHAPTERS = 20;
    /** Stored as the chapter count of a run that goes on until the site has no next chapter. */
    static final int UNTIL_END = 500;

    static final class Item {
        final String url;
        final int chapters;
        final String name;

        Item(String url, int chapters, String name) {
            this.url = url;
            this.chapters = chapters;
            this.name = name;
        }
    }

    static final class Done {
        final long at;
        final String name;
        final String message;
        /** Where the run started, so the history page can go back to it; empty for a run saved by an older build. */
        final String url;
        final boolean quick;

        Done(long at, String name, String message) {
            this(at, name, message, "", false);
        }

        Done(long at, String name, String message, String url, boolean quick) {
            this.at = at;
            this.name = name;
            this.message = message;
            this.url = url == null ? "" : url;
            this.quick = quick;
        }
    }

    /** The queue without the chapter at this address; the same list when it is not there. */
    static List<Item> withoutUrl(List<Item> queue, String url) {
        List<Item> out = new ArrayList<>();
        for (Item item : queue) if (!item.url.equals(url)) out.add(item);
        return out;
    }

    /** A token the server refused, or a spent limit, would refuse every later batch and chapter too. */
    static boolean refusesTheRest(int status) {
        return status == 401 || status == 403 || status == 429;
    }

    /** Where a chapter's send leaves a series or queue run. */
    static final class AfterSend {
        final boolean goOn;
        final boolean queueRunning;
        final String message;

        AfterSend(boolean goOn, boolean queueRunning, String message) {
            this.goOn = goOn;
            this.queueRunning = queueRunning;
            this.message = message;
        }
    }

    /** A refusal ends the series and the queue run as well as the chapter; the queue keeps its chapters, so it can be
     * started again once the server takes pages. `moreDue` is whether later chapters were waiting. */
    static AfterSend afterSend(String message, boolean sent, boolean refused, boolean queueRunning, boolean moreDue) {
        String said = refused && moreDue
                ? message + (message.endsWith(".") ? " " : ". ") + "Later chapters would be refused too, so it stopped here."
                : message;
        return new AfterSend(sent && !refused, queueRunning && !refused, said);
    }

    /** Any count above the pick-a-number cap means the run was asked to go to the last chapter. */
    static boolean untilEnd(int chapters) {
        return chapters > MAX_CHAPTERS;
    }

    static String chaptersLabel(int chapters) {
        if (untilEnd(chapters)) return "to the last chapter";
        return chapters + (chapters == 1 ? " chapter" : " chapters");
    }

    /** The site a queued page is on, so a queue that mixes sites tells its rows apart. */
    static String siteOf(String url) {
        try {
            String host = new URI(url == null ? "" : url).getHost();
            if (host == null) return "";
            return host.toLowerCase(java.util.Locale.ROOT).replaceFirst("^www\\.", "");
        } catch (java.net.URISyntaxException e) {
            return "";
        }
    }

    /** The page's title, or its host when it has none, short enough for one line of a list. */
    static String nameFor(String title, String url) {
        String name = title == null ? "" : title.trim();
        if (name.isEmpty()) {
            try {
                String host = new URI(url == null ? "" : url).getHost();
                name = host == null ? "" : host;
            } catch (java.net.URISyntaxException e) {
                name = "";
            }
        }
        if (name.isEmpty()) name = "Page";
        return name.length() > 60 ? name.substring(0, 60) : name;
    }

    /** The queue with this item last, or the same list when the chapter is already waiting or the queue is full. */
    static List<Item> withItem(List<Item> queue, Item item) {
        if (queue.size() >= MAX_QUEUED) return queue;
        for (Item waiting : queue) if (waiting.url.equals(item.url)) return queue;
        List<Item> out = new ArrayList<>(queue);
        out.add(item);
        return out;
    }

    /** History with this run first; the oldest runs fall off the end. */
    static List<Done> withDone(List<Done> history, Done done) {
        List<Done> out = new ArrayList<>();
        out.add(done);
        out.addAll(history.subList(0, Math.min(history.size(), MAX_HISTORY - 1)));
        return out;
    }

    static String queueJson(List<Item> queue) {
        JSONArray out = new JSONArray();
        try {
            for (Item item : queue) {
                out.put(new JSONObject().put("url", item.url).put("chapters", item.chapters).put("name", item.name));
            }
        } catch (JSONException e) {
            return "[]";
        }
        return out.toString();
    }

    static List<Item> parseQueue(String json) {
        List<Item> out = new ArrayList<>();
        try {
            JSONArray rows = new JSONArray(json == null || json.isEmpty() ? "[]" : json);
            for (int i = 0; i < rows.length() && out.size() < MAX_QUEUED; i++) {
                JSONObject row = rows.getJSONObject(i);
                String url = row.optString("url", "");
                if (url.isEmpty()) continue;
                out.add(new Item(url, Math.max(1, row.optInt("chapters", 1)), row.optString("name", "Page")));
            }
        } catch (JSONException e) {
            // A damaged list is better empty than blocking Grab from opening.
        }
        return out;
    }

    static String historyJson(List<Done> history) {
        JSONArray out = new JSONArray();
        try {
            for (Done done : history) {
                out.put(new JSONObject().put("at", done.at).put("name", done.name).put("message", done.message)
                        .put("url", done.url).put("quick", done.quick));
            }
        } catch (JSONException e) {
            return "[]";
        }
        return out.toString();
    }

    static List<Done> parseHistory(String json) {
        List<Done> out = new ArrayList<>();
        try {
            JSONArray rows = new JSONArray(json == null || json.isEmpty() ? "[]" : json);
            for (int i = 0; i < rows.length() && out.size() < MAX_HISTORY; i++) {
                JSONObject row = rows.getJSONObject(i);
                out.add(new Done(row.optLong("at", 0), row.optString("name", "Page"), row.optString("message", ""),
                        row.optString("url", ""), row.optBoolean("quick", false)));
            }
        } catch (JSONException e) {
            // A damaged list is better empty than blocking Grab from opening.
        }
        return out;
    }
}
