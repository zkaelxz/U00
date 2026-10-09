import { expect, test, type Page } from '@playwright/test'
import { mockUploadSettings } from './uploadLimitMocks'
import { openSettingsGroups } from './settingsNav'

// Settings > Advanced > Uploads: the saved upload size limit. The settings
// call is mocked (reads of everything else go to the seeded API); any other
// write is aborted and recorded.

const uploads = (page: Page) => page.locator('details.section:has(> summary > .section-title:text-is("Uploads"))')

async function openUploads(page: Page) {
  await page.goto('/#/settings')
  await openSettingsGroups(page)
  const s = uploads(page)
  await s.locator('summary').click()
  await expect(s).toHaveAttribute('open', '')
  return s
}

test('saves a new upload limit and rejects out-of-range input', async ({ page }) => {
  const { posts, unmocked } = await mockUploadSettings(page)
  const s = await openUploads(page)
  const input = s.getByLabel('Upload size limit (MB)', { exact: true })
  await expect(input).toHaveValue('20480')
  await input.fill('50')
  await s.getByRole('button', { name: 'Save' }).click()
  await expect(s.getByRole('alert')).toContainText('from 100 to 1,048,576')
  expect(posts).toEqual([])
  await input.fill('4096')
  await s.getByRole('button', { name: 'Save' }).click()
  await expect(s.getByRole('status')).toHaveText('Saved.')
  expect(posts).toEqual([{ max_upload_mb: 4096 }])
  expect(unmocked).toEqual([])
})

test('the limit is read-only when the environment sets it', async ({ page }) => {
  const { posts, unmocked } = await mockUploadSettings(page, { fromEnv: true })
  const s = await openUploads(page)
  const input = s.getByLabel('Upload size limit (MB)', { exact: true })
  await expect(input).toBeDisabled()
  await expect(input).toHaveValue('500')
  await expect(s.getByText('Set by the environment, so it can')).toBeVisible()
  expect(posts).toEqual([])
  expect(unmocked).toEqual([])
})

test('the jump link opens Advanced at the Uploads block', async ({ page }) => {
  const { unmocked } = await mockUploadSettings(page)
  await page.goto('/#/settings?section=uploads')
  await expect(page.locator('#settings-advanced > details.section')).toHaveAttribute('open', '')
  await expect(uploads(page)).toBeVisible()
  expect(unmocked).toEqual([])
})
