import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { test, type Page } from '@playwright/test'

import { mockAiResegment } from './resegmentLlmMocks'

// Before/after screenshots for the B5 Review gaps (R05 search jump, R49 bulk,
// R47 AI re-segmentation preview). Skipped unless B5_SHOTS_DIR=<dir> is set.
// Drama 3 gets 50 lines; the bulk list and the AI preview are mocked.

const DIR = process.env.B5_SHOTS_DIR
test.skip(!DIR, 'set B5_SHOTS_DIR to save screenshots')

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeEach(() => {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5,
                       zh=('独一无二的剑，他说今天一定要走，因为明天师父就回来了' if i in (5, 44) else f'第{i}句'),
                       en=f'Line {i}') for i in range(50)])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

const SIZES = [
  ['desktop', { width: 1280, height: 900 }],
  ['phone', { width: 390, height: 844 }],
] as const

async function mockBulk(page: Page) {
  await page.route('**/api/translate-run/dramas/3/bulk', (r) =>
    r.fulfill({
      json: {
        drama_id: 3,
        jobs: [{
          bulk_job_id: 12, engine: 'claude', model: null, kind: 'consistency', stage: null, pipeline_id: null,
          status: 'submitted', pending: true, cancellable: true, line_count: 50, scheduled_for: null,
          result_summary: null, last_error: null, submitted_at: '2026-09-30T10:00:00', updated_at: '2026-09-30T10:05:00',
        }],
      },
    }),
  )
}

async function open(page: Page, name: string) {
  const s = page.locator('summary', { hasText: name }).first()
  const d = s.locator('xpath=..')
  if (!(await d.getAttribute('open'))) await s.click()
  await s.scrollIntoViewIfNeeded()
}

for (const [vp, size] of SIZES) {
  test(`b5 screens (${vp})`, async ({ page }) => {
    await page.setViewportSize(size)
    await page.emulateMedia({ colorScheme: 'dark' })
    await mockBulk(page)
    await mockAiResegment(page)
    await page.goto('/#/drama/3/review')
    await page.locator('.review-line:not(.review-skeleton)').first().waitFor()

    // R05: search results.
    if (vp === 'phone') {
      const toggle = page.locator('.review-search-toggle')
      if (await toggle.count()) await toggle.click()
    }
    await page.getByPlaceholder('Search source or translation').fill('独一无二')
    await page.waitForTimeout(800)
    await page.screenshot({ path: `${DIR}/r05-search-${vp}.png` })
    const show = page.getByRole('button', { name: /on its page/ }).last()
    if (await show.count()) {
      await show.click()
      await page.waitForTimeout(600)
      await page.screenshot({ path: `${DIR}/r05-jumped-${vp}.png` })
    }
    await page.getByPlaceholder('Search source or translation').fill('')

    // R49: the AI review jobs.
    await open(page, 'AI review')
    const bulk = page.getByRole('switch', { name: 'Bulk: Check consistency' })
    if (await bulk.count()) await bulk.click()
    await page.waitForTimeout(500)
    await page.locator('[aria-label="AI checks"]').screenshot({ path: `${DIR}/r49-ai-review-${vp}.png` })

    // R47: the Structure section with Use AI on.
    await open(page, 'Structure')
    const preview = page.getByRole('button', { name: /Preview re-segmentation|Preview again/ })
    if (await preview.count()) await preview.click()
    const useAi = page.getByRole('switch', { name: 'Use AI' })
    if (await useAi.count()) {
      await useAi.click()
      const adv = page.locator('summary', { hasText: 'Advanced' })
      if (await adv.count()) await adv.first().click()
    }
    await page.waitForTimeout(500)
    await page.locator('[aria-label="Structure"]').screenshot({ path: `${DIR}/r47-structure-${vp}.png` })
    const aiPreview = page.getByRole('button', { name: 'Preview with AI' })
    if (await aiPreview.count()) {
      await aiPreview.click()
      await page.getByTestId('resegment-llm-preview').or(page.getByText('by Gemini')).first().waitFor({ timeout: 5000 }).catch(() => {})
      await page.waitForTimeout(500)
      await page.locator('[aria-label="Structure"]').screenshot({ path: `${DIR}/r47-ai-preview-${vp}.png` })
    }
  })
}
