import { expect, test, type Page } from '@playwright/test'
import { glossaryTab } from './glossaryTabs'

// "Check names across all titles" on the Translate stage's Glossary panel. The whole API
// is mocked (names are invented): the scan, the marks, the export and the glossary term
// routes the Apply step uses.

const BASE = '**/api/glossary/name-report'

const GROUP = {
  key: 'yalo', canonical: 'Yalo', total: 6, titles_total: 2, mark: null as string | null,
  variants: [{ text: 'Yalo', count: 4 }, { text: 'Yaloch', count: 2 }],
  titles: [
    { drama_id: 1, title: 'Alpha Stream', count: 4, in_glossary: true, glossary_term: 'Yalo', glossary_english: 'Yarrow' },
    { drama_id: 2, title: 'Beta Stream', count: 2, in_glossary: false, glossary_term: null, glossary_english: null },
  ],
  examples: [{ drama_id: 2, line_idx: 4, snippet: 'then Yaloch left' }],
}
const OTHER = { ...GROUP, key: 'mirel', canonical: 'Mirel', total: 3, titles_total: 1, titles: [GROUP.titles[1]],
  variants: [{ text: 'Mirel', count: 2 }, { text: 'Mirelle', count: 1 }], examples: [] }

function report(marks: Record<string, string>) {
  const groups = [GROUP, OTHER].map((g) => ({ ...g, mark: marks[g.key] ?? null }))
  const count = (m: string | null) => groups.filter((g) => g.mark === m).length
  return {
    titles_total: 2, titles_scanned: 2, titles_unavailable: 0, titles_failed: 0, reasons: [], truncated: false, stopped_early: false,
    singletons_omitted: 3, groups_omitted: 0, groups,
    counts: { unresolved: count(null), right: count('right'), wrong: count('wrong'), skip: count('skip') },
  }
}
const done = (marks: Record<string, string>) => ({ job_id: 'glossary_name_report_pc', status: 'done', progress: 1, message: '', result: report(marks) })

async function openPanel(page: Page) {
  await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({ json: [] }))
  await page.goto('/#/drama/1/translate')
  const glossary = page.getByRole('region', { name: 'Glossary' })
  await glossaryTab(page, 'Names')
  await glossary.getByRole('button', { name: 'Check names across all titles' }).click()
  return glossary.getByRole('region', { name: 'Name check across all titles' })
}

async function mockScan(page: Page, marks: Record<string, string> = {}) {
  let started = false
  await page.route(BASE, (route) => {
    if (route.request().method() === 'POST') { started = true; return route.fulfill({ json: { job_id: 'glossary_name_report_pc' } }) }
    return route.fulfill({ json: started || Object.keys(marks).length
      ? done(marks)
      : { job_id: '', status: 'idle', progress: 0, message: '', result: null } })
  })
  await page.route(`${BASE}/marks`, (route) => {
    const body = route.request().postDataJSON() as { key: string; mark: string }
    if (body.mark === 'clear') delete marks[body.key]
    else marks[body.key] = body.mark
    return route.fulfill({ json: { key: body.key, mark: body.mark === 'clear' ? null : body.mark } })
  })
}

test('runs the check and lists the groups with counts, titles and examples', async ({ page }) => {
  await mockScan(page)
  const panel = await openPanel(page)
  await expect(panel).toContainText('No check has been run yet')
  await expect(panel.getByRole('button', { name: 'Copy report' })).toBeDisabled()
  await panel.getByRole('button', { name: 'Check names' }).click()
  const yalo = panel.getByRole('listitem', { name: 'Name Yalo' })
  await expect(yalo).toContainText('2 titles · 6 mentions')
  await expect(yalo).toContainText('in glossary as “Yalo” → “Yarrow”')
  await expect(yalo).toContainText('Beta Stream #5: then Yaloch left')
  await expect(panel.getByRole('button', { name: /To check/ })).toContainText('2')
  await expect(panel.getByRole('button', { name: 'Check again' })).toBeVisible()
})

test('marking a group wrong hides it from To check and keeps it under Wrong until undone', async ({ page }) => {
  await mockScan(page, { })
  const panel = await openPanel(page)
  await panel.getByRole('button', { name: 'Check names' }).click()
  await panel.getByRole('button', { name: 'Mark Mirel wrong' }).click()
  await expect(panel.getByRole('listitem', { name: 'Name Mirel' })).toHaveCount(0)
  await expect(panel.getByRole('button', { name: /Wrong/ })).toContainText('1')
  await panel.getByRole('button', { name: /Wrong/ }).click()
  const mirel = panel.getByRole('listitem', { name: 'Name Mirel' })
  await expect(mirel.getByRole('button', { name: 'Mark Mirel wrong' })).toHaveAttribute('aria-pressed', 'true')
  await mirel.getByRole('button', { name: 'Undo mark' }).click()
  await expect(panel.getByText('Nothing marked wrong yet.')).toBeVisible()
  await panel.getByRole('button', { name: /To check/ }).click()
  await expect(panel.getByRole('listitem', { name: 'Name Mirel' })).toBeVisible()
})

