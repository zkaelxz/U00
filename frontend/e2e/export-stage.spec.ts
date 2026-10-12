import { expect, test, type Page } from '@playwright/test'

import { asMediaType, exportBlock, mockExportReady, openExportBlock } from './exportBlocks'
import { FIRST_OPEN, expectOpen, mockSoftsubRun, summaryOf } from './exportBlocksCases'
import { withExportLines } from './stageLineMocks'

// Reads, the ASS/subtitle text and the flag actions hit the real seeded API
// (the seeded dramas have no lines, so counts are zero and flags write
// nothing). Job starts are mocked: no ffmpeg runs.

const job = (status: string, extra: object = {}) => ({
  job_id: 'fake-export', status, progress: 0.5, message: 'encoding', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1, ...extra,
})

// The seeded drama has no narration or dub, so those two starts are disabled; these jobs need them.
const withTracks = (page: Page) =>
  page.route('**/api/workflow/dramas/1/progress', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), has_dub_track: true, has_narration_track: true } })
  })

// The seeded title has no source video and ffmpeg may be missing here; most specs need the exports enabled.
test.beforeEach(async ({ page }) => {
  await mockExportReady(page)
})

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function mockJob(page: Page, startPath: string, finalStatus: 'done' | 'cancelled') {
  const bodies: unknown[] = []
  let finished = false
  await page.route(`**${startPath}`, async (route) => {
    bodies.push(route.request().postDataJSON())
    await route.fulfill({ json: { job_id: 'fake-export' } })
  })
  await page.route('**/api/jobs/fake-export', async (route) => {
    await route.fulfill({ json: finished ? job(finalStatus, { finished_at: 2 }) : job('running') })
  })
  await page.route('**/api/jobs/fake-export/cancel', async (route) => {
    finished = true
    await route.fulfill({ json: { job_id: 'fake-export', cancel_requested: true, status: 'cancelled' } })
  })
  return { bodies, finish: () => (finished = true) }
}

test('with no lines, Export is disabled and links to Source (rule 22)', async ({ page }) => {
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Subtitle file')
  await expect(page.getByTestId('readiness')).toContainText('0 lines')
  await expect(page.getByRole('button', { name: 'Export', exact: true })).toBeDisabled()
  const blocker = page.getByTestId('export-blocker')
  await expect(blocker).toContainText('No lines to export yet.')
  await blocker.getByRole('link', { name: 'Go to Media' }).click()
  await expect(page).toHaveURL(/#\/drama\/1\/source$/)
})

test('shows readiness and generates subtitle and ASS text', async ({ page }) => {
  // The seeded drama has no lines; pretend it has some so Export is enabled
  // (the real subtitle text is still empty).
  await withExportLines(page)
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Subtitle file')
  await expect(page.getByTestId('readiness')).toContainText('3 lines')

  const srt = page.waitForResponse((r) => r.url().includes('/subtitle?') && r.status() === 200)
  await page.getByRole('button', { name: 'Export', exact: true }).click()
  await srt
  await expect(page.getByTestId('export-empty')).toBeVisible()

  await page.getByLabel('Format', { exact: true }).selectOption('ass')
  await page.getByRole('button', { name: 'Export', exact: true }).click()
  await expect(page.getByTestId('export-text')).toContainText('[Script Info]')
  await expect(page.getByTestId('export-download')).toHaveAttribute('download', 'Grandmaster of Demonic Cultivation - subtitles (en).ass')
})

test('bad ASS settings are explained before any request', async ({ page }) => {
  await withExportLines(page)
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Subtitle file')
  await page.getByLabel('Format', { exact: true }).selectOption('ass')
  await page.getByText('ASS style', { exact: true }).click()
  await page.getByLabel(/^Text colour/).fill('red')
  await page.getByRole('button', { name: 'Export', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: '#RRGGBB' })).toBeVisible()
})

