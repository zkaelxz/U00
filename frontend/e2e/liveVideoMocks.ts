import type { Frame, Page } from '@playwright/test'

// A tiny stand-in for the YouTube embed. It plays a live stream with a
// seekable window of `windowS` seconds: starts at the live edge, answers
// "listening" with a report, clamps seekTo into the window like the real
// player and reports again. It keeps every command it receives, and `__ignore`
// makes it drop the next N seeks (a player that was not ready).
//
// It starts unstarted (state -1) like a real embed: it reports no window and
// drops seeks until it plays. It plays by itself when the address carries
// autoplay=1, unless `blockSound` (a browser autoplay policy): then only
// playVideo after mute starts it. `blockAll` never starts it by itself.
// `elapsedS` makes duration the time since the stream began (as YouTube documents
// for live events) while currentTime stays inside the window, so duration minus
// currentTime is not the delay. `advancing` also makes the clocks run: currentTime
// counts from where playback started (0), the live edge is W on that clock, and
// both move on every second, as a real player's do.
export const ytHtml = (windowS = 300, ignoreSeeks = 0, opts: { blockSound?: boolean; blockAll?: boolean; elapsedS?: number; advancing?: boolean } = {}) => `<!doctype html><title>fake player</title><p>player</p><script>
window.__cmds = []; window.__ignore = ${ignoreSeeks};
var W = ${windowS}, dur = 0, cur = 0, state = -1, muted = false;
var blockSound = ${!!opts.blockSound}, blockAll = ${!!opts.blockAll}, elapsed = ${opts.elapsedS ?? 0}, advancing = ${!!opts.advancing}, edge = W;
function start() {
  state = 1; dur = elapsed || W; cur = advancing ? 0 : W; report();
  if (advancing) setInterval(function () { cur += 1; edge += 1; dur += 1; report(); }, 1000);
}
function report() { parent.postMessage(JSON.stringify({ event: 'infoDelivery', info: { currentTime: cur, duration: dur, playerState: state, videoData: { isLive: true } } }), '*'); }
if (/autoplay=1/.test(location.search) && !blockSound && !blockAll) setTimeout(start, 50);
addEventListener('message', function (e) {
  var m = JSON.parse(e.data);
  if (m.event === 'command') {
    window.__cmds.push(m);
    if (m.func === 'mute') muted = true;
    if (m.func === 'unMute') muted = false;
    window.__muted = muted;
    if (m.func === 'playVideo' && state === -1 && !blockAll && (!blockSound || muted)) start();
    if (m.func === 'seekTo' && state !== -1 && window.__ignore-- <= 0) { cur = Math.max(0, Math.min(edge, m.args[0])); report(); }
  }
  if (m.event === 'listening') report();
});
</script>`
export const YT_HTML = ytHtml()

export async function mockEmbedHosts(page: Page, html = YT_HTML) {
  await page.route('https://www.youtube-nocookie.com/**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: html }))
  await page.route('https://player.twitch.tv/**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<p>twitch</p>' }))
}

export const ytFrame = (page: Page): Frame | undefined => page.frames().find((f) => f.url().includes('youtube-nocookie.com'))
