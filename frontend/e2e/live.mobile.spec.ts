import { expect, test, type Page } from '@playwright/test'

import { SCREENS, cue, mockLive, openLive } from './liveMocks'

// Phone project (390x844, touch): the Live page fits the screen and its
// controls are 44px targets, idle and while lines arrive.

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function smallTargets(page: Page) {
  return page.getByRole('region', { name: 'Live' })
    .locator('button:not(.link, .field-help-btn, .toggle), select, input:not([type=checkbox]), summary, label:has(> input[type=checkbox])')
    .evaluateAll((els) => els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: e.getBoundingClientRect().height, what: e.getAttribute('aria-label') ?? e.textContent }))
      .filter((x) => x.h < 44))
}

test('live page on a phone: no sideways scroll, 44px targets', async ({ page }) => {
  const m = await mockLive(page)
  const live = await openLive(page)
  await live.getByText('Advanced').tap()
  await expect(live.getByLabel('Use GPU for Whisper', { exact: true })).toBeVisible()
  await noSideways(page)
  expect(await smallTargets(page)).toEqual([])

  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByRole('button', { name: 'Start', exact: true }).tap()
  m.state.status = 'running'
  m.state.message = 'Listening'
  m.state.cues = [cue(0), cue(1), cue(2), cue(3)]
  await expect(live.getByTestId('live-status')).toHaveText('Listening · 4 lines')
  await live.getByText('Advanced').tap()
  await noSideways(page)
  expect(await smallTargets(page)).toEqual([])
  await page.screenshot({ path: `${SCREENS}/phone-running.png`, fullPage: true })
  expect(m.unmocked).toEqual([])
})

test('live page on a phone: the model picker fits and is a 44px target', async ({ page }) => {
  const m = await mockLive(page, { ollama: true })
  const live = await openLive(page)
  await live.getByLabel('AI engine', { exact: true }).selectOption('ollama')
  await expect(live.getByLabel('Model', { exact: true })).toBeVisible()
  await noSideways(page)
  expect(await smallTargets(page)).toEqual([])
  await expect(live.getByLabel('Model', { exact: true })).toHaveValue('gemma4:12b')
  await live.getByLabel('Stream link', { exact: true }).fill('https://www.youtube.com/watch?v=abc')
  await live.getByRole('button', { name: 'Start', exact: true }).tap()
  await expect.poll(() => m.posts.length).toBe(1)
  expect(m.posts[0].body).toMatchObject({ engine: 'ollama', model: 'gemma4:12b' })
  await page.screenshot({ path: `${SCREENS}/phone-model.png`, fullPage: true })
  expect(m.unmocked).toEqual([])
})