test('the readiness warning links to Review checks instead of hosting flag buttons', async ({ page }) => {
  await page.route('**/api/export/dramas/1/readiness', (route) =>
    route.fulfill({ json: { drama_id: 1, total_lines: 10, zh_filled: 10, en_filled: 10, fully_translated: true, overlap_count: 2, auto_qc_issue_count: 0, dense_line_count: 1 } }))
  await page.goto('/#/drama/1/export')
  await expect(page.getByTestId('readiness-warnings')).toContainText('2 overlapping lines, 1 dense line.')
  await expect(page.getByRole('button', { name: 'Flag overlapping lines' })).toHaveCount(0)
  await page.getByRole('link', { name: 'Open Review checks' }).click()
  await expect(page).toHaveURL(/#\/drama\/1\/review$/)
})

test('the ASS style shows at the top only when ASS is chosen', async ({ page }) => {
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Subtitle file')
  await page.getByLabel('Format', { exact: true }).selectOption('srt')
  await expect(page.getByText('ASS style', { exact: true })).toBeHidden()
  await page.getByLabel('Format', { exact: true }).selectOption('ass')
  await expect(page.getByText('ASS style', { exact: true })).toBeVisible()
})

test('a title that is not novel narration has EPUB disabled with its reason', async ({ page }) => {
  await page.goto('/#/drama/1/export')
  const epub = exportBlock(page, 'EPUB')
  await expect(summaryOf(page, 'EPUB')).toContainText('Only for titles in novel-narration mode.')
  await openExportBlock(page, 'EPUB')
  await expect(epub.getByRole('button', { name: 'Download EPUB' })).toBeDisabled()
})

test('audiobook job can be cancelled', async ({ page }) => {
  await withTracks(page)
  await mockJob(page, '/api/export/dramas/1/audiobook', 'cancelled')
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Audiobook')
  await page.getByRole('button', { name: 'Start audiobook export' }).click()
  await expect(page.getByTestId('job-status')).toContainText('Running')
  await page.getByRole('button', { name: /^Cancel / }).click()
  await expect(page.getByTestId('job-status')).toContainText('Cancelled')
})

test('burned-in video sends the style and offers the artifact on done', async ({ page }) => {
  const { bodies, finish } = await mockJob(page, '/api/export/dramas/1/burned-video', 'done')
  await page.route('**/api/artifacts/dramas/1/video/info', (route) =>
    route.fulfill({ json: { name: 'burned_video_1.mp4', size: 3 * 1024 * 1024, kind: 'video' } }),
  )
  await page.goto('/#/drama/1/export')
  // With SRT chosen, the ASS style sits in the burned-in video block.
  await openExportBlock(page, 'Burned-in video')
  await page.getByText('ASS style', { exact: true }).click()
  await page.getByLabel(/^Font size/).fill('48')
  await page.getByRole('button', { name: 'Start burned-in video export' }).click()
  await expect(page.getByTestId('job-status')).toContainText('Running')
  finish()
  const link = page.getByTestId('artifact-video').getByRole('link')
  await expect(link).toHaveText('Download burned_video_1.mp4')
  await expect(link).toHaveAttribute('href', /\/api\/artifacts\/dramas\/1\/video$/)
  expect(bodies[0]).toMatchObject({ field: 'en', style: { size: 48 } })
})

test('a 422 from a job start is shown as a banner', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), has_narration_track: true } })
  })
  await page.route('**/api/export/dramas/1/audiobook', (route) =>
    route.fulfill({ status: 422, json: { error: { code: 'invalid_input', message: 'No narration audio yet.' } } }),
  )
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Audiobook')
  await page.getByRole('button', { name: 'Start audiobook export' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'No narration audio yet.' })).toBeVisible()
})

test('subtitle-track video sends the chosen subtitles and offers the artifact', async ({ page }) => {
  const { bodies, finish } = await mockJob(page, '/api/export/dramas/1/softsub-video', 'done')
  await page.route('**/api/artifacts/dramas/1/softsub_video/info', (route) =>
    route.fulfill({ json: { name: 'softsub_video_1.mkv', size: 1024, kind: 'softsub_video' } }),
  )
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Video with a subtitle track')
  const group = page.getByRole('group', { name: 'Video with a subtitle track' })
  await group.getByLabel('Subtitles').selectOption('bilingual')
  const format = group.getByLabel('Subtitle format', { exact: true })
  await expect(format).toHaveValue('srt')
  await format.selectOption('ass')
  await group.getByRole('button', { name: 'Help: Subtitle format' }).click()
  await expect(group).toContainText('ASS keeps overlapping lines and speaker colours.')
  await expect(group).toContainText('saved as MKV')
  await group.getByRole('button', { name: 'Start subtitle-track video export' }).click()
  await expect(group.getByTestId('job-status')).toContainText('Running')
  finish()
  const link = page.getByTestId('artifact-softsub').getByRole('link')
  await expect(link).toHaveText('Download softsub_video_1.mkv')
  await expect(link).toHaveAttribute('href', /\/api\/artifacts\/dramas\/1\/softsub_video$/)
  expect(bodies[0]).toEqual({ field: 'bilingual', subtitle_format: 'ass' })
})

