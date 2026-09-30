import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { ME, maybeScreenshot } from './authMocks'

// Review → AI extras. Drama 3 gets three lines (the first two short and
// close, so they merge) written straight into the seeded throwaway library.
// Merge preview/apply runs against the real API; learn-style, SenseVoice and
// the burned preview (LLM, model, ffmpeg) are mocked with page.route.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

function python(code: string): string {
  return execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], {
    cwd: repoRoot,
  }).toString()
}

let lineIds: number[] = []

test.beforeEach(async ({ page }) => {
  const out = python(`
from core import Line
db.update_drama(3, audio_filename=None)
db.save_lines(3, [
    Line(idx=0, start=0.0, end=0.5, zh='你', en='You'),
    Line(idx=1, start=0.6, end=1.0, zh='好', en='good'),
    Line(idx=2, start=5.0, end=9.0, zh='再见了朋友', en='Goodbye, friend'),
])
print(','.join(str(ln.id) for ln in db.load_line_objects(3)))
`)
  lineIds = out.trim().split(',').map(Number)
  await page.route('**/api/auth/me', (route) => route.fulfill({ json: ME.authOff }))
})

const job = (id: string, status: string) => ({
  job_id: id, status, progress: status === 'running' ? 0.5 : 1, message: '', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: status === 'running' ? null : 2, updated_at: 1,
})

async function openExtras(page: Page) {
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(3)
  const extras = page.getByRole('group', { name: 'AI extras' })
  await extras.locator('details.section > summary', { hasText: 'AI extras' }).first().click()
  return extras
}

async function openSub(page: Page, title: string) {
  const extras = page.getByRole('group', { name: 'AI extras' })
  // The inner sections (the outer group's summary lists every title too).
  await extras.locator('details.section details.section > summary', { hasText: title }).first().click()
}

test('merge short lines: preview is read-only, apply merges against the real API', async ({ page }) => {
  const extras = await openExtras(page)
  await openSub(page, 'Merge short lines')

  // Bad option: caught before any request.
  await extras.getByLabel('Max length', { exact: true }).fill('5')
  await expect(extras.getByRole('button', { name: 'Preview merge' })).toBeDisabled()
  await extras.getByLabel('Max length', { exact: true }).fill('80')

  await extras.getByRole('button', { name: 'Preview merge' }).click()
  const preview = page.getByTestId('merge-short-preview')
  await expect(preview).toContainText('3 → 2 lines: 1 merge.')
  await expect(preview).toContainText('你好')
  // Preview wrote nothing.
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(3)

  await preview.getByLabel(/Type merge to confirm/).fill('merge')
  await preview.getByRole('button', { name: 'Merge lines' }).click()
  await expect(extras.getByRole('status')).toContainText('Merged 1 group')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(2)
})

test('merge apply refuses when the lines changed after the preview', async ({ page }) => {
  const extras = await openExtras(page)
  await openSub(page, 'Merge short lines')
  await extras.getByRole('button', { name: 'Preview merge' }).click()
  await expect(page.getByTestId('merge-short-preview')).toContainText('1 merge')
  python(`
from core import Line
lines = db.load_line_objects(3)
lines.append(Line(idx=3, start=10.0, end=11.0, zh='新', en='new'))
db.save_lines(3, lines)
`)
  const preview = page.getByTestId('merge-short-preview')
  await preview.getByLabel(/Type merge to confirm/).fill('merge')
  await preview.getByRole('button', { name: 'Merge lines' }).click()
  await expect(extras.getByRole('alert')).toContainText('changed')
})

