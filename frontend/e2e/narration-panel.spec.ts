import { expect, test, type Page } from '@playwright/test'

// The narration chunk-and-tag panel on a novel's Media stage. The seeded
// title is made a novel by replacing its media type; the narration config and
// run endpoints are mocked so no LLM runs.

const job = (status: string) => ({
  job_id: 'fake-job', status, progress: 0.5, message: 'working', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1,
})

async function asNovel(page: Page) {
  await page.route('**/api/library/dramas/1', async (route) => {
    const res = await route.fetch()
    await route.fulfill({ response: res, json: { ...(await res.json()), media_type: 'novel', content_mode: 'novel_narration' } })
  })
}

test('narration chunk-and-tag needs a confirmation before replacing lines', async ({ page }) => {
  await asNovel(page)
  await page.route('**/api/narration/dramas/1/config', (route) =>
    route.fulfill({
      json: {
        drama_id: 1, is_narration: true, has_novel_source: true,
        engines: [{ key: 'fake', key_configured: true }], default_engine: 'fake',
        max_chunk_chars: 500, existing_line_count: 12, replaces_existing_lines: true, job_running: false,
      },
    }))
  const bodies: unknown[] = []
  await page.route('**/api/narration/dramas/1/run', async (route) => {
    bodies.push(route.request().postDataJSON())
    await route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'Already running.' } } })
  })
  await page.goto('/#/drama/1/source')
  await page.getByText('Chunk and tag speakers', { exact: true }).click()
  const start = page.getByRole('button', { name: 'Chunk and tag' })
  await expect(start).toBeDisabled()
  await page.getByLabel('Replace the 12 existing lines').check()
  await start.click()
  await expect(page.getByRole('alert').filter({ hasText: 'cannot be done right now' })).toBeVisible()
  expect(bodies[0]).toEqual({ engine: 'fake' })
})

test('narration Start over sends ?fresh=true only while it is on, and a resumed run says so', async ({ page }) => {
  await asNovel(page)
  await page.route('**/api/narration/dramas/1/config', (route) =>
    route.fulfill({
      json: {
        drama_id: 1, is_narration: true, has_novel_source: true,
        engines: [{ key: 'fake', key_configured: true }], default_engine: 'fake',
        max_chunk_chars: 500, existing_line_count: 0, replaces_existing_lines: false, job_running: false,
      },
    }))
  const posts: { url: string; body: unknown }[] = []
  // A regex, so the start is matched with or without its query string.
  await page.route(/\/api\/narration\/dramas\/1\/run(\?.*)?$/, async (route) => {
    posts.push({ url: route.request().url(), body: route.request().postDataJSON() })
    await route.fulfill({ json: { job_id: `fake-narration-${posts.length}` } })
  })
  let resumed = true
  await page.route('**/api/jobs/fake-narration-*', (route) =>
    route.fulfill({
      json: {
        ...job('done'), job_id: 'fake-narration', progress: 1,
        message: resumed ? 'Resuming: 3 of 8 chunks already tagged...' : 'Tagged 8 chunks',
      },
    }))
  await page.goto('/#/drama/1/source')
  await page.getByText('Chunk and tag speakers', { exact: true }).click()
  const startOver = page.getByRole('switch', { name: 'Start over' })
  const start = page.getByRole('button', { name: 'Chunk and tag' })
  await expect(startOver).toHaveAttribute('aria-checked', 'false')

  await start.click()
  await expect.poll(() => posts.length).toBe(1)
  expect(new URL(posts[0].url).search).toBe('')
  expect(posts[0].body).toEqual({ engine: 'fake' })
  await expect(page.getByTestId('job-status')).toContainText('Resuming: 3 of 8')
  await expect(page.getByTestId('job-note')).toHaveText(
    'Resuming an interrupted run. Use Start over to tag everything again.',
  )

  resumed = false
  await startOver.click()
  await expect(startOver).toHaveAttribute('aria-checked', 'true')
  await expect(start).toBeEnabled()
  await start.click()
  await expect.poll(() => posts.length).toBe(2)
  expect(new URL(posts[1].url).search).toBe('?fresh=true')
  expect(posts[1].body).toEqual({ engine: 'fake' })
  await expect(page.getByTestId('job-status')).toContainText('Tagged 8 chunks')
  await expect(page.getByTestId('job-note')).toHaveCount(0)
})
