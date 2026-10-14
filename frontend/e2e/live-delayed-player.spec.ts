import { expect, test } from '@playwright/test'

import { SCREENS, SID, cue, mockLive, openLive } from './liveMocks'
import { mockEmbedHosts } from './liveVideoMocks'

// Live page: the "Delayed player" option plays the server's rolling copy
// instead of the YouTube embed. The test browser cannot decode H.264, so this
// covers the wiring (the start request, the wait for the recording, the player
// files requested by name, the embed staying away); playback is checked on a PC.

const YT = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
const PLAYLIST = '#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:0\n'
const MASTER = '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="audio",DEFAULT=YES,AUTOSELECT=YES,URI="audio.m3u8"\n'
  + '#EXT-X-STREAM-INF:BANDWIDTH=2000000,AUDIO="a"\nvideo.m3u8\n'

test('the delayed player replaces the embed once the recording is ready', async ({ page }) => {
  const m = await mockLive(page)
  await mockEmbedHosts(page)
  const asked: string[] = []
  await page.route(`**/api/live/sessions/${SID}/player/*`, (route) => {
    const name = new URL(route.request().url()).pathname.split('/').pop() ?? ''
    asked.push(name)
    return route.fulfill({ status: 200, contentType: 'application/vnd.apple.mpegurl', body: name === 'master.m3u8' ? MASTER : PLAYLIST })
  })
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill(YT)
  await live.getByText('Advanced').click()
  await live.getByLabel('Delayed player', { exact: true }).click()
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  expect(m.posts[0].body).toMatchObject({ delayed_player: true })

  m.state.status = 'running'
  m.state.message = 'Listening'
  m.state.cues = [cue(0), cue(1)]
  m.state.figures = { delayed_player: true }
  await expect(live.getByTestId('live-status')).toHaveText('Listening · 2 lines')
  // Not ready yet: a note, no player files requested, and the embed is not used at all.
  await expect(live.getByTestId('live-video-note')).toHaveText('Preparing the delayed player…')
  expect(asked).toEqual([])
  await expect(page.locator('iframe[title="Stream video"]')).toHaveCount(0)

  m.state.figures = { delayed_player: true, player_ready: true }
  await expect.poll(() => asked.includes('master.m3u8'), { timeout: 15_000 }).toBe(true)
  await expect(live.getByTestId('live-delayed-video')).toHaveCount(1)
  // The delay controls are there for a stream the embed could not delay.
  await expect(live.getByLabel('Video delay', { exact: true })).toBeVisible()
  await page.screenshot({ path: `${SCREENS}/desktop-delayed-player.png`, fullPage: true })

  await live.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(live.getByTestId('live-status')).toHaveText('Stopped · 2 lines')
  await expect(live.getByTestId('live-delayed-video')).toHaveCount(0)
})

test('without the option the embed is used and the start request does not ask for the player', async ({ page }) => {
  const m = await mockLive(page)
  await mockEmbedHosts(page)
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill(YT)
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  expect(m.posts[0].body).not.toHaveProperty('delayed_player')
  m.state.status = 'running'
  m.state.cues = [cue(0)]
  await expect(page.locator('iframe[title="Stream video"]')).toHaveCount(1)
  await expect(live.getByTestId('live-delayed-video')).toHaveCount(0)
})

test('a session that records no copy keeps the embed even when the form has the option on', async ({ page }) => {
  // Started elsewhere (another tab, a followed channel): the status says so, whatever this form holds.
  const m = await mockLive(page)
  await mockEmbedHosts(page)
  const live = await openLive(page)
  await live.getByLabel('Stream link', { exact: true }).fill(YT)
  await live.getByText('Advanced').click()
  await live.getByLabel('Delayed player', { exact: true }).click()
  await live.getByRole('button', { name: 'Start', exact: true }).click()
  m.state.status = 'running'
  m.state.cues = [cue(0), cue(1)]
  m.state.figures = { delayed_player: false }
  await expect(page.locator('iframe[title="Stream video"]')).toHaveCount(1)
  await expect(live.getByTestId('live-delayed-video')).toHaveCount(0)
  await expect(live.getByText('Preparing the delayed player…')).toHaveCount(0)
})