test('learn my style: learn, pause, reset, restore (LLM mocked)', async ({ page }) => {
  const base = { drama_id: 3, scope: 'global', edit_count: 9, drama_edit_count: 9, min_samples: 8, history: [], message: null }
  const profile = { summary: 'Terse', confidence: 'high', preferences: ['Keep lines short'], sample_count: 9, updated_at: null, applied: true }
  const bodies: Record<string, unknown[]> = { learn: [], apply: [], reset: [], restore: [] }
  await page.route('**/api/review-extras/dramas/3/style', (route) => route.fulfill({ json: { ...base, profile: null } }))
  await page.route('**/api/review-extras/dramas/3/style/learn', (route) => {
    bodies.learn.push(route.request().postDataJSON())
    return route.fulfill({ json: { ...base, profile, message: 'Learned 1 preference(s).' } })
  })
  await page.route('**/api/review-extras/dramas/3/style/apply', (route) => {
    const b = route.request().postDataJSON()
    bodies.apply.push(b)
    return route.fulfill({ json: { ...base, profile: { ...profile, applied: b.apply } } })
  })
  const resetHeaders: (string | null)[] = []
  await page.route('**/api/review-extras/dramas/3/style/reset', (route) => {
    bodies.reset.push(route.request().postDataJSON())
    resetHeaders.push(route.request().headers()['x-baihe-local'] ?? null)
    return route.fulfill({ json: { ...base, profile: null, history: [{ summary: 'Terse', preference_count: 1, updated_at: null }] } })
  })
  await page.route('**/api/review-extras/dramas/3/style/restore', (route) => {
    bodies.restore.push(route.request().postDataJSON())
    return route.fulfill({ json: { ...base, profile, message: 'Restored an earlier learned style.' } })
  })

  const extras = await openExtras(page)
  await openSub(page, 'Learn my style')
  await expect(page.getByTestId('style-summary')).toContainText('Nothing learned yet')
  await extras.getByRole('button', { name: 'Learn my style' }).click()
  await expect(page.getByTestId('style-preferences')).toContainText('Keep lines short')
  expect(bodies.learn).toEqual([{}])

  // A Toggle (role=switch) controlled by the server's answer: click, then wait.
  const useIt = extras.getByRole('switch', { name: 'Use in future translations' })
  await expect(useIt).toHaveAttribute('aria-checked', 'true')
  await useIt.click()
  await expect(page.getByTestId('style-summary')).toContainText('paused')
  await expect(useIt).toHaveAttribute('aria-checked', 'false')
  expect(bodies.apply).toEqual([{ apply: false }])

  await extras.getByRole('button', { name: /Reset/ }).click()
  await extras.getByRole('button', { name: /Confirm reset/ }).click()
  await expect(page.getByTestId('style-preferences')).toHaveCount(0)
  expect(bodies.reset).toEqual([{ confirm: true }])
  expect(resetHeaders).toEqual(['1'])

  await extras.getByRole('button', { name: 'Restore previous' }).click()
  await expect(page.getByTestId('style-preferences')).toContainText('Keep lines short')
  expect(bodies.restore).toEqual([{ index: 0 }])
})

