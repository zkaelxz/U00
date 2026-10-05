import { expect, test, type Page } from '@playwright/test'

import { mockAssistant } from './assistantMocks'
import { navLink, openMenu } from './settingsNav'
import { installHitArea } from './hitArea'

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the Maintenance assistant. Every /api/assistant call is mocked.

const SHOTS = '/tmp/claude-0/-home-user-U00/780be93c-8b60-5332-b9fb-fd0d9036666f/scratchpad/shots'

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

// Visible buttons, selects, inputs, textareas and summaries are at least 44 px tall.
// (The Toggle's track is 24 px; its hit area is widened by CSS, checked below.)
async function tallTargets(page: Page) {
  const small = await page.locator('.assistant-page').evaluate((root) => {
    const sel = 'button:not(.link):not(.field-help-btn):not(.toggle), select, input, textarea, summary, a.btn'
    return [...root.querySelectorAll<HTMLElement>(sel)]
      .filter((e) => e.offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30) }))
      .filter((x) => x.h < 44)
  })
  expect(small).toEqual([])
}

test('phone: ask, tools, patch and backlog fit the screen with 44 px targets', async ({ page }) => {
  const s = await mockAssistant(page, { developerMode: true })
  await page.goto('/#/assistant')
  await openMenu(page)
  await expect(navLink(page, 'Assistant')).toBeVisible()
  const chat = page.getByRole('region', { name: 'Ask the assistant' })
  await chat.getByRole('textbox', { name: 'Question' }).fill('Why does dub skip lines?')
  await chat.getByRole('button', { name: 'Ask', exact: true }).click()
  await expect(chat.getByTestId('assistant-answer')).toBeVisible()
  await chat.getByText('Tools used (2)').click()
  await expect(chat.getByRole('list', { name: 'Tools used' }).getByRole('listitem')).toHaveCount(2)
  await expect(chat.getByRole('figure', { name: 'Proposed fix' })).toContainText('not applied')
  await page.getByRole('region', { name: 'Tools' }).getByText('What it can read').click()

  const card = page.getByRole('region', { name: 'Backlog' })
  await card.getByLabel('New item').fill('A very long backlog note that should wrap on a phone and never push the page sideways at all')
  await card.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(card.getByRole('list', { name: 'Backlog items', exact: true }).getByRole('listitem')).toHaveCount(2)
  await page.getByRole('region', { name: 'Changelog' }).getByLabel('From', { exact: true }).fill('v0.9')
  await page.getByRole('region', { name: 'Changelog' }).getByRole('button', { name: 'Generate' }).click()
  await expect(page.getByTestId('changelog-result')).toBeVisible()

  await noSideways(page)
  await tallTargets(page)
  await page.screenshot({ path: `${SHOTS}/assistant-phone.png`, fullPage: true })
  expect(s.unmocked).toEqual([])
})

test('phone: mode off shows only the switch, with a 44 px hit area', async ({ page }) => {
  await mockAssistant(page)
  await page.goto('/#/assistant')
  await expect(navLink(page, 'Assistant')).toHaveCount(0)
  const sw = page.getByRole('region', { name: 'Developer Mode is off' }).getByRole('switch', { name: 'Developer Mode' })
  const hit = await sw.evaluate((el) => parseFloat(getComputedStyle(el, '::after').height) || el.getBoundingClientRect().height)
  expect(hit).toBeGreaterThanOrEqual(44)
  await noSideways(page)
})
