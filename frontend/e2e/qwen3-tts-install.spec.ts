import { expect, test, type Page, type Request } from '@playwright/test'

import { guard, mockDiagnostics } from './diagnosticsInstallsMocks'

// Diagnostics > Packages and installs "Voice cloning: Qwen3-TTS": a server job started with a
// two-step confirm, polled for progress, then a result. Every endpoint is mocked; nothing is
// installed.

const JOB = 'voice_engine_install_qwen3_tts'
const row = (o: Record<string, unknown> = {}) => ({
  engine: 'qwen3_tts', label: 'Qwen3-TTS', installed: false, required_mb: 6000, refusal: null,
  job_id: JOB, job: null, last_result: null, ...o,
})

async function mockVoiceEngines(page: Page, sent: Request[]) {
  let step = -1
  await page.route((u) => u.pathname === '/api/diagnostics/voice-engines', (r) => {
    if (step >= 0) step += 1
    const engine = step < 0 ? row()
      : step <= 2 ? row({ job: { status: 'running', progress: 0.4, message: 'Installing PyTorch…', error: null } })
        : row({
          installed: true, refusal: 'Qwen3-TTS is already installed.',
          job: { status: 'done', progress: 1, message: '', error: null },
          last_result: { ok: true, message: 'Qwen3-TTS installed.', output_tail: ['done'] },
        })
    return r.fulfill({ json: { free_mb: 50_000, engines: [engine] } })
  })
  await page.route((u) => u.pathname === '/api/diagnostics/voice-engines/qwen3_tts/install', (r) => {
    sent.push(r.request())
    step = 0
    return r.fulfill({ json: { job_id: JOB, started: true } })
  })
}

test('install takes two presses, shows progress, then says it is installed', async ({ page }) => {
  const m = await guard(page)
  await mockDiagnostics(page, m, { jsFound: true })
  const sent: Request[] = []
  await mockVoiceEngines(page, sent)
  await page.goto('/#/diagnostics?card=qwen3-tts')
  const block = page.getByTestId('qwen3-tts-install')
  await expect(block).toContainText('Clone a voice locally. Installs into its own folder so it does not change your other packages.')
  await expect(block).toContainText('Qwen3-TTS needs about 5 GB of disk space and downloads its model on first use.')
  await block.getByRole('button', { name: 'Install Qwen3-TTS' }).click()
  expect(sent).toHaveLength(0) // first press only arms
  await block.getByRole('button', { name: 'Confirm install Qwen3-TTS (about 5 GB)' }).click()
  await expect(block.getByTestId('qwen3-tts-progress')).toContainText('40% · Installing PyTorch…')
  expect(sent[0].postDataJSON()).toEqual({ confirm: true })
  expect(sent[0].headers()['x-baihe-local']).toBe('1')
  await expect(block.getByTestId('qwen3-tts-result')).toContainText('Qwen3-TTS installed.', { timeout: 15_000 })
  await expect(block.getByTestId('qwen3-tts-status')).toHaveText('Qwen3-TTS is installed.')
  await expect(block.getByRole('button', { name: 'Install Qwen3-TTS' })).toHaveCount(0)
  expect(m.unmocked).toEqual([])
})
