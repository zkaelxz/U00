package app.baihe.shell;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.util.ArrayList;
import java.util.List;

/**
 * Shrinks page pictures before a Quick read upload: the PC starts a batch only once the whole
 * request has arrived, so on a slow link the upload, not the PC, sets how soon a page shows.
 */
final class ImageShrink {
    /** OCR and lettering stay accurate at this width, and a page this wide is about 100 to 150 KB as JPEG, which a slow link can carry. */
    static final int MAX_WIDTH = 1000;
    /** A picture already about this small (150 KB) is sent as it is. */
    static final int SMALL_BASE64_CHARS = 200_000;
    private static final int JPEG_QUALITY = 65;

    private ImageShrink() {}

    /** The width to scale to, or 0 when the picture should be re-encoded at its own size. */
    static int targetWidth(int width) {
        return width > MAX_WIDTH ? MAX_WIDTH : 0;
    }

    static boolean worthShrinking(PageImage image) {
        return image.base64.length() > SMALL_BASE64_CHARS;
    }

    static List<PageImage> forUpload(List<PageImage> batch) {
        List<PageImage> out = new ArrayList<>(batch.size());
        for (PageImage image : batch) out.add(forUpload(image));
        return out;
    }

    /** The smaller copy, or the original when it is small already or cannot be decoded. */
    static PageImage forUpload(PageImage image) {
        if (!worthShrinking(image)) return image;
        try {
            byte[] raw = Base64.decode(image.base64, Base64.DEFAULT);
            BitmapFactory.Options bounds = new BitmapFactory.Options();
            bounds.inJustDecodeBounds = true;
            BitmapFactory.decodeByteArray(raw, 0, raw.length, bounds);
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return image;
            BitmapFactory.Options decode = new BitmapFactory.Options();
            decode.inSampleSize = Math.max(1, bounds.outWidth / (MAX_WIDTH * 2));
            Bitmap bitmap = BitmapFactory.decodeByteArray(raw, 0, raw.length, decode);
            if (bitmap == null) return image;
            int width = targetWidth(bitmap.getWidth());
            if (width > 0) {
                int height = Math.max(1, Math.round(bitmap.getHeight() * (width / (float) bitmap.getWidth())));
                Bitmap scaled = Bitmap.createScaledBitmap(bitmap, width, height, true);
                if (scaled != bitmap) bitmap.recycle();
                bitmap = scaled;
            }
            ByteArrayOutputStream jpeg = new ByteArrayOutputStream();
            bitmap.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, jpeg);
            bitmap.recycle();
            String encoded = Base64.encodeToString(jpeg.toByteArray(), Base64.NO_WRAP);
            if (encoded.length() >= image.base64.length()) return image;
            return new PageImage(image.key, image.url, "image/jpeg", encoded);
        } catch (RuntimeException | OutOfMemoryError e) {
            return image;
        }
    }
}
