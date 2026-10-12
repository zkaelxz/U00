import { expect, test } from '@playwright/test'

import { guard, mockDiagnostics } from './diagnosticsInstallsMocks'

// "CUDA runtime for GPU Whisper" (Packages and installs): offered only when the libraries are
// missing; PC only; a refused plan shows the server's reason and points at the Packages list.

const status = (o: Record<string, unknown> = {}) => ({ missing: true, show: true, install: null, job: null, ...o })

test('missing libraries: the row offers a two-press install and shows the server reason when refused', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m, { jsFound: true })
  await page.route((u) => u.pathname === '/api/diagnostics/cuda-runtime', (r) => r.fulfill({ json: status() }))
  await page.route((u) => u.pathname === '/api/diagnostics/cuda-runtime/install', (r) => {
    m.sent.push(r.request())
    return r.fulfill({ status: 409, json: { detail: 'This would downgrade numpy. Confirm again to go ahead anyway.' } })
  })
  await page.goto('/#/diagnostics?card=cuda-runtime')
  const block = page.getByTestId('cuda-runtime-install')
  await expect(block).toContainText('CUDA libraries (cuBLAS 12 and cuDNN 9) weren')
  await block.getByRole('button', { name: 'Install CUDA runtime for GPU Whisper' }).click()
  expect(m.sent).toHaveLength(0)
  await block.getByRole('button', { name: 'Confirm install (about 1.2 GB)' }).click()
  await expect(block.getByRole('alert')).toContainText('Packages list')
  expect(m.sent.map((r) => new URL(r.url()).pathname)).toEqual(['/api/diagnostics/cuda-runtime/install'])
  expect(m.sent[0].postDataJSON()).toEqual({ confirm: true })
  expect(m.unmocked).toEqual([])
})

test('libraries found: no CUDA runtime row', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m, { jsFound: true })
  await page.route((u) => u.pathname === '/api/diagnostics/cuda-runtime', (r) =>
    r.fulfill({ json: status({ missing: false, show: false }) }))
  await page.goto('/#/diagnostics?card=packages')
  await expect(page.getByTestId('cuda-runtime-install')).toHaveCount(0)
})
