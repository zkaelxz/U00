import { expect, test } from './fixtures'

test('tapping a GPU bar on a phone shows its value', async ({ page }) => {
  const days = Array.from({ length: 30 }, (_, i) => ({ day: `2026-11-${String(i + 1).padStart(2, '0')}`, gpu_seconds: 3600 * (i % 5), jobs: i % 5 }))
  await page.route('**/api/activity/gpu-daily**', (route) => route.fulfill({ json: { days, total_gpu_seconds: 3600 * 60 } }))
  await page.goto('/#/jobs')
  await page.getByText('Activity log and GPU time').click()
  const chart = page.getByTestId('gpu-chart')
  await chart.getByRole('img', { name: /2026-11-04/ }).tap()
  await expect(chart.getByTestId('chart-readout')).toContainText('2026-11-04')
})
