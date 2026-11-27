package app.baihe.shell;

import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PixelFormat;
import android.graphics.RectF;
import android.graphics.drawable.Drawable;

/** The check box and radio mark Grab's settings draw in the page's theme colours, instead of font glyphs that ignore them. */
final class GrabMark extends Drawable {
    private final boolean radio;
    private final boolean on;
    private final int accent;
    private final int edge;
    private final int onAccent;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);

    GrabMark(boolean radio, boolean on, GrabUi.Palette p) {
        this.radio = radio;
        this.on = on;
        this.accent = p.accent;
        this.edge = p.fieldEdge;
        this.onAccent = p.onAccent;
    }

    @Override
    public void draw(Canvas canvas) {
        float s = Math.min(getBounds().width(), getBounds().height());
        float stroke = s / 11f;
        RectF box = new RectF(getBounds().left + stroke, getBounds().top + stroke,
                getBounds().left + s - stroke, getBounds().top + s - stroke);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(stroke);
        paint.setColor(on ? accent : edge);
        if (radio) {
            canvas.drawOval(box, paint);
            if (on) {
                paint.setStyle(Paint.Style.FILL);
                canvas.drawCircle(box.centerX(), box.centerY(), s * 0.22f, paint);
            }
            return;
        }
        float corner = s * 0.2f;
        if (on) {
            paint.setStyle(Paint.Style.FILL);
            canvas.drawRoundRect(box, corner, corner, paint);
            Path tick = new Path();
            tick.moveTo(box.left + s * 0.2f, box.centerY());
            tick.lineTo(box.left + s * 0.4f, box.bottom - s * 0.25f);
            tick.lineTo(box.right - s * 0.18f, box.top + s * 0.25f);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeCap(Paint.Cap.ROUND);
            paint.setStrokeJoin(Paint.Join.ROUND);
            paint.setColor(onAccent);
            canvas.drawPath(tick, paint);
        } else {
            canvas.drawRoundRect(box, corner, corner, paint);
        }
    }

    @Override
    public void setAlpha(int alpha) {
        paint.setAlpha(alpha);
    }

    @Override
    public void setColorFilter(ColorFilter filter) {
        paint.setColorFilter(filter);
    }

    @Override
    public int getOpacity() {
        return PixelFormat.TRANSLUCENT;
    }
}
