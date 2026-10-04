import { expect, type Page } from '@playwright/test'

// The model, speaker and advanced settings sit under "More options", folded by
// default; open it the way a user would (click only when closed).
export async function openTranscribeOptions(page: Page) {
  // The settings summary shows once the options have loaded; a click before
  // that can be undone by the first render.
  await expect(page.getByTestId('settings-summary')).toBeVisible()
  const summary = page
    .locator('summary')
    .filter({ has: page.locator('.section-title', { hasText: /^More options$/ }) })
    .first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
}
