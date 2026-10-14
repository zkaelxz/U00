import { expect, test } from '@playwright/test'

import { mockRestructure, openRestructure } from './titleRestructureMocks'

// Merge or move chapters on the Details tab of the seeded novel (drama 2), with
// the restructure routes mocked (titleRestructureMocks.ts).

test('moving a chapter shows a preview first and confirms with its fingerprint', async ({ page }) => {
  const calls = await mockRestructure(page)
  await openRestructure(page)
  const panel = page.getByTestId('title-restructure')
  const preview = panel.getByRole('button', { name: 'Preview' })
  await expect(preview).toBeDisabled()
  await expect(panel.getByText('Still needed: the title to move to.')).toBeVisible()
  await panel.getByLabel('Move to', { exact: true }).selectOption({ label: 'Heaven Official (part 2)' })
  await expect(panel.getByText('Still needed: at least one chapter.')).toBeVisible()
  await panel.getByRole('checkbox', { name: /Chapter 2/ }).check()
  await preview.click()

  const shown = page.getByTestId('title-restructure-preview')
  await expect(shown).toContainText('1 chapter will move to the end of “Heaven Official (part 2)”.')
  await expect(shown).toContainText('that copy stays here')
  await expect(shown).toContainText("A copy of the library database and both titles' original text is saved")
  expect(calls).toEqual([{ path: 'preview', body: { target_id: 5, kind: 'move', chapter_ids: ['2'], move_duplicates: false } }])

  await shown.getByRole('button', { name: 'Move 1 chapter to “Heaven Official (part 2)”' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Moved 1 chapter to' })).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: 'backups/restructure' })).toBeVisible()
  expect(calls[1]).toEqual({
    path: 'apply',
    body: { target_id: 5, kind: 'move', chapter_ids: ['2'], move_duplicates: false, fingerprint: 'f'.repeat(64), confirm: true },
  })
})

test('changing a choice drops the preview, and a merge sends no chapter list', async ({ page }) => {
  const calls = await mockRestructure(page)
  await openRestructure(page)
  const panel = page.getByTestId('title-restructure')
  await panel.getByRole('radio', { name: 'Merge this whole title into another' }).check()
  await expect(panel.getByRole('checkbox', { name: /Chapter 1/ })).toHaveCount(0)
  await panel.getByLabel('Move to', { exact: true }).selectOption({ label: 'Heaven Official (part 2)' })
  await panel.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByTestId('title-restructure-preview')).toBeVisible()
  expect(calls[0].body).toEqual({ target_id: 5, kind: 'merge', move_duplicates: false })
  await panel.getByRole('checkbox', { name: /Also move chapters the other title already has/ }).check()
  await expect(page.getByTestId('title-restructure-preview')).toHaveCount(0)
})
