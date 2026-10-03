import type { Frame, Page } from '@playwright/test'

// A tiny stand-in for the YouTube embed: answers "listening" with a player
// report (a 300 s seekable window) and keeps every command it receives.
export const YT_HTML = `<!doctype html><title>fake player</title><p>player</p><script>
window.__cmds = [];
addEventListener('message', function (e) {
  var m = JSON.parse(e.data);
  if (m.event === 'command') window.__cmds.push(m);
  if (m.event === 'listening') parent.postMessage(JSON.stringify({ event: 'infoDelivery', info: { currentTime: 290, duration: 300 } }), '*');
});
</script>`

export async function mockEmbedHosts(page: Page, html = YT_HTML) {
  await page.route('https://www.youtube-nocookie.com/**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: html }))
  await page.route('https://player.twitch.tv/**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<p>twitch</p>' }))
}

export const ytFrame = (page: Page): Frame | undefined => page.frames().find((f) => f.url().includes('youtube-nocookie.com'))
