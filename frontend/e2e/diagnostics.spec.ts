import { expect, test } from '@playwright/test'

// The page is not routed yet (the lead wires #/diagnostics), so this checks
// the contract the page depends on through the real proxy + API: the
// overview shape, an empty job list on the seeded library, and the 404 a
// Cancel click on an unknown job must turn into an error banner.
test('diagnostics and jobs endpoints match what the page reads', async ({ request }) => {
  const diag = await (await request.get('/api/diagnostics')).json()
  expect(typeof diag.dependencies).toBe('object')
  expect(typeof diag.library_writable).toBe('boolean')
  expect(diag.gpu).toHaveProperty('available')

  const jobs = await (await request.get('/api/jobs')).json()
  expect(jobs).toEqual({ items: [], count: 0 })

  const cancel = await request.post('/api/jobs/nope/cancel', { headers: { 'X-Baihe-Local': '1' } })
  expect(cancel.status()).toBe(404)
  expect((await cancel.json()).error.code).toBe('not_found')
})
