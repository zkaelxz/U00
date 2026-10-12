import type { Page } from '@playwright/test'

import { mediaTab } from './mediaTabs'

// Opens a Source stage group Section if it is closed (its state is remembered,
// so a plain click could just as well collapse it).
export async function openGroup(page: Page, title: string) {
  const group = page.locator('details.section', { has: page.locator('summary > .section-title', { hasText: new RegExp(`^${title}$`) }) }).first()
  if ((await group.getAttribute('open')) === null) await group.locator('summary').first().click()
}

// Details live on their own Media sub-tab: edit details, credits, cover and Fill in details.
export async function openDetailsTab(page: Page) {
  await mediaTab(page, 'details').click()
}

// The Credits tab (author, studio, cover, Fill in details), with the cover and romanized credits card opened on request.
export async function openCreditsTab(page: Page, coverCard = false) {
  await mediaTab(page, 'credits').click()
  if (!coverCard) return
  const card = page.locator('details.section', { has: page.locator('summary > .section-title', { hasText: /^Cover and romanized credits$/ }) })
  if ((await card.getAttribute('open')) === null) await card.locator(':scope > summary').click()
}

// Opens the Credits tab and "Fill in details", then picks how to fill in.
export async function openFillIn(page: Page, mode: 'From a page or text' | 'Research online' | 'From the audio') {
  await openCreditsTab(page)
  // Not openGroup: its details.section filter would match the outer group that contains this fold.
  const fold = page.locator('summary', { has: page.locator(':scope > .section-title', { hasText: /^Fill in details$/ }) })
  if ((await fold.locator('xpath=..').getAttribute('open')) === null) await fold.click()
  await page.locator('.segmented label', { hasText: new RegExp(`^${mode}$`) }).click()
}
