import type { Locator } from '@playwright/test'

/** Opens the themed Style picker inside `scope` and taps one option. */
export async function pickStyle(scope: Locator, option: string | RegExp) {
  await scope.getByLabel('Style', { exact: true }).click()
  await scope.page().getByRole('dialog', { name: 'Style' }).getByRole('button', { name: option }).click()
}
