package app.baihe.shell;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Quick read lets the PC download pictures itself, so a slow phone link carries only addresses.
 * What the PC cannot fetch comes back as "unfetched" and goes up from the phone as before.
 */
final class PcFetch {
    private PcFetch() {}

    /** The batch with every picture that has a web address marked for the PC; null when none has. */
    static List<PageImage> mark(List<PageImage> batch) {
        List<PageImage> out = new ArrayList<>(batch.size());
        boolean any = false;
        for (PageImage image : batch) {
            if (image.url.startsWith("http://") || image.url.startsWith("https://")) {
                out.add(image.withFetch());
                any = true;
            } else {
                out.add(image);
            }
        }
        return any ? out : null;
    }

    /** The pictures of `batch` named by the PC's "unfetched" list, in page order. */
    static List<PageImage> unfetched(List<PageImage> batch, JSONArray unfetched) {
        Set<String> keys = new HashSet<>();
        for (int i = 0; unfetched != null && i < unfetched.length(); i++) {
            JSONObject item = unfetched.optJSONObject(i);
            if (item != null) keys.add(item.optString("key", ""));
        }
        List<PageImage> out = new ArrayList<>();
        for (PageImage image : batch) if (keys.contains(image.key)) out.add(image);
        return out;
    }
}
