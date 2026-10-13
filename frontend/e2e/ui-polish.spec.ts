import { expect, test, type Locator, type Page } from '@playwright/test'

import { pickTab } from './tabPicker'

// Shared-CSS regressions the owner circled: segmented pills, tab bars, flush-left card bodies, selects stretched
// or overlapping in a table, and hover/dock layout shifts. Runs against the seeded API, at a PC and a phone width.

const WIDTHS = [1440, 390] as const

async function noPageScroll(page: Page) {
  const [scroll, client] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth])
  expect(scroll).toBeLessThanOrEqual(client)
}

async function expectInside(inner: Locator, outer: Locator, slack = 1) {
  const a = await inner.boundingBox()
  const b = await outer.boundingBox()
  expect(a && b).toBeTruthy()
  expect(a!.x).toBeGreaterThanOrEqual(b!.x - slack)
  expect(a!.y).toBeGreaterThanOrEqual(b!.y - slack)
  expect(a!.x + a!.width).toBeLessThanOrEqual(b!.x + b!.width + slack)
  expect(a!.y + a!.height).toBeLessThanOrEqual(b!.y + b!.height + slack)
}

// Text is whole when no label clips its own content.
async function expectNoClippedLabels(group: Locator) {
  const clipped = await group.evaluate((el) =>
    [...el.querySelectorAll('label, a, button, [role=tab]')].filter((n) => n.scrollWidth > n.clientWidth + 1).map((n) => n.textContent),
  )
  expect(clipped).toEqual([])
}

for (const width of WIDTHS) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height: 900 } })

    test('Library: segmented chips sit inside their own labels and selects keep a natural width', async ({ page }) => {
      await page.goto('/#/library')
      const toolbar = page.getByRole('search')
      await expect(toolbar.getByRole('group', { name: 'Search in' })).toBeVisible()
      for (const group of await toolbar.locator('.segmented').all()) {
        await expectNoClippedLabels(group)
        const checked = group.locator('label:has(input:checked)')
        await expectInside(checked, group)
        // Equal padding: the chip's text is centred in its label.
        const box = await checked.boundingBox()
        const text = await checked.evaluate((l) => {
          // The text node only: the label's radio input is absolutely positioned over the whole label.
          const node = document.createTreeWalker(l, NodeFilter.SHOW_TEXT).nextNode()!
          const r = document.createRange()
          r.selectNodeContents(node)
          const t = r.getBoundingClientRect()
          const b = l.getBoundingClientRect()
          return { left: t.left - b.left, right: b.right - t.right }
        })
        expect(box).toBeTruthy()
        expect(Math.abs(text.left - text.right)).toBeLessThanOrEqual(2)
        // A one-word label never breaks inside the word.
        for (const label of await group.locator('label').all()) {
          const lineHeight = await label.evaluate((l) => parseFloat(getComputedStyle(l).lineHeight))
          const textHeight = await label.evaluate((l) => {
            const r = document.createRange()
            r.selectNodeContents(document.createTreeWalker(l, NodeFilter.SHOW_TEXT).nextNode()!)
            return r.getBoundingClientRect().height
          })
          expect(textHeight).toBeLessThanOrEqual(lineHeight * 1.5)
        }
        expect(text.left).toBeGreaterThanOrEqual(8)
      }
      for (const select of await toolbar.locator('.toolbar-row select').all()) {
        const box = await select.boundingBox()
        expect(box!.width).toBeLessThanOrEqual(width >= 1000 ? 320 : width)
      }
      await noPageScroll(page)
    })

    test('Library tools: tab bar and card bodies are not clipped or flush left', async ({ page }) => {
      await page.goto('/#/library-tools')
      // A phone shows five or more tabs as a picker; wider screens keep the tab row.
      const tabs = page.getByRole('tablist')
      const picker = page.locator('.ui-tab-picker-btn').first()
      await expect(tabs.or(picker).first()).toBeVisible()
      const asRow = await tabs.isVisible()
      if (asRow) await expectNoClippedLabels(tabs)
      else await expectInside(picker, page.locator('body'))
      for (const name of ['Organize', 'Storage', 'Activity']) {
        await pickTab(page, name)
        if (asRow) {
          await expectInside(page.getByRole('tab', { name }), tabs)
          const bottom = await page.getByRole('tab', { name }).evaluate((t) => getComputedStyle(t).borderBottomWidth)
          expect(bottom).toBe('0px')
        }
        if (name === 'Organize') continue
        // First visible text of the panel starts well inside the card edge.
        const panel = page.getByRole('tabpanel').filter({ visible: true })
        const card = panel.locator('xpath=ancestor-or-self::*[contains(@class,"tools-stack") or contains(@class,"card") or contains(@class,"panel")][1]')
        const gap = await panel.evaluate((p) => {
          const holder = p.closest('.tools-stack, .card, .panel') ?? p
          const walker = document.createTreeWalker(p, NodeFilter.SHOW_TEXT)
          let min = Infinity
          for (let n = walker.nextNode(); n; n = walker.nextNode()) {
            if (!n.textContent?.trim()) continue
            const r = document.createRange()
            r.selectNodeContents(n)
            const box = r.getBoundingClientRect()
            if (box.width) min = Math.min(min, box.left - holder.getBoundingClientRect().left)
          }
          return min
        })
        expect(gap, `${name} text offset from card edge (${await card.count()})`).toBeGreaterThanOrEqual(12)
      }
      await noPageScroll(page)
    })

    test('Sources All sites: the Rules select stays in its cell, clear of Details', async ({ page }) => {
      await page.goto('/#/sources')
      await page.getByRole('tab', { name: 'Manage sources' }).click()
      const rows = page.locator('.sources-table tbody tr')
      if (width < 641) {
        // Phones list the sites as cards, not a table.
        await expect(page.locator('ul.source-cards')).toBeVisible()
        await noPageScroll(page)
        return
      }
      await expect(rows.first()).toBeVisible()
      for (const row of (await rows.all()).slice(0, 6)) {
        const select = row.locator('select')
        const details = row.getByRole('button', { name: /Details/ })
        if (!(await select.count())) continue
        await expectInside(select, select.locator('xpath=ancestor::td'))
        const a = (await select.boundingBox())!
        const b = (await details.boundingBox())!
        expect(a.x + a.width).toBeLessThanOrEqual(b.x)
      }
      await noPageScroll(page)
    })
  })
}

