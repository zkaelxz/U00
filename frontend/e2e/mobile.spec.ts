import { expect, test, type Page } from '@playwright/test'

// Phone project only (390x844, touch): each screen fits the width and its
// main touch targets are at least 44px tall.

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function expectTall(page: Page, selector: string) {
  const heights = await page.locator(selector).evaluateAll((els) =>
    els
      .filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, text: (e.textContent ?? '').trim().slice(0, 40) })),
  )
  expect(heights.length, `no visible ${selector}`).toBeGreaterThan(0)
  for (const { h, text } of heights) expect(h, `${selector} "${text}"`).toBeGreaterThanOrEqual(44)
}

async function visiblePrimaries(page: Page) {
  return page.locator('button.primary, .btn-primary').evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null).length,
  )
}

async function checkScreen(page: Page, extra: string[] = []) {
  await expectNoHorizontalOverflow(page)
  await expectTall(page, '.app-header nav a')
  for (const sel of extra) await expectTall(page, sel)
  if ((await visiblePrimaries(page)) > 0) await expectTall(page, 'button.primary, .btn-primary')
}

test('Library: cards, title opens the workspace', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('drama-count')).toBeVisible()
  await expect(page.locator('.drama-grid')).toBeVisible()
  await checkScreen(page, ['.drama-grid .drama-card', '.drama-card-foot .btn', '.library-page .btn-primary', '.continue-item .btn'])
  await page.getByRole('link', { name: 'Signal', exact: true }).click()
  await expect(page).toHaveURL(/#\/drama\/\d+\/source$/)
})

for (const stage of ['source', 'translate', 'review', 'dub', 'export']) {
  test(`Workspace ${stage} stage`, async ({ page }) => {
    await page.goto(`/#/drama/1/${stage}`)
    await expect(page.locator('nav.stage-tabs')).toBeVisible()
    await page.waitForLoadState('networkidle')
    await checkScreen(page, ['nav.stage-tabs a'])
  })
}

for (const [name, path] of [
  ['Settings', '/#/settings'],
  ['Diagnostics', '/#/diagnostics'],
]) {
  test(`${name} page`, async ({ page }) => {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    await checkScreen(page)
  })
}

test('Library at 360px: no sideways scroll; New drama and Details open bottom sheets', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 })
  await page.goto('/')
  await expect(page.getByTestId('drama-count')).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await expectTall(page, '.segmented label')
  await page.getByRole('button', { name: 'New drama' }).click()
  await expect(page.getByRole('dialog', { name: 'New drama' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.getByRole('button', { name: 'Close' }).click()
  await page.getByRole('button', { name: 'Details: Signal' }).click()
  const sheet = page.getByRole('dialog', { name: 'Signal' })
  // The details load after the sheet opens.
  await expect(sheet.getByRole('link', { name: 'Open workspace' })).toBeVisible()
  await expectTall(page, '.drama-detail-actions .btn')
  const box = await sheet.boundingBox()
  expect(box && Math.round(box.y + box.height)).toBe(800)
})
