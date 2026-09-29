import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'

// Reader page (#/read/<id>). The seeded API has dramas but no lines, so
// before every test this writes 90 lines for drama 2 straight into the
// throwaway library the test server uses and clears its reading progress.
// Paging, progress and preferences use the real API; the AI routes, engine
// list and exports are mocked.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

function python(code: string) {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })
}

test.beforeEach(() => {
  python(`
import contextlib
from core import Line
db.save_lines(2, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh='第%d句话' % i, en='Line %d' % i) for i in range(90)])
with contextlib.closing(db.get_conn()) as c:
    c.execute('DELETE FROM progress WHERE drama_id = 2')
    c.commit()
`)
})

const frameRows = (page: Page) => page.frameLocator('iframe.reader-frame').locator('.line-row')
const label = (page: Page) => page.getByTestId('reader-page-label').first()

const engines = (items: { name: string; free: boolean }[]) => (route: import('@playwright/test').Route) =>
  route.fulfill({
    json: { items: items.map((e) => ({ ...e, label: e.name, models: null, key_configured: true })) },
  })

test('opens at page 1, pages forward and updates ?page', async ({ page }) => {
  await page.goto('/#/read/2')
  await expect(page).toHaveURL(/#\/read\/2\?page=1$/)
  await expect(label(page)).toHaveText('Page 1 of 3')
  await expect(page.getByTestId('reader-metrics')).toContainText('90 lines')
  const frame = page.locator('iframe.reader-frame')
  await expect(frame).toHaveAttribute('sandbox', 'allow-scripts')
  await expect(frameRows(page)).toHaveCount(40)

  await page.getByRole('button', { name: 'Next page' }).click()
  await expect(page).toHaveURL(/#\/read\/2\?page=2$/)
  await expect(label(page)).toHaveText('Page 2 of 3')

  await page.getByLabel('Go to page').fill('3')
  await page.getByRole('button', { name: 'Go', exact: true }).click()
  await expect(page).toHaveURL(/page=3$/)
  await expect(frameRows(page)).toHaveCount(10)
  await expect(page.getByRole('button', { name: 'Next page' })).toBeDisabled()

  // Only sections with data: no media, vocabulary or glossary on this drama.
  await expect(page.locator('summary', { hasText: 'Words' })).toBeVisible()
  await expect(page.locator('summary', { hasText: 'Story tools' })).toBeVisible()
  await expect(page.locator('summary', { hasText: 'My notes' })).toBeVisible()
  for (const t of ['Watch / listen', 'Vocabulary', 'Glossary']) {
    await expect(page.locator('summary', { hasText: t })).toHaveCount(0)
  }
})

test('text size and lines per page persist across a reload', async ({ page }) => {
  await page.goto('/#/read/2?page=1')
  await expect(frameRows(page)).toHaveCount(40)
  await page.getByRole('button', { name: 'Reading settings' }).click()
  await page.getByRole('button', { name: 'Larger text' }).click()
  await expect(page.getByLabel('Text size')).toHaveValue('23')
  const perPage = page.getByRole('spinbutton', { name: 'Lines per page' })
  await perPage.fill('20')
  await perPage.press('Enter')
  await expect(label(page)).toHaveText('Page 1 of 5')
  await expect(frameRows(page)).toHaveCount(20)

  await page.reload()
  await expect(label(page)).toHaveText('Page 1 of 5')
  await page.getByRole('button', { name: 'Reading settings' }).click()
  await expect(page.getByLabel('Text size')).toHaveValue('23')
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('baihe.reader.prefs') ?? '{}'))
  expect(stored).toMatchObject({ fontSize: 23, chapterSize: 20 })
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: 'Reading settings' })).toHaveCount(0)
})

test('resumes at the saved page and saves progress', async ({ page }) => {
  python('db.save_progress(2, last_line_idx=45, last_page=2, percent_complete=51.1)')
  const saved = page.waitForRequest((r) => r.url().endsWith('/api/reader/dramas/2/progress') && r.method() === 'POST')
  await page.goto('/#/read/2')
  await expect(page.getByText('Resumed at page 2.')).toBeVisible()
  await expect(page).toHaveURL(/page=2$/)
  await expect(label(page)).toHaveText('Page 2 of 3')
  expect((await saved).postDataJSON()).toEqual({ page: 2, chapter_size: 40 })

  await page.getByRole('button', { name: 'Dismiss' }).click()
  await expect(page.getByText('Resumed at page 2.')).toHaveCount(0)
  await page.getByRole('button', { name: 'Next page' }).click()
  await expect(page).toHaveURL(/page=3$/)
})

test('an empty drama says so and links to Source', async ({ page }) => {
  await page.goto('/#/read/1')
  await expect(page.getByText('No lines to read yet.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Add lines on Source' })).toHaveAttribute('href', '#/drama/1/source')
  await expect(page.locator('iframe')).toHaveCount(0)
})

