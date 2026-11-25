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

    static final int MAX_QUEUED = 50;
    static final int MAX_HISTORY = 50;

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
