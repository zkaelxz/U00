import { expect, test, type Page } from '@playwright/test'
import { glossaryTab, type GlossaryTabName } from './glossaryTabs'
import { glossarySetup } from './glossaryScanMocks'
import { gotoTranslate } from './translateTabs'

// The Glossary panel under one level of tabs: Terms | Find terms | Names | Instructions |
// Re-translate. Terms, instructions and the scans are mocked.

test.use({ viewport: { width: 1280, height: 800 } })

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const TABS: GlossaryTabName[] = ['Terms', 'Find terms', 'Names', 'Instructions', 'Re-translate']
const region = (page: Page) => page.getByRole('region', { name: 'Glossary' })

async function open(page: Page, opts: Parameters<typeof glossarySetup>[1] = {}) {
  await glossarySetup(page, opts)
  await page.route('**/api/glossary/dramas/1/from-lines', (r) => r.fulfill({ json: { job_id: '', status: 'idle', progress: 0, message: '', proposals: null, run_id: null } }))
  await gotoTranslate(page, 'glossary')
  await expect(region(page).getByRole('tab', { name: 'Terms', exact: true })).toBeVisible()
}

test('five tabs in one row, and no other tab row inside the Glossary', async ({ page }) => {
  await open(page)
  await expect(region(page).getByRole('tablist')).toHaveCount(1)
  await expect(region(page).getByRole('tab')).toHaveText(TABS)
  await expect(region(page).getByRole('tab', { name: 'Terms', exact: true })).toHaveAttribute('aria-selected', 'true')
})

test('the last tab is remembered for the title, and another title starts on Terms', async ({ page }) => {
  await open(page)
  await glossaryTab(page, 'Names')
  await page.reload()
  await expect(region(page).getByRole('tab', { name: 'Names', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(region(page).getByRole('button', { name: 'Find possible names' })).toBeVisible()
  const other = await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('baihe.pref.glossary.ui.')))
  expect(other).toEqual(['baihe.pref.glossary.ui.1'])
})

test('Instructions: project and series are saved separately, each with a count and a set/not set line', async ({ page }) => {
  await glossarySetup(page)
  const saved: { scope: string; body: unknown }[] = []
  let project = ''
  let series = ''
  await page.route('**/api/glossary/dramas/1/instructions', (route) => route.fulfill({ json: { project_instructions: project, series_instructions: series } }))
  await page.route('**/api/glossary/dramas/1/instructions/*', (route) => {
    const scope = route.request().url().split('/').pop()!
    const body = route.request().postDataJSON() as { text: string }
    saved.push({ scope, body })
    if (scope === 'project') project = body.text
    else series = body.text
    return route.fulfill({ json: { project_instructions: project, series_instructions: series } })
  })
  await page.route('**/api/glossary/dramas/1/from-lines', (r) => r.fulfill({ json: { job_id: '', status: 'idle', progress: 0, message: '', proposals: null, run_id: null } }))
  await gotoTranslate(page, 'glossary')
  await glossaryTab(page, 'Instructions')
  const state = page.getByTestId('instructions-state')
  await expect(state).toHaveText('Project: not set · Series: not set')

  await page.getByLabel('Project instructions').fill('Keep honorifics')
  await expect(page.getByTestId('project-instructions-count')).toHaveText('15 characters')
  // Typing alone claims nothing: the line follows what was saved.
  await expect(state).toHaveText('Project: not set · Series: not set')
  await page.getByRole('button', { name: 'Save project instructions' }).click()
  await expect(state).toHaveText('Project: set, 15 characters · Series: not set')
  expect(saved).toEqual([{ scope: 'project', body: { text: 'Keep honorifics' } }])

  await region(page).getByText('Series instructions', { exact: true }).first().click()
  await page.getByLabel('Series instructions').fill('Names stay in pinyin')
  await expect(page.getByTestId('series-instructions-count')).toHaveText('20 characters')
  await page.getByRole('button', { name: 'Save series instructions' }).click()
  await expect(state).toHaveText('Project: set, 15 characters · Series: set, 20 characters')
  expect(saved.map((s) => s.scope)).toEqual(['project', 'series'])
})

test('Instructions: the first card is open the first time and the rest closed', async ({ page }) => {
  await open(page)
  await glossaryTab(page, 'Instructions')
  const sections = region(page).locator('details.section', { has: page.locator('.section-title', { hasText: /instructions$/ }) })
  await expect(sections).toHaveCount(2)
  await expect(sections.nth(0)).toHaveAttribute('open', '')
  await expect(sections.nth(1)).not.toHaveAttribute('open', '')
  await expect(sections.nth(1).locator('.section-summary')).toHaveText('Not set')
})

test('a row action on Terms opens its panel on Names', async ({ page }) => {
  await open(page)
  await region(page).getByRole('button', { name: 'Find similar spellings of ヤロ' }).click()
  await expect(region(page).getByRole('tab', { name: 'Names', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(region(page).getByRole('region', { name: 'Similar spellings of ヤロ' })).toBeVisible()
})

test('tabs with nothing to act on say why', async ({ page }) => {
  await open(page, { terms: [], series: false })
  await expect(region(page).getByRole('status').filter({ hasText: 'No terms yet. Add this title to a series first' })).toBeVisible()
  await glossaryTab(page, 'Re-translate')
  await expect(region(page).getByRole('status').filter({ hasText: 'Add this title to a series first' })).toBeVisible()
  await glossaryTab(page, 'Instructions')
  await expect(page.getByText('Series instructions need a series')).toBeVisible()
})

test('Re-translate shows the list button once the title has terms', async ({ page }) => {
  await open(page)
  await glossaryTab(page, 'Re-translate')
  await expect(region(page).getByRole('button', { name: 'Re-translate lines affected by the glossary…' })).toBeVisible()
})

for (const scheme of ['light', 'dark'] as const) {
  test(`each tab is about one screen and does not scroll sideways (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme })
    await open(page)
    for (const name of TABS) {
      await glossaryTab(page, name)
      const box = await region(page).boundingBox()
      expect(box!.height, `${name} tab height`).toBeLessThanOrEqual(800)
      const { scroll, client } = await page.evaluate(() => ({
        scroll: document.documentElement.scrollWidth,
        client: document.documentElement.clientWidth,
      }))
      expect(scroll, `${name} scrolls sideways`).toBeLessThanOrEqual(client)
    }
  })
}
