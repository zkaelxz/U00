import { expect, type Page } from '@playwright/test'

// Review's lower tools sit in four folds; a tool's own section is only
// reachable once its fold is open. AI review itself is open by default.
const FOLD_OF: Record<string, string> = {
  Structure: 'Restructure lines',
  'Re-split long lines': 'Restructure lines',
  'Merge short lines': 'Restructure lines',
  'Shorten overlong': 'Restructure lines',
  Records: 'Versions and history',
  'Edit tendencies': 'Versions and history',
  'Compare versions': 'Versions and history',
  'Compare transcription': 'Versions and history',
  'Notes export': 'Versions and history',
  'Learn my style': 'Versions and history',
  'Audio tags (SenseVoice)': 'Extras',
  'Burned subtitle preview': 'Extras',
}

/** Opens the fold that holds the named review section (a no-op for other titles). */
export async function openFoldFor(page: Page, title: string) {
  const fold = FOLD_OF[title]
  if (!fold) return
  const details = page
    .locator('details.section')
    .filter({ has: page.locator(':scope > summary .section-title', { hasText: new RegExp(`^${fold}$`) }) })
    .first()
  if ((await details.getAttribute('open')) === null) await details.locator(':scope > summary').click()
  await expect(details).toHaveAttribute('open', '')
}
