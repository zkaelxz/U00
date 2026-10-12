import { expect, test } from '@playwright/test'

import { ME } from './authMocks'
import { untilTestEnds } from './stageLineMocks'
import { hitHeight, installHitArea } from './hitArea'

// Phone (390x844): the tidied Translate tab has no sideways scroll in light or
// dark, and its new controls are tall enough to touch.

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
  await page.route(/\/api\/auth\/me$/, (route) => route.fulfill({ json: ME.authOff }))
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
  await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/translate-run/dramas/1/config', (route) =>
    untilTestEnds(async () => {
      const real = await (await route.fetch()).json()
      await route.fulfill({
        json: { ...real, line_count: 4, untranslated_count: 4, translation_engine: 'claude', batch_size_defaults: { claude: { '': 60 } } },
      })
    }),
  )
  await page.addInitScript(() => {
    if (!localStorage.getItem('baihe.draft.1.translate')) {
      localStorage.setItem('baihe.draft.1.translate', JSON.stringify({
        v: 1, values: { v: 2, form: { batch_size: '30' }, baseEngine: 'claude', reviewFirst: false },
      }))
    }
  })
})

for (const scheme of ['light', 'dark'] as const) {
  test(`translate tab fits a 390px phone (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('/#/drama/1/translate')
    const run = page.getByRole('region', { name: 'Translate run' })
    await expect(run.getByRole('button', { name: 'Translate 4 lines' })).toBeVisible()
    for (const t of [
      run.getByRole('button', { name: 'Translate 4 lines' }),
      run.getByRole('button', { name: 'Estimate cost' }),
      run.getByLabel('Style', { exact: true }),
    ]) expect(await hitHeight(t)).toBeGreaterThanOrEqual(44)

    await run.getByText('More options', { exact: true }).click()
    await expect(page.getByTestId('batch-size-note')).toBeVisible()
    expect(await hitHeight(page.getByRole('button', { name: 'Use default' }))).toBeGreaterThanOrEqual(44)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: `test-results/translate-tidy-phone-${scheme}.png`, fullPage: true })
  })
}
