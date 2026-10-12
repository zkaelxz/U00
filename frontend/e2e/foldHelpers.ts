import { expect, type Page } from '@playwright/test'

const openCount = (page: Page) =>
  page.evaluate(() => Object.entries(window.localStorage).filter(([k, v]) => k.startsWith('baihe.section.') && v === '1').length)

/**
 * Opens the top-level card with this title on screen if it is closed (a tab starts with only its first card open).
 * A card's fold is saved from the details 'toggle' event, after the click returns; waiting for the saved key keeps a
 * card that remounts right afterwards (its list reloads) from coming back closed.
 */
export async function openFold(page: Page, title: string | RegExp) {
  const name = typeof title === 'string' ? new RegExp(`^${title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`) : title
  const card = page.locator('details.section', { has: page.locator('> summary > .section-title', { hasText: name }) }).first()
  await expect(card).toBeVisible()
  if ((await card.getAttribute('open')) !== null) return
  const before = await openCount(page)
  await card.locator(':scope > summary').click()
  await expect(card).toHaveAttribute('open', '')
  await page.waitForFunction((n) =>
    Object.entries(window.localStorage).filter(([k, v]) => k.startsWith('baihe.section.') && v === '1').length > n, before)
}
