import { expect, test, type Page } from '@playwright/test'

import { openMenu } from './settingsNav'
import { mockAccess } from './sourcesAccessMocks'
import { NOVEL_PREVIEW, mockImports } from './sourcesImportMocks'
import { SERIES_LINKS, mockSources, searchResult } from './sourcesMocks'
import { hitHeight, installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the Sources page. Every Sources job and
// write is mocked (sourcesMocks.ts).

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

// Visible buttons, selects, text inputs and checkbox labels on the page are at least 44 px tall.
async function tallTargets(page: Page) {
  const small = await page.locator('.sources-page').evaluate((root) => {
    const sel = 'button:not(.link):not(.field-help-btn):not(.toggle), select, input[type="search"], input[type="number"], .segmented label, .source-on, .setting-list > .field-item, .source-adult .field-item, .sources-back'
    return [...root.querySelectorAll<HTMLElement>(sel)]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30) }))
      .filter((x) => x.h < 44)
  })
  expect(small).toEqual([])
}

// Many rows: no ancestor of the chapter list (or the list itself) is a scroll area with hidden rows.
async function noInnerScroll(page: Page) {
  const bad = await page.locator('.sources-chapters').evaluate((list) => {
    const out: string[] = []
    for (let e: HTMLElement | null = list; e && e !== document.documentElement; e = e.parentElement) {
      const oy = getComputedStyle(e).overflowY
      if ((oy === 'auto' || oy === 'scroll') && e.scrollHeight > e.clientHeight) out.push(e.className || e.tagName)
    }
    return out
  })
  expect(bad, 'inner vertical scroll area').toEqual([])
}

test('phone: series replaces results, ‹ Results restores them, no sideways scroll', async ({ page }) => {
  const s = await mockSources(page, { searchBody: { ...searchResult(12), errors: {} } })
  await page.goto('/#/sources')
  await page.getByRole('searchbox', { name: 'Title' }).fill('Heaven')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page.getByText(/Searching…/)).toBeVisible()
  s.search = 'done'
  await expect(page.getByText('12 results', { exact: true })).toBeVisible()
  await noSideways(page)
  await tallTargets(page)

  const opener = page.getByRole('button', { name: 'Open on Alpha Comics' }).nth(5)
  await opener.scrollIntoViewIfNeeded()
  await opener.click()
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(panel.getByText('Alpha Comics · 124 chapters · Ongoing · Chinese')).toBeVisible()
  await expect(page.getByTestId('search-results')).toBeHidden()
  await expect(panel.getByRole('heading', { level: 3 })).toBeFocused()
  await noSideways(page)
  await tallTargets(page)
  const more = panel.getByRole('button', { name: 'More' })
  expect((await hitHeight(more))).toBeGreaterThanOrEqual(44)
  await more.click()
  await expect(panel.getByRole('button', { name: 'Less' })).toBeVisible()

  // "‹ Results" at the top and again after the chapter list; use the bottom one.
  await expect(panel.getByRole('button', { name: '‹ Results' })).toHaveCount(2)
  await panel.getByRole('button', { name: '‹ Results' }).last().click()
  await expect(page.getByTestId('search-results')).toBeVisible()
  await expect(opener).toBeFocused()
  await expect(page.locator('img')).toHaveCount(0)
  expect(s.unmocked).toEqual([])
})

test('phone: download links are 44 px tall and wrap without sideways scroll', async ({ page }) => {
  const s = await mockSources(page, { searchBody: { ...searchResult(3), errors: {} }, seriesLinks: SERIES_LINKS })
  await page.goto('/#/sources')
  await page.getByRole('searchbox', { name: 'Title' }).fill('Heaven')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  s.search = 'done'
  await page.getByRole('button', { name: 'Open on Alpha Comics' }).first().click()
  const links = page.getByRole('region', { name: 'Series' }).getByRole('group', { name: 'Download links' })
  await links.scrollIntoViewIfNeeded()
  for (const name of ['百度网盘 (Baidu Pan) ↗', '蓝奏云 (Lanzou) ↗']) {
    expect((await hitHeight(links.getByRole('link', { name })))).toBeGreaterThanOrEqual(44)
  }
  await expect(links.getByText('roh1')).toBeVisible()
  await noSideways(page)
  expect(s.unmocked).toEqual([])
})

test('phone: source settings render as cards with 44 px toggles', async ({ page }) => {
  const s = await mockSources(page)
  await page.goto('/#/sources')
  const settings = page.getByRole('region', { name: 'Source settings' })
  await settings.getByText('Source settings').click()
  await expect(settings.locator('table')).toHaveCount(0)
  const cards = settings.locator('ul.source-cards > li')
  await expect(cards).toHaveCount(3)
  await expect(cards.first()).toContainText('OK')
  await expect(cards.nth(1)).toContainText('Sign-in saved')
  await expect(settings.getByRole('switch', { name: 'On: Alpha Comics' })).toBeChecked()
  await expect(settings.getByRole('spinbutton', { name: 'Cache limit (MB)' })).toBeVisible()
  await noSideways(page)
  await tallTargets(page)
  expect(s.unmocked).toEqual([])
})

test('phone: a failing source shows its plain-language reason, raw type in the tooltip', async ({ page }) => {
  const s = await mockSources(page)
  await mockAccess(page, s)
  await page.goto('/#/sources')
  const settings = page.getByRole('region', { name: 'Source settings' })
  await settings.getByText('Source settings').click()
  const card = settings.locator('ul.source-cards > li').nth(1)
  await card.getByRole('button', { name: 'Details' }).click()
  const line = card.getByText(/Last failure .* \(site down\)/)
  await expect(line).toBeVisible()
  await expect(line).toHaveAttribute('title', 'Error type: SERVER_ERROR')
  await expect(card).not.toContainText('SERVER_ERROR')
  await noSideways(page)
  expect(s.unmocked).toEqual([])
})

