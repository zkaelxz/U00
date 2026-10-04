import { expect as baseExpect, test, type Page, type Route } from '@playwright/test'

// Workspace Source stage, "From a URL" (R5, PC only). Drama reads come from
// the seeded API; the download, its job and the media status are mocked,
// and any other write is aborted and recorded (must stay empty).
const expect = baseExpect.configure({ timeout: 15_000 })

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

async function mockUrlDownload(page: Page, opts: { local?: boolean; hasAudio?: boolean } = {}) {
  const s = { bodies: [] as unknown[], unmocked: [] as string[], job: 'none' as 'none' | 'running' | 'done' }
  // Guard: every non-GET API call nothing below mocks is aborted; GETs go to the seeded API.
  await page.route(/\/api\//, (route) => {
    if (route.request().method() === 'GET') return route.continue()
    s.unmocked.push(`${route.request().method()} ${route.request().url()}`)
    return route.abort()
  })
  if (opts.local !== undefined) {
    await page.route(/\/api\/meta$/, (route) => json(route, { app: 'Baihe Studio', api_version: '0.1', environment: 'test', local: opts.local }))
  }
  await page.route(/\/api\/media\/dramas\/1\/status$/, (route) =>
    json(route, { drama_id: 1, has_audio: !!opts.hasAudio, has_source_video: false, upload_max_mb: 2048 }),
  )
  // Read-only (ffprobe) for the Transcribe time estimate (D04); a POST, so mocked here.
  await page.route(/\/api\/metadata\/dramas\/1\/analyze-media$/, (route) =>
    json(route, { drama_id: 1, duration_seconds: 600, has_video: false, has_audio: true, audio_track_count: 1, sample_rate: 44100 }),
  )
  await page.route(/\/api\/media\/dramas\/1\/download-url$/, (route) => {
    s.bodies.push(route.request().postDataJSON())
    s.job = 'running'
    return json(route, { job_id: 'urlmedia_1' })
  })
  await page.route(/\/api\/jobs\/urlmedia_1$/, (route) => {
    if (s.job === 'none') return json(route, { error: { code: 'not_found', message: 'No such job.' } }, 404)
    const running = s.job === 'running'
    if (running) s.job = 'done'
    return json(route, {
      job_id: 'urlmedia_1', status: running ? 'running' : 'done', progress: running ? 0.3 : 1,
      message: running ? 'Downloading…' : 'Downloaded.', error: null, description: null, gpu_touching: false,
      started_at: 1, finished_at: running ? null : 2, updated_at: 1, outcome: running ? null : 'ok',
    })
  })
  return s
}

test('From a URL: toggle, checks, download starts the urlmedia job', async ({ page }) => {
  const s = await mockUrlDownload(page, { hasAudio: true })
  await page.goto('/#/drama/1/source')
  await expect(page.getByTestId('media-status')).toBeVisible()
  await expect(page.getByLabel('Audio or video file')).toBeVisible()

  await page.getByRole('radio', { name: 'From a URL' }).check()
  await expect(page.getByLabel('Audio or video file')).toHaveCount(0)
  const link = page.getByRole('textbox', { name: 'Video or audio link' })
  const download = page.getByRole('button', { name: 'Download', exact: true })
  await link.fill('ftp://x.example/a')
  await expect(page.getByText('Enter a link that starts with http:// or https://.')).toBeVisible()
  await expect(download).toBeDisabled()
  await link.fill('https://video.example/watch?v=1')
  // Audio drama: audio only starts on. The drama has audio, so replacing needs a tick.
  await expect(page.getByRole('switch', { name: 'Audio only' })).toBeChecked()
  await expect(page.getByText('Still needed: tick “Replace the current audio”.')).toBeVisible()
  await page.getByRole('checkbox', { name: 'Replace the current audio' }).check()
  await download.click()
  await expect.poll(() => s.bodies).toEqual([{ url: 'https://video.example/watch?v=1', audio_only: true, confirm_replace_audio: true }])
  await expect(page.getByTestId('job-status')).toContainText('Done')

  // The choice is remembered.
  await page.reload()
  await expect(page.getByRole('radio', { name: 'From a URL' })).toBeChecked()
  expect(s.unmocked).toEqual([])
})

test('From a URL on a remote viewer: PC only note, no form', async ({ page }) => {
  const s = await mockUrlDownload(page, { local: false })
  await page.goto('/#/drama/1/source')
  await expect(page.getByTestId('media-status')).toBeVisible()
  await page.getByRole('radio', { name: 'From a URL' }).check()
  await expect(page.getByText('Downloading from a link is PC only for now.')).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Video or audio link' })).toHaveCount(0)
  expect(s.bodies).toEqual([])
  expect(s.unmocked).toEqual([])
})
