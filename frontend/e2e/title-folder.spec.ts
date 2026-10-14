import { expect, test } from './fixtures'

// "Open folder" on a title: the title page and the Library details sheet call
// the real PC-only route with only the title id (the e2e server stubs out the
// file manager itself); from another device the button is not shown.

test('Open folder on the title page and in Library details reaches the real route', async ({ page }) => {
  await page.goto('/#/drama/3')
  await expect(page.getByTestId('drama-title')).toHaveText('Signal')
  const fromTitle = page.waitForResponse((r) => r.url().endsWith('/api/dramas/3/folder/open'))
  await page.getByRole('button', { name: 'Open folder' }).click()
  const r1 = await fromTitle
  expect(r1.request().method()).toBe('POST')
  expect(r1.status()).toBe(200)
  expect(await r1.json()).toEqual({ opened: true })
  await expect(page.getByRole('alert')).toHaveCount(0)

  await page.goto('/#/library')
  await page.getByRole('button', { name: 'Details: Signal' }).click()
  const fromDetails = page.waitForResponse((r) => r.url().endsWith('/api/dramas/3/folder/open'))
  await page.getByRole('dialog', { name: 'Signal' }).getByRole('button', { name: 'Open folder' }).click()
  expect((await fromDetails).status()).toBe(200)
})

test('from another device the title has no Open folder button', async ({ page }) => {
  await page.route(/\/api\/meta$/, (route) =>
    route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'test', local: false } }))
  await page.goto('/#/drama/3')
  await expect(page.getByTestId('drama-title')).toHaveText('Signal')
  await expect(page.getByRole('link', { name: 'Read' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Open folder' })).toHaveCount(0)
})
