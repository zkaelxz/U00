import { expect, test } from './fixtures'
import { openBenchTab } from './benchSections'
import { finished, mockTranslateSweep } from './translateSweepMocks'

// T1: the Translation sweep card and the Run card's "Translate like a title" options. Everything is mocked:
// a sweep spends on a real engine. Set TSWEEP_SHOTS_DIR=<dir> to also save screenshots in each theme.
const SHOTS = process.env.TSWEEP_SHOTS_DIR
// The sticky app header would cover the card's top in an element screenshot.
const NO_HEADER = '.app-header { visibility: hidden !important; }'

for (const theme of ['light', 'dark', 'sepia', 'oled']) {
  test(`Translation sweep: estimate, start, and a finished recommendation (${theme})`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem('baihe.theme', t), theme)
    let sweep: Record<string, unknown> | null = null
    await mockTranslateSweep(page, () => sweep)
    await page.goto('/#/benchmark')
    await openBenchTab(page, 'Run a benchmark')
    const card = page.getByRole('region', { name: 'Translation sweep' })
    await expect(card.getByLabel('Golden set', { exact: true })).toHaveValue('My scenes')
    await expect(card.getByTestId('tsweep-start-reason')).toHaveText('Estimate this sweep first.')
    await card.getByRole('button', { name: 'Estimate', exact: true }).click()
    const est = card.getByTestId('tsweep-estimate')
    await expect(est).toContainText('8 passes to run')
    await expect(est).toContainText('Held out to confirm: 2 titles')
    await expect(card.getByTestId('tsweep-cost')).toContainText('of the $16.00 left this month')
    await expect(est.getByRole('list', { name: 'Left out of this sweep' })).toContainText('Reflect')
    if (SHOTS) await card.screenshot({ path: `${SHOTS}/tsweep-estimate-${theme}-desktop.png`, style: NO_HEADER })

    const sent = page.waitForRequest((r) => r.url().endsWith('/api/benchmark/translate-sweep') && r.method() === 'POST')
    await card.getByRole('button', { name: 'Resume sweep' }).click()
    expect((await sent).postDataJSON()).toMatchObject({ set_name: 'My scenes', language: 'zh', series_id: null, confirm: true })

    sweep = finished
    await page.goto('/#/benchmark?tab=run')
    const rec = page.getByRole('region', { name: 'Translation sweep' }).getByTestId('sweep-recommendation')
    await expect(rec).toContainText('Change recommended')
    await expect(rec).toContainText('claude, Batch size 15')
    await expect(rec).toContainText('3.0 chrF points better')
    if (SHOTS) await page.getByRole('region', { name: 'Translation sweep' }).screenshot({ path: `${SHOTS}/tsweep-recommendation-${theme}-desktop.png`, style: NO_HEADER })
  })
}

test('Run card: translate like a title sends the changed options', async ({ page }) => {
  await mockTranslateSweep(page, () => null)
  await page.goto('/#/benchmark')
  await openBenchTab(page, 'Run a benchmark')
  const fields = page.getByTestId('bench-translate-settings')
  await fields.locator('summary').click()
  await fields.getByRole('switch').first().click()
  await fields.getByRole('spinbutton', { name: 'Batch size' }).fill('15')
  await expect(fields.locator('summary')).toContainText('1 changed')
  if (SHOTS) await fields.screenshot({ path: `${SHOTS}/translate-options-desktop.png`, style: NO_HEADER })
})
