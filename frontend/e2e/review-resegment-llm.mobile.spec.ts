import { expect, test, type Page } from '@playwright/test'

import { PREVIEW, clearLines, mockAiResegment, openAiStructure, seedLines } from './resegmentLlmMocks'

// Phone project (390x844, touch), parity R47: the AI re-segmentation preview
// fits the width with 44px targets, and a long list folds to the first
// eight with "and N more".

test.beforeEach(() => seedLines())
test.afterAll(() => clearLines())

async function expectNoHorizontalOverflow(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function expectTouchTargets(page: Page, selector: string, min = 1) {
  const sizes = await page.locator(selector).evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null).map((e) => [e.getBoundingClientRect().height, e.outerHTML.slice(0, 80)] as const),
  )
  expect(sizes.length).toBeGreaterThanOrEqual(min)
  for (const [h, html] of sizes) expect(h, html).toBeGreaterThanOrEqual(44)
}

const long = '这是一句非常非常长的台词需要在手机屏幕上自动换行而且不能让页面横向滚动'
const many = Array.from({ length: 10 }, (_, i) => ({
  line_id: 200 + i,
  idx: i,
  zh: long,
  pieces: [long.slice(0, 18), long.slice(18)],
}))

test('the AI preview fits a phone and folds a long list', async ({ page }) => {
  await mockAiResegment(page, { preview: { ...PREVIEW, changed: many, line_count_after: 13 } })
  const group = await openAiStructure(page)
  await expectTouchTargets(page, '[aria-label="Structure"] button:not(.field-help-btn, .toggle), [aria-label="Structure"] summary', 3)
  await group.getByRole('button', { name: 'Preview with AI' }).tap()

  const shown = page.getByTestId('resegment-ai-preview')
  const items = shown.getByRole('list', { name: 'Proposed splits' }).locator(':scope > li')
  await expect(items).toHaveCount(8)
  const more = shown.getByRole('button', { name: 'and 2 more — show all' })
  await expect(more).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await expectTouchTargets(page, '[data-testid="resegment-ai-preview"] button', 2)
  await more.tap()
  await expect(items).toHaveCount(10)
  await expect(shown.getByRole('button', { name: 'Show fewer' })).toBeVisible()

  await shown.getByLabel('Type resegment to confirm').fill('resegment')
  await expectTouchTargets(page, '[data-testid="resegment-ai-preview"] button, [data-testid="resegment-ai-preview"] input', 3)
  await expectNoHorizontalOverflow(page)
})