test('the library detail panel links to the reader', async ({ page }) => {
  await page.goto('/#/library')
  await page.getByRole('button', { name: "Heaven Official's Blessing" }).click()
  await page.getByRole('link', { name: 'Read', exact: true }).click()
  await expect(page).toHaveURL(/#\/read\/2/)
  await expect(label(page)).toHaveText('Page 1 of 3')
})

test('a busy AI shows the 429 copy and Try again repeats the request', async ({ page }) => {
  await page.route('**/api/translate/engines', engines([{ name: 'ollama', free: true }]))
  const bodies: unknown[] = []
  await page.route('**/api/reader/dramas/2/story/who', (route) => {
    bodies.push(route.request().postDataJSON())
    return bodies.length === 1
      ? route.fulfill({ status: 429, json: { error: { code: 'rate_limited', message: 'busy' } } })
      : route.fulfill({ json: { drama_id: 2, answer: 'A wandering god.' } })
  })
  await page.goto('/#/read/2?page=2')
  await expect(frameRows(page)).toHaveCount(40)
  await page.locator('summary', { hasText: 'Story tools' }).click()
  await page.getByLabel('Who is…').fill('Xie Lian')
  await page.getByRole('button', { name: 'Ask', exact: true }).first().click()
  await expect(page.getByText('The AI is busy with another request. Try again in a moment.')).toBeVisible()
  await page.getByRole('button', { name: 'Try again' }).click()
  await expect(page.getByText('A wandering god.')).toBeVisible()
  // Spoiler-free (default on): scoped to the last line of page 2.
  expect(bodies[1]).toEqual({ name: 'Xie Lian', engine: 'ollama', up_to_line_idx: 79 })
})

test('a paid engine refused with 403 shows the paid copy', async ({ page }) => {
  await page.route('**/api/translate/engines', engines([{ name: 'claude', free: false }]))
  await page.route('**/api/reader/dramas/2/story/explain', (route) =>
    route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } }),
  )
  await page.goto('/#/read/2?page=1')
  await expect(frameRows(page)).toHaveCount(40)
  await page.locator('summary', { hasText: 'Story tools' }).click()
  await page.getByLabel('Explain').fill('Heaven official')
  await page.getByRole('button', { name: 'Explain', exact: true }).click()
  await expect(page.getByText("This lookup uses a paid engine, which this account can't use.")).toBeVisible()
})

test('sentence cards without media permission say audio was left out', async ({ page }) => {
  await page.route('**/api/reader/dramas/2/vocab', (route) =>
    route.fulfill({
      json: {
        drama_id: 2,
        count: 2,
        words: [
          { word: '你好', reading: 'nǐ hǎo', definitions: ['hello'], language: 'zh', first_seen_line_idx: 0, export_rich: true },
          { word: '朋友', reading: 'péng you', definitions: ['friend'], language: 'zh', first_seen_line_idx: 1, export_rich: false },
        ],
      },
    }),
  )
  await page.route((url) => url.pathname.endsWith('/api/reader/dramas/2/vocab/export.apkg') && url.searchParams.get('rich') === 'true', (route) =>
    route.fulfill({
      body: 'deck',
      headers: {
        'Content-Type': 'application/octet-stream',
        'Content-Disposition': 'attachment; filename="drama_2_vocab_sentence.apkg"',
        'X-Audio-Omitted': 'true',
      },
    }),
  )
  await page.goto('/#/read/2?page=1')
  await expect(frameRows(page)).toHaveCount(40)
  await page.locator('summary', { hasText: 'Vocabulary' }).click()
  await expect(page.getByRole('button', { name: 'Queue 0 for sentence cards' })).toBeDisabled()
  await page.getByLabel(/朋友/).check()
  await expect(page.getByRole('button', { name: 'Queue 1 for sentence cards' })).toBeEnabled()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Sentence cards (.apkg)' }).click()
  expect((await download).suggestedFilename()).toBe('drama_2_vocab_sentence.apkg')
  await expect(page.getByText('Audio left out (needs media playback permission).')).toBeVisible()
})

// ---- phone layout (390x844, touch) ----

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })

  test('fits the width, keeps 44px targets and puts Go to in the Aa sheet', async ({ page }) => {
    await page.goto('/#/read/2?page=1')
    await expect(frameRows(page)).toHaveCount(40)
    const { scroll, client } = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }))
    expect(scroll).toBeLessThanOrEqual(client)

    const bar = page.locator('.reader-bottom')
    await expect(bar).toBeVisible()
    for (const name of ['Previous page', 'Next page']) {
      const box = await bar.getByRole('button', { name }).boundingBox()
      expect(box!.height).toBeGreaterThanOrEqual(44)
      expect(box!.width).toBeGreaterThanOrEqual(44)
    }
    const head = await page.locator('.reader-phone-head').boundingBox()
    expect(head!.height).toBeGreaterThanOrEqual(48)

    await page.getByRole('button', { name: 'Reading settings' }).tap()
    const sheet = page.getByRole('dialog', { name: 'Reading settings' })
    await expect(sheet).toBeVisible()
    await expect(sheet.getByLabel('Width')).toHaveCount(0)
    // The Field (i) help button is the shared pattern with a padded hit area.
    const small = await sheet.locator('select, input:not([type=checkbox]), button:not(.field-help-btn), label:has(> input[type=checkbox])').evaluateAll((els) =>
      els
        .filter((e) => (e as HTMLElement).offsetParent !== null)
        .map((e) => ({ h: e.getBoundingClientRect().height, t: (e.textContent ?? '').trim() || e.getAttribute('aria-label') }))
        .filter(({ h }) => h < 44),
    )
    expect(small).toEqual([])
    await expect(sheet.getByTestId('reader-metrics')).toContainText('90 lines')
    await sheet.getByLabel('Go to page').fill('2')
    await sheet.getByRole('button', { name: 'Go', exact: true }).tap()
    await expect(page).toHaveURL(/page=2$/)
    await sheet.getByRole('button', { name: 'Close' }).tap()
    await bar.getByRole('button', { name: 'Next page' }).tap()
    await expect(page).toHaveURL(/page=3$/)
    await expect(label(page)).toHaveText('Page 3 of 3')
  })
})
