import { expect, test } from '@playwright/test'

// Library parity batch B3 (inventory P06, L01, L04, L05, P03). Seed data:
// e2e/serve_seeded_api.py. Anything that creates a drama deletes it again,
// so the seeded three are unchanged afterwards.

test('New drama sends the Summary (P06)', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New drama' }).click()
  const sheet = page.getByRole('dialog', { name: 'New drama' })
  await sheet.getByLabel('English title').fill('E2E Summary Drama')
  await sheet.getByText('Credits, summary, series and preset').click()
  await sheet.getByLabel('Summary').fill('  Two cultivators solve a mystery.  ')
  const req = page.waitForRequest((r) => r.url().endsWith('/api/dramas') && r.method() === 'POST')
  await sheet.getByRole('button', { name: 'Create drama', exact: true }).click()
  expect((await req).postDataJSON()).toMatchObject({ title_en: 'E2E Summary Drama', summary: 'Two cultivators solve a mystery.' })
  await expect(page.getByTestId('created-notice')).toContainText('Created “E2E Summary Drama”.')

  const detail = page.getByRole('dialog', { name: 'E2E Summary Drama' })
  await detail.getByRole('button', { name: 'Delete drama…' }).click()
  await detail.getByLabel('Type DELETE to confirm').fill('DELETE')
  await detail.getByRole('button', { name: 'Delete permanently' }).click()
  await expect(page.getByRole('dialog', { name: 'E2E Summary Drama' })).toHaveCount(0)
})
