import AxeBuilder from '@axe-core/playwright'
import type { Page, TestInfo } from '@playwright/test'

import { mockComic } from './comicMocks'
import { mockEditor } from './comicEditorMocks'
import { expect, test } from './fixtures'
import { cue, mockLive, openLive, SID } from './liveMocks'
import { guardWrites } from './mockHelpers'
import { enableDeveloperMode } from './sourceHelpers'

// Fails on any axe violation (WCAG 2.1 A and AA) on these pages. Every page had none when this became a
// gate, so a new one is a regression; the annotations and the "axe-violations" attachment say what and where.
// Colour contrast is left to theme-contrast.spec.ts: over the frosted background axe marks almost every
// contrast check "incomplete", and the few it does measure flip with the moment it reads them (a primary
// button on #/translate failed one run in three on the same build), which made this gate flaky.

const PAGES = [
  '#/', '#/settings', '#/jobs', '#/translate', '#/make-subtitles', '#/diagnostics',
  '#/benchmark', '#/sources', '#/manga', '#/library-tools', '#/drama/3', '#/read/2',
]

async function axeSummary(page: Page, testInfo: TestInfo) {
  const { violations } = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .disableRules(['color-contrast']).analyze()
  const summary = violations.map((v) => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    nodes: v.nodes.length,
    examples: v.nodes.slice(0, 3).map((n) => n.target.join(' ')),
  }))
  for (const v of summary) testInfo.annotations.push({ type: `axe ${v.impact}`, description: `${v.id}: ${v.help} (${v.nodes})` })
  await testInfo.attach('axe-violations', { body: JSON.stringify(summary, null, 2), contentType: 'application/json' })
  return summary
}

for (const hash of PAGES) {
  test(`axe: ${hash}`, async ({ page }, testInfo) => {
    await enableDeveloperMode(page)
    await page.goto(`/${hash}`)
    await page.getByRole('heading', { level: 1 }).first().waitFor()
    expect(await axeSummary(page, testInfo), `axe violations on ${hash}`).toEqual([])
  })
}

// The screens that hold lines, opened with mocked lines so nothing in the shared library changes. They report what
// axe finds without failing yet: they were never scanned before, and become part of the gate above once a run
// shows them clean.
const flagged = (id: number) => ({
  id, idx: id - 1, start: id * 2, end: id * 2 + 1.5, zh: `第${id}句`, en: `Line ${id}`, speaker: null,
  speaker_manual: false, sfx: false, flag: id === 2 ? 'reading_speed' : id === 4 ? 'source_mismatch' : null,
  flag_note: id === 2 ? '25.2 characters/second' : id === 4 ? 'Source match: the English reads unlike its source line (similarity 0.42)' : null,
  dub_filename: null, lang: null, confidence: id === 3 ? 0.5 : null,
})

async function mockLines(page: Page) {
  const lines = [1, 2, 3, 4, 5, 6].map(flagged)
  await guardWrites(page)
  await page.route('**/api/review/dramas/3/lines?*', (route) =>
    route.fulfill({ json: { lines, page: 1, page_size: 40, total: lines.length, flagged_count: 2, untranslated_count: 0 } }))
}

const LINE_SCREENS: { name: string; open: (page: Page) => Promise<unknown> }[] = [
  { name: 'Review', open: async (page) => {
    await mockLines(page)
    await page.goto('/#/drama/3/review')
    await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(6)
  } },
  { name: 'Watch', open: async (page) => {
    await mockLines(page)
    await page.goto('/#/watch/3')
    await expect(page.getByText('Line 6').first()).toBeVisible()
  } },
  { name: 'comic reader', open: async (page) => {
    await mockComic(page)
    await page.goto('/#/comic/7')
    await expect(page.getByTestId('comic-page').first()).toBeVisible()
  } },
  { name: 'comic editor', open: async (page) => {
    await mockEditor(page)
    await page.goto('/#/comic/7/edit?page=1')
    await page.getByRole('heading', { level: 1 }).first().waitFor()
  } },
  { name: 'Live', open: async (page) => {
    const m = await mockLive(page)
    m.state.sessions = [{ session_id: SID, status: 'running', engine: 'deepseek', cue_count: 3 }]
    m.state.status = 'running'
    m.state.message = 'Listening'
    m.state.cues = [cue(0), cue(1), cue(2)]
    const live = await openLive(page)
    await expect(live.getByTestId('live-status')).toHaveText('Listening · 3 lines')
  } },
]

for (const { name, open } of LINE_SCREENS) {
  test(`axe report: ${name} with lines`, async ({ page }, testInfo) => {
    await open(page)
    const summary = await axeSummary(page, testInfo)
    testInfo.annotations.push({ type: 'axe report', description: `${name}: ${summary.length} rule(s) broken` })
  })
}
