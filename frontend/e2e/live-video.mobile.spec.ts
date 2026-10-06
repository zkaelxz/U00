import { expect, test } from '@playwright/test'

import { SCREENS, cue, mockLive, openLive } from './liveMocks'
import { mockEmbedHosts } from './liveVideoMocks'

// Phone project (390x844, touch): the video sits above the lines, 16:9,
// no sideways scroll, 44px controls.

test('video above the lines on a phone', async ({ page }) => {
  const m = await mockLive(page)
  await mockEmbedHosts(page)
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill('https://youtu.be/dQw4w9WgXcQ')
  await live.getByRole('button', { name: 'Start', exact: true }).tap()
  m.state.status = 'running'
  m.state.message = 'Listening'
  m.state.cues = [cue(0), cue(1), cue(2)]
  await expect(live.getByTestId('live-status')).toHaveText('Listening · 3 lines')
  const iframe = page.locator('iframe[title="Stream video"]')
  await expect(iframe).toHaveCount(1)

  const frameBox = (await iframe.boundingBox())!
  const listBox = (await live.getByRole('list', { name: 'Live lines, newest first' }).boundingBox())!
  expect(frameBox.y + frameBox.height).toBeLessThanOrEqual(listBox.y)
  expect(frameBox.width / frameBox.height).toBeCloseTo(16 / 9, 1)
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll).toBeLessThanOrEqual(client)

  const small = await live.locator('.live-video').locator('button:not(.field-help-btn, .toggle), input').evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, what: e.getAttribute('aria-label') ?? e.getAttribute('type') }))
      .filter((x) => x.h < 44))
  expect(small).toEqual([])
  await page.screenshot({ path: `${SCREENS}/phone-video.png`, fullPage: true })

  await live.getByRole('button', { name: 'Stop', exact: true }).tap()
  await expect(live.getByTestId('live-status')).toHaveText('Stopped · 3 lines')
  await expect(page.locator('iframe')).toHaveCount(0)
})

test('Larger video is a desktop control: on a phone the layout is the same either way', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('baihe.pref.live.theater', 'true'))
  const m = await mockLive(page)
  await mockEmbedHosts(page)
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill('https://youtu.be/dQw4w9WgXcQ')
  await live.getByRole('button', { name: 'Start', exact: true }).tap()
  m.state.status = 'running'
  m.state.message = 'Listening'
  m.state.cues = [cue(0), cue(1)]
  await expect(live.getByTestId('live-status')).toHaveText('Listening · 2 lines')
  await expect(live.getByRole('switch', { name: 'Larger video' })).toBeHidden()
  const frameBox = (await page.locator('iframe[title="Stream video"]').boundingBox())!
  const listBox = (await live.getByRole('list', { name: 'Live lines, newest first' }).boundingBox())!
  expect(frameBox.y + frameBox.height).toBeLessThanOrEqual(listBox.y)
  expect(frameBox.width).toBeGreaterThan(280)
  await expect(live.getByTestId('live-video-note')).toHaveText('Playing about 15 s behind live.')
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll).toBeLessThanOrEqual(client)
})