test('learn my style when remote: pausing works, Reset is PC only', async ({ page }) => {
  const base = { drama_id: 3, scope: 'series', edit_count: 9, drama_edit_count: 9, min_samples: 8, history: [{ summary: '', preference_count: 1, updated_at: null }], message: null }
  const profile = { summary: '', confidence: 'high', preferences: ['Keep lines short'], sample_count: 9, updated_at: null, applied: true }
  const applied: unknown[] = []
  await page.route('**/api/meta', (route) =>
    route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
  await page.route('**/api/review-extras/dramas/3/style', (route) => route.fulfill({ json: { ...base, profile } }))
  await page.route('**/api/review-extras/dramas/3/style/apply', (route) => {
    const b = route.request().postDataJSON()
    applied.push(b)
    return route.fulfill({ json: { ...base, profile: { ...profile, applied: b.apply } } })
  })

  const extras = await openExtras(page)
  await openSub(page, 'Learn my style')
  await expect(page.getByTestId('style-preferences')).toContainText('Keep lines short')
  await expect(extras.getByText('Resetting or restoring the learned style is PC only.')).toBeVisible()
  await expect(extras.getByRole('button', { name: /Reset/ })).toHaveCount(0)
  await expect(extras.getByRole('button', { name: 'Restore previous' })).toHaveCount(0)
  await extras.getByRole('switch', { name: 'Use in future translations' }).click()
  await expect(extras.getByRole('switch', { name: 'Use in future translations' })).toHaveAttribute('aria-checked', 'false')
  expect(applied).toEqual([{ apply: false }])
})

test('learn my style when remote: the all-projects style is PC only to learn or pause', async ({ page }) => {
  const base = { drama_id: 3, scope: 'global', edit_count: 9, drama_edit_count: 9, min_samples: 8, history: [], message: null }
  const profile = { summary: '', confidence: 'high', preferences: ['Keep lines short'], sample_count: 9, updated_at: null, applied: true }
  await page.route('**/api/meta', (route) =>
    route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
  await page.route('**/api/review-extras/dramas/3/style', (route) => route.fulfill({ json: { ...base, profile } }))

  const extras = await openExtras(page)
  await openSub(page, 'Learn my style')
  await expect(page.getByTestId('style-preferences')).toContainText('Keep lines short')
  await expect(extras.getByText('Learning or pausing the style for all projects is PC only.')).toBeVisible()
  await expect(extras.getByRole('button', { name: 'Learn again' })).toBeDisabled()
  await expect(extras.getByRole('switch', { name: 'Use in future translations' })).toBeDisabled()
})

test('SenseVoice: start the job, then show the side-by-side table (model mocked)', async ({ page }) => {
  let tagged = false
  let started = false
  const rows = [
    { line_id: lineIds[0], idx: 0, text: '你', text_emotion: 'happy', audio_emotion: 'sad', audio_events: 'crying', disagree: true },
  ]
  await page.route('**/api/review-extras/dramas/3/sensevoice', (route) => {
    if (route.request().method() === 'POST') {
      started = true
      return route.fulfill({ json: { job_id: 'sensevoice_3', drama_id: 3, line_count: 3 } })
    }
    return route.fulfill({
      json: {
        drama_id: 3, installed: true, has_audio: true, license_note: 'Model weights have their own licence.',
        tagged: tagged ? 1 : 0, disagree: tagged ? 1 : 0, rows: tagged ? rows : [],
      },
    })
  })
  // Before the POST the stage's reattach lookup reads this id too: no job yet.
  await page.route('**/api/jobs/sensevoice_3', (route) => {
    if (!started) return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No job.' } } })
    tagged = true
    return route.fulfill({ json: job('sensevoice_3', 'done') })
  })

  const extras = await openExtras(page)
  await openSub(page, 'Audio tags')
  await expect(page.getByTestId('sensevoice-summary')).toHaveText('Not tagged yet.')
  await extras.getByRole('button', { name: 'Tag from the audio' }).click()
  await expect(page.getByTestId('sensevoice-table')).toContainText('crying')
  await expect(page.getByTestId('sensevoice-summary')).toContainText('1 line tagged')
})

test('SenseVoice without funasr explains why the button is off', async ({ page }) => {
  await page.route('**/api/review-extras/dramas/3/sensevoice', (route) =>
    route.fulfill({ json: { drama_id: 3, installed: false, has_audio: true, license_note: '', tagged: 0, disagree: 0, rows: [] } }))
  const extras = await openExtras(page)
  await openSub(page, 'Audio tags')
  await expect(extras.getByRole('button', { name: 'Tag from the audio' })).toBeDisabled()
  await expect(extras.getByText(/pip install funasr/)).toBeVisible()
})

test('burned preview: a line number resolves to its id, then the clip plays (ffmpeg mocked)', async ({ page }) => {
  let rendered = false
  const bodies: Record<string, unknown>[] = []
  const info = () => ({
    drama_id: 3, has_video: true, ffmpeg_available: true, presets: ['Clean', 'Bold'], max_clip_seconds: 30, max_pad_seconds: 5,
    clip: rendered ? { line_id: lineIds[1], idx: 1, start: 0, end: 3, preset: 'Bold', created_at: '2026-09-29T10:00:00' } : null,
  })
  await page.route('**/api/review-extras/dramas/3/burn-preview/info', (route) => route.fulfill({ json: info() }))
  await page.route('**/api/review-extras/dramas/3/burn-preview', (route) => {
    bodies.push(route.request().postDataJSON())
    return route.fulfill({ json: { job_id: 'burnpreview_3', drama_id: 3, line_id: lineIds[1], start: 0, end: 3 } })
  })
  await page.route('**/api/jobs/burnpreview_3', (route) => {
    if (!bodies.length) return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No job.' } } })
    rendered = true
    return route.fulfill({ json: job('burnpreview_3', 'done') })
  })
  await page.route('**/api/review-extras/dramas/3/burn-preview/clip*', (route) =>
    route.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.from('not really a video') }))

  const extras = await openExtras(page)
  await openSub(page, 'Burned subtitle preview')
  await extras.getByLabel('Line', { exact: true }).fill('9')
  await extras.getByRole('button', { name: 'Render preview' }).click()
  await expect(extras.getByText('No line #9 in this drama.')).toBeVisible()
  expect(bodies).toEqual([])

  await extras.getByLabel('Line', { exact: true }).fill('2')
  await extras.getByLabel('Style', { exact: true }).selectOption('Bold')
  await extras.getByRole('button', { name: 'Render preview' }).click()
  const clip = page.getByTestId('burn-preview-clip')
  await expect(clip).toContainText('Line #2')
  expect(bodies).toEqual([{ line_id: lineIds[1], pad_seconds: 2, preset: 'Bold' }])
  await expect(clip.locator('video')).toHaveAttribute('src', /\/api\/review-extras\/dramas\/3\/burn-preview\/clip\?v=/)
  await maybeScreenshot(page, 'review-ai-extras-desktop')
})

test.describe('phone width', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true })

  test('every AI extras section fits without sideways scrolling', async ({ page }) => {
    await page.route('**/api/review-extras/dramas/3/sensevoice', (route) =>
      route.fulfill({
        json: {
          drama_id: 3, installed: true, has_audio: true, license_note: 'Licence note.', tagged: 1, disagree: 1,
          rows: [{ line_id: lineIds[0], idx: 0, text: '这是一个相当长的句子用来测试表格的宽度', text_emotion: 'happy', audio_emotion: 'sad', audio_events: 'background music, laughter', disagree: true }],
        },
      }))
    const extras = await openExtras(page)
    for (const title of ['Merge short lines', 'Learn my style', 'Audio tags', 'Burned subtitle preview']) await openSub(page, title)
    await extras.getByRole('button', { name: 'Preview merge' }).click()
    await expect(page.getByTestId('merge-short-preview')).toBeVisible()
    await expect(page.getByTestId('sensevoice-table')).toBeVisible()
    const { scroll, client } = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }))
    expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
    await maybeScreenshot(page, 'review-ai-extras-390')
  })
})
