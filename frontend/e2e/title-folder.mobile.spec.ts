import { expect, test } from './fixtures'

// Under a background service the "open it yourself" notice must stay readable at phone width: Dismiss on one line,
// the path box and Copy button inside the viewport, no sideways scroll.

test('the Open folder notice fits a phone: Dismiss stays on one line and nothing overflows', async ({ page }) => {
  await page.route(/\/api\/dramas\/3\/folder\/open$/, (route) =>
    route.fulfill({ json: { opened: false, folder: 'C:\\Users\\Jellyfin\\AppData\\Local\\Baihe\\library\\dramas\\3' } }))
  await page.goto('/#/drama/3')
  await expect(page.getByTestId('drama-title')).toBeVisible()
  const open = page.getByRole('button', { name: 'Open folder' })
  if (!(await open.isVisible())) await page.getByRole('button', { name: 'Title actions' }).click()
  await open.click()
  const notice = page.getByRole('status').filter({ hasText: 'Open this folder yourself' })
  await expect(notice).toBeVisible()
  const vw = page.viewportSize()!.width
  const box = (await notice.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(vw)
  const dismiss = notice.getByRole('button', { name: 'Dismiss' })
  expect((await dismiss.boundingBox())!.height).toBeLessThan(40)
  await expect(notice.getByLabel('Folder path')).toBeVisible()
  await expect(notice.getByRole('button', { name: 'Copy path' })).toBeVisible()
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
  await dismiss.click()
  await expect(notice).toHaveCount(0)
})
