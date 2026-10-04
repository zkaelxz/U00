import { expect, test } from './fixtures'

// The sticky title + stage strip, the Next button and the reserved tab slots.
// The drama read hits the seeded API; only the progress read is mocked.

const progress = (states: Record<string, string>, over: Record<string, unknown> = {}) => ({
  drama_id: 1, stage_index: 1, stage: 'translate', line_count: 12, untranslated_count: 2,
  flagged_count: 1, has_audio: true, has_dub_track: false, exported: false,
  stages: Object.entries(states).map(([key, state]) => ({ key, state })),
  ...over,
})
const SOURCE_DONE = { source: 'done', translate: 'current', review: 'pending', dub: 'optional', export: 'pending' }

test('the title and stage tabs stay visible after scrolling', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(SOURCE_DONE) }))
  await page.goto('/#/drama/1/translate')
  await expect(page.getByTestId('drama-title')).toBeVisible()
  // The seeded drama's stage is short; make the page tall enough to scroll.
  await page.evaluate(() => { (document.querySelector('section.workspace') as HTMLElement).style.minHeight = '3000px' })
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(100)
  const strip = page.locator('.ws-strip')
  await expect(strip).toBeInViewport({ ratio: 1 })
  expect((await strip.boundingBox())!.y).toBeLessThanOrEqual(1)
  // Review reads the strip height as --bar-h.
  const barH = await page.locator('section.workspace').evaluate((el) => el.style.getPropertyValue('--bar-h'))
  expect(parseInt(barH, 10)).toBeGreaterThan(30)
})

test('Next shows once the open stage is done and goes to the next stage', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(SOURCE_DONE) }))
  await page.goto('/#/drama/1/source')
  const next = page.getByTestId('next-action')
  await expect(next).toHaveText('Next: Translate 2 lines')
  await next.click()
  await expect(page).toHaveURL(/#\/drama\/1\/translate$/)
  // Translate is "current", not done: no Next there.
  await expect(page.getByTestId('next-action')).toHaveCount(0)
})

test('tab widths do not change when the progress counts arrive', async ({ page }) => {
  let release: () => void = () => {}
  const gate = new Promise<void>((r) => { release = r })
  await page.route('**/api/workflow/dramas/1/progress', async (route) => {
    await gate
    await route.fulfill({ json: progress(SOURCE_DONE, { untranslated_count: 32, flagged_count: 12 }) })
  })
  await page.goto('/#/drama/1/translate')
  const tab = (s: string) => page.locator(`nav.stage-tabs a[href$="/drama/1/${s}"]`)
  await expect(tab('translate')).toBeVisible()
  const before = await Promise.all(['source', 'translate', 'review', 'dub', 'export'].map(async (s) => (await tab(s).boundingBox())!))
  release()
  await expect(tab('translate').locator('.stage-count').filter({ hasText: '32 left' })).toBeAttached()
  const after = await Promise.all(['source', 'translate', 'review', 'dub', 'export'].map(async (s) => (await tab(s).boundingBox())!))
  before.forEach((b, i) => {
    expect(after[i].width).toBeCloseTo(b.width, 0)
    expect(after[i].x).toBeCloseTo(b.x, 0)
    expect(after[i].height).toBeCloseTo(b.height, 0)
  })
})
