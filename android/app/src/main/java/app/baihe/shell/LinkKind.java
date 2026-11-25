package app.baihe.shell;

import java.net.URI;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

/** Tells a video link from a comic or novel link by its site, so Baihe offers the right thing for a copied link. */
final class LinkKind {
    enum Kind { VIDEO, COMIC, NONE }

    /** Sites whose pages are videos or streams Baihe can subtitle. */
    private static final List<String> VIDEO_HOSTS = Arrays.asList(
            "youtube.com", "youtu.be", "bilibili.com", "b23.tv", "twitch.tv", "nicovideo.jp",
            "vimeo.com", "tiktok.com", "douyin.com");
    /**
     * Comic and novel sites that have an adapter in sources/adapters. A site that moves to a new
     * address needs its new host added here, or its copied links are simply not offered.
     */
    private static final List<String> COMIC_HOSTS = Arrays.asList(
            "manga.bilibili.com", "rawkuma.net", "manhuagui.com", "baozimh.org", "bzmh.org",
            "kuaikanmanhua.com", "mangak.io", "mh03.com", "guazimanhua.com", "weloma.net", "twmanga.com",
            "marumaru108.com", "nihonkuni.com", "zerosumonline.com", "syosetu.com", "piaotia.com",
            "ptwxz.com", "xbanxia.cc", "52shuku.net", "lightnovel.fun", "fanjiao.co", "miaoqumh.org");

    static Kind of(String link) {
        String host = host(link);
        if (host == null) return Kind.NONE;
        // Comic sites first: Bilibili's manga site sits under the video site's name.
        if (matches(host, COMIC_HOSTS)) return Kind.COMIC;
        if (matches(host, VIDEO_HOSTS)) return Kind.VIDEO;
        return Kind.NONE;
    }

    private static boolean matches(String host, List<String> sites) {
        for (String site : sites) {
            if (host.equals(site) || host.endsWith("." + site)) return true;
        }
        return false;
    }

    static String host(String link) {
        if (link == null) return null;
        try {
            String host = new URI(link).getHost();
            return host == null ? null : host.toLowerCase(Locale.ROOT);
        } catch (Exception e) {
            return null;
        }
    }

    private LinkKind() {}
}