test('a group marked right is added to the chosen glossaries only after a confirm, through the term routes', async ({ page }) => {
  await mockScan(page, { yalo: 'right' })
  const saved: Array<{ id: number; body: unknown }> = []
  await page.route('**/api/glossary/dramas/2/terms', (route) => {
    if (route.request().method() === 'POST') { saved.push({ id: 2, body: route.request().postDataJSON() }); return route.fulfill({ json: {} }) }
    return route.fulfill({ json: [] })
  })
  const panel = await openPanel(page)
  // Registered after the panel opens: openPanel's own empty-glossary mock for title 1 would win otherwise.
  await page.route('**/api/glossary/dramas/1/terms', (route) => {
    if (route.request().method() === 'POST') { saved.push({ id: 1, body: route.request().postDataJSON() }); return route.fulfill({ json: {} }) }
    return route.fulfill({ json: [{ id: 11, term_original: 'Yalo', term_translation: 'Yarrow', notes: '', category: null, policy: null, enforce_exact: false, aliases: [], banned_translations: [] }] })
  })
  await panel.getByRole('button', { name: /Right/ }).click()
  const yalo = panel.getByRole('listitem', { name: 'Name Yalo' })
  await yalo.getByRole('button', { name: 'Apply to titles…' }).click()
  const form = yalo.getByRole('group', { name: 'Apply Yalo to titles' })
  await form.getByRole('button', { name: 'Add to 2 titles…' }).click()
  await expect(form.getByRole('alert')).toContainText('Add to 2 glossaries?')
  expect(saved).toEqual([])
  await form.getByRole('button', { name: 'Cancel' }).click()
  expect(saved).toEqual([])
  await form.getByRole('button', { name: 'Add to 2 titles…' }).click()
  await form.getByRole('button', { name: 'Yes, add' }).click()
  await expect(form.getByLabel('Results')).toContainText('Alpha Stream: Aliases added.')
  await expect(form.getByLabel('Results')).toContainText('Beta Stream: Term added.')
  expect(saved).toEqual([
    { id: 1, body: { id: 11, aliases: ['Yaloch'] } },
    { id: 2, body: { term_original: 'Yalo', term_translation: 'Yarrow', aliases: ['Yaloch'] } },
  ])
})

test('Apply is disabled with a reason until the group is marked right', async ({ page }) => {
  await mockScan(page, { })
  const panel = await openPanel(page)
  await panel.getByRole('button', { name: 'Check names' }).click()
  const yalo = panel.getByRole('listitem', { name: 'Name Yalo' })
  await expect(yalo.getByRole('button', { name: 'Apply to titles…' })).toBeDisabled()
  await expect(yalo).toContainText('Mark it Right first.')
})

test('Copy report copies the unresolved names from the export route', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await mockScan(page, { })
  await page.route(`${BASE}/export.md`, (route) => route.fulfill({ contentType: 'text/markdown', body: '# Name check across titles\n\n## 1. Yalo\n' }))
  const panel = await openPanel(page)
  await panel.getByRole('button', { name: 'Check names' }).click()
  await panel.getByRole('button', { name: 'Copy report' }).click()
  await expect(panel.getByText('Copied the unresolved names.')).toBeVisible()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain('## 1. Yalo')
})

test('shows progress and can cancel a running check', async ({ page }) => {
  let cancelled = false
  await page.route(BASE, (route) => route.fulfill({ json: cancelled
    ? { job_id: 'glossary_name_report_pc', status: 'cancelled', progress: 0.4, message: '', result: null }
    : { job_id: 'glossary_name_report_pc', status: 'running', progress: 0.4, message: 'Checking title 2 of 5...', result: null } }))
  await page.route(`${BASE}/cancel`, (route) => { cancelled = true; return route.fulfill({ json: { job_id: 'glossary_name_report_pc', cancel_requested: true, status: 'running' } }) })
  const panel = await openPanel(page)
  await expect(panel).toContainText('Checking title 2 of 5... 40%')
  await expect(panel.getByRole('button', { name: 'Check names' })).toBeDisabled()
  await expect(panel).toContainText('A check is already running.')
  await panel.getByRole('button', { name: 'Cancel' }).click()
  await expect(panel).toContainText('Cancelled. Nothing was changed.')
})

test('a missing lines.read shows a muted note, not an error banner', async ({ page }) => {
  await page.route(BASE, (route) => route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } }))
  const panel = await openPanel(page)
  await expect(panel).toContainText('Seeing line text needs permission to read lines.')
  await expect(panel.locator('.error-banner')).toHaveCount(0)
  await expect(panel.getByRole('button', { name: 'Check names' })).toBeDisabled()
})

test('says so when there is nothing to check', async ({ page }) => {
  await page.route(BASE, (route) => route.fulfill({ json: { job_id: 'x', status: 'done', progress: 1, message: '', result: {
    ...report({}), groups: [], singletons_omitted: 0, counts: { unresolved: 0, right: 0, wrong: 0, skip: 0 } } } }))
  const panel = await openPanel(page)
  await expect(panel.getByText('No names left to check. Run the check again')).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Copy report' })).toBeDisabled()
})
