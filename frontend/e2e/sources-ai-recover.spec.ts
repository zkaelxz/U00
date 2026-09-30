import { expect as baseExpect, test } from '@playwright/test'

import { mockExtraction, novelReview } from './sourcesExtractionMocks'
import { mockImports } from './sourcesImportMocks'
import { mockSources, posted } from './sourcesMocks'

// A chapter whose page layout changed ("Needs AI help"): pick an engine,
// Confirm shows engine / "1 AI call" / paid or not, nothing is sent before
// Confirm, and the finished job opens the Review extraction. Every call is
// mocked. Jobs poll every 1.5 s, so allow a few polls per assertion.
const expect = baseExpect.configure({ timeout: 15_000 })
test.describe.configure({ timeout: 90_000 })

const NEEDS_AI = 'The page loaded but this source’s layout has changed. Needs AI help: confirm.'

test('Needs AI help: nothing is sent until Confirm; it shows engine, 1 AI call and paid; ends in the review', async ({ page }) => {
  const s = await mockSources(page, { series: 'done' })
  const m = await mockImports(page, s, {
    importState: {
      imported_chapter_ids: ['c1'],
      retry: [{ chapter_id: 'c2', title: 'Chapter 2', status: 'needs_ai', error: NEEDS_AI }],
    },
  })
  await mockExtraction(page, s, m, { review: novelReview({ drama_id: 12, why: 'recovery' }) })
  const recoverPath = '/api/sources/alpha/import/c2/ai-recover'
  await page.route(/\/api\/sources\/alpha\/import\/c2\/ai-recover$/, (route) => {
    s.calls.push({ method: 'POST', path: recoverPath, body: route.request().postDataJSON() })
    m.importJob = 'running'
    m.importKind = 'url'
    m.urlImportBody = { kind: 'url_import', needs_review: true, char_count: 5120, review_open: true, llm_calls: 1 }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ job_id: 'sourceimport_12' }) })
  })

  await page.addInitScript(() => {
    localStorage.setItem('baihe.pref.sources.lastSeries', JSON.stringify({ source: 'alpha', series_id: 'a0', title: 'Heaven Book 1' }))
    localStorage.setItem('baihe.pref.sources.importInto.alpha:a0', '12')
  })
  await page.goto('/#/sources')
  const panel = page.getByRole('region', { name: 'Series' })

  // It is marked in the picker and listed, but the normal retry does not include it.
  const recover = panel.getByTestId('ai-recover')
  await expect(recover.getByText('Chapter 2')).toBeVisible()
  await expect(panel.getByTestId('import-retry')).toHaveCount(0)

  await recover.getByRole('button', { name: 'Use AI help…' }).click()
  await expect(recover.getByText(NEEDS_AI)).toBeVisible()
  await expect(recover.getByTestId('ai-recover-summary')).toHaveText('Claude · 1 AI call · may use paid credits')
  await recover.getByRole('combobox', { name: 'AI engine' }).selectOption('ollama')
  await expect(recover.getByTestId('ai-recover-summary')).toHaveText('Ollama (on this PC) · 1 AI call · free engine')
  await recover.getByRole('combobox', { name: 'AI engine' }).selectOption('claude')
  expect(posted(s, recoverPath)).toHaveLength(0)

  await recover.getByRole('button', { name: 'Confirm' }).click()
  await expect.poll(() => posted(s, recoverPath)[0]?.body).toEqual({ series_id: 'a0', drama_id: 12, engine: 'claude', confirm: true })
  await expect(panel.getByTestId('extraction-review')).toBeVisible()
  await expect(panel.getByText('AI read it once', { exact: false })).toBeVisible()
  expect(posted(s, recoverPath)).toHaveLength(1)
  expect(s.unmocked).toEqual([])
})
