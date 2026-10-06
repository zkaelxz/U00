import { expect, test } from '@playwright/test'

import { EXPECTED_SUMMARY, clearLines, mockSnapshot, seedLines } from './snapshotPreviewMocks'

// Phone project: the preview opens by tap and fits the width.

test.beforeEach(() => seedLines())
test.afterAll(() => clearLines())

test('snapshot preview fits a phone', async ({ page }) => {
  await mockSnapshot(page)
  const button = page.getByTestId('history-list').getByRole('button', { name: 'Preview' })
  await button.tap()
  await expect(page.getByTestId('snapshot-preview')).toContainText(EXPECTED_SUMMARY)
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth,
  }))
  expect(scroll).toBeLessThanOrEqual(client)
})
