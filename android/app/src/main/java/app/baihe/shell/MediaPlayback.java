package app.baihe.shell;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ServiceInfo;
import android.graphics.drawable.Icon;
import android.media.AudioManager;
import android.media.MediaMetadata;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.view.KeyEvent;

/**
 * The lock-screen, notification and headset controls for the Watch page's player. The page's own audio or
 * video element keeps playing inside the WebView; this only mirrors what the page reports and hands presses
 * back to it, so there is no second player whose place could drift from the page's. Main thread only.
 *
 * Audio focus is left to the WebView: its player already takes focus and pauses when a call or another app
 * takes it, and a request from here would take focus away from the WebView and pause the page.
 */
public class MediaPlayback extends Service {
    private static final String CHANNEL = "playback";
    private static final int NOTIFICATION_ID = 9;
    private static final String PRESS = "app.baihe.shell.media.";
    private static final String CLOSE = "close";
    private static final String DISMISS = PRESS + CLOSE;
    /** The activity intent of a tap on the controls: MainActivity only comes to the front for it. */
    static final String SHOW_PLAYER = "app.baihe.shell.SHOW_PLAYER";
    /**
     * How long paused controls stay. The service stays foreground meanwhile: a plain started service is stopped
     * by Android about a minute after the app goes idle, which would take the controls with it.
     */
    static final long PAUSE_GRACE_MS = 10L * 60 * 1000;

    /** Where presses go: the page that reported. */
    interface Page {
        void send(String action, double value);
    }

    private static Page page;
    private static MediaState latest;
    private static MediaPlayback running;
    /** startForegroundService was called and its startForeground is still owed. */
    private static boolean startOwed;

    private MediaSession session;
    private boolean foreground;
    private boolean noisyListening;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Runnable pauseOver = this::finish;
    /** What the posted notification and metadata show, so a position-only report touches neither. */
    private String shownNotification;
    private String shownMetadata;

