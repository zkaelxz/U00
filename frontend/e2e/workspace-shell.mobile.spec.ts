import { expect, test } from './fixtures'

// Phone project (390x844, touch): the app header and the Workspace
// header keep to a few compact rows so the stage content starts high.

const progress = {
  drama_id: 1, stage_index: 3, stage: 'translate', line_count: 12, untranslated_count: 2,
  flagged_count: 1, has_audio: true, has_dub_track: false, exported: false,
  stages: [
    { key: 'source', state: 'done' }, { key: 'translate', state: 'current' }, { key: 'review', state: 'pending' },
    { key: 'dub', state: 'optional' }, { key: 'export', state: 'pending' },
  ],
}

test('the header is one slim row and the drawer lists the pages, each link 44px tall and on screen', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  const header = (await page.locator('.app-header').boundingBox())!
  // Brand, Menu, Jobs, bell, report, theme and account in one row: no 3 by 2 nav grid under them.
  expect(header.height, 'header height').toBeLessThanOrEqual(72)
  await page.getByRole('button', { name: 'Menu', exact: true }).tap()
  const links = page.getByRole('dialog', { name: 'Main menu' }).getByRole('navigation', { name: 'Main' }).getByRole('link')
  await expect(links.first()).toBeVisible()
  const texts = (await links.allTextContents()).map((t) => t.trim())
  expect(texts).toEqual(expect.arrayContaining(['Library', 'Quick translate', 'Sources', 'Discover', 'Live', 'Settings']))
  const boxes = await links.evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return { left: r.left, right: r.right, h: r.height } }))
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
