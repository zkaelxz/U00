import { expect, test } from './fixtures'

// "Open folder" on a title: the title page and the Library details sheet send
// only the title id; from another device the button is not shown. The open
// itself is mocked so the test never starts a file manager.

test('Open folder on the title page and in Library details posts the title id', async ({ page }) => {
  const opened: string[] = []
  await page.route(/\/api\/dramas\/\d+\/folder\/open$/, (route) => {
    opened.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`)
    return route.fulfill({ json: { opened: true } })
  })
  await page.goto('/#/drama/3')
  await expect(page.getByTestId('drama-title')).toHaveText('Signal')
  await page.getByRole('button', { name: 'Open folder' }).click()
  await expect.poll(() => opened).toEqual(['POST /api/dramas/3/folder/open'])

  await page.goto('/#/library')
  await page.getByRole('button', { name: 'Details: Signal' }).click()
  await page.getByRole('dialog', { name: 'Signal' }).getByRole('button', { name: 'Open folder' }).click()
  await expect.poll(() => opened).toHaveLength(2)
})

test('from another device the title has no Open folder button', async ({ page }) => {
  await page.route(/\/api\/meta$/, (route) =>
    route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'test', local: false } }))
  await page.goto('/#/drama/3')
  await expect(page.getByTestId('drama-title')).toHaveText('Signal')
  await expect(page.getByRole('button', { name: 'Read' }).or(page.getByRole('link', { name: 'Read' }))).toBeVisible()
  await expect(page.getByRole('button', { name: 'Open folder' })).toHaveCount(0)
})
