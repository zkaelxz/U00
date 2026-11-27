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
import android.os.IBinder;
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
    private static final String DISMISS = PRESS + "dismiss";
    /** The activity intent of a tap on the controls: MainActivity only comes to the front for it. */
    static final String SHOW_PLAYER = "app.baihe.shell.SHOW_PLAYER";

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
            finish();
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

    private void send(String action, double value) {
        if (page != null) page.send(action, value);
        else finish();
    }

    private void show(MediaState state) {
        boolean playing = state.status == MediaState.Status.PLAYING;
        session.setMetadata(metadata(state));
        session.setPlaybackState(playbackState(state));
        session.setActive(true);
        listenForNoisy(playing);
        Notification n = notification(state);
        if (playing && goForeground(n)) return;
        // Paused, the notification stays (dismissible) so the lock screen can start the sound again, but the
        // app no longer holds itself in the foreground for a player that is not playing.
        if (foreground) {
            stopForeground(STOP_FOREGROUND_DETACH);
            foreground = false;
        }
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
        if (startOwed) {
            // Stopped between the start and its first command: an unpaid startForeground crashes the app.
            goForeground(notification(null));
            startOwed = false;
        }
        // Reports from here on start a new instance instead of reaching one that is going away.
        if (running == this) running = null;
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
                .build();
    }

    private Notification notification(MediaState state) {
        boolean playing = state != null && state.status == MediaState.Status.PLAYING;
        return new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle(state == null ? "Baihe Studio" : titleOf(state))
                .setContentText(state == null ? "" : artistOf(state))
                .setContentIntent(session.getController().getSessionActivity())
                .setDeleteIntent(press(DISMISS))
                .setOngoing(playing)
                .setShowWhen(false)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .addAction(button(android.R.drawable.ic_media_rew, "Back 10 seconds", MediaState.BACK))
                .addAction(playing
                        ? button(android.R.drawable.ic_media_pause, "Pause", MediaState.PAUSE)
                        : button(android.R.drawable.ic_media_play, "Play", MediaState.PLAY))
                .addAction(button(android.R.drawable.ic_media_ff, "Forward 10 seconds", MediaState.FORWARD))
                .setStyle(new Notification.MediaStyle()
                        .setMediaSession(session.getSessionToken())
                        .setShowActionsInCompactView(0, 1, 2))
                .build();
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
