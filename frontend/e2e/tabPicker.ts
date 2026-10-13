import { expect, type Locator, type Page } from '@playwright/test'

// On a phone a tab bar with five or more tabs is a picker (components/Tabs.tsx): the same sections, reached by opening
// the picker and choosing an option. Wider screens keep the tab row. These helpers use whichever form is on screen,
// waiting for it first because a page draws its tabs only after its data arrives.
const pickerButton = (scope: Page | Locator, bar?: string | RegExp) =>
  bar ? scope.getByRole('button', { name: bar instanceof RegExp ? bar : new RegExp(`^${bar}: `) }) : scope.locator('.ui-tab-picker-btn').first()

export async function pickTab(scope: Page | Locator, name: string | RegExp, bar?: string | RegExp): Promise<void> {
  const tab = scope.getByRole('tab', { name }).first()
  const picker = pickerButton(scope, bar)
  await expect(tab.or(picker).first()).toBeVisible()
  if (await tab.isVisible()) {
    await tab.click()
    return
  }
  if ((await picker.getAttribute('aria-expanded')) !== 'true') await picker.click()
  await scope.locator('.ui-tab-picker-list').getByRole('option', { name }).click()
}

// The picker button names the open section: "Settings: Translation and keys".
export async function expectPickedTab(scope: Page | Locator, bar: string, name: string | RegExp): Promise<void> {
  const tab = scope.getByRole('tab', { name }).first()
  const picker = pickerButton(scope, bar)
  await expect(tab.or(picker).first()).toBeVisible()
  if (await tab.isVisible()) {
    await expect(tab).toHaveAttribute('aria-selected', 'true')
    return
  }
  const wanted = name instanceof RegExp ? name.source.replace(/^\^/, '') : name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  await expect(picker).toHaveAccessibleName(new RegExp(`^${bar}: ${wanted}`))
}