test('dubbed video sends the mix choice and offers its own artifact', async ({ page }) => {
  await withTracks(page)
  const { bodies, finish } = await mockJob(page, '/api/export/dramas/1/dubbed-video', 'done')
  await page.route('**/api/artifacts/dramas/1/dubbed_video/info', (route) =>
    route.fulfill({ json: { name: 'dubbed_video_1.mp4', size: 2048, kind: 'dubbed_video' } }),
  )
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Video with the dub audio')
  const group = page.getByRole('group', { name: 'Video with the dub audio' })
  await group.getByRole('switch', { name: 'Mix the original audio in quietly underneath' }).click()
  await group.getByRole('button', { name: 'Start dubbed video export' }).click()
  await expect(group.getByTestId('job-status')).toBeVisible()
  finish()
  const link = page.getByTestId('artifact-dubbed').getByRole('link')
  await expect(link).toHaveText('Download dubbed_video_1.mp4')
  await expect(link).toHaveAttribute('href', /\/api\/artifacts\/dramas\/1\/dubbed_video$/)
  expect(bodies[0]).toEqual({ keep_original: true })
})

test('mark as exported posts once and shows the new status', async ({ page }) => {
  let posts = 0
  await page.route('**/api/export/dramas/1/mark-exported', (route) => {
    posts++
    return route.fulfill({ json: { drama_id: 1, status: 'exported' } })
  })
  await page.goto('/#/drama/1/export')
  await page.getByRole('button', { name: 'Mark as exported' }).click()
  await expect(page.getByTestId('mark-exported')).toContainText('marked as exported')
  expect(posts).toBe(1)
})

