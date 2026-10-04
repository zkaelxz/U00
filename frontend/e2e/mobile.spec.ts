import { expect, test, type Page } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

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
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent ?? '').trim().slice(0, 40) })),
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
    const contentTop = await page.locator('.ws-strip').evaluate((n) => {
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

test('Notion: the settings card and Export to Notion fit a phone', async ({ page }) => {
  // Mocked: Notion set up, and this drama already has a page.
  await page.route('**/api/notion/config', (route) =>
    route.fulfill({ json: { target_type: 'database', target_id: '01234567-89ab-cdef-0123-456789abcdef', token_configured: true } }))
  await page.route('**/api/notion/dramas/1', (route) =>
    route.fulfill({ json: { drama_id: 1, page_id: 'abc', page_url: 'https://www.notion.so/Signal-abc' } }))
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Notion' })
  await expect(card.getByTestId('notion-token')).toHaveText('Token saved')
  await expectNoHorizontalOverflow(page)
  for (const name of ['Save token', 'Test connection', 'Clear token']) {
    expect(await hitHeight(card.getByRole('button', { name })), name).toBeGreaterThanOrEqual(44)
  }

  await page.goto('/#/drama/1/export')
  const panel = page.getByRole('region', { name: 'Export to Notion' })
  await panel.locator('.section-title', { hasText: 'Export to Notion' }).click()
  await expect(panel.getByRole('button', { name: 'Update Notion page' })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await expectTall(page, '[aria-label="Export to Notion"] .btn-primary')
  await expectTall(page, '[aria-label="Export to Notion"] a.button-link')
})

test('Jellyfin: the settings card fits a phone (labels on one line, full-width fields)', async ({ page }, info) => {
  // Mocked: connector on and set up, so every field and button is shown.
  await page.route('**/api/jellyfin/config', (route) =>
    route.fulfill({ json: { enabled: true, server_url: 'http://192.168.1.20:8096', library_dir: 'D:\\Media\\Dramas', key_configured: true } }))
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const card = page.getByRole('region', { name: 'Jellyfin' })
  await expect(card.getByTestId('jellyfin-key')).toHaveText('Set')
  await expectNoHorizontalOverflow(page)
  await card.screenshot({ path: info.outputPath('jellyfin-phone.png') })
  const labels = await card.locator('.field-label-row label').evaluateAll((els) =>
    els.map((e) => {
      const lh = parseFloat(getComputedStyle(e).lineHeight) || 20
      return { text: e.textContent ?? '', lines: Math.round(window.hitHeight(e) / lh) }
    }),
  )
  expect(labels.length).toBeGreaterThanOrEqual(4)
  for (const { text, lines } of labels) expect(lines, `label "${text}" wraps`).toBeLessThanOrEqual(1)
  for (const input of await card.locator('input[type="url"], input[type="text"], input[type="password"], select').all()) {
    const box = await input.boundingBox()
    expect(box?.width ?? 0, 'field too narrow').toBeGreaterThanOrEqual(250)
  }
  for (const name of ['Save', 'Save key', 'Remove key', 'Test connection', 'Scan library']) {
    expect(await hitHeight(card.getByRole('button', { name, exact: true })), name).toBeGreaterThanOrEqual(44)
  }
})
