import { expect, test } from './fixtures'
import { openBenchTab } from './benchSections'
import { enableDeveloperMode } from './sourceHelpers'

test.beforeEach(async ({ page }) => {
  await enableDeveloperMode(page)
})

// "Hard-sub reading result" on the Golden sets tab: the reading route is mocked (a real one costs money);
// the section is real.
const outcome = {
  groups: [
    { key: 'white-left', lines: [{ start: 0, end: 3, text: '你好吗' }, { start: 65, end: 68, text: '再见了' }] },
    { key: 'yellow-right', lines: [{ start: 3, end: 6, text: '我很好' }] },
  ],
  windows_read: 40, windows_total: 40, unreadable: 0, unclear_pictures: 0, failed_reads: 0, cost_usd: 0.2234,
  stopped: null, note: null,
  comparison: {
    groups: [{ key: 'white-left', characters: 6, recall: 0.8333 }, { key: 'yellow-right', characters: 3, recall: 1 }],
    recall: 0.8889, precision: 0.8, label_overlap: {}, label_match: null,
  },
}

test('Benchmark Lab: a finished hard-sub reading shows its cost, comparison and lines per speaker', async ({ page }) => {
  await page.route('**/api/benchmark/dramas/*/hard-sub-reading', (r) =>
    r.fulfill({ json: { status: 'done', progress: 1, message: 'Done', result: outcome } }))
  await page.goto('/#/benchmark')
  await openBenchTab(page, 'Golden sets')
  const section = page.locator('details', { hasText: 'Hard-sub reading result' })
  await section.getByText('Hard-sub reading result', { exact: true }).click()
  await section.getByLabel('Title', { exact: true }).selectOption({ index: 1 })
  const result = section.getByTestId('hard-sub-reading-result')
  await expect(result).toContainText('Read 40 of 40 stretches. Cost $0.22.')
  await expect(result).toContainText('The transcript has 88.9% of the on-screen characters')
  await expect(result.getByRole('row', { name: /white-left 2 6 83.3%/ })).toBeVisible()
  await result.getByText('white-left', { exact: true }).last().click()
  await expect(result).toContainText('1:05–1:08 再见了')
})

test('Benchmark Lab: a title with no reading says so', async ({ page }) => {
  await page.route('**/api/benchmark/dramas/*/hard-sub-reading', (r) =>
    r.fulfill({ status: 404, json: { detail: 'No hard-sub reading has been run for this title.' } }))
  await page.goto('/#/benchmark')
  await openBenchTab(page, 'Golden sets')
  const section = page.locator('details', { hasText: 'Hard-sub reading result' })
  await section.getByText('Hard-sub reading result', { exact: true }).click()
  await section.getByLabel('Title', { exact: true }).selectOption({ index: 1 })
  await expect(section.getByTestId('hard-sub-reading-none')).toHaveText('No reading has been run for this title.')
})
