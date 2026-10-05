import type { Locator, Page } from '@playwright/test'

// Every Settings group starts closed on each visit; a spec for a card in one of them opens them all first.
export async function openSettingsGroups(page: Page) {
  // Sharing renders at once; the rest wait for the settings call, and Jobs is the first of them.
  await page.locator('#settings-jobs').waitFor({ state: 'attached', timeout: 4000 }).catch(() => {})
  for (const summary of await page.locator('.settings-fold > details.section > summary').all()) {
    if (!(await summary.evaluate((el) => (el.parentElement as HTMLDetailsElement).open))) {
      await summary.click()
      // Section applies the toggle in React state; a click made before it lands can be undone by the re-render.
      await summary.evaluate((el) => new Promise<void>((done) => {
        const d = el.parentElement as HTMLDetailsElement
        const check = () => (d.open ? done() : requestAnimationFrame(check))
        check()
      }))
    }
  }
  // The opened section can land a field's help icon under the pointer, which opens its tooltip.
  await page.mouse.move(0, 0)
}

// Settings, Admin, Diagnostics and Assistant: a left-rail link from 1024px up, the header cogwheel menu below it.
const gearMenu = (page: Page): Locator => page.getByRole('group', { name: 'Settings and tools pages' })
const railNav = (page: Page): Locator => page.getByRole('navigation', { name: 'Main' })
export const gearLink = (page: Page, name: string): Locator =>
  railNav(page).getByRole('link', { name, exact: true }).or(gearMenu(page).getByRole('link', { name, exact: true }))

export async function openGear(page: Page) {
  const summary = page.locator('summary[aria-label="Settings and tools"]')
  // No cogwheel on a wide screen: the links are always in the rail.
  if ((await summary.count()) === 0) return
  if (!(await summary.evaluate((el) => (el.parentElement as HTMLDetailsElement).open))) await summary.click()
}
