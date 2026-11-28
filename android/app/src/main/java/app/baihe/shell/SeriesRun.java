package app.baihe.shell;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.URI;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Works out which chapters of a series page still need grabbing, for the "Grab whole series" menu item. */
final class SeriesRun {
    private SeriesRun() {}

    /** Fewer links than this is a chapter page's previous/next, not a series' chapter list. */
    static final int MIN_LIST = 3;
    static final int MAX_LINKS = 500;

    /** Run on the open page; answers the chapter links it lists as JSON, the same way Grab reads them. */
    static final String LINKS_JS = "(function(){var T=/第\\s*[\\d０-９一二三四五六七八九十百零]+\\s*[章话話回集卷期]"
            + "|(?:chapter|chap|ch|episode|ep|vol)\\.?\\s*[\\d.]+|[\\d.]+\\s*[화話话章]|^\\s*#?\\d+\\s*$/i;"
            + "var out=[],keys={},a=document.querySelectorAll('a[href]');"
            + "for(var i=0;i<a.length&&out.length<" + MAX_LINKS + ";i++){"
            + "var x=a[i],t=(x.textContent||'').replace(/\\s+/g,' ').trim().slice(0,120);"
            + "if(!t||!T.test(t))continue;var u;try{u=new URL(x.href)}catch(e){continue}"
            + "if(u.host!==location.host||!/^https?:$/.test(u.protocol))continue;"
            + "var k=u.origin+u.pathname;if(keys[k]||k===location.origin+location.pathname)continue;"
            + "keys[k]=true;out.push({title:t,url:k})}return JSON.stringify(out)})()";

    static final class Plan {
        final List<GrabQueue.Item> fresh;
        final int total;
        final int done;
        final int waiting;

        Plan(List<GrabQueue.Item> fresh, int total, int done, int waiting) {
            this.fresh = fresh;
            this.total = total;
            this.done = done;
            this.waiting = waiting;
        }
    }

    private static final Pattern NUMBER = Pattern.compile("\\d+(?:\\.\\d+)?");

    /** An address without query, fragment or a trailing slash, so one chapter compares equal however it was reached. */
    static String key(String url) {
        try {
            URI u = new URI(url == null ? "" : url.trim());
            String host = u.getHost() == null ? "" : u.getHost().toLowerCase(Locale.ROOT);
            if (host.startsWith("www.")) host = host.substring(4);
            String path = u.getRawPath() == null ? "" : u.getRawPath();
            while (path.endsWith("/")) path = path.substring(0, path.length() - 1);
            return host + path;
        } catch (Exception e) {
            return "";
        }
    }

    /** The links the page script listed, on the page's own site only, without repeats. */
    static List<GrabQueue.Item> parseLinks(String json, String pageUrl) {
        List<GrabQueue.Item> out = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        String site = host(pageUrl);
        try {
            JSONArray rows = new JSONArray(json == null || json.isEmpty() ? "[]" : json);
            for (int i = 0; i < rows.length() && out.size() < MAX_LINKS; i++) {
                JSONObject row = rows.optJSONObject(i);
                if (row == null) continue;
                String url = ServerAddress.nextChapterUrl(pageUrl, row.optString("url", ""));
                if (url == null || !host(url).equals(site) || !seen.add(key(url))) continue;
                out.add(new GrabQueue.Item(url, 1, GrabQueue.nameFor(row.optString("title", ""), url)));
            }
        } catch (org.json.JSONException e) {
            // A page script that answers nonsense lists no chapters.
        }
        return out;
    }

    private static String host(String url) {
        String k = key(url);
        int slash = k.indexOf('/');
        return slash < 0 ? k : k.substring(0, slash);
    }

    private static double numberOf(String title) {
        Matcher m = NUMBER.matcher(title);
        return m.find() ? Double.parseDouble(m.group()) : Double.NaN;
    }

    /** Oldest chapter first: sites list newest first as often as not, so the order is read from the numbers. */
    static List<GrabQueue.Item> readingOrder(List<GrabQueue.Item> links) {
        List<GrabQueue.Item> out = new ArrayList<>(links);
        double first = Double.NaN;
        double last = Double.NaN;
        for (GrabQueue.Item item : out) {
            double n = numberOf(item.name);
            if (Double.isNaN(n)) continue;
            if (Double.isNaN(first)) first = n;
            last = n;
        }
        if (!Double.isNaN(first) && first > last) Collections.reverse(out);
        return out;
    }

