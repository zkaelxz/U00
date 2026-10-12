import { expect, type Page } from '@playwright/test'

export type TranslateTabName = 'translate' | 'glossary' | 'characters' | 'novel'
export const TRANSLATE_TAB_LABEL: Record<TranslateTabName, string> = {
  translate: 'Translate',
  glossary: 'Glossary',
  characters: 'Characters',
  novel: 'Reference novel',
}

/** Opens a title's Translate stage on one sub-tab through the deep link. */
export async function gotoTranslate(page: Page, tab: TranslateTabName = 'translate', id = 1) {
  await page.goto(`/#/drama/${id}/translate?tab=${tab}`)
  await expect(translateTab(page, tab)).toHaveAttribute('aria-selected', 'true')
}

export const translateTab = (page: Page, tab: TranslateTabName) =>
  page.getByRole('tablist', { name: 'Translate sections' }).getByRole('tab', { name: TRANSLATE_TAB_LABEL[tab], exact: true })

/** The visible body of one sub-tab. */
export const translatePanel = (page: Page, tab: TranslateTabName) =>
  page.getByRole('tabpanel', { name: TRANSLATE_TAB_LABEL[tab], exact: true })

// The tab each Translate card sits on, so a spec that means to use a card shows its tab first.
const CARD_TAB: Record<string, TranslateTabName> = {
  'Translate run': 'translate',
  'More options': 'translate',
  'Bulk batches': 'translate',
  Glossary: 'glossary',
  Characters: 'characters',
  'Novel reference (English translation)': 'novel',
}

/** Switches to the tab that holds the named Translate card; does nothing for a card elsewhere or off the stage. */
export async function showTranslateCard(page: Page, title: string) {
  const tab = CARD_TAB[title]
  if (!tab || !page.url().includes('/translate')) return
  const button = translateTab(page, tab)
  if ((await button.getAttribute('aria-selected')) !== 'true') await button.click()
}
