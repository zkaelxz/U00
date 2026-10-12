import { expect, type Page } from '@playwright/test'
import { glossaryTab } from './glossaryTabs'

// Picks the Suggest terms bar's source. The bar starts on the title's own
// default (transcript for audio), so specs that mean the novel choose it. The bar lives on
// the Glossary's Find terms tab.
export async function suggestFrom(page: Page, source: 'Transcript' | 'Novel') {
  await glossaryTab(page, 'Find terms')
  const picker = page.getByRole('combobox', { name: 'Suggest terms from' })
  await expect(picker).toBeVisible()
  await picker.selectOption({ label: source })
}
