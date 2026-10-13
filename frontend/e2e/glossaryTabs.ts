import type { Page } from '@playwright/test'
import { expectPickedTab, pickTab } from './tabPicker'

import { showTranslateCard, translatePanel, type TranslateTabName } from './translateTabs'

export type GlossaryTabName = 'Terms' | 'Find terms' | 'Names' | 'Packs' | 'Instructions' | 'Re-translate'

// The Glossary panel's tab row. Every panel stays in the page (hidden), so a spec that
// means to use one switches to it first. A tab with something to look at has a count after its
// name ("Find terms 1 to look at"), so the name is matched at its start.
export async function glossaryTab(page: Page, name: GlossaryTabName) {
  // On the Translate stage the whole panel sits on the stage's Glossary tab.
  await showTranslateCard(page, 'Glossary')
  const region = page.getByRole('region', { name: 'Glossary' })
  const tabName = new RegExp(`^${name}( \\d|$)`)
  await pickTab(region, tabName, 'Glossary')
  await expectPickedTab(region, 'Glossary', tabName)
}

export const GLOSSARY_TAB_NAMES: GlossaryTabName[] = ['Terms', 'Find terms', 'Names', 'Packs', 'Instructions', 'Re-translate']

// Opens every fold of a Translate sub-tab. The Glossary's folds sit in its own tabs and a hidden
// tab's fold can't be clicked, so each Glossary tab is shown in turn.
export async function openAllFolds(page: Page, tab: TranslateTabName) {
  for (const sub of tab === 'glossary' ? GLOSSARY_TAB_NAMES : [null]) {
    if (sub) await glossaryTab(page, sub)
    const folds = translatePanel(page, tab).locator('details.section:visible')
    for (let i = 0; i < (await folds.count()); i++) {
      const fold = folds.nth(i)
      if ((await fold.getAttribute('open')) === null) await fold.locator(':scope > summary').click()
    }
  }
}