    /** Enough for several long series; the oldest keys go first. */
    static final int MAX_DONE = 5000;

    /** The stored list of finished chapter keys with this chapter added last. */
    static String withDone(String json, String url) {
        String k = key(url);
        List<String> keys = new ArrayList<>(doneKeys(json));
        if (!k.isEmpty()) {
            keys.remove(k);
            keys.add(k);
        }
        int from = Math.max(0, keys.size() - MAX_DONE);
        return new JSONArray(keys.subList(from, keys.size())).toString();
    }

    static LinkedHashSet<String> doneKeys(String json) {
        LinkedHashSet<String> out = new LinkedHashSet<>();
        try {
            JSONArray rows = new JSONArray(json == null || json.isEmpty() ? "[]" : json);
            for (int i = 0; i < rows.length(); i++) {
                String k = rows.optString(i, "");
                if (!k.isEmpty()) out.add(k);
            }
        } catch (org.json.JSONException e) {
            // A damaged list remembers nothing, so chapters are grabbed again rather than skipped.
        }
        return out;
    }

    /**
     * A history row written before chapters were remembered on their own. Only a row that sent
     * every page with English counts; anything partial is grabbed again.
     */
    static boolean isDone(GrabQueue.Done done) {
        String m = done.message;
        return m.startsWith("Sent ") && !m.startsWith("Sent 0 ") && !m.contains("Not sent")
                && !m.contains("without English");
    }

    /** Whether an earlier run already sent this chapter, so a run to the last chapter can stop where an earlier one got to. */
    static boolean alreadyDone(String url, Set<String> done, List<GrabQueue.Done> history) {
        String k = key(url);
        if (k.isEmpty()) return false;
        if (done.contains(k)) return true;
        for (GrabQueue.Done d : history) if (isDone(d) && key(d.url).equals(k)) return true;
        return false;
    }

    /**
     * Whether a run to the last chapter should stop before `next`. A site that names its chapters
     * only in the query (chapter.php?id=N) gives every chapter the same key, so a next chapter that
     * looks like the one just grabbed is not taken as grabbed before.
     */
    static boolean stopsBefore(String next, String current, Set<String> done, List<GrabQueue.Done> history) {
        return !key(next).equals(key(current)) && alreadyDone(next, done, history);
    }

    static Plan plan(List<GrabQueue.Item> links, Set<String> done, List<GrabQueue.Done> history,
                     List<GrabQueue.Item> queue) {
        Set<String> finished = new HashSet<>(done);
        for (GrabQueue.Done d : history) if (isDone(d)) finished.add(key(d.url));
        Set<String> waiting = new HashSet<>();
        for (GrabQueue.Item q : queue) waiting.add(key(q.url));
        List<GrabQueue.Item> fresh = new ArrayList<>();
        int doneCount = 0;
        int queued = 0;
        for (GrabQueue.Item item : readingOrder(links)) {
            String k = key(item.url);
            if (finished.contains(k)) doneCount++;
            else if (waiting.contains(k)) queued++;
            else fresh.add(item);
        }
        return new Plan(fresh, links.size(), doneCount, queued);
    }

    /** What the person is told before anything starts: the size of the job and what it can cost. */
    static String warning(Plan plan, int queueRoom) {
        int n = Math.min(plan.fresh.size(), queueRoom);
        StringBuilder out = new StringBuilder();
        out.append(plan.total).append(" chapters are listed here");
        if (plan.done > 0) out.append("; ").append(plan.done).append(" already done");
        if (plan.waiting > 0) out.append("; ").append(plan.waiting).append(" already waiting");
        out.append(".\n\nThis adds ").append(n).append(" chapters to the queue, oldest first.");
        if (n < plan.fresh.size()) out.append(" The queue only has room for that many; run this again later for the rest.");
        out.append("\n\nTime: roughly ").append(n * 2).append(" to ").append(n * 5)
                .append(" minutes, one chapter at a time, while this phone stays on and Grab stays open."
                        + " Stop pauses it; Queue and history picks it up again.");
        out.append("\n\nCost: free if your PC translates with a free engine. With a billed engine every page of every"
                + " chapter is billed, and the monthly cap on your PC stops the run when it is spent.");
        return out.toString();
    }
}
