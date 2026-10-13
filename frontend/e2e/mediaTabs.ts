import { expect, type Locator, type Page } from '@playwright/test'

import { expectPickedTab, pickTab } from './tabPicker'

export type MediaTabName = 'add' | 'transcribe' | 'tools' | 'details' | 'credits'
export const TAB_LABEL: Record<MediaTabName, string> = { add: 'Add media', transcribe: 'Transcribe', tools: 'Tools', details: 'Details', credits: 'Credits' }

/** Opens a title's Media stage on one sub-tab through the deep link. */
export async function gotoMedia(page: Page, tab: MediaTabName, id = 1) {
  await page.goto(`/#/drama/${id}/source?tab=${tab}`)
  // A phone shows the five sections as a picker, not a tab row.
  await expectPickedTab(page, 'Media sections', new RegExp(`^${TAB_LABEL[tab]}( \\(unsaved changes\\))?$`))
}

// A tab with unsaved edits carries "(unsaved changes)" in its name.
export const mediaTab = (page: Page, tab: MediaTabName) =>
  page.getByRole('tablist', { name: 'Media sections' }).getByRole('tab', { name: new RegExp(`^${TAB_LABEL[tab]}( \\(unsaved changes\\))?$`) })

/** Opens one sub-tab: a click on the tab row, or the picker a phone shows instead. */
export const selectMediaTab = (page: Page, tab: MediaTabName) =>
  pickTab(page, new RegExp(`^${TAB_LABEL[tab]}( \\(unsaved changes\\))?$`), 'Media sections')

/** The visible body of one sub-tab. */
export const mediaPanel = (page: Page, tab: MediaTabName) =>
  page.getByRole('tabpanel', { name: new RegExp(`^${TAB_LABEL[tab]}( \\(unsaved changes\\))?$`) })

/** Opens every fold in a sub-tab: their state is remembered, so a plain click could as well close one. */
export async function openAllFolds(scope: Locator) {
  const folds = scope.locator('details.section')
  // The Advanced options folds appear once the saved options load, ahead of the folds already there.
  if ((await scope.getAttribute('id')) === 'media-panel-tools') await scope.getByRole('region', { name: 'Advanced options' }).waitFor()
  // A tab may have no folds at all (Transcribe).
  await folds.first().waitFor({ state: 'visible', timeout: 3000 }).catch(() => undefined)
  for (let i = 0; i < (await folds.count()); i++) {
    const fold = folds.nth(i)
    if ((await fold.getAttribute('open')) !== null) continue
    await fold.locator(':scope > summary').click()
    await expect(fold).toHaveAttribute('open', '')
  }
}

export async function expectNoSidewaysScroll(page: Page, what: string) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, `${what}: page scrolls sideways`).toBeLessThanOrEqual(client)
}

/** The titles of a sub-tab's top-level cards (folds not inside another fold) and whether each is open. */
export async function topCards(panel: Locator): Promise<{ title: string; open: boolean }[]> {
  return panel.locator('details.section').evaluateAll((all) =>
    all
      .filter((d) => !d.parentElement?.closest('details.section'))
      .map((d) => ({ title: d.querySelector(':scope > summary > .section-title')?.textContent ?? '', open: (d as HTMLDetailsElement).open })))
}
