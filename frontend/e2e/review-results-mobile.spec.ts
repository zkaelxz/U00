import { expect, test, type Page } from '@playwright/test'

import { clearReviewResults, seedReviewResults } from './reviewResultsSeed'

// Phone project (390x844, touch): the Review results and checks fit the width,
// their line links are full-size touch targets and still open the line.

test.beforeEach(() => seedReviewResults())
test.afterAll(() => clearReviewResults())

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')
const section = (page: Page, title: string) =>
  page.locator('details.section').filter({ has: page.locator('summary .section-title', { hasText: new RegExp(`^${title}$`) }) })
const TITLES = ['AI review', 'Consistency', 'Emotion', 'Coverage and pacing', 'Edit tendencies', 'Compare versions', 'Notes export']

async function openAll(page: Page) {
  for (const t of TITLES) {
    const s = section(page, t)
    if ((await s.getAttribute('open')) === null) await s.locator('summary').click()
    await expect(s).toHaveAttribute('open', '')
  }
  await section(page, 'Consistency').getByRole('button', { name: 'Show lines' }).click()
  await section(page, 'Compare versions').getByRole('button', { name: 'Show differences' }).click()
  await expect(section(page, 'Compare versions').getByTestId('compare-list')).toBeVisible()
}

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

test('Review results: no sideways scroll, 44px line links, a link opens its line', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(4)
  await openAll(page)
  await expectNoHorizontalOverflow(page)

  const heights = await page.locator('.review-jump, .review-fix button, .review-fix select, .review-fix input').evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null).map((e) => [e.getBoundingClientRect().height, e.outerHTML.slice(0, 80)] as const),
  )
  expect(heights.length).toBeGreaterThan(5)
  for (const [h, html] of heights) expect(h, html).toBeGreaterThanOrEqual(44)

  await section(page, 'Emotion').getByRole('button', { name: '#2' }).tap()
  await expect(rows(page).nth(1)).toHaveAttribute('aria-current', 'true')
})

test('screenshots (phone)', async ({ page }) => {
  const dir = process.env.REVIEW_SHOTS
  test.skip(!dir, 'REVIEW_SHOTS not set')
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('about:blank')
    await page.goto('/#/drama/3/review')
    await expect(rows(page)).toHaveCount(4)
    await openAll(page)
    await section(page, 'AI review').scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${dir}/phone-${scheme}-ai-review.png` })
    await section(page, 'Coverage and pacing').scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${dir}/phone-${scheme}-checks.png` })
    await page.screenshot({ path: `${dir}/phone-${scheme}-full.png`, fullPage: true })
  }
})
