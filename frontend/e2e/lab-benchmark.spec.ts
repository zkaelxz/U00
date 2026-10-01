import { expect, test, type Page } from '@playwright/test'
import { gearLink, openGear } from './settingsNav'

// Benchmark Lab (#/benchmark) against the real API on the seeded e2e
// library: import a golden set, add and delete a case, estimate and run the
// fake engine twice (it never calls out and costs nothing:
// output is "[TEST] <source>"), then compare the two runs in the Arena.
// Set BENCH_SHOTS_DIR=<dir> to also save a desktop screenshot.
// Named lab-* on purpose: a run leaves a finished "benchmark_lab" job on the
// shared e2e server (there is no route to clear it), and diagnostics.spec.ts
// expects an empty job list, so these specs must sort after it.

const SHOTS = process.env.BENCH_SHOTS_DIR

async function runOnce(page: Page, label: string, promptVersion: string) {
  const card = page.getByRole('region', { name: 'Run a benchmark' })
  await card.getByLabel('Run label', { exact: true }).fill(label)
  await card.getByLabel('Prompt version', { exact: true }).fill(promptVersion)
  await expect(card.getByTestId('start-reason')).toHaveText('Estimate the cost of this selection first.')
  await expect(card.getByRole('button', { name: 'Start run' })).toBeDisabled()
  await card.getByRole('button', { name: 'Estimate cost' }).click()
  await expect(card.getByTestId('bench-estimate')).toContainText('Estimate for 3 cases: $0.00')
  await card.getByRole('button', { name: 'Start run' }).click()
  const runs = page.getByRole('region', { name: 'Recent runs' })
  await expect(runs.getByRole('row', { name: new RegExp(label) })).toContainText('Done', { timeout: 15_000 })
  await expect(page.getByTestId('bench-finished')).toContainText('Run finished.')
}

test('Benchmark Lab: import a set, run the offline engine twice, compare in the Arena', async ({ page }) => {
  const setName = `e2e-${Date.now()}`

  // Reached from Diagnostics; its nav item stays active.
  await page.goto('/#/diagnostics')
  await page.getByRole('link', { name: 'Benchmark Lab' }).click()
  await expect(page).toHaveURL(/#\/benchmark$/)
  await expect(page.getByRole('heading', { name: 'Benchmark Lab' })).toBeVisible()
  await openGear(page)
  await expect(gearLink(page, 'Diagnostics')).toHaveAttribute('aria-current', 'page')

  // Import a golden set from pasted TSV.
  const sets = page.getByRole('region', { name: 'Golden sets' })
  await sets.locator('summary', { hasText: 'Import golden set' }).click()
  await sets.getByLabel('Set name', { exact: true }).first().fill(setName)
  await sets.getByLabel('Format', { exact: true }).selectOption('tsv')
  await sets.getByLabel('Cases', { exact: true }).fill('你好\tHello\n谢谢\tThank you\n再见\tGoodbye')
  await sets.getByRole('button', { name: 'Import' }).click()
  await expect(sets.getByRole('status')).toContainText(`Added 3 cases to “${setName}”`)
  const row = sets.getByRole('row', { name: new RegExp(setName) })
  await expect(row).toContainText('Public')
  await expect(row).toContainText('Translation')

  // Add one case by hand to the same set, then delete it (two steps).
  await sets.locator('summary', { hasText: 'Add one case' }).click()
  const add = sets.locator('details', { hasText: 'Add one case' })
  await add.getByLabel('Label', { exact: true }).fill('Morning')
  await add.getByLabel('Set name', { exact: true }).fill(setName)
  await add.getByLabel('Tier', { exact: true }).selectOption('public')
  await add.getByLabel('Source text', { exact: true }).fill('早上好')
  await add.getByLabel('Reference translation', { exact: true }).fill('Good morning')
  await add.getByRole('button', { name: 'Add case' }).click()
  await expect(add.getByRole('status')).toHaveText(`Added “Morning” to ${setName}.`)
  await expect(row).toContainText('4')

  await sets.getByRole('button', { name: `Show cases in ${setName} (Public)` }).click()
  const cases = sets.getByRole('region', { name: `Cases in ${setName}` })
  await expect(cases.locator('li')).toHaveCount(4)
  await cases.getByRole('button', { name: 'Delete Morning' }).click()
  await cases.getByRole('button', { name: 'Confirm delete Morning' }).click()
  await expect(cases.locator('li')).toHaveCount(3)

  // Run card: pick the set and the free offline engine.
  const card = page.getByRole('region', { name: 'Run a benchmark' })
  await card.getByLabel('Stage', { exact: true }).selectOption('translation')
  await card.getByLabel('Tier', { exact: true }).selectOption('public')
  await card.getByLabel('Golden set', { exact: true }).selectOption(setName)
  await card.getByLabel('Engine 1', { exact: true }).selectOption('fake')
  await expect(card).toContainText('3 cases selected')

  // Two engines make it an Arena run; the estimate lists each one.
  await card.getByRole('button', { name: 'Add engine' }).click()
  await card.getByLabel('Engine 2', { exact: true }).selectOption('libretranslate')
  await card.getByRole('button', { name: 'Estimate cost' }).click()
  await expect(card.getByTestId('bench-estimate').locator('li')).toHaveCount(2)
  await expect(card.getByRole('button', { name: 'Start arena (2 engines)' })).toBeEnabled()
  // Not started: back to one engine (the estimate no longer matches).
  await card.getByRole('button', { name: /^Remove LibreTranslate/ }).click()
  await expect(card.getByTestId('bench-estimate')).toHaveCount(0)

  await runOnce(page, 'e2e A', 'v1')
  await runOnce(page, 'e2e B', 'v2')

  // Compare the two runs.
  const runs = page.getByRole('region', { name: 'Recent runs' })
  await expect(runs.getByTestId('compare-reason')).toContainText('Tick 2 to 4 runs')
  await runs.getByRole('checkbox', { name: /e2e A$/ }).check()
  await runs.getByRole('checkbox', { name: /e2e B$/ }).check()
  await runs.getByRole('button', { name: 'Compare in Arena (2)' }).click()

  const arena = page.getByRole('region', { name: 'Model Arena' })
  await expect(arena).toContainText('similarity to the reference translation')
  await expect(arena).toContainText('Baseline')
  await expect(arena).toContainText('±0 pts vs first')
  await expect(arena.getByRole('list', { name: 'Cases' }).locator(':scope > li')).toHaveCount(3)
  const first = arena.getByRole('list', { name: 'Cases' }).locator(':scope > li', { hasText: '你好' })
  await expect(first).toContainText('Hello')
  await expect(first.getByLabel('Output of Fake').first()).toContainText('[TEST] 你好')
  await expect(first.getByLabel('Output of Fake').first()).toContainText(/Pass|Fail/)

  // One run's own results.
  await runs.getByRole('button', { name: /^Results of run/ }).first().click()
  await expect(page.getByRole('region', { name: 'Run results' })).toContainText('[TEST]')

  if (SHOTS) {
    await page.setViewportSize({ width: 1440, height: 900 })
    await runs.getByRole('button', { name: 'Compare in Arena (2)' }).click()
    await expect(page.getByRole('region', { name: 'Model Arena' })).toBeVisible()
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: `${SHOTS}/benchmark-desktop.png`, fullPage: true })
  }
})
