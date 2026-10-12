import { expect, type Page } from '@playwright/test'

export type GlossaryTabName = 'Terms' | 'Find terms' | 'Names' | 'Instructions' | 'Re-translate'

// The Glossary panel's tab row. Every panel stays in the page (hidden), so a spec that
// means to use one switches to it first. A tab with something to look at has a count after its
// name ("Find terms 1 to look at"), so the name is matched at its start.
export async function glossaryTab(page: Page, name: GlossaryTabName) {
  const tab = page.getByRole('region', { name: 'Glossary' }).getByRole('tab', { name: new RegExp(`^${name}( \\d|$)`) })
  await tab.click()
  await expect(tab).toHaveAttribute('aria-selected', 'true')
}
