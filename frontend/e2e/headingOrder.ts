import { expect, type Page } from '@playwright/test'

/**
 * The heading levels a screen reader hears on the page, in order, and each place where a level is skipped
 * (an h2 followed by an h4). Headings in the nav, in closed folds and in hidden panels are left out, as they
 * are for a screen reader; a visually hidden heading counts. The first heading may be an h1 or an h2.
 */
export async function headingSkips(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const headings = [...document.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6, [role="heading"]')]
      .filter((h) => !h.closest('nav') && h.getClientRects().length > 0)
    const skips: string[] = []
    let previous = 1
    for (const h of headings) {
      const level = Number(h.getAttribute('aria-level') ?? h.tagName.slice(1))
      if (level > previous + 1) skips.push(`h${previous} then h${level} "${(h.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 60)}"`)
      previous = level
    }
    return skips
  })
}

/** Opens every closed fold in the page's main area so the headings inside them count too. */
export async function openAllFolds(page: Page) {
  for (let pass = 0; pass < 3; pass++) {
    const closed = await page.evaluate(() => {
      const folds = [...document.querySelectorAll<HTMLDetailsElement>('main details:not([open])')]
      for (const d of folds) d.open = true
      return folds.length
    })
    if (closed === 0) return
    await page.waitForTimeout(100)
  }
}

/**
 * Checks the heading order on the page as it is, then once more for every tab in the page's main area, with
 * every fold open each time. Tabs that a tab reveals (Glossary inside Translate) are visited too.
 */
export async function expectHeadingOrderOnEveryTab(page: Page) {
  const seen = new Set<string>()
  let where = 'as opened'
  for (;;) {
    await openAllFolds(page)
    expect(await headingSkips(page), `${page.url()} (${where})`).toEqual([])
    // Every Tabs tab has an id (tabButtonId); its text can carry a count badge, so the id names it.
    const tabs = await page.locator('main [role="tablist"] [role="tab"]').evaluateAll((els) =>
      els.filter((e) => e.getClientRects().length > 0).map((e) => e.id))
    const next = tabs.find((id) => !seen.has(id))
    if (next === undefined) return
    seen.add(next)
    const tab = page.locator(`main [role="tab"][id="${next}"]`)
    if (!(await tab.isEnabled())) continue
    await tab.click()
    await expect(tab).toHaveAttribute('aria-selected', 'true')
    where = `tab ${next}`
  }
}
