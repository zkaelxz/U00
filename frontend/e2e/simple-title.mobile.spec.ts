import { expect, test, type Locator } from '@playwright/test'

import { expectNoSidewaysScroll } from './mockHelpers'

// The Simple title view at phone width. Title 1 is the seeded audio drama.

const progress = {
  drama_id: 1, stage_index: 1, stage: 'translate', line_count: 10, untranslated_count: 10,
  flagged_count: 0, has_audio: false, has_dub_track: false, exported: false, stages: [],
}

const actions = ['Transcribe and translate', 'Add media', 'Glossary', 'Review', 'Export']

test.beforeEach(async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress }))
})

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

// Height is the repo's touch-target check; the short "Full" label is under 44px wide but sits flush against its neighbour.
async function expectTall(loc: Locator, what: string) {
  await expect(loc).toBeVisible()
  const box = await loc.boundingBox()
  expect(box?.height ?? 0, `${what} is too short to tap`).toBeGreaterThanOrEqual(44)
}

test('Simple fits a phone: switch and five actions visible, tall enough to tap, no sideways scroll', async ({ page }) => {
  await page.goto('/#/drama/1')
  await page.getByRole('radio', { name: 'Simple' }).check({ force: true })
  await expect(page.getByRole('link', { name: 'Transcribe and translate' })).toBeVisible()

  // The radios are visually hidden; the visible control is their label.
  for (const name of ['Simple', 'Full']) {
    await expectTall(page.locator('label').filter({ hasText: new RegExp(`^${name}$`) }).first(), `${name} switch`)
  }
  for (const name of actions) {
    await expectTall(page.getByRole('link', { name, exact: true }), name)
  }
  await expectNoSidewaysScroll(page)
})

test('choosing Simple then reloading keeps Simple', async ({ page }) => {
  await page.goto('/#/drama/1')
  await page.getByRole('radio', { name: 'Simple' }).check({ force: true })
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('link', { name: 'Transcribe and translate' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  await expect(page.getByRole('radio', { name: 'Simple' })).toBeChecked()
})
