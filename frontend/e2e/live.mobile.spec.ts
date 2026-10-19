import { expect, test, type Page } from '@playwright/test'

import { expectTargetsAtLeast44 } from './hitArea'
import { SCREENS, cue, expectSwitchKeepsItsSize, mockLive, openLive } from './liveMocks'
import { expectNoSidewaysScroll } from './mockHelpers'

// Phone project (390x844, touch): the Live page fits the screen and its
// controls are 44px targets, idle and while lines arrive.

async function smallTargets(page: Page) {
  await expectTargetsAtLeast44(
    page.getByRole('region', { name: 'Live' }),
    'button:not(.link, .field-help-btn, .toggle), select, input:not([type=checkbox]), summary, label:has(> input[type=checkbox])',
    { box: true },
  )
}

test('live page on a phone: no sideways scroll, 44px targets', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByText('Advanced').tap()
  await expect(live.getByLabel('Use GPU for Whisper', { exact: true })).toBeVisible()
  await expectNoSidewaysScroll(page)
  await smallTargets(page)

  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByRole('button', { name: 'Start', exact: true }).tap()
  m.state.status = 'running'
  m.state.message = 'Listening'
  m.state.cues = [cue(0), cue(1), cue(2), cue(3)]
  await expect(live.getByTestId('live-status')).toHaveText('Listening · 4 lines')
  await live.getByText('Advanced').tap()
  await expectNoSidewaysScroll(page)
  await smallTargets(page)
  await page.screenshot({ path: `${SCREENS}/phone-running.png`, fullPage: true })
  expect(m.unmocked).toEqual([])
})

test('live page on a phone: the model picker fits and is a 44px target', async ({ page }) => {
  const m = await mockLive(page, { ollama: true })
  const live = await openLive(page)
  await live.getByLabel('AI engine', { exact: true }).selectOption('ollama')
  await expect(live.getByLabel('Model', { exact: true })).toBeVisible()
  await expectNoSidewaysScroll(page)
  await smallTargets(page)
  await expect(live.getByLabel('Model', { exact: true })).toHaveValue('gemma4:12b')
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByRole('button', { name: 'Start', exact: true }).tap()
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({ engine: 'ollama', model: 'gemma4:12b' })
  await page.screenshot({ path: `${SCREENS}/phone-model.png`, fullPage: true })
  expect(m.unmocked).toEqual([])
})

test('a switch inside a field keeps its own size on a phone', async ({ page }) => {
  await mockLive(page)
  const live = await openLive(page)
  await live.getByText('Advanced').tap()
  await expectSwitchKeepsItsSize(live)
})
