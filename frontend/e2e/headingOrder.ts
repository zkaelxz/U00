import { expect, type Page } from '@playwright/test'

/**
 * The places where the page's headings skip a level (an h2 followed by an h4), in the order a screen reader
 * reads them. Headings in the nav, in closed folds and in hidden panels are left out, as they are for a screen
 * reader; a visually hidden heading counts. The first heading may be an h1 or an h2.
 */
export async function headingSkips(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const headings = [...document.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6, [role="heading"]')]
      .filter((h) => !h.closest('nav') && h.getClientRects().length > 0)
    const skips: string[] = []
    let previous = 1
    for (const h of headings) {
      // A role="heading" without aria-level is a level 2 heading.
      const level = /^H[1-6]$/.test(h.tagName) && !h.hasAttribute('aria-level')
        ? Number(h.tagName.slice(1))
        : Number(h.getAttribute('aria-level') ?? 2)
      if (level > previous + 1) skips.push(`h${previous} then h${level} "${(h.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 60)}"`)
      previous = level
    }
    return skips
  })
}

/** Lets what a fold or tab loads on opening arrive before the headings are read. */
async function settle(page: Page) {
  await page.waitForLoadState('networkidle').catch(() => {})
}

/** Opens every closed fold in the page's main area so the headings inside them count too. */
export async function openAllFolds(page: Page) {
  for (let pass = 0; pass < 3; pass++) {
    const closed = await page.evaluate(() => {
      const folds = [...document.querySelectorAll<HTMLDetailsElement>('main details:not([open])')]
      for (const d of folds) d.open = true
      return folds.length
    })
    await settle(page)
    if (closed === 0) return
  }
}

async function expectNoSkips(page: Page, where: string) {
  expect(await headingSkips(page), `${page.url()} (${where})`).toEqual([])
}

/**
 * Checks the heading order with every fold open, then once more with each fold that an accordion kept closed
 * opened on its own (opening one fold of a group closes the others).
 */
async function expectHeadingOrderWithFoldsOpen(page: Page, where: string) {
  await openAllFolds(page)
  await expectNoSkips(page, where)
  // Opening one fold of a group closes others, so the folds still closed now are marked first and then opened one by one.
  const closed = await page.evaluate(() => {
    document.querySelectorAll('[data-heading-check]').forEach((d) => d.removeAttribute('data-heading-check'))
    const folds = [...document.querySelectorAll<HTMLDetailsElement>('main details:not([open])')]
    folds.forEach((d, i) => d.setAttribute('data-heading-check', String(i)))
    return folds.length
  })
  for (let i = 0; i < closed; i++) {
    const fold = page.locator(`main details[data-heading-check="${i}"]`)
    if (!(await fold.isVisible())) continue
    await fold.evaluate((d: HTMLDetailsElement) => { d.open = true })
    await settle(page)
    await expectNoSkips(page, `${where}, accordion fold ${i + 1}`)
  }
}

/**
 * Checks the heading order on the page as it is, then for every tab in the page's main area. The last tab not
 * yet visited goes next, so tabs that a tab reveals (Glossary's, inside Translate) are visited while their
 * parent tab is still open.
 */
export async function expectHeadingOrderOnEveryTab(page: Page) {
  const seen = new Set<string>()
  let where = 'as opened'
  for (;;) {
    await expectHeadingOrderWithFoldsOpen(page, where)
    // Every Tabs tab has an id (tabButtonId); its text can carry a count badge, so the id names it.
    const tabs = await page.locator('main [role="tablist"] [role="tab"]').evaluateAll((els) =>
      els.filter((e) => e.getClientRects().length > 0).map((e) => e.id))
    const next = tabs.filter((id) => !seen.has(id)).at(-1)
    if (next === undefined) return
    seen.add(next)
    const tab = page.locator(`main [role="tab"][id="${next}"]`)
    if (!(await tab.isEnabled())) continue
    await tab.click()
    await expect(tab).toHaveAttribute('aria-selected', 'true')
    await settle(page)
    where = `tab ${next}`
  }
}
