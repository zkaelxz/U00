import { expect, test } from './fixtures'

const progress = {
  drama_id: 1, stage_index: 1, stage: 'translate', line_count: 12, untranslated_count: 2,
  flagged_count: 1, has_audio: true, has_dub_track: false, exported: false,
  stages: Object.entries({ source: 'done', translate: 'done', review: 'current', dub: 'optional', export: 'pending' })
    .map(([key, state]) => ({ key, state })),
}

test('phone: strip sticks, Next is a 44px bottom bar, no sideways scroll', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress }))
  await page.goto('/#/drama/1/translate')
  const next = page.getByTestId('next-action')
  await expect(next).toHaveText('Next: Review 1 flagged')
  const box = (await next.boundingBox())!
  expect(box.height).toBeGreaterThanOrEqual(44)
  expect(box.y + box.height).toBeGreaterThan(800)
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
  expect((await page.locator('.ws-strip').boundingBox())!.y).toBeLessThanOrEqual(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await next.click()
  await expect(page).toHaveURL(/#\/drama\/1\/review$/)
})

test('phone: no Next bar on Review, where the edit bar owns the bottom edge', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) =>
    route.fulfill({ json: { ...progress, stages: progress.stages.map((s) => (s.key === 'review' ? { ...s, state: 'done' } : s)) } }))
  await page.goto('/#/drama/1/review')
  await expect(page.getByTestId('drama-title')).toBeVisible()
  await expect(page.getByTestId('next-action')).toHaveCount(0)
})
