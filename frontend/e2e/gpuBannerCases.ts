import { expect, test, type Page } from '@playwright/test'

import { openSection } from './diagnosticsInstallsMocks'
import { mockTranscription } from './transcriptionMissingMocks'

// "Turn on GPU": shown only with an NVIDIA GPU, a CUDA torch and the setting off;
// the click saves use_gpu through POST /api/settings. Everything is mocked, and a
// catch-all refuses any other non-GET /api call.

const CU128 = {
  variant: 'cu128', label: 'CUDA 12.8', index_url: 'https://download.pytorch.org/whl/cu128',
  versions: { torch: '2.11.0', torchvision: '0.26.0', torchaudio: '2.11.0' }, needs_nvidia: true,
}
const torchStatus = (o: { found?: boolean; build?: 'cuda' | 'cpu' } = {}) => ({
  nvidia: { found: o.found ?? true, gpu_name: 'NVIDIA GeForce RTX 3080 Ti', driver_version: '581.42', status: 'ok', recommended: '570.65', minimum: '528.33' },
  installed: [{ name: 'torch', version: '2.11.0', build: o.build ?? 'cuda' }],
  problems: [], state: 'recommended', python_supported: true, recommended: CU128, variants: [CU128], probe: null,
})

async function open(page: Page, o: { useGpu: boolean; torch?: ReturnType<typeof torchStatus>; local?: boolean; to?: string }) {
  const { sent } = await mockTranscription(page, true)
  const saved: unknown[] = []
  let useGpu = o.useGpu
  await page.route((u) => u.pathname === '/api/diagnostics/gpu-torch', (r) => r.fulfill({ json: o.torch ?? torchStatus() }))
  await page.route((u) => u.pathname === '/api/settings', async (r) => {
    if (r.request().method() === 'POST') {
      saved.push(r.request().postDataJSON())
      useGpu = true
    }
    const resp = await r.fetch({ method: 'GET' })
    return r.fulfill({ response: resp, json: { ...(await resp.json()), use_gpu: useGpu } })
  })
  if (o.local === false) {
    await page.route('**/api/meta', async (route) => {
      const resp = await route.fetch()
      await route.fulfill({ response: resp, json: { ...(await resp.json()), local: false } })
    })
  }
  await page.goto(o.to ?? '/#/drama/1/source?tab=transcribe')
  return { saved, sent }
}

export function registerGpuBannerCases(prefix: string) {
  test.afterEach(async ({ page }) => {
    await page.unrouteAll({ behavior: 'ignoreErrors' })
  })

  test(`${prefix}Transcribe: Turn on GPU saves the setting and hides the banner`, async ({ page }) => {
    const { saved, sent } = await open(page, { useGpu: false })
    const banner = page.getByTestId('gpu-banner')
    await expect(banner).toContainText('GPU PyTorch is installed')
    await expect(page.getByTestId('gpu-note')).toContainText('GPU: off')
    await page.getByTestId('before-you-run').locator('summary').click()
    await banner.getByRole('button', { name: 'Turn on GPU' }).click()
    await expect(banner).toHaveCount(0)
    expect(saved).toEqual([{ use_gpu: true }])
    await expect(page.getByTestId('gpu-note')).toContainText('GPU: on')
    expect(sent).toEqual([])
  })

  test(`${prefix}Transcribe: no banner when the GPU is already on`, async ({ page }) => {
    await open(page, { useGpu: true })
    await expect(page.getByTestId('gpu-note')).toContainText('GPU: on')
    await expect(page.getByTestId('gpu-banner')).toHaveCount(0)
  })

  test(`${prefix}Transcribe: no banner for CPU-only torch or without an NVIDIA GPU`, async ({ page }) => {
    await open(page, { useGpu: false, torch: torchStatus({ build: 'cpu' }) })
    await expect(page.getByTestId('gpu-note')).toContainText('GPU: off')
    await expect(page.getByTestId('gpu-banner')).toHaveCount(0)
    await page.unrouteAll({ behavior: 'ignoreErrors' })
    await open(page, { useGpu: false, torch: torchStatus({ found: false }) })
    await expect(page.getByTestId('gpu-note')).toContainText('GPU: off')
    await expect(page.getByTestId('gpu-banner')).toHaveCount(0)
  })

  test(`${prefix}Transcribe: away from the main PC it says where to turn it on, with no button`, async ({ page }) => {
    await open(page, { useGpu: false, local: false })
    await expect(page.getByTestId('gpu-banner')).toContainText('done on the main PC')
    await expect(page.getByTestId('gpu-banner').getByRole('button')).toHaveCount(0)
  })

  test(`${prefix}Diagnostics GPU card: Turn on GPU saves the setting`, async ({ page }) => {
    const { saved } = await open(page, { useGpu: false, to: '/#/diagnostics?card=packages' })
    await openSection(page, /^Packages/)
    // A healthy PyTorch leaves the fold closed.
    await page.locator('summary', { hasText: /^GPU PyTorch/ }).click()
    const card = page.getByTestId('gpu-torch')
    await expect(card).toBeVisible()
    const banner = card.getByTestId('gpu-banner')
    await banner.getByRole('button', { name: 'Turn on GPU' }).click()
    await expect(banner).toHaveCount(0)
    expect(saved).toEqual([{ use_gpu: true }])
  })
}
