import { expect, type Locator, type Page } from '@playwright/test'

// Review's tools sit in six tabs under the line list; a tool's own section is
// only reachable once its tab is open. The first card of each tab opens by
// itself the first time, the others stay folded.
const TAB_OF: Record<string, string> = {
  'AI review': 'Check',
  'Check options': 'Check',
  Consistency: 'Check',
  Emotion: 'Check',
  Options: 'Fix',
  'Flag lines for review': 'Fix',
  'Source changed': 'Coverage',
  'Coverage and pacing': 'Coverage',
  Structure: 'Restructure',
  'Re-split long lines': 'Restructure',
  'Merge short lines': 'Restructure',
  'Fix common errors': 'Restructure',
  'Shorten overlong': 'Restructure',
  Records: 'History',
  'Edit tendencies': 'History',
  'Compare versions': 'History',
  'Compare transcription': 'History',
  'Notes export': 'History',
  'Learn my style': 'History',
  'Re-time with Qwen3 aligner': 'History',
  'Re-transcribe selected lines': 'History',
  'Audio tags (SenseVoice)': 'Extras',
  'Burned subtitle preview': 'Extras',
}

/** Shows the named tab of the Review tools. */
export async function openTab(page: Page, tab: string) {
  const button = page.getByRole('tablist', { name: 'Review tools' }).getByRole('tab', { name: new RegExp(`^${tab}`) })
  if ((await button.getAttribute('aria-selected')) !== 'true') await button.click()
  await expect(button).toHaveAttribute('aria-selected', 'true')
}

/** Opens the tab that holds the named review section, then the section itself (a no-op for other titles). */
export async function openFoldFor(page: Page, title: string) {
  const tab = TAB_OF[title]
  if (!tab) return
  await openTab(page, tab)
  const summary = page.locator('details.section > summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title.replace(/[()]/g, '\\$&')}$`) }) }).first()
  await unfold(summary)
}

/** The AI checks group (job status, Check and Fix tabs), with the Check tab showing. */
export async function openAi(page: Page): Promise<Locator> {
  await openTab(page, 'Check')
  return aiChecks(page)
}

export const aiChecks = (page: Page) => page.getByRole('group', { name: 'AI checks' })

/** Opens the fold a <summary> belongs to; a tab's first card is already open the first time. */
export async function unfold(summary: Locator) {
  const details = summary.locator('xpath=..')
  if ((await details.getAttribute('open')) === null) await summary.click()
  await expect(details).toHaveAttribute('open', '')
}
