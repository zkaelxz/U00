import { expect, test } from './fixtures'
import { hitHeight, installHitArea } from './hitArea'
import { finished, mockLab } from './sweepMocks'
import { enableDeveloperMode } from './sourceHelpers'

// The Benchmark Lab page is behind Developer Mode.
test.beforeEach(async ({ page }) => {
  await enableDeveloperMode(page)
})

// Phone (390x844): the Settings sweep tab with a finished recommendation fits the width, and its
// buttons are 44px targets. Mocked, as on desktop. SWEEP_SHOTS_DIR=<dir> saves a screenshot per theme.
const SHOTS = process.env.SWEEP_SHOTS_DIR

for (const theme of ['light', 'dark', 'sepia', 'oled']) {
  test(`Settings sweep on a phone: nothing scrolls sideways (${theme})`, async ({ page }) => {
    await installHitArea(page)
    await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
    await mockLab(page, () => finished)
    await page.goto('/#/benchmark?tab=run')
    const card = page.getByRole('region', { name: 'Settings sweep' })
    await expect(card.getByTestId('sweep-recommendation')).toContainText('Change recommended')
    await expect(card.getByRole('list', { name: 'Held-out score by tag' })).toContainText('Too few clips to decide')
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(sideways).toBeLessThanOrEqual(0)
    const approve = card.getByRole('button', { name: 'Approve the winner' })
    expect(await hitHeight(approve)).toBeGreaterThanOrEqual(44)
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/sweep-recommendation-${theme}-phone.png`, fullPage: true, style: '.app-header { visibility: hidden !important; }' })
  })
}

// The sweep's start and approve/reject routes are PC only (local_only on the server): away from the PC
// the card keeps Estimate and the results, and leaves the PC-only controls and their notes out.
test('Settings sweep on the remote view leaves out the PC-only controls and notes', async ({ page }) => {
  await page.route('**/api/meta', (r) =>
    r.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } }))
  await mockLab(page, () => finished)
  await page.goto('/#/benchmark?tab=run')
  const card = page.getByRole('region', { name: 'Settings sweep' })
  await expect(card.getByTestId('sweep-recommendation')).toContainText('Change recommended')
  await expect(card.getByRole('button', { name: 'Estimate', exact: true })).toBeVisible()
  await expect(card.getByRole('button', { name: /^(Start|Resume) sweep$/ })).toHaveCount(0)
  await expect(card.getByTestId('sweep-start-reason')).toHaveCount(0)
  await expect(card.getByRole('button', { name: 'Approve the winner' })).toHaveCount(0)
  await expect(card.getByRole('button', { name: /^Reject/ })).toHaveCount(0)
  await expect(card.getByLabel('Why?', { exact: true })).toHaveCount(0)
  await expect(card).not.toContainText('PC only')
  await expect(card).not.toContainText('main PC only')
})
