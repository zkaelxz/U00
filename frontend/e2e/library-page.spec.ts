import { expect, test } from '@playwright/test'

// Seed data: e2e/serve_seeded_api.py.
// Creates then deletes its own drama so the seeded three are unchanged afterwards.

test('stats line, Continue shelf and global line search', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('stats')).toContainText('3 titles')
  const shelf = page.getByRole('region', { name: 'Continue' })
  // One entry per seeded drama (reading or workspace activity).
  await expect(shelf.getByRole('listitem')).toHaveCount(3)
  const resume = shelf.getByRole('link', { name: 'Resume work on Signal' })
  // The drama's current stage: no stage in the link (the workspace picks it, #433) or an explicit one.
  await expect(resume).toHaveAttribute('href', /#\/drama\/\d+$/)
  await expect(resume).toHaveClass(/btn/)

  await page.getByRole('radio', { name: 'Lines' }).check()
  await page.getByLabel('Search all lines').fill('zzz-no-such-line')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page.getByTestId('search-count')).toHaveText('0 matches')
  // Back to Titles: the same text now filters titles (no match), and
  // Clear filters brings the whole library back.
  await page.getByRole('radio', { name: 'Titles' }).check()
  await expect(page.getByLabel('Search title or summary')).toHaveValue('zzz-no-such-line')
  await expect(page.getByText('No titles match.')).toBeVisible()
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await expect(page.getByTestId('drama-count')).toHaveText('3 titles')
})

test('create form validates client-side', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New title' }).click()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  await expect(sheet.getByLabel('English title')).toBeFocused()
  await sheet.getByRole('button', { name: 'Create title' }).click()
  await expect(sheet.getByRole('alert')).toContainText('title')
  // Humanized options; the values stay raw.
  await expect(sheet.getByLabel('Media type').locator('option:checked')).toHaveText('Audio drama')
  await expect(sheet.getByLabel('Source language').locator('option:checked')).toHaveText('Chinese')
})

test('New title keeps what was typed when the sheet closes; Cancel discards it', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New title' }).click()
  let sheet = page.getByRole('dialog', { name: 'New title' })
  await sheet.getByLabel('English title').fill('Draft title')
  await sheet.getByLabel('Media type').selectOption('novel')
  await page.keyboard.press('Escape')
  await expect(sheet).toBeHidden()

  await page.getByRole('button', { name: 'New title' }).click()
  sheet = page.getByRole('dialog', { name: 'New title' })
  await expect(sheet.getByLabel('English title')).toHaveValue('Draft title')
  await expect(sheet.getByLabel('Media type')).toHaveValue('novel')
  await sheet.getByRole('button', { name: 'Close' }).click()
  await page.getByRole('button', { name: 'New title' }).click()
  await expect(sheet.getByLabel('English title')).toHaveValue('Draft title')

  await sheet.getByRole('button', { name: 'Cancel' }).click()
  await expect(sheet).toBeHidden()
  await page.getByRole('button', { name: 'New title' }).click()
  await expect(sheet.getByLabel('English title')).toHaveValue('')
  await expect(sheet.getByLabel('Media type')).toHaveValue('audio_drama')
  // Nothing was created.
  await expect(page.getByTestId('drama-count')).toHaveText('3 titles')
})

test('create (Enter submits) then delete with typed confirmation', async ({ page }) => {
  try {
    await page.goto('/')
    await page.getByRole('button', { name: 'New title' }).click()
    await page.getByLabel('English title').fill('E2E Temp Title')
    await page.getByLabel('English title').press('Enter')
    // Creating goes straight to the new drama's workspace; its details sheet is one click away in the Library.
    await expect(page).toHaveURL(/#\/drama\/\d+$/)
    await expect(page.getByTestId('drama-title')).toHaveText('E2E Temp Title')
    await page.getByRole('link', { name: 'Back to Library' }).click()
    await page.getByRole('button', { name: 'Details: E2E Temp Title' }).click()
    const detail = page.getByRole('dialog', { name: 'E2E Temp Title' })
    await expect(detail).toBeVisible()

    await detail.getByRole('button', { name: 'Delete title…' }).click()
    const confirm = detail.getByRole('button', { name: 'Delete permanently' })
    await expect(confirm).toBeDisabled()
    await detail.getByLabel('Type DELETE to confirm').fill('DELETE')
    await confirm.click()
    await expect(page.getByRole('dialog', { name: 'E2E Temp Title' })).toHaveCount(0)
    await expect(page.getByRole('region', { name: 'Titles' }).getByRole('link', { name: 'E2E Temp Title' })).toHaveCount(0)
  } finally {
    // A failure before the typed delete would leave the title behind and break the "3 titles" count in every later spec.
    const list = await (await page.request.get('/api/library/dramas')).json()
    for (const d of list.items.filter((x: { title_en: string }) => x.title_en === 'E2E Temp Title')) {
      await page.request.delete(`/api/dramas/${d.id}?confirm=true&confirm_text=DELETE`)
    }
  }
})
