import { expect, test } from './fixtures'
import { openBenchTab } from './benchSections'
import { installHitArea } from './hitArea'
import { finished, mockTranslateSweep } from './translateSweepMocks'

// Phone project (390x844, touch): the Translation sweep card and the Run card's translate options fit the
// width, with 44px targets. Mocked like the desktop spec. Set TSWEEP_SHOTS_DIR=<dir> to save screenshots.
const SHOTS = process.env.TSWEEP_SHOTS_DIR
const NO_HEADER = '.app-header { visibility: hidden !important; }'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

test('Translation sweep and translate options on a phone', async ({ page }) => {
  await mockTranslateSweep(page, () => finished)
  await page.goto('/#/benchmark')
  await openBenchTab(page, 'Run a benchmark')
  const card = page.getByRole('region', { name: 'Translation sweep' })
  await card.getByRole('button', { name: 'Estimate', exact: true }).click()
  await expect(card.getByTestId('tsweep-estimate')).toContainText('8 passes to run')
  const fields = page.getByTestId('bench-translate-settings')
  await fields.locator('summary').click()
  await fields.getByRole('switch').first().click()
  const small = await page.locator('.bench-page button:not(.link):not(.field-help-btn):not(.toggle), .bench-page summary, .bench-page select, .bench-page input:not([type=checkbox])')
    .evaluateAll((els) => els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent || e.getAttribute('aria-label') || '').trim().slice(0, 30) }))
      .filter(({ h }) => h < 44))
  expect(small).toEqual([])
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  if (SHOTS) {
    await card.screenshot({ path: `${SHOTS}/tsweep-phone.png`, style: NO_HEADER })
    await fields.screenshot({ path: `${SHOTS}/translate-options-phone.png`, style: NO_HEADER })
  }
})
