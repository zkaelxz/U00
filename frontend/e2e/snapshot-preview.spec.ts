import { expect, test } from '@playwright/test'

import { EXPECTED_SUMMARY, clearLines, mockSnapshot, seedLines } from './snapshotPreviewMocks'

test.beforeEach(() => seedLines())
test.afterAll(() => clearLines())

test('previewing a snapshot shows what would change and writes nothing', async ({ page }) => {
  const calls = await mockSnapshot(page)
  const list = page.getByTestId('history-list')
  await list.getByRole('button', { name: 'Preview' }).click()
  const preview = page.getByTestId('snapshot-preview')
  await expect(preview).toContainText(EXPECTED_SUMMARY)
  await expect(preview).toContainText('#1 Translation: Hello my friend, lovely day → Old greeting')
  await expect(preview).toContainText('#2 Translation: (empty) → Old walk line')
  expect(calls.restores).toBe(0)
  await list.getByRole('button', { name: 'Restore…' }).click()
  await expect(page.getByLabel('Type restore to confirm')).toBeVisible()
  await list.getByRole('button', { name: 'Hide preview' }).click()
  await expect(preview).toHaveCount(0)
  expect(calls.restores).toBe(0)
})
