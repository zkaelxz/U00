import { expect, test } from '@playwright/test'

import { mockEditor } from './comicEditorMocks'
import { expectNoSidewaysScroll } from './mockHelpers'

// Phone project: the comic editor stacks the page above the side panel.

test('select a box, edit it and Apply on a phone', async ({ page }) => {
  const s = await mockEditor(page)
  await page.goto('/#/comic/7/edit?page=1')
  await expect(page.getByTestId('ce-box-12')).toBeVisible()
  await expectNoSidewaysScroll(page)
  await page.getByTestId('ce-box-12').tap()
  const english = page.getByLabel('English')
  await expect(english).toBeVisible()
  await expectNoSidewaysScroll(page)
  await english.fill('Who is behind you?')
  await page.getByRole('button', { name: 'Apply' }).click()
  await expect.poll(() => s.patches.length).toBe(1)
  expect(s.patches[0].body.expected_rev).toBe(1)
  expect(s.patches[0].body.changes).toEqual({ translated_text: 'Who is behind you?' })
  await expectNoSidewaysScroll(page)
  expect(s.comic.unmocked).toEqual([])
})
