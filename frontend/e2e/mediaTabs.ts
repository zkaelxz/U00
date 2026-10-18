import { expect, type Locator, type Page } from '@playwright/test'

import { expectPickedTab, pickTab } from './tabPicker'

// Tools shares the Transcribe tab and Credits the Details tab; each keeps its own panel, so a spec can still say which.
export type MediaTabName = 'add' | 'transcribe' | 'tools' | 'details' | 'credits'
export const TAB_LABEL: Record<MediaTabName, string> = { add: 'Add media', transcribe: 'Transcribe', tools: 'Transcribe', details: 'Details', credits: 'Details' }

/** Opens a title's Media stage on one sub-tab through the deep link. */
export async function gotoMedia(page: Page, tab: MediaTabName, id = 1) {
  await page.goto(`/#/drama/${id}/source?tab=${tab}`)
  // A phone shows the sections as a picker, not a tab row.
  await expectPickedTab(page, 'Media sections', new RegExp(`^${TAB_LABEL[tab]}( \\(unsaved changes\\))?$`))
}

// A tab with unsaved edits carries "(unsaved changes)" in its name.
export const mediaTab = (page: Page, tab: MediaTabName) =>
  page.getByRole('tablist', { name: 'Media sections' }).getByRole('tab', { name: new RegExp(`^${TAB_LABEL[tab]}( \\(unsaved changes\\))?$`) })

/** Opens one sub-tab: a click on the tab row, or the picker a phone shows instead. */
export const selectMediaTab = (page: Page, tab: MediaTabName) =>
  pickTab(page, new RegExp(`^${TAB_LABEL[tab]}( \\(unsaved changes\\))?$`), 'Media sections')

/** The visible body of one sub-tab. */
export const mediaPanel = (page: Page, tab: MediaTabName) => page.locator(`#media-panel-${tab}`)

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

/**
 * Holds a sub-tab to about one desktop screen (1.25 x the 800px viewport). Transcribe and Details each hold two
 * sections (with Tools and Credits under them), so the limit is applied per section: the page chrome plus that
 * section's own height must stay within it, and the whole page within one limit per section. One section growing
 * past the old limit still fails.
 */
export async function expectFitsScreens(page: Page, tab: MediaTabName, sections: MediaTabName[] = [tab], viewportHeight = 800) {
  const limit = viewportHeight * 1.25
  const { page: total, heights } = await page.evaluate((ids) => ({
    page: document.documentElement.scrollHeight,
    heights: ids.map((id) => document.getElementById(`media-panel-${id}`)?.getBoundingClientRect().height ?? 0),
  }), sections)
  const chrome = total - heights.reduce((a, b) => a + b, 0)
  heights.forEach((h, i) => expect(chrome + h, `${sections[i]}: page chrome plus this section`).toBeLessThanOrEqual(limit))
  expect(total, `${tab}: page height`).toBeLessThanOrEqual(limit * sections.length)
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
