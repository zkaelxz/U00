import { expect, test } from './fixtures'
import type { Page } from '@playwright/test'

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

// Simple leaves the Characters tab and the run settings out of the Translate stage, but links elsewhere still
// name them; each must open what it names rather than land on a page without it.
const simpleView = (page: Page) => page.addInitScript(() => localStorage.setItem('baihe.pref.title.view', '"simple"'))

for (const link of ['focus=characters', 'tab=characters']) test(`Simple: a link to the Characters tab (${link}) opens it`, async ({ page }) => {
  await simpleView(page)
  await setup(page, 3, { line_count: 4, untranslated_count: 4 })
  await page.goto(`/#/drama/3/translate?${link}`)
  await expect(page.getByRole('tab', { name: 'Characters', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('region', { name: 'Characters', exact: true })).toBeInViewport()
})

test('a member gets no engine or options: the run sends none, so the server picks one their account may use', async ({ page }) => {
  await setup(page, 2, { line_count: 4, untranslated_count: 4 })
  await page.route(/\/api\/auth\/me$/, (route) => route.fulfill({ json: {
    ...ME.signedIn, permissions: ['library.read', 'lines.read', 'lines.edit', 'jobs.start', 'jobs.cancel', 'review.use'],
  } }))
  await page.addInitScript(() => {
    localStorage.setItem('baihe.draft.2.translate', JSON.stringify({ v: 1, values: { form: { engine: 'claude', model: 'claude-opus-5-5' }, baseEngine: 'claude', reviewFirst: false } }))
  })
  const sent: unknown[] = []
  await page.route('**/api/translate-run/dramas/2/run', (route) => {
    sent.push(route.request().postDataJSON())
    return route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'Stop here.' } } })
  })
  await gotoTranslate(page, 'translate', 2)
  await expect(page.getByTestId('translate-summary')).toHaveText('The engine and settings are chosen for you.')
  await expect(more(page)).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Estimate cost' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Translate 4 lines' }).click()
  await expect.poll(() => sent.length).toBe(1)
  expect(sent[0]).not.toHaveProperty('engine')
  expect(sent[0]).not.toHaveProperty('model')
})
