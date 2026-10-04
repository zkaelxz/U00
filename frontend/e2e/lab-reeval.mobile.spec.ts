import { expect, test, type Page } from '@playwright/test'

import { mockReeval, overview } from './reevalMocks'
import { hitHeight, installHitArea } from './hitArea'

// .btn-sm keeps a 44px hit area but is 32px tall: measure the hit area, not the box.
test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the Model re-evaluation card with a mocked
// report (reevalMocks.ts; the e2e API's production model is Claude with no
// key). Checks the report stacks without sideways scroll, 44px targets, and
// that Promote still needs its second press. REEVAL_SHOTS_DIR=<dir> saves a
// screenshot of the card. Named lab-* to sort after diagnostics.spec.ts (see
// lab-benchmark.spec.ts).

const SHOTS = process.env.REEVAL_SHOTS_DIR

const card = (page: Page) => page.getByRole('region', { name: 'Model re-evaluation' })

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function smallTargets(page: Page) {
  return page.locator('.reeval button:not(.link):not(.field-help-btn):not(.toggle), .reeval summary, .reeval select, .reeval input, .reeval a.btn').evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent || e.getAttribute('aria-label') || '').trim().slice(0, 30) }))
      .filter(({ h }) => h < 44))
}

test('re-evaluation on a phone: stacked report, 44px targets, no sideways scroll, two-step promote', async ({ page }) => {
  const { calls, unmocked } = await mockReeval(page, overview({ withReport: true, error: 'Skipped: estimated $0.0400, above the $0.02 limit set for scheduled runs.' }))
  await page.goto('/#/benchmark')
  const c = card(page)
  await expect(c.getByTestId('reeval-production')).toContainText('Ollama · qwen3:8b')
  const rows = c.getByRole('list', { name: 'Candidates against production' })
  await expect(rows.locator(':scope > li')).toHaveCount(2)

  // The five figures sit two to a row inside the card.
  const stats = rows.locator(':scope > li').first().locator('.reeval-stats > div')
  const boxes = await stats.evaluateAll((els) => els.map((e) => e.getBoundingClientRect()))
  expect(boxes).toHaveLength(5)
  expect(boxes[0].top).toBe(boxes[1].top)
  expect(boxes[2].top).toBeGreaterThan(boxes[1].top)

  await c.locator('summary', { hasText: 'Schedule and golden set' }).click()
  await c.locator('summary', { hasText: 'Decision history' }).click()
  await c.scrollIntoViewIfNeeded()
  await noSideways(page)
  expect(await smallTargets(page)).toEqual([])
  // The schedule switch's hit area is 44px tall (its ::after), not the track.
  const toggle = c.getByRole('switch', { name: 'Re-evaluate on a schedule' })
  await expect(toggle).toBeVisible()

  if (SHOTS) await c.screenshot({ path: `${SHOTS}/reeval-phone.png` })

  // Promote on a phone: the first tap arms it; nothing is sent until the second.
  const qwen = rows.locator(':scope > li', { hasText: 'qwen2.5:14b' })
  await qwen.getByRole('button', { name: 'Promote Ollama · qwen2.5:14b to production' }).tap()
  expect(calls.filter((x) => x.path.endsWith('/promote'))).toEqual([])
  const confirm = qwen.getByRole('button', { name: 'Confirm: make Ollama · qwen2.5:14b production' })
  expect((await hitHeight(confirm))).toBeGreaterThanOrEqual(44)
  await noSideways(page)
  await confirm.tap()
  await expect(c.getByTestId('reeval-promote-status')).toHaveText('Ollama · qwen2.5:14b is now the production model.')
  expect(calls.find((x) => x.path.endsWith('/promote'))?.body).toEqual({ confirm: true, reason: '' })
  expect(unmocked).toEqual([])
})
