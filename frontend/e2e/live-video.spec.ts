import { expect, test, type Page } from '@playwright/test'

import { SCREENS, cue, mockLive, openLive } from './liveMocks'
import { mockEmbedHosts, ytFrame } from './liveVideoMocks'

// Live page: the optional stream video. The embed hosts are route-mocked.
// The picture must be gone from the DOM (not hidden) whenever playback
// should stop.

const YT = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'

async function startRunning(page: Page, url = YT, playerHtml?: string) {
  const m = await mockLive(page)
  await mockEmbedHosts(page, playerHtml)
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill(url)
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  m.state.status = 'running'
  m.state.message = 'Listening'
  m.state.cues = [cue(0), cue(1)]
  await expect(live.getByTestId('live-status')).toHaveText('Listening · 2 lines')
  return { m, live }
}

test('video plays behind live while running and is removed on Stop', async ({ page }) => {
  const { live } = await startRunning(page)
  const iframe = page.locator('iframe[title="Stream video"]')
  await expect(iframe).toHaveCount(1)
  await expect(iframe).toHaveAttribute('src', 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?enablejsapi=1&playsinline=1')
  await expect(iframe).toHaveAttribute('sandbox', 'allow-scripts allow-same-origin allow-presentation')
  await expect(iframe).toHaveAttribute('referrerpolicy', 'strict-origin-when-cross-origin')
  // The default 15 s delay: seek to 300 - 15.
  await expect.poll(() => ytFrame(page)?.evaluate(() => (window as unknown as { __cmds: unknown[] }).__cmds.length)).toBeGreaterThan(0)
  expect(await ytFrame(page)!.evaluate(() => (window as unknown as { __cmds: { func: string; args: number[] }[] }).__cmds[0]))
    .toMatchObject({ func: 'seekTo', args: [285, true] })
  await expect(live.getByTestId('live-video-note')).toHaveText('Playing about 15 s behind live.')
  await page.screenshot({ path: `${SCREENS}/desktop-video.png`, fullPage: true })

  // Moving the slider seeks again.
  await live.getByLabel('Video delay', { exact: true }).fill('30')
  await expect.poll(() => ytFrame(page)?.evaluate(() => (window as unknown as { __cmds: { args: number[] }[] }).__cmds.at(-1)?.args[0])).toBe(270)
  await expect.poll(() => page.evaluate(() => localStorage.getItem('baihe.pref.live.videoDelay'))).toBe('30')

  await live.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(live.getByTestId('live-status')).toHaveText('Stopped · 2 lines')
  await expect(page.locator('iframe')).toHaveCount(0)
})

test('toggling Show video off removes the iframe, and the choice is remembered', async ({ page }) => {
  const { live } = await startRunning(page)
  await expect(page.locator('iframe')).toHaveCount(1)
  await live.getByRole('switch', { name: 'Show video' }).click()
  await expect(page.locator('iframe')).toHaveCount(0)
  await expect.poll(() => page.evaluate(() => localStorage.getItem('baihe.pref.live.showVideo'))).toBe('false')
  await live.getByRole('switch', { name: 'Show video' }).click()
  await expect(page.locator('iframe')).toHaveCount(1)
})

test('leaving the page removes the iframe', async ({ page }) => {
  await startRunning(page)
  await expect(page.locator('iframe')).toHaveCount(1)
  await page.getByRole('link', { name: 'Library', exact: true }).click()
  await expect(page).toHaveURL(/#\/library/)
  await expect(page.locator('iframe')).toHaveCount(0)
})

test('a stream that cannot be rewound says so and is left at the live edge', async ({ page }) => {
  test.setTimeout(45_000)
  const { live } = await startRunning(page, YT, '<p>no dvr</p>')
  await expect(page.locator('iframe')).toHaveCount(1)
  await expect(live.getByTestId('live-video-note')).toHaveText(
    "This stream can't be delayed, so the picture runs ahead of the lines.", { timeout: 20_000 })
})

test('an unsupported site shows one calm line and no iframe', async ({ page }) => {
  const { live } = await startRunning(page, 'https://www.bilibili.com/video/BV1xx411c7mD')
  await expect(live.getByText("This site can't be shown here; the lines still work.")).toBeVisible()
  await expect(page.locator('iframe')).toHaveCount(0)
  await expect(live.getByRole('list', { name: 'Live lines, newest first' }).getByRole('listitem')).toHaveCount(2)
})

test('a lookalike host never reaches an iframe', async ({ page }) => {
  await startRunning(page, 'https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ')
  await expect(page.locator('iframe')).toHaveCount(0)
})