    /** Headphones pulled out or a Bluetooth set gone: the sound would carry on from the speaker. */
    private final BroadcastReceiver noisy = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            send(MediaState.PAUSE, 0);
        }
    };

    static void attach(Page target) {
        page = target;
    }

    static void detach(Page target) {
        if (page == target) page = null;
        report(null, null);
    }

    /**
     * Sends the last report again when it was playing but its service never started (Android refused a start
     * from the background), moved on to now so the lock screen shows where the sound is.
     */
    static void retryIfRefused(Context context) {
        MediaState state = latest;
        if (running == null && !startOwed && state != null && state.status == MediaState.Status.PLAYING) {
            report(context, state.advancedTo(System.currentTimeMillis()));
        }
    }

    /** The page's latest report; null or stopped ends the controls. Never throws. */
    static void report(Context context, MediaState state) {
        latest = state;
        if (state == null || state.status == MediaState.Status.STOPPED) {
            if (running != null) running.finish();
            return;
        }
        if (running != null) {
            running.show(state);
            return;
        }
        // A paused player that never played from here needs no controls yet.
        if (state.status != MediaState.Status.PLAYING || context == null) return;
        try {
            context.startForegroundService(new Intent(context, MediaPlayback.class));
            startOwed = true;
        } catch (RuntimeException ignored) {
            // Android refuses a foreground start from the background; the page still plays while the app is open.
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        running = this;
        session = new MediaSession(this, "Baihe");
        session.setCallback(new MediaSession.Callback() {
            @Override
            public void onPlay() {
                send(MediaState.PLAY, 0);
            }

            @Override
            public void onPause() {
                send(MediaState.PAUSE, 0);
            }

            @Override
            public void onStop() {
                send(MediaState.PAUSE, 0);
                finish();
            }

            @Override
            public void onSeekTo(long positionMs) {
                send(MediaState.SEEK, positionMs / 1000.0);
            }

            @Override
            public void onFastForward() {
                send(MediaState.FORWARD, MediaState.SKIP_SECONDS);
            }

            @Override
            public void onRewind() {
                send(MediaState.BACK, MediaState.SKIP_SECONDS);
            }

            @Override
            public void onCustomAction(String action, Bundle extras) {
                if (MediaState.FORWARD.equals(action) || MediaState.BACK.equals(action)) send(action, MediaState.SKIP_SECONDS);
                else if (CLOSE.equals(action)) close();
            }

            @Override
            @SuppressWarnings("deprecation")
            public boolean onMediaButtonEvent(Intent intent) {
                // A headset's next and previous skip 10 seconds; declaring skip actions instead would put track
                // arrows on the lock screen in place of the 10-second buttons.
                KeyEvent key = intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT);
                if (key != null && key.getAction() == KeyEvent.ACTION_DOWN && key.getRepeatCount() == 0) {
                    if (key.getKeyCode() == KeyEvent.KEYCODE_MEDIA_NEXT) {
                        send(MediaState.FORWARD, MediaState.SKIP_SECONDS);
                        return true;
                    }
                    if (key.getKeyCode() == KeyEvent.KEYCODE_MEDIA_PREVIOUS) {
                        send(MediaState.BACK, MediaState.SKIP_SECONDS);
                        return true;
                    }
                }
                return super.onMediaButtonEvent(intent);
            }
        });
        session.setSessionActivity(PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class).setAction(SHOW_PLAYER).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
        manager().createNotificationChannel(new NotificationChannel(
                CHANNEL, "Baihe playback", NotificationManager.IMPORTANCE_LOW));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? null : intent.getAction();
        if (DISMISS.equals(action)) {
            close();
            return START_NOT_STICKY;
        }
        if (action != null && action.startsWith(PRESS)) {
            String press = action.substring(PRESS.length());
            if (latest == null) finish();
            else send(press, MediaState.PLAY.equals(press) || MediaState.PAUSE.equals(press) ? 0 : MediaState.SKIP_SECONDS);
            return START_NOT_STICKY;
        }
        // A start from report(): Android requires startForeground even when the page has paused or stopped since.
        MediaState state = latest;
        goForeground(notification(state));
        startOwed = false;
        if (state == null || state.status == MediaState.Status.STOPPED) finish();
        else show(state);
        return START_NOT_STICKY;
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        // Swiped away from recent apps: nothing is left to bring the page back to.
        send(MediaState.PAUSE, 0);
        finish();
    }

    @Override
    public void onDestroy() {
        main.removeCallbacks(pauseOver);
        // After finish() a newer instance may already own the notification, so only a stop finish() did not see clears it.
        if (running == this) {
            running = null;
            manager().cancel(NOTIFICATION_ID);
        }
        listenForNoisy(false);
        session.release();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    /** Close or a swipe: the sound stops with the controls, or it would carry on with nothing to stop it. */
    private void close() {
        if (page != null) page.send(MediaState.PAUSE, 0);
        finish();
    }

    private void send(String action, double value) {
        if (page != null) page.send(action, value);
        else finish();
    }

    private void show(MediaState state) {
        boolean playing = state.status == MediaState.Status.PLAYING;
        String metadataKey = titleOf(state) + "\n" + artistOf(state) + "\n" + state.durationMs;
        if (!metadataKey.equals(shownMetadata)) {
            session.setMetadata(metadata(state));
            shownMetadata = metadataKey;
        }
        session.setPlaybackState(playbackState(state));
        session.setActive(true);
        listenForNoisy(playing);
        main.removeCallbacks(pauseOver);
        if (!playing) main.postDelayed(pauseOver, PAUSE_GRACE_MS);
        String notificationKey = state.status + "\n" + titleOf(state) + "\n" + artistOf(state);
        // A playing player that is not foreground yet (a start Android refused) tries again on each report.
        if (notificationKey.equals(shownNotification) && (foreground || !playing)) return;
        shownNotification = notificationKey;
        Notification n = notification(state);
        if (playing && goForeground(n)) return;
        // Paused, the service stays foreground for the grace period, so the same notification only changes.
        manager().notify(NOTIFICATION_ID, n);
    }

    private boolean goForeground(Notification n) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
            } else {
                startForeground(NOTIFICATION_ID, n);
            }
            foreground = true;
            return true;
        } catch (RuntimeException e) {
            // Refused from the background (Android 12+ outside its exemptions): the plain notification still shows.
            return false;
        }
    }

    private void finish() {
        // An instance already finished (or replaced) must not touch the notification a newer one owns.
        if (running != this) return;
        if (startOwed) {
            // Stopped between the start and its first command: an unpaid startForeground crashes the app.
            goForeground(notification(null));
            startOwed = false;
        }
        // Reports from here on start a new instance instead of reaching one that is going away.
        running = null;
        main.removeCallbacks(pauseOver);
        listenForNoisy(false);
        session.setActive(false);
        stopForeground(STOP_FOREGROUND_REMOVE);
        foreground = false;
        manager().cancel(NOTIFICATION_ID);
        stopSelf();
    }

    private void listenForNoisy(boolean on) {
        if (on == noisyListening) return;
        noisyListening = on;
        try {
            if (on) registerReceiver(noisy, new IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY));
            else unregisterReceiver(noisy);
        } catch (RuntimeException ignored) {
            // Not registered after all; nothing to undo.
        }
    }

    private MediaMetadata metadata(MediaState state) {
        MediaMetadata.Builder b = new MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE, titleOf(state))
                .putString(MediaMetadata.METADATA_KEY_ARTIST, artistOf(state));
        if (state.durationMs > 0) b.putLong(MediaMetadata.METADATA_KEY_DURATION, state.durationMs);
        return b.build();
    }

    private PlaybackState playbackState(MediaState state) {
        long actions = PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE | PlaybackState.ACTION_PLAY_PAUSE
                | PlaybackState.ACTION_STOP | PlaybackState.ACTION_FAST_FORWARD | PlaybackState.ACTION_REWIND;
        if (state.durationMs > 0) actions |= PlaybackState.ACTION_SEEK_TO;
        // The system moves the shown place on from the moment the page read it, so nothing here counts time.
        long readAt = SystemClock.elapsedRealtime() - state.ageMs(System.currentTimeMillis());
        return new PlaybackState.Builder()
                .setActions(actions)
                .setState(state.status == MediaState.Status.PLAYING ? PlaybackState.STATE_PLAYING : PlaybackState.STATE_PAUSED,
                        state.positionMs, state.extrapolationSpeed(), readAt)
                // Android 13+ draws its controls from these, not from the notification's buttons.
                .addCustomAction(new PlaybackState.CustomAction.Builder(
                        MediaState.BACK, "Back 10 seconds", android.R.drawable.ic_media_rew).build())
                .addCustomAction(new PlaybackState.CustomAction.Builder(
                        MediaState.FORWARD, "Forward 10 seconds", android.R.drawable.ic_media_ff).build())
                // Android 8-13 cannot swipe a foreground notification away, and paused controls stay foreground.
                .addCustomAction(new PlaybackState.CustomAction.Builder(
                        CLOSE, "Close", android.R.drawable.ic_menu_close_clear_cancel).build())
                .build();
    }

    private Notification notification(MediaState state) {
        boolean playing = state != null && state.status == MediaState.Status.PLAYING;
        // A locked phone set to hide sensitive content shows this one: a title can be adult or private.
        Notification.MediaStyle plain = new Notification.MediaStyle().setShowActionsInCompactView(0, 1, 2);
        Notification shown = controls(playing, "Baihe Studio", playing ? "Playing" : "Paused")
                .setStyle(plain)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .build();
        Notification.MediaStyle withSession = new Notification.MediaStyle()
                .setMediaSession(session.getSessionToken())
                .setShowActionsInCompactView(0, 1, 2);
        return controls(playing, state == null ? "Baihe Studio" : titleOf(state), state == null ? "" : artistOf(state))
                .setStyle(withSession)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .setPublicVersion(shown)
                .build();
    }

    private Notification.Builder controls(boolean playing, String title, String text) {
        return new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle(title)
                .setContentText(text)
                .setContentIntent(session.getController().getSessionActivity())
                .setDeleteIntent(press(DISMISS))
                .setOngoing(playing)
                .setShowWhen(false)
                .addAction(button(android.R.drawable.ic_media_rew, "Back 10 seconds", MediaState.BACK))
                .addAction(playing
                        ? button(android.R.drawable.ic_media_pause, "Pause", MediaState.PAUSE)
                        : button(android.R.drawable.ic_media_play, "Play", MediaState.PLAY))
                .addAction(button(android.R.drawable.ic_media_ff, "Forward 10 seconds", MediaState.FORWARD))
                .addAction(button(android.R.drawable.ic_menu_close_clear_cancel, "Close", CLOSE));
    }

    private Notification.Action button(int icon, String label, String action) {
        return new Notification.Action.Builder(Icon.createWithResource(this, icon), label, press(PRESS + action)).build();
    }

    private PendingIntent press(String action) {
        return PendingIntent.getService(this, action.hashCode(),
                new Intent(this, MediaPlayback.class).setAction(action), PendingIntent.FLAG_IMMUTABLE);
    }

    private static String titleOf(MediaState state) {
        return state.title.isEmpty() ? "Baihe Studio" : state.title;
    }

    private static String artistOf(MediaState state) {
        return state.artist.isEmpty() ? "Baihe Studio" : state.artist;
    }

    private NotificationManager manager() {
        return (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
    }
}
