import { expect, test, type Page } from '@playwright/test'

import { SCREENS, cue, mockLive, openLive } from './liveMocks'
import { mockEmbedHosts, ytFrame, ytHtml } from './liveVideoMocks'

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

test('the player may go fullscreen', async ({ page }) => {
  await startRunning(page)
  const iframe = page.locator('iframe[title="Stream video"]')
  await expect(iframe).toHaveAttribute('allowfullscreen', '')
  await expect(iframe).toHaveAttribute('allow', /(^|;\s*)fullscreen(;|$)/)
  // The sandbox has no token that blocks fullscreen; the browser's own answer for the cross-origin frame is the proof.
  await expect.poll(() => ytFrame(page)?.evaluate(() => document.fullscreenEnabled)).toBe(true)
})

test('the note follows the slider within a second and reports what the player did', async ({ page }) => {
  const { live } = await startRunning(page)
  const note = live.getByTestId('live-video-note')
  await expect(note).toHaveText('Playing about 15 s behind live.')
  await live.getByLabel('Video delay', { exact: true }).fill('40')
  await expect(note).toHaveText('Playing about 40 s behind live.', { timeout: 1_500 })
  await live.getByLabel('Video delay', { exact: true }).fill('0')
  await expect(note).toHaveText('Playing about 0 s behind live.', { timeout: 1_500 })
})

test('says when the stream allows less rewind than asked', async ({ page }) => {
  const { live } = await startRunning(page, YT, ytHtml(45))
  await expect(live.getByTestId('live-video-note')).toHaveText('Playing about 15 s behind live.')
  await live.getByLabel('Video delay', { exact: true }).fill('90')
  await expect(live.getByTestId('live-video-note')).toHaveText(
    'Playing about 45 s behind live (this stream allows at most 45 s).', { timeout: 1_500 })
})

test('shows a waiting note until the player reports, then asks again when seeks are dropped', async ({ page }) => {
  test.setTimeout(45_000)
  const { live } = await startRunning(page, YT, ytHtml(300, 2))
  const note = live.getByTestId('live-video-note')
  await expect(note).toHaveText('Moving to about 15 s behind live…')
  await expect(note).toHaveText('Playing about 15 s behind live.', { timeout: 10_000 })
  expect(await ytFrame(page)!.evaluate(() => (window as unknown as { __cmds: unknown[] }).__cmds.length)).toBe(3)
})

test('before the player reports the note says it is waiting', async ({ page }) => {
  test.setTimeout(45_000)
  const { live } = await startRunning(page, YT, '<p>silent</p>')
  await expect(live.getByTestId('live-video-note')).toHaveText('Waiting for the player…')
})

test('Larger video gives the picture the row and puts the lines under it, and is remembered', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  const { live } = await startRunning(page)
  const frame = page.locator('iframe[title="Stream video"]')
  const list = live.getByRole('list', { name: 'Live lines, newest first' })
  const body = page.locator('.live-body')

  // Default wide layout: lines left, video right and the larger column.
  let f = (await frame.boundingBox())!
  let l = (await list.boundingBox())!
  let b = (await body.boundingBox())!
  expect(f.x).toBeGreaterThan(l.x + l.width - 1)
  expect(f.width / b.width).toBeGreaterThan(0.5)
  expect(f.width).toBeGreaterThan(440)

  await live.getByRole('switch', { name: 'Larger video' }).click()
  f = (await frame.boundingBox())!
  l = (await list.boundingBox())!
  b = (await body.boundingBox())!
  expect(f.y + f.height).toBeLessThanOrEqual(l.y)
  expect(f.width).toBeGreaterThan(b.width * 0.95)
  await expect.poll(() => page.evaluate(() => localStorage.getItem('baihe.pref.live.theater'))).toBe('true')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  await page.screenshot({ path: `${SCREENS}/desktop-theater.png`, fullPage: true })

  await live.getByRole('switch', { name: 'Larger video' }).click()
  f = (await frame.boundingBox())!
  l = (await list.boundingBox())!
  expect(f.x).toBeGreaterThan(l.x)
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
