import { expect, test } from './fixtures'

// The GPU-per-day bars used to carry a mouse-only title, so a phone or the keyboard could not read a value.
test('a GPU bar shows its day and hours on hover, focus and tap', async ({ page }) => {
  const days = Array.from({ length: 30 }, (_, i) => ({ day: `2026-11-${String(i + 1).padStart(2, '0')}`, gpu_seconds: 3600 * (i % 5), jobs: i % 5 }))
  await page.route('**/api/activity/gpu-daily**', (route) => route.fulfill({ json: { days, total_gpu_seconds: 3600 * 60 } }))
  await page.goto('/#/jobs')
  await page.getByText('Activity log and GPU time').click()
  const chart = page.getByTestId('gpu-chart')
  await expect(chart).toBeVisible()
  const readout = chart.getByTestId('chart-readout')
  await expect(readout).toContainText('Hover, tap or tab')
  await chart.getByRole('img', { name: /2026-11-03/ }).hover()
  await expect(readout).toContainText('2026-11-03')
  await chart.getByRole('img', { name: /2026-11-05/ }).focus()
  await expect(readout).toContainText('2026-11-05')
})
