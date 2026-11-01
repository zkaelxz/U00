import { expect, test, type Page } from '@playwright/test'

import { ME } from './authMocks'
import { untilTestEnds } from './stageLineMocks'
import { gotoTranslate } from './translateTabs'

// Novel and comic titles get one Translate button with the engine and run options inside "More options"; video
// and audio titles keep them in view. Seeded titles: 1 audio drama, 2 novel, 3 video drama. Config reads hit the
// real seeded API with the line counts overridden.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function setup(page: Page, id: number, counts: { line_count: number; untranslated_count: number }) {
  await page.route(/\/api\/auth\/me$/, (route) => route.fulfill({ json: ME.authOff }))
  await page.route(`**/api/translate-run/dramas/${id}/config`, (route) =>
    untilTestEnds(async () => {
      const real = await (await route.fetch()).json()
      await route.fulfill({ json: { ...real, ...counts, translation_engine: 'claude' } })
    }),
  )
}

const engine = (page: Page) => page.getByLabel('AI engine', { exact: true })
const more = (page: Page) => page.locator('details.section').filter({ has: page.locator('.section-title', { hasText: /^More options$/ }) })

test('a novel shows one Translate button and keeps the engine inside More options', async ({ page }) => {
  await setup(page, 2, { line_count: 4, untranslated_count: 4 })
  await gotoTranslate(page, 'translate', 2)
  await expect(page.getByRole('button', { name: 'Translate 4 lines' })).toBeVisible()
  await expect(engine(page)).toBeHidden()
  await expect(page.getByRole('button', { name: 'Estimate cost' })).toBeHidden()
  await more(page).locator(':scope > summary').click()
  await expect(engine(page)).toBeVisible()
  await expect(page.getByLabel('English variant', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Estimate cost' })).toBeVisible()
})

test('a novel with nothing left to translate has Next as its only primary', async ({ page }) => {
  await setup(page, 2, { line_count: 4, untranslated_count: 0 })
  await gotoTranslate(page, 'translate', 2)
  const run = page.getByRole('region', { name: 'Translate run' })
  await expect(run.getByRole('link', { name: 'Next: Review' })).toBeVisible()
  await expect(run.getByRole('button', { name: /^Translate/ })).toHaveCount(0)
})

test('a saved choice still applies to a folded novel run', async ({ page }) => {
  await setup(page, 2, { line_count: 4, untranslated_count: 4 })
  await page.addInitScript(() => {
    localStorage.setItem('baihe.draft.2.translate', JSON.stringify({ v: 1, values: { form: { engine: 'ollama' }, baseEngine: 'claude', reviewFirst: false } }))
  })
  await gotoTranslate(page, 'translate', 2)
  await more(page).locator(':scope > summary').click()
  await expect(engine(page)).toHaveValue('ollama')
})

test('a video title keeps the engine, variant and cost estimate in view', async ({ page }) => {
  await setup(page, 3, { line_count: 4, untranslated_count: 4 })
  await gotoTranslate(page, 'translate', 3)
  await expect(engine(page)).toBeVisible()
  await expect(page.getByLabel('English variant', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Estimate cost' })).toBeVisible()
})

test('a video title with nothing left to translate keeps its Translate button beside Next', async ({ page }) => {
  await setup(page, 3, { line_count: 4, untranslated_count: 0 })
  await gotoTranslate(page, 'translate', 3)
  const run = page.getByRole('region', { name: 'Translate run' })
  await expect(run.getByRole('button', { name: /^Translate/ })).toBeVisible()
  await expect(run.getByRole('link', { name: 'Next: Review' })).toBeVisible()
})
