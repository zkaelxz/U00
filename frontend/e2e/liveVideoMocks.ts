import type { Frame, Page } from '@playwright/test'

// A tiny stand-in for the YouTube embed. It plays a live stream with a
// seekable window of `windowS` seconds: starts at the live edge, answers
// "listening" with a report, clamps seekTo into the window like the real
// player and reports again. It keeps every command it receives, and `__ignore`
// makes it drop the next N seeks (a player that was not ready).
export const ytHtml = (windowS = 300, ignoreSeeks = 0) => `<!doctype html><title>fake player</title><p>player</p><script>
window.__cmds = []; window.__ignore = ${ignoreSeeks};
var dur = ${windowS}, cur = ${windowS};
function report() { parent.postMessage(JSON.stringify({ event: 'infoDelivery', info: { currentTime: cur, duration: dur, videoData: { isLive: true } } }), '*'); }
addEventListener('message', function (e) {
  var m = JSON.parse(e.data);
  if (m.event === 'command') {
    window.__cmds.push(m);
    if (m.func === 'seekTo' && window.__ignore-- <= 0) { cur = Math.max(0, Math.min(dur, m.args[0])); report(); }
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
