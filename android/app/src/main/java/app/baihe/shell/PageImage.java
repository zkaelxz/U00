package app.baihe.shell;

/** One grabbed page image: base64 body as the bridge expects it. */
final class PageImage {
    final String key;
    final String url;
    final String contentType;
    final String base64;

    PageImage(String key, String url, String contentType, String base64) {
        this.key = key;
        this.url = url;
        this.contentType = contentType;
        this.base64 = base64;
    }
}
