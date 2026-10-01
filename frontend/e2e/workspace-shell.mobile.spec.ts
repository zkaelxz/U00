import { expect, test } from './fixtures'

// Phone project (390x844, touch): the app header's nav and the Workspace
// header keep to a few compact rows so the stage content starts high.

const progress = {
  drama_id: 1, stage_index: 3, stage: 'translate', line_count: 12, untranslated_count: 2,
  flagged_count: 1, has_audio: true, has_dub_track: false, exported: false,
  stages: [
    { key: 'source', state: 'done' }, { key: 'translate', state: 'current' }, { key: 'review', state: 'pending' },
    { key: 'dub', state: 'optional' }, { key: 'export', state: 'pending' },
  ],
}

test('the main nav is two rows of four links, in order, each 44px tall', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  const links = page.getByRole('navigation', { name: 'Main' }).getByRole('link')
  await expect(links).toHaveText(['Library', 'Translate', 'Sources', 'Discover', 'Live', 'Settings', 'Diagnostics'])
  const boxes = await links.evaluateAll((els) =>
    els.map((e) => { const r = e.getBoundingClientRect(); return { top: Math.round(r.top), left: r.left, right: r.right, h: r.height } }))
  const tops = [...new Set(boxes.map((b) => b.top))]
  expect(tops, 'nav rows').toHaveLength(2)
  expect(boxes.filter((b) => b.top === tops[0])).toHaveLength(4)
  expect(boxes.filter((b) => b.top === tops[1])).toHaveLength(3)
  for (const b of boxes) {
    expect(b.h).toBeGreaterThanOrEqual(44)
    expect(b.left).toBeGreaterThanOrEqual(0)
    expect(b.right).toBeLessThanOrEqual(390)
  }
})

test('Workspace header on a phone: a 44px back button and the title; media, line count and Read are hidden', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress }))
  await page.goto('/#/drama/1/translate')
  const header = page.locator('.workspace-header')
  const back = header.getByRole('link', { name: 'Back to Library' })
  await expect(back).toBeVisible()
  const backBox = (await back.boundingBox())!
  expect(backBox.width).toBeGreaterThanOrEqual(44)
  expect(backBox.height).toBeGreaterThanOrEqual(44)
  await expect(back.locator('.ws-back-text')).toBeHidden()
  await expect(header.locator('.ws-media')).toBeHidden()
  await expect(header.getByTestId('stage-counts')).toBeHidden()
  await expect(header.getByRole('link', { name: 'Read', exact: true })).toBeHidden()
  // The title shares the back button's row.
  const title = (await header.getByTestId('drama-title').boundingBox())!
  expect(title.y).toBeLessThan(backBox.y + backBox.height)
  // The stepper drops its counts but keeps the marks.
  await expect(page.locator('nav.stage-tabs .stage-count').first()).toBeHidden()
  await expect(page.locator('nav.stage-tabs .stage-mark').first()).toBeVisible()
})
