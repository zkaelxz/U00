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
  await page.getByRole('region', { name: 'Dramas' }).getByRole('link', { name: 'Signal', exact: true }).click()
  // No stage in the link: the workspace opens the drama's current stage
  // (Source for the seeded drama, which has no lines).
  await expect(page).toHaveURL(/#\/drama\/\d+$/)
  await expect(page.getByRole('link', { name: 'Source', exact: true })).toHaveAttribute('aria-current', 'page')
})

for (const stage of ['source', 'translate', 'review', 'dub', 'export']) {
  test(`Workspace ${stage} stage`, async ({ page }) => {
    await page.goto(`/#/drama/1/${stage}`)
    await expect(page.locator('nav.stage-tabs')).toBeVisible()
    await page.waitForLoadState('networkidle')
    await checkScreen(page, ['nav.stage-tabs a', '.workspace-header .ws-back'])
    // All five stages in one row: none cut off or pushed off-screen.
    const boxes = await page.locator('nav.stage-tabs a').evaluateAll((els) =>
      els.map((e) => { const r = e.getBoundingClientRect(); return { top: Math.round(r.top), right: r.right } }))
    expect(boxes).toHaveLength(5)
    expect(new Set(boxes.map((b) => b.top)).size).toBe(1)
    for (const b of boxes) expect(b.right).toBeLessThanOrEqual(390)
    // §3.3: the app header and Workspace header are compact enough that stage content starts by y=300.
    const contentTop = await page.locator('nav.stage-tabs').evaluate((n) => {
      let s = n.nextElementSibling
      while (s && s.getBoundingClientRect().height === 0) s = s.nextElementSibling
      return s ? s.getBoundingClientRect().top : Infinity
    })
    expect(contentTop).toBeLessThanOrEqual(300)
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
