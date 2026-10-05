import { expect, type Page } from '@playwright/test'

// Picks the Suggest terms bar's source. The bar starts on the title's own
// default (transcript for audio), so specs that mean the novel choose it.
export async function suggestFrom(page: Page, source: 'Transcript' | 'Novel') {
  const picker = page.getByRole('combobox', { name: 'Suggest terms from' })
  await expect(picker).toBeVisible()
  await picker.selectOption({ label: source })
}
