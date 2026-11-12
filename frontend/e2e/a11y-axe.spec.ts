import AxeBuilder from '@axe-core/playwright'
import { test } from './fixtures'
import { enableDeveloperMode } from './sourceHelpers'

// Reports axe violations per page without failing: the app has existing violations, and a blocking gate
// should only replace this once the list is cleared (see the annotations and the "axe-violations" attachment).

const PAGES = [
  '#/', '#/settings', '#/jobs', '#/translate', '#/make-subtitles', '#/diagnostics',
  '#/benchmark', '#/sources', '#/manga', '#/library-tools', '#/drama/3', '#/read/2',
]

for (const hash of PAGES) {
  test(`axe report: ${hash}`, async ({ page }, testInfo) => {
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
    console.log(`AXE ${hash}: ${JSON.stringify(summary)}`)
  })
}
