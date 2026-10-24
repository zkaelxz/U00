import { expect, test } from '@playwright/test'

import { json } from './mockHelpers'
import { mockScanlate } from './scanlateMocks'

// The Comic page's Translate panel when things go wrong: a refused start, a
// job that fails part-way, and a settings read that fails. All /api calls are
// mocked (comicMocks + scanlateMocks); nothing may fall through.

async function openPanel(page: import('@playwright/test').Page) {
  await page.goto('/#/comic/7?page=1')
  await expect(page.getByTestId('comic-page-label')).toHaveText('Page 1 of 3')
  await page.getByRole('button', { name: 'Translate', exact: true }).click()
  const panel = page.getByRole('region', { name: 'Translate pages' })
  await expect(panel).toBeVisible()
  return panel
}

test('a refused start shows the server\'s reason, starts no job and leaves Translate usable', async ({ page }) => {
  const s = await mockScanlate(page, { pageCount: 3, lastPage: 1 })
  await page.route(/\/api\/scanlate\/dramas\/7\/run$/, (route) =>
    json(route, { error: { code: 'conflict', message: 'Another job is running for this title.' } }, 409))
  const panel = await openPanel(page)
  const run = panel.getByRole('button', { name: 'Translate all pages' })
  await run.click()
  await expect(panel.getByRole('alert')).toContainText('Another job is running for this title.')
  await expect(panel.getByTestId('job-panel')).toHaveCount(0)
  await expect(run).toBeEnabled()
  expect(s.jobPolls).toBe(0)
  expect(s.unmocked).toEqual([])
})

test('a run that fails shows why, keeps the original pages and lets you start again', async ({ page }) => {
  const s = await mockScanlate(page, { pageCount: 3, lastPage: 1 })
  await page.route(/\/api\/jobs\/scanlate_7$/, (route) => json(route, {
    job_id: 'scanlate_7', status: 'error', progress: 0.3, message: 'Page 1 of 3',
    error: 'CUDA out of memory on page 1.', description: null, gpu_touching: true,
    started_at: 1, finished_at: 2, updated_at: 2, result: null, outcome: null,
  }))
  const panel = await openPanel(page)
  const run = panel.getByRole('button', { name: 'Translate all pages' })
  await run.click()
  await expect(panel.getByTestId('job-status')).toHaveText('Failed')
  await expect(panel.getByTestId('job-panel')).toContainText('CUDA out of memory on page 1.')
  await expect(run).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Typeset' })).toBeDisabled()
  expect(s.runs).toHaveLength(1)
  expect(s.unmocked).toEqual([])
})

test('a failed settings read is shown and every run button stays off', async ({ page }) => {
  const s = await mockScanlate(page, { pageCount: 3, lastPage: 1 })
  await page.route(/\/api\/scanlate\/dramas\/7\/config$/, (route) =>
    json(route, { error: { code: 'internal', message: 'Boom.' } }, 500))
  const panel = await openPanel(page)
  await expect(panel.getByRole('alert')).toContainText('Could not load this. Try again.')
  await expect(panel.getByRole('button', { name: 'Translate all pages' })).toBeDisabled()
  await expect(panel.getByRole('button', { name: 'Redo page 1' })).toBeDisabled()
  expect(s.runs).toEqual([])
  expect(s.unmocked).toEqual([])
})
