package app.baihe.shell;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;
import org.json.JSONArray;
import org.json.JSONObject;

/** Reads Baihe's alert list (the header bell) and picks what the phone should announce and where a tap goes. */
final class AlertFeed {
    /** Only what a person acts on: "This PC" and remote-access notes stay in the bell. */
    private static final Set<String> KINDS = new HashSet<>(Arrays.asList(
            "job_done", "job_failed", "waiting", "live", "chapters"));
    /** A page of Baihe's own web app: a hash route with no scheme, so a tap can never leave Baihe. */
    private static final Pattern SAFE_ROUTE = Pattern.compile("#/[A-Za-z0-9/_.=&%?-]{1,200}");
    /** More than this at once is a backlog (the phone was off), announced as one line. */
    static final int MAX_SEPARATE = 4;

    static final class Item {
        final long id;
        final double at;
        final String kind;
        final String text;
        /** Hash route to open on a tap, or null to just open Baihe. */
        final String route;

        Item(long id, double at, String kind, String text, String route) {
            this.id = id;
            this.at = at;
            this.kind = kind;
            this.text = text;
            this.route = route;
        }
    }

    private AlertFeed() {}

    /** The list, newest first as the server sends it; an unreadable answer is an empty list. */
    static List<Item> parse(String json) {
        List<Item> out = new ArrayList<>();
        try {
            JSONArray items = new JSONObject(json).optJSONArray("items");
            for (int i = 0; items != null && i < items.length(); i++) {
                JSONObject o = items.getJSONObject(i);
                String kind = o.optString("kind", "");
                String text = o.optString("text", "");
                if (text.isEmpty()) continue;
                out.add(new Item(o.optLong("id", 0), o.optDouble("at", 0), kind, text,
                        route(kind, o.optJSONObject("target"))));
            }
        } catch (Exception e) {
            return new ArrayList<>();
        }
        return out;
    }

    /** The page an item opens: its target when it names one this build knows, else null. */
    static String route(String kind, JSONObject target) {
        if (target == null) return "live".equals(kind) ? "#/live" : null;
        String page = target.optString("page", "");
        long drama = target.optLong("drama_id", 0);
        long series = target.optLong("series_id", 0);
        long follow = target.optLong("follow_id", 0);
        String route = null;
        if ("watch".equals(page) && drama > 0) route = "#/watch/" + drama;
        else if ("title".equals(page) && drama > 0) route = "#/drama/" + drama;
        else if ("review".equals(page) && drama > 0) route = "#/drama/" + drama + "/review";
        else if ("glossary".equals(page) && drama > 0) route = "#/drama/" + drama + "/translate?tab=glossary";
        else if ("series".equals(page) && series > 0) route = "#/series/" + series;
        else if ("jobs".equals(page)) route = "#/jobs";
        else if ("live".equals(page)) route = follow > 0 ? "#/live?follow=" + follow : "#/live";
        return safeRoute(route) ? route : null;
    }

    static boolean safeRoute(String route) {
        return route != null && SAFE_ROUTE.matcher(route).matches();
    }

    /**
     * What is new since the mark, oldest first. An id counts as new when it is larger than the mark's
     * or its time is later, because the server's list starts over when it restarts.
     */
    static List<Item> fresh(List<Item> items, long markId, double markAt) {
        List<Item> out = new ArrayList<>();
        for (int i = items.size() - 1; i >= 0; i--) {
            Item item = items.get(i);
            if (KINDS.contains(item.kind) && (item.id > markId || item.at > markAt)) out.add(item);
        }
        return out;
    }

    static long newestId(List<Item> items) {
        long best = 0;
        for (Item item : items) best = Math.max(best, item.id);
        return best;
    }

    static double newestAt(List<Item> items) {
        double best = 0;
        for (Item item : items) best = Math.max(best, item.at);
        return best;
    }
}
