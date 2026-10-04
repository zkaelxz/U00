import { expect, test } from '@playwright/test'

import { SUMMARY, clearLines, mockResplit, openResplit, seedLines } from './resplitMocks'

// Phone project: the re-split controls fit the width with 44px targets.

test.beforeEach(() => seedLines())
test.afterAll(() => clearLines())

test('re-split controls fit a phone', async ({ page }) => {
  await mockResplit(page, () => ({ json: SUMMARY }))
  const group = await openResplit(page)
  const buttons = group.getByRole('button')
  for (const name of ['Re-split long lines', 'Re-assign speakers from saved detection']) {
    const box = await group.getByRole('button', { name }).boundingBox()
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
  await expect(buttons.first()).toBeVisible()
  await group.getByRole('button', { name: 'Re-split long lines' }).tap()
  await expect(group.getByTestId('resplit-summary')).toHaveText('Split 31 lines into 118; speakers re-assigned.')
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(scroll).toBeLessThanOrEqual(client)
})
