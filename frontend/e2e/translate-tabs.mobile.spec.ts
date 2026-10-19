import { expect, test } from '@playwright/test'

import { expectNoSidewaysScroll } from './mediaTabs'
import { openAllFolds } from './glossaryTabs'
import { gotoTranslate, translatePanel, translateTab, type TranslateTabName } from './translateTabs'

// Phone (390x844): the tab row and every sub-tab stay inside the screen.

test('the sub-tab row fits the screen with every tab reachable', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  const row = page.getByRole('tablist', { name: 'Translate sections' })
  await expect(row).toBeVisible()
  await expectNoSidewaysScroll(page, 'Translate tab row')
  // The row scrolls inside itself, so the last tab is reached by tapping it into view.
  for (const name of ['Translate', 'Glossary', 'Characters', 'Original novel']) {
    await row.getByRole('tab', { name, exact: true }).scrollIntoViewIfNeeded()
    await expect(row.getByRole('tab', { name, exact: true })).toBeVisible()
  }
})

for (const scheme of ['light', 'dark'] as const) {
  for (const tab of ['translate', 'glossary', 'characters', 'novel'] as const satisfies TranslateTabName[]) {
    test(`${tab}: no sideways scroll, even with every fold open (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme })
      await gotoTranslate(page, tab)
      await openAllFolds(page, tab)
      await expectNoSidewaysScroll(page, `${tab} ${scheme}`)
    })
  }
}

test('tapping a tab switches the panel', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  await translateTab(page, 'characters').tap()
  await expect(translateTab(page, 'characters')).toHaveAttribute('aria-selected', 'true')
  await expect(translatePanel(page, 'characters').getByRole('region', { name: 'Characters' })).toBeVisible()
})