test('phone: every drawer link is inside the viewport at 360 and 390 px', async ({ page }) => {
  const s = await mockSources(page)
  for (const width of [360, 390]) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/#/sources')
    await openMenu(page)
    const links = page.getByRole('navigation', { name: 'Main' }).getByRole('link')
    await expect(links.first()).toBeVisible()
    for (const link of await links.all()) {
      const box = (await link.boundingBox())!
      expect(box.x, `${await link.textContent()} at ${width}`).toBeGreaterThanOrEqual(0)
      expect(box.x + box.width, `${await link.textContent()} at ${width}`).toBeLessThanOrEqual(width)
      expect(box.height).toBeGreaterThanOrEqual(44)
    }
    await noSideways(page)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog', { name: 'Main menu' })).toBeHidden()
  }
  expect(s.unmocked).toEqual([])
})

// Import (S-4/S-5) on a phone: the link box, the preview card, the chapter
// checkboxes and the sticky Import bar all fit and have 44 px targets.
async function tallImportTargets(page: Page) {
  const small = await page.locator('.sources-page').evaluate((root) => {
    const sel = 'button:not(.link):not(.field-help-btn):not(.toggle), select, input[type="url"], input[type="text"], .sources-pick label, .sources-select-all, .sources-preview a, .sources-outcomes a'
    return [...root.querySelectorAll<HTMLElement>(sel)]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30) }))
      .filter((x) => x.h < 44)
  })
  expect(small).toEqual([])
}

test('phone: paste a chapter link, open the series, import from the sticky bar', async ({ page }) => {
  const s = await mockSources(page)
  await mockImports(page, s)
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://alpha.example/a/c2')
  await page.getByRole('button', { name: 'Preview' }).click()
  const card = page.getByRole('article', { name: 'Link preview' })
  await expect(card.getByRole('heading', { name: 'Heaven Book 1' })).toBeVisible({ timeout: 15_000 })
  await noSideways(page)
  await tallImportTargets(page)

  await card.getByRole('button', { name: 'Open series' }).click()
  const panel = page.getByRole('region', { name: 'Series' })
  await expect(panel.getByRole('checkbox', { name: 'Chapter 2', exact: true })).toBeChecked({ timeout: 15_000 })
  await panel.getByRole('combobox', { name: 'Import into' }).selectOption({ label: 'Alpha Comic' })
  await panel.getByRole('checkbox', { name: 'Chapter 5', exact: true }).check()

  // The Import bar stays at the bottom of the screen while scrolling the list.
  const bar = panel.getByTestId('import-bar')
  await panel.getByRole('checkbox', { name: 'Chapter 40', exact: true }).scrollIntoViewIfNeeded()
  const box = (await bar.boundingBox())!
  expect(box.y + box.height).toBeLessThanOrEqual(844 + 1)
  expect(box.y + box.height).toBeGreaterThan(844 - 120)
  await noSideways(page)
  await tallImportTargets(page)

  await bar.getByRole('button', { name: 'Import 2 chapters' }).click()
  await expect(panel.getByTestId('import-outcomes').getByText('1 imported · 1 already there · 1 failed')).toBeVisible({ timeout: 15_000 })
  await noSideways(page)
  await tallImportTargets(page)
  expect(s.unmocked).toEqual([])
})

test('phone: novel link preview and import, one column', async ({ page }) => {
  const s = await mockSources(page)
  await mockImports(page, s, { previewBody: NOVEL_PREVIEW })
  await page.goto('/#/sources')
  await page.getByRole('radio', { name: 'Paste a link' }).check()
  await page.getByRole('textbox', { name: 'Paste a link' }).fill('https://novels.example/book/5')
  await page.getByRole('button', { name: 'Preview' }).click()
  const card = page.getByRole('article', { name: 'Link preview' })
  await card.getByRole('combobox', { name: 'Import into' }).selectOption({ label: 'New title…' }, { timeout: 15_000 })
  await expect(card.getByRole('group', { name: 'New title' })).toBeVisible()
  await noSideways(page)
  await tallImportTargets(page)
  await card.getByRole('button', { name: 'Cancel' }).click()
  await card.getByRole('combobox', { name: 'Import into' }).selectOption({ label: 'Heaven Novel' })
  await card.getByRole('button', { name: 'Import text' }).click()
  await expect(card.getByTestId('url-import-result')).toBeVisible({ timeout: 15_000 })
  await noSideways(page)
  expect(s.unmocked).toEqual([])
})

test('open series with many rows scrolls with the page, no inner scrollbar', async ({ page }) => {
  const s = await mockSources(page, { searchBody: { ...searchResult(12), errors: {} }, series: 'done' })
  await page.goto('/#/sources')
  await page.getByRole('searchbox', { name: 'Title' }).fill('Heaven')
  await page.getByRole('searchbox', { name: 'Title' }).press('Enter')
  await expect(page.getByText(/Searching…/)).toBeVisible()
  s.search = 'done'
  await page.getByRole('button', { name: 'Open on Alpha Comics' }).first().click()
  const panel = page.getByRole('region', { name: 'Series' })
  await panel.getByRole('button', { name: 'Show all 124' }).click()
  await expect(panel.getByRole('heading', { level: 4, name: 'Extras' })).toBeVisible()
  await noInnerScroll(page)
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeGreaterThan(await page.evaluate(() => innerHeight))
})
