import AxeBuilder from '@axe-core/playwright'
import { expect, test } from './fixtures'
import { enableDeveloperMode } from './sourceHelpers'

// Fails on any axe violation (WCAG 2.1 A and AA) on these pages. Every page had none when this became a
// gate, so a new one is a regression; the annotations and the "axe-violations" attachment say what and where.

const PAGES = [
  '#/', '#/settings', '#/jobs', '#/translate', '#/make-subtitles', '#/diagnostics',
  '#/benchmark', '#/sources', '#/manga', '#/library-tools', '#/drama/3', '#/read/2',
]

for (const hash of PAGES) {
  test(`axe: ${hash}`, async ({ page }, testInfo) => {
    await enableDeveloperMode(page)
    await page.goto(`/${hash}`)
    await page.getByRole('heading', { level: 1 }).first().waitFor()
    const { violations } = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
    const summary = violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      help: v.help,
      nodes: v.nodes.length,
      examples: v.nodes.slice(0, 3).map((n) => n.target.join(' ')),
    }))
    for (const v of summary) testInfo.annotations.push({ type: `axe ${v.impact}`, description: `${v.id}: ${v.help} (${v.nodes})` })
    await testInfo.attach('axe-violations', { body: JSON.stringify(summary, null, 2), contentType: 'application/json' })
    expect(summary, `axe violations on ${hash}`).toEqual([])
  })
}
