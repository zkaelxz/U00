import { expect, test } from '@playwright/test'

import { SHOTS_DIR } from './comicMocks'
import { mockScanlate } from './scanlateMocks'

test('phone: the Translate panel opens in a sheet, fits the width, 44 px targets', async ({ page }) => {
  const s = await mockScanlate(page, { pageCount: 3, lastPage: 1 })
  await page.goto('/#/comic/7?page=1')
  const open = page.getByRole('button', { name: 'Translate', exact: true })
  expect((await open.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await open.click()
  const panel = page.getByRole('region', { name: 'Translate pages' })
  await expect(panel).toBeVisible()
  const start = panel.getByRole('button', { name: 'Translate all pages' })
  expect((await start.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll).toBeLessThanOrEqual(client)
  const box = (await panel.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(390)
  await start.click()
  await expect(panel.getByTestId('job-status')).toHaveText(/done/)
  if (SHOTS_DIR) await page.screenshot({ path: `${SHOTS_DIR}/scanlate-phone.png` })
  expect(s.unmocked).toEqual([])
})
