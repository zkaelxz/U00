import { expect, test } from './fixtures'
import { installHitArea } from './hitArea'
import { mockJobsApi } from './jobsMenuMocks'
import { mockQueueApi, queueItem } from './queueMocks'

// The Queue card on a phone (390 and 360 px, touch): 44px targets, no
// sideways scroll, and tap reordering.

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

for (const width of [390, 360]) {
  test(`phone ${width}px: 44px targets, no sideways scroll, tap to reorder and start`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 })
    await mockJobsApi(page, [], true)
    await mockQueueApi(page, [
      queueItem(1, { title: `Signal ${'x'.repeat(120)}`, step: 'translate', engine: 'claude', paid: true }),
      queueItem(2, { drama_id: 4, title: 'Kae', status: 'skipped', reason: 'Skipped: The title was deleted.' }),
    ])
    await page.goto('/#/jobs')
    const card = page.getByRole('region', { name: 'Queue' })
    await expect(card.getByTestId('queue-items').locator('> li')).toHaveCount(2)
    const targets = await card.locator('button, a, select, input')
      .evaluateAll((els) => els.filter((e) => (e as HTMLElement).offsetParent !== null)
        .map((e) => ({ h: window.hitHeight(e), text: (e.getAttribute('aria-label') || e.textContent || e.tagName).trim().slice(0, 40) })))
    expect(targets.length).toBeGreaterThan(8)
    expect(targets.filter(({ h }) => h < 44)).toEqual([])
    const { scroll, client } = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
    }))
    expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)

    await card.getByRole('button', { name: 'Move up Transcribe Kae' }).tap()
    await expect(card.getByTestId('queue-items').locator('> li').first()).toContainText('Kae')
    await card.getByRole('button', { name: 'Start queue now' }).tap()
    await expect(card.getByRole('button', { name: 'Pause' })).toBeVisible()
  })
}

test('phone: Saved settings opens as a stacked list that fits, with a 44px link', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockJobsApi(page, [], true)
  await mockQueueApi(page)
  await page.goto('/#/jobs')
  const card = page.getByRole('region', { name: 'Queue' })
  await card.getByLabel('Title').selectOption({ label: 'Signal' })
  await card.getByLabel('Step').selectOption('translate')
  const panel = card.locator('details.section').filter({ hasText: 'Saved settings for this step' })
  await panel.locator('summary').tap()
  await expect(panel.getByTestId('queue-saved-settings')).toContainText('Monthly cap left')
  const link = panel.getByRole('link', { name: "Change in the title's Translate stage" })
  expect(await link.evaluate((e) => window.hitHeight(e))).toBeGreaterThanOrEqual(44)
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
})
