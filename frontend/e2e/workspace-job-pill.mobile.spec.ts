import { expect, test } from './fixtures'
import { serveJobs } from './workspaceJobPillMocks'

test('phone: pill is a 44px target, no sideways scroll, stage strip stays on screen', async ({ page }) => {
  const t = Math.floor(Date.now() / 1000)
  const jobs = {
    current: [{
      job_id: 'translate_3', status: 'running', progress: 0.42, message: '', error: null, description: null, gpu_touching: false,
      started_at: t, finished_at: null, updated_at: t, owned_by_me: true, drama_id: 3, kind: 'translate',
    }],
  }
  await serveJobs(page, jobs)
  await page.goto('/#/drama/3/source')
  const pill = page.getByTestId('job-pill')
  await expect(pill).toHaveText('● Translating 42%')
  const box = (await pill.boundingBox())!
  expect(box.height).toBeGreaterThanOrEqual(44)
  expect(box.x + box.width).toBeLessThanOrEqual(390)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  const tabs = await page.getByRole('navigation', { name: 'Stages' }).getByRole('link').all()
  expect(tabs).toHaveLength(5)
  for (const tab of tabs) {
    const b = (await tab.boundingBox())!
    expect(b.x).toBeGreaterThanOrEqual(0)
    expect(b.x + b.width).toBeLessThanOrEqual(390)
  }
  expect((await page.locator('.ws-strip').boundingBox())!.height).toBeLessThanOrEqual(112)
  await pill.tap()
  const panel = page.getByRole('region', { name: 'Running on this drama' })
  await expect(panel).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  expect((await panel.getByRole('button', { name: /^Cancel / }).boundingBox())!.height).toBeGreaterThanOrEqual(44)
})
