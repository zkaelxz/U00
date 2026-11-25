package app.baihe.shell;

import java.util.ArrayList;
import java.util.List;

/**
 * The saved queue and history are the single copy, read fresh by every screen that shows or
 * changes them, so Grab and the history page never disagree about what is waiting.
 */
final class GrabStore {
    private static final Object LOCK = new Object();
    private static final String KEY_QUEUE = "queue";
    private static final String KEY_HISTORY = "history";

    private GrabStore() {}

    static List<GrabQueue.Item> queue(android.content.SharedPreferences prefs) {
        synchronized (LOCK) {
            return GrabQueue.parseQueue(prefs.getString(KEY_QUEUE, ""));
        }
    }

    static List<GrabQueue.Done> history(android.content.SharedPreferences prefs) {
        synchronized (LOCK) {
            return GrabQueue.parseHistory(prefs.getString(KEY_HISTORY, ""));
        }
    }

    /** False when the chapter is already waiting or the queue is full. */
    static boolean add(android.content.SharedPreferences prefs, GrabQueue.Item item) {
        synchronized (LOCK) {
            List<GrabQueue.Item> before = GrabQueue.parseQueue(prefs.getString(KEY_QUEUE, ""));
            List<GrabQueue.Item> after = GrabQueue.withItem(before, item);
            if (after == before) return false;
            prefs.edit().putString(KEY_QUEUE, GrabQueue.queueJson(after)).apply();
            return true;
        }
    }

    static void remove(android.content.SharedPreferences prefs, String url) {
        synchronized (LOCK) {
            prefs.edit().putString(KEY_QUEUE, GrabQueue.queueJson(GrabQueue.withoutUrl(GrabQueue.parseQueue(prefs.getString(KEY_QUEUE, "")), url))).apply();
        }
    }

    /** The first waiting chapter, taken off the queue; null when none is waiting. */
    static GrabQueue.Item take(android.content.SharedPreferences prefs) {
        synchronized (LOCK) {
            List<GrabQueue.Item> waiting = GrabQueue.parseQueue(prefs.getString(KEY_QUEUE, ""));
            if (waiting.isEmpty()) return null;
            prefs.edit().putString(KEY_QUEUE, GrabQueue.queueJson(new ArrayList<>(waiting.subList(1, waiting.size())))).apply();
            return waiting.get(0);
        }
    }

    static void record(android.content.SharedPreferences prefs, GrabQueue.Done done) {
        synchronized (LOCK) {
            prefs.edit().putString(KEY_HISTORY, GrabQueue.historyJson(GrabQueue.withDone(GrabQueue.parseHistory(prefs.getString(KEY_HISTORY, "")), done))).apply();
        }
    }

    static void clearHistory(android.content.SharedPreferences prefs) {
        synchronized (LOCK) {
            prefs.edit().putString(KEY_HISTORY, "[]").apply();
        }
    }
}