test.describe('hover never repaints or reflows', () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test('primary buttons are a flat fill that does not repaint or animate background on hover', async ({ page }) => {
    await page.goto('/#/jobs')
    const start = page.getByRole('button', { name: 'Start queue now' })
    await expect(start).toBeVisible()
    const rest = await start.evaluate((b) => ({ image: getComputedStyle(b).backgroundImage, colour: getComputedStyle(b).backgroundColor }))
    await start.hover()
    const hover = await start.evaluate((b) => ({ image: getComputedStyle(b).backgroundImage, colour: getComputedStyle(b).backgroundColor, transition: getComputedStyle(b).transitionProperty }))
    expect(rest.image).toBe('none')
    expect(hover.image).toBe('none')
    expect(hover.colour).toBe(rest.colour)
    expect(hover.transition).not.toMatch(/background|all/)
  })

  test('the side dock keeps its box and its icons in place on hover and focus', async ({ page }) => {
    await page.goto('/#/library')
    const dock = page.locator('.app-rail')
    await expect(dock).toBeVisible()
    const icons = dock.locator('nav a svg')
    const snapshot = async () => ({
      top: (await dock.boundingBox())!.y,
      height: (await dock.boundingBox())!.height,
      icons: await icons.evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().y))),
    })
    await page.mouse.move(700, 450)
    const before = await snapshot()
    await icons.nth(2).hover()
    await page.waitForTimeout(400)
    const during = await snapshot()
    expect(during.top).toBe(before.top)
    expect(during.height).toBe(before.height)
    expect(during.icons).toEqual(before.icons)
    await page.mouse.move(700, 450)
    await dock.locator('nav a').first().focus()
    await page.waitForTimeout(400)
    const focused = await snapshot()
    expect(focused.top).toBe(before.top)
    expect(focused.icons).toEqual(before.icons)
  })
})

test('Live Advanced: Fast captions keeps its natural width with one short line', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/live')
  await page.getByText('Advanced').click()
  const button = page.getByTestId('live-fast-captions')
  const box = await button.boundingBox()
  expect(box!.width).toBeLessThan(220)
  await expect(page.getByText('Replaces Whisper model, Chunk, Overlap and Reply without thinking.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Help: Fast captions' })).toBeVisible()
})
