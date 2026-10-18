import { expect, test, type Page } from '@playwright/test'
import { mockJobsApi } from './jobsMenuMocks'
import { mockQueueApi } from './queueMocks'

// Library selection bar "Add to queue": real API for the Library list (titles this
// spec creates and deletes), /api/queue mocked. It only adds; nothing starts.

async function createDrama(page: Page, title: string): Promise<number> {
  const r = await page.request.post('/api/dramas', { data: { source_language: 'zh', title_en: title } })
  expect(r.ok()).toBeTruthy()
  return (await r.json()).id
}

test.describe('Library bar: Add to queue', () => {
  const ids: number[] = []

  test.afterAll(async ({ request }) => {
    for (const id of ids) await request.delete(`/api/dramas/${id}?confirm=true&confirm_text=DELETE`)
  })

  test('select three, add all steps, see the skip reason, find them in the Queue', async ({ page }) => {
    const titles = ['Queue E2E One', 'Queue E2E Two', 'Queue E2E Three']
    for (const t of titles) ids.push(await createDrama(page, t))
    await mockJobsApi(page, [], true)
    const mock = await mockQueueApi(page)
    mock.refuse[ids[2]] = 'Title not found.'
    await page.goto('/')
    await page.getByLabel('Search title or summary').fill('Queue E2E')
    await expect(page.getByTestId('drama-count')).toHaveText('3 titles')
    await page.getByRole('radio', { name: 'List' }).check()
    await page.getByRole('button', { name: 'Select', exact: true }).click()
    for (const t of titles) await page.getByRole('checkbox', { name: `Select ${t}` }).check()
    const bar = page.getByRole('region', { name: 'Selection' })
    await bar.getByRole('combobox', { name: 'Queue step' }).selectOption('all')
    await bar.getByRole('button', { name: 'Add to queue' }).click()
    const result = bar.getByTestId('queue-add-result')
    await expect(result).toContainText('Added 2 titles to the queue.')
    await expect(result).toContainText('Skipped: Queue E2E Three (Title not found)')
    expect(mock.addBodies).toEqual([{ drama_ids: expect.arrayContaining(ids), step: 'all' }])
    expect(mock.calls).not.toContain('/start')

    await result.getByRole('link', { name: 'Open the queue' }).click()
    const card = page.getByRole('region', { name: 'Queue' })
    await expect(card.getByTestId('queue-items').locator('> li')).toHaveCount(6)
  })
})
