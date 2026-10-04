import { expect as baseExpect, test, type Page } from '@playwright/test'

import { chapterImportResult, mockImports } from './sourcesImportMocks'
import { mockSources, posted } from './sourcesMocks'
import { installHitArea } from './hitArea'

// .btn-sm keeps a 44px hit area but is 32px tall: measure the hit area, not the box.
test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Step 107, phone project (390x844, touch): the marked chapter picker and
// "Retry failed chapters (N)" fit one column, keep 44 px targets, and Retry
// sends exactly the retry set. Every call is mocked (sourcesImportMocks.ts).
const expect = baseExpect.configure({ timeout: 15_000 })
test.describe.configure({ timeout: 90_000 })

const SHOTS = process.env.STEP107_SHOTS_DIR
const TIMED_OUT = 'The site took too long to answer.'
const NOT_ATTEMPTED = 'Not attempted: the import stopped before this chapter.'

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function tallTargets(page: Page) {
  const small = await page.locator('.sources-page').evaluate((root) => {
    const sel = 'button:not(.link):not(.field-help-btn):not(.toggle), select, .sources-pick label, .sources-select-all'
    return [...root.querySelectorAll<HTMLElement>(sel)]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent || e.tagName).trim().slice(0, 30) }))
      .filter((x) => x.h < 44)
  })
  expect(small).toEqual([])
}

const row = (page: Page, name: string) =>
  page.locator('.sources-pick > li').filter({ has: page.getByRole('checkbox', { name, exact: true }) })

test('phone: marked chapters, a partial run and Retry, one column', async ({ page }) => {
  const s = await mockSources(page, { series: 'done' })
  const m = await mockImports(page, s, {
    importState: {
      imported_chapter_ids: ['c1', 'c2'],
      retry: [
        { chapter_id: 'c3', title: 'Chapter 3', status: 'failed', error: TIMED_OUT },
        { chapter_id: 'c4', title: 'Chapter 4', status: 'not_attempted', error: NOT_ATTEMPTED },
      ],
    },
    importBody: chapterImportResult({
      chapters: [
        { chapter_id: 'c3', title: 'Chapter 3', outcome: 'failed', error: TIMED_OUT },
        { chapter_id: 'c4', title: 'Chapter 4', outcome: 'not_attempted', error: NOT_ATTEMPTED },
      ],
      imported_count: 0, skipped_count: 0, failed_count: 1, not_attempted_count: 1, retry_chapter_ids: ['c3', 'c4'], partial: true,
    }),
  })
  await page.addInitScript(() => {
    localStorage.setItem('baihe.pref.sources.lastSeries', JSON.stringify({ source: 'alpha', series_id: 'a0', title: 'Heaven Book 1' }))
    localStorage.setItem('baihe.pref.sources.importInto.alpha:a0', '12')
  })
  await page.goto('/#/sources')
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(row(page, 'Chapter 1').getByText('Imported', { exact: true })).toBeVisible()
  await expect(row(page, 'Chapter 3').getByText(TIMED_OUT)).toBeVisible()
  await expect(row(page, 'Chapter 4').getByText('Not attempted', { exact: true })).toBeVisible()
  await expect(panel.getByRole('checkbox', { name: 'Select all 122 not yet imported' })).toBeVisible()
  const retry = panel.getByRole('button', { name: 'Retry failed chapters (2)' })
  await expect(retry).toBeVisible()
  await noSideways(page)
  await tallTargets(page)

  if (SHOTS) {
    await retry.scrollIntoViewIfNeeded()
    await page.evaluate(() => window.scrollBy(0, 120))
    await page.screenshot({ path: `${SHOTS}/step107-phone.png` })
  }

  await retry.click()
  await expect.poll(() => posted(s, '/api/sources/alpha/import')[0]?.body).toEqual({ series_id: 'a0', chapter_ids: ['c3', 'c4'], drama_id: 12 })
  const outcomes = panel.getByTestId('import-outcomes')
  await expect(outcomes.getByText('0 imported · 1 failed · 1 not attempted')).toBeVisible()
  await expect(outcomes.getByText('Not attempted', { exact: true })).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Retry failed chapters (2)' })).toBeEnabled()
  await noSideways(page)
  await tallTargets(page)
  expect(m.importJob).toBe('done')
  expect(s.unmocked).toEqual([])
})
