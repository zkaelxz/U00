import { expect, type Locator, type Page } from '@playwright/test'

// Review's tools sit in four groups (tabs) under the line list; a tool's own
// section is only reachable once its group is open. The first card of a group
// opens by itself the first time, the others stay folded.
const PROBLEMS = 'Look for problems'
const CHANGE = 'Change many lines'
const TAB_OF: Record<string, string> = {
  'AI review': PROBLEMS,
  'Check options': PROBLEMS,
  Consistency: PROBLEMS,
  Emotion: PROBLEMS,
  'Flag lines for review': PROBLEMS,
  'Source changed': PROBLEMS,
  'Coverage and pacing': PROBLEMS,
  'Compare transcription': PROBLEMS,
  'Audio tags (SenseVoice)': PROBLEMS,
  Options: CHANGE,
  'Run 3 times, review differences': CHANGE,
  Structure: CHANGE,
  'Re-split long lines': CHANGE,
  'Merge short lines': CHANGE,
  'Shorten overlong': CHANGE,
  'Fix common errors': CHANGE,
  'Re-time with Qwen3 aligner': CHANGE,
  'Re-transcribe selected lines': CHANGE,
  Records: 'Records',
  'Edit tendencies': 'Records',
  'Compare versions': 'Records',
  'Notes export': 'Records',
  'Learn my style': 'Records',
  'Burned subtitle preview': 'Finish',
}

/** Shows the named group (tab) of the Review tools. */
export async function openTab(page: Page, tab: string) {
  const button = page.getByRole('tablist', { name: 'Review tools' }).getByRole('tab', { name: new RegExp(`^${tab}`) })
  if ((await button.getAttribute('aria-selected')) !== 'true') await button.click()
  await expect(button).toHaveAttribute('aria-selected', 'true')
}

/** Opens the group that holds the named review section, then the section itself (a no-op for other titles). */
export async function openFoldFor(page: Page, title: string) {
  const tab = TAB_OF[title]
  if (!tab) return
  await openTab(page, tab)
  const summary = page.locator('details.section > summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title.replace(/[()]/g, '\\$&')}$`) }) }).first()
  await unfold(summary)
}

/** The AI checks group (job status and the tools), with Look for problems showing. */
export async function openAi(page: Page): Promise<Locator> {
  await openTab(page, PROBLEMS)
  return aiChecks(page)
}

export const aiChecks = (page: Page) => page.getByRole('group', { name: 'AI checks' })

/** Opens the fold a <summary> belongs to; a group's first card is already open the first time. */
export async function unfold(summary: Locator) {
  const details = summary.locator('xpath=..')
  if ((await details.getAttribute('open')) === null) await summary.click()
  await expect(details).toHaveAttribute('open', '')
}

// Escape only warns while a save is in flight, so wait for the POST and a
// render before the caller presses anything else.
export async function ctrlS(page: Page, row: Locator) {
  const saved = page.waitForResponse(
    (r) => r.request().method() === 'POST' && /\/api\/lines\/dramas\/\d+\/lines\/\d+$/.test(r.url()),
  )
  await row.getByLabel('Translation').press('Control+s')
  await (await saved).finished()
  await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))))
}
