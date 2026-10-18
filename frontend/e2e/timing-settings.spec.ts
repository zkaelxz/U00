import { expect, test, type Page } from '@playwright/test'
import { openSettingsGroups } from './settingsNav'
import { mockTimingSettings } from './timingSettingsMocks'

// Settings > System > Timing and splitting: grouped rows with their defaults, tooltips,
// range checks and a Reset per row. The settings calls are mocked (see timingSettingsMocks).

async function openCard(page: Page) {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Performance and advanced')
  const card = page.getByRole('region', { name: 'Timing and splitting' })
  await expect(card).toBeVisible()
  return card
}

test('shows the groups with each row\'s value, default and tooltip', async ({ page }) => {
  const { posts, unmocked } = await mockTimingSettings(page)
  const card = await openCard(page)
  await expect(card.getByRole('group', { name: 'Line splitting' })).toBeVisible()
  await expect(card.getByRole('group', { name: 'Sentence grouping' })).toBeVisible()
  // Every Voice detection row is Developer Mode only, so the group is absent without it.
  await expect(card.getByRole('group', { name: 'Voice detection' })).toHaveCount(0)
  await expect(card.getByText('More settings appear here when Developer Mode is on.')).toBeVisible()

  const longest = card.getByLabel('Longest line (time)', { exact: true })
  await expect(longest).toHaveValue('8')
  await expect(card.getByTestId('timing-default-split_max_seconds')).toHaveText('Default: 8 s')
  await expect(card.getByTestId('timing-default-scene_break_gap_seconds')).toHaveText('Default: 3 s')

  await card.getByRole('button', { name: 'Help: Longest line (time)' }).focus()
  await expect(card.getByRole('tooltip').filter({ hasText: 'Lines that run longer than this are cut' })).toBeVisible()
  expect(posts).toEqual([])
  expect(unmocked).toEqual([])
})

test('saves a value, refuses out-of-range ones, and Reset puts the default back', async ({ page }) => {
  const { posts, unmocked } = await mockTimingSettings(page)
  const card = await openCard(page)
  const longest = card.getByLabel('Longest line (time)', { exact: true })
  const reset = card.getByRole('button', { name: 'Reset Longest line (time) to default' })
  await expect(reset).toBeDisabled()

  for (const bad of ['0', '3', '25']) {
    await longest.fill(bad)
    await card.getByRole('button', { name: 'Save' }).click()
    await expect(card.getByRole('alert')).toContainText('from 4 to 20')
  }
  expect(posts).toEqual([])

  await longest.fill('6')
  await card.getByRole('button', { name: 'Save' }).click()
  await expect(card.getByRole('status')).toHaveText('Saved.')
  expect(posts).toEqual([{ split_max_seconds: 6 }])
  await expect(longest).toHaveValue('6')
  await expect(reset).toBeEnabled()

  await reset.click()
  await expect(longest).toHaveValue('8')
  await expect(reset).toBeDisabled()
  expect(posts).toEqual([{ split_max_seconds: 6 }, { split_max_seconds: 8 }])
  expect(unmocked).toEqual([])
})

test('the sentence pause has its own value and its own reset', async ({ page }) => {
  const { posts } = await mockTimingSettings(page)
  const card = await openCard(page)
  await card.getByLabel('Pause inside one sentence', { exact: true }).fill('1.5')
  await card.getByRole('button', { name: 'Save' }).click()
  await expect(card.getByRole('status')).toHaveText('Saved.')
  expect(posts).toEqual([{ sentence_group_gap_seconds: 1.5 }])
  await card.getByRole('button', { name: 'Reset Pause inside one sentence to default' }).click()
  await expect(card.getByLabel('Pause inside one sentence', { exact: true })).toHaveValue('1')
})

test('Developer Mode adds the voice detection rows, and 0 is a real value where the minimum is 0', async ({ page }) => {
  const { posts, unmocked } = await mockTimingSettings(page, { developerMode: true })
  const card = await openCard(page)
  await expect(card.getByRole('group', { name: 'Voice detection' })).toBeVisible()
  await expect(card.getByText('More settings appear here when Developer Mode is on.')).toHaveCount(0)
  const pad = card.getByLabel('Qwen3 detection: padding', { exact: true })
  await expect(pad).toHaveValue('100')
  await pad.fill('0')
  await card.getByRole('button', { name: 'Save' }).click()
  await expect(card.getByRole('status')).toHaveText('Saved.')
  expect(posts).toEqual([{ qwen_vad_pad_ms: 0 }])
  await expect(pad).toHaveValue('0')
  await card.getByRole('button', { name: 'Reset Qwen3 detection: padding to default' }).click()
  await expect(pad).toHaveValue('100')
  // A row whose minimum is above 0 still refuses 0.
  const words = card.getByLabel('Shortest piece (words)', { exact: true })
  await words.fill('0')
  await card.getByRole('button', { name: 'Save' }).click()
  await expect(card.getByRole('alert')).toContainText('from 1 to 4')
  expect(unmocked).toEqual([])
})
