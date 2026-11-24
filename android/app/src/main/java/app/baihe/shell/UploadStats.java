package app.baihe.shell;

import java.util.Locale;

/** Adds up what a Quick read's requests cost on the phone's link, to show in its closing line. */
final class UploadStats {
    private int batches;
    private long bytes;
    private long uploadMs;
    private long waitMs;

    /** One request: its body size, how long the body took to send, and how long until the PC's first byte. */
    synchronized void add(long sentBytes, long sendMs, long firstByteMs) {
        batches++;
        bytes += sentBytes;
        uploadMs += sendMs;
        waitMs += firstByteMs;
    }

    /** Empty when nothing was sent. The rate is per connection: parallel requests overlap. */
    synchronized String describe() {
        if (batches == 0) return "";
        double mb = bytes / 1048576.0;
        double kbPerSecond = uploadMs <= 0 ? 0 : (bytes / 1024.0) / (uploadMs / 1000.0);
        return String.format(Locale.ROOT, "sent %.1f MB in %d requests at %.0f KB/s each, PC first answer %d s after",
                mb, batches, kbPerSecond, waitMs / batches / 1000);
    }
}