async function exportAss(page: Page, noCopy: boolean) {
  await page.addInitScript((refuseExec) => {
    // Plain http on a LAN address: no async clipboard API.
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    if (refuseExec) document.execCommand = () => false
  }, noCopy)
  await withExportLines(page)
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Subtitle file')
  await page.getByLabel('Format', { exact: true }).selectOption('ass')
  // The style options arrive after the page paints; Export before then is refused.
  await expect(page.getByText('ASS style', { exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.getByRole('button', { name: 'Export', exact: true }).click()
  await expect(page.getByTestId('export-text')).toContainText('[Script Info]')
}

test('Copy works without the async clipboard API (plain http on a LAN address)', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await exportAss(page, false)
  await page.getByRole('button', { name: 'Copy', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Copied.' })).toBeVisible()
  expect(errors).toEqual([])
})

test('when nothing can copy, Copy selects the text and says so', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await exportAss(page, true)
  await page.getByRole('button', { name: 'Copy', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: "Couldn't copy automatically. The text below is selected" }))
    .toBeVisible()
  const selected = await page.evaluate(() => window.getSelection()?.toString() ?? '')
  expect(selected).toContain('[Script Info]')
  expect(errors).toEqual([])
})

test.describe('export blocks fold under their headings', () => {
  test('the title kind decides which block leads and starts open', async ({ page }) => {
    await page.goto('/#/drama/1/export')
    await expect(page.getByText('Audio title')).toBeVisible()
    await expectOpen(page, { [FIRST_OPEN.audio]: true, 'Video with a subtitle track': false, 'Subtitle file': false, EPUB: false })
    await page.goto('/#/drama/3/export')
    await expect(page.getByText('Video title')).toBeVisible()
    await expectOpen(page, { [FIRST_OPEN.video]: true, 'Burned-in video': false, Audiobook: false, 'Subtitle file': false })
    await expect(page.getByRole('button', { name: 'Start subtitle-track video export' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Start audiobook export' })).toBeHidden()
  })

  test('novel and subtitle-only titles lead with their own block', async ({ page }) => {
    await page.goto('/#/drama/2/export')
    await expect(page.getByRole('region', { name: 'Export' }).getByText('Novel', { exact: true })).toBeVisible()
    await expectOpen(page, { [FIRST_OPEN.novel]: true, 'Subtitle file': false, Audiobook: false })
    await asMediaType(page, 'other')
    await page.goto('/#/drama/1/export')
    await expect(page.getByText('Subtitle-only title')).toBeVisible()
    await expectOpen(page, { [FIRST_OPEN.subtitle]: true, Audiobook: false })
  })

  test('every export is reachable for every kind, collapsed rather than removed', async ({ page }) => {
    await page.goto('/#/drama/3/export')
    for (const title of ['Video with a subtitle track', 'Burned-in video', 'Video with the dub audio', 'Audiobook', 'Subtitle file', 'EPUB']) {
      await expect(exportBlock(page, title)).toHaveCount(1)
    }
  })

  test('Enter and Space on the focused heading fold and unfold it', async ({ page }) => {
    await page.goto('/#/drama/1/export')
    const summary = summaryOf(page, 'Video with the dub audio')
    await summary.focus()
    await page.keyboard.press('Enter')
    await expectOpen(page, { 'Video with the dub audio': true })
    await expect(page.getByText('Replaces the video\'s sound')).toBeVisible()
    await page.keyboard.press('Space')
    await expectOpen(page, { 'Video with the dub audio': false })
  })

  test('the last block opened is remembered per title kind', async ({ page }) => {
    await page.goto('/#/drama/3/export')
    await openExportBlock(page, 'Burned-in video')
    await expectOpen(page, { 'Burned-in video': true, 'Video with a subtitle track': false })
    await page.reload()
    await expectOpen(page, { 'Burned-in video': true, 'Video with a subtitle track': false })
    // Another kind keeps its own default.
    await page.goto('/#/drama/1/export')
    await expectOpen(page, { Audiobook: true, 'Burned-in video': false })
  })

  test('works with storage blocked: the defaults still hold', async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(window, 'localStorage', { get: () => { throw new Error('blocked') } })
    })
    await page.goto('/#/drama/3/export')
    await expectOpen(page, { 'Video with a subtitle track': true })
    await openExportBlock(page, 'Audiobook')
    await expectOpen(page, { Audiobook: true, 'Video with a subtitle track': false })
  })

  test('a running job and its download link stay visible while the block is folded', async ({ page }) => {
    const { finish } = await mockSoftsubRun(page, 3)
    await page.goto('/#/drama/3/export')
    const group = exportBlock(page, 'Video with a subtitle track')
    await group.getByRole('button', { name: 'Start subtitle-track video export' }).click()
    await expect(group.getByTestId('job-status')).toContainText('Running')
    await summaryOf(page, 'Video with a subtitle track').click()
    await expectOpen(page, { 'Video with a subtitle track': false })
    await expect(group.getByRole('button', { name: 'Start subtitle-track video export' })).toBeHidden()
    await expect(group.getByTestId('job-status')).toContainText('Running')
    finish()
    await expect(page.getByTestId('artifact-softsub').getByRole('link')).toHaveText('Download softsub_video_1.mkv')
  })

  test('an unavailable export is disabled with a one-line reason, also while folded', async ({ page }) => {
    await mockExportReady(page, { hasSourceVideo: false, ffmpeg: false })
    await page.goto('/#/drama/3/export')
    await expect(summaryOf(page, 'Burned-in video')).toContainText('No source video yet. Add one in Media.')
    await openExportBlock(page, 'Burned-in video')
    await expect(exportBlock(page, 'Burned-in video').getByRole('button', { name: 'Start burned-in video export' })).toBeDisabled()
    // ffmpeg missing: the one Install action is offered beside the reasons.
    await expect(page.getByText('Not found. Baihe needs it to read audio and video.')).toBeVisible()
    await mockExportReady(page, { hasSourceVideo: true, ffmpeg: false })
    await page.reload()
    await expect(summaryOf(page, 'Video with a subtitle track')).toBeVisible()
    await openExportBlock(page, 'Video with a subtitle track')
    await expect(exportBlock(page, 'Video with a subtitle track')).toContainText('ffmpeg is not installed.')
    await expect(exportBlock(page, 'Video with a subtitle track').getByRole('button', { name: 'Start subtitle-track video export' })).toBeDisabled()
  })
})

test('a double-clicked Export asks the server once, and a response for the old format is dropped', async ({ page }) => {
  await withExportLines(page)
  const urls: string[] = []
  await page.route('**/api/export/dramas/1/subtitle?*', async (route) => {
    urls.push(route.request().url())
    await new Promise((r) => setTimeout(r, 400))
    await route.fulfill({ json: { text: '1\n00:00:01,000 --> 00:00:02,000\nhi\n', filename: 'x.srt' } })
  })
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Subtitle file')
  await expect(page.getByTestId('readiness')).toContainText('3 lines')
  const button = page.getByRole('button', { name: 'Export', exact: true })
  await button.dblclick()
  await expect(page.getByTestId('export-text')).toBeVisible()
  expect(urls).toHaveLength(1)

})

test('switching format while an export is in flight drops the old response', async ({ page }) => {
  await withExportLines(page)
  await page.route('**/api/export/dramas/1/subtitle?*', async (route) => {
    await new Promise((r) => setTimeout(r, 400))
    await route.fulfill({ json: { text: '1\n00:00:01,000 --> 00:00:02,000\nhi\n', filename: 'x.srt' } })
  })
  await page.goto('/#/drama/1/export')
  await openExportBlock(page, 'Subtitle file')
  await expect(page.getByTestId('readiness')).toContainText('3 lines')
  await page.getByRole('button', { name: 'Export', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Exporting...' })).toBeDisabled()
  await page.getByLabel('Format', { exact: true }).selectOption('ass')
  await expect(page.getByRole('button', { name: 'Export', exact: true })).toBeEnabled()
  await page.waitForTimeout(700)
  await expect(page.getByTestId('export-text')).toHaveCount(0)
})
