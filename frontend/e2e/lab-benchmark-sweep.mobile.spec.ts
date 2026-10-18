import { expect, test } from './fixtures'
import { hitHeight, installHitArea } from './hitArea'
import { finished, mockLab } from './sweepMocks'

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
