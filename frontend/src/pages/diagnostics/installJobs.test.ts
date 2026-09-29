import { describe, expect, it } from 'vitest'

import type { DiagnosticsDenoStatus } from '../../types/diagnosticsInstalls'
import { canOfferDeno, denoNote, denoResultLine, showDeno } from './denoInstallText'
import { jobProgressLine, jobRunning } from './jobPoll'

const deno = (o: Partial<DiagnosticsDenoStatus> = {}): DiagnosticsDenoStatus => ({
  runtime_found: false, runtime_name: null, deno_on_path: false, deno_installed: false,
  can_install: true, install_method: 'download', job_id: 'deno_install', job: null, last_result: null, ...o,
})
const running = { status: 'running', progress: 0.42, message: 'Downloading Deno v2.9.7... 42%', error: null }
const done = { status: 'done', progress: 1, message: '', error: null }

describe('job progress', () => {
  it('running and the progress line', () => {
    expect(jobRunning(running)).toBe(true)
    expect(jobRunning({ ...running, status: 'queued' })).toBe(true)
    expect(jobRunning(done)).toBe(false)
    expect(jobRunning(null)).toBe(false)
    expect(jobProgressLine(running)).toBe('42% · Downloading Deno v2.9.7... 42%')
    expect(jobProgressLine({ ...running, progress: 0, message: '' })).toBe('Starting…')
  })
})

describe('Deno install block', () => {
  it('shows only when there is something to say', () => {
    expect(showDeno(null)).toBe(false)
    expect(showDeno(deno({ runtime_found: true }))).toBe(false)
    expect(showDeno(deno())).toBe(true)
    expect(showDeno(deno({ runtime_found: true, job: running }))).toBe(true)
  })

  it('offers the install only when it can run', () => {
    expect(canOfferDeno(deno())).toBe(true)
    expect(canOfferDeno(deno({ can_install: false }))).toBe(false)
    expect(canOfferDeno(deno({ deno_on_path: true }))).toBe(false)
    expect(canOfferDeno(deno({ deno_installed: true }))).toBe(false) // installed, needs a restart
    expect(canOfferDeno(deno({ job: running }))).toBe(false)
  })

  it('notes: installed but not on PATH, or no download for this system', () => {
    expect(denoNote(deno({ deno_installed: true }))).toMatch(/Restart Baihe/)
    expect(denoNote(deno({ can_install: false }))).toMatch(/yourself/)
    expect(denoNote(deno())).toBeNull()
    expect(denoNote(deno({ deno_installed: true, job: running }))).toBeNull()
  })

  it('result line by outcome', () => {
    const r = { ok: true, on_path: true, needs_restart: false, message: 'Deno installed.', output_tail: [] }
    expect(denoResultLine(deno({ last_result: r }))).toEqual({ text: 'Deno installed.', tone: 'ok' })
    expect(denoResultLine(deno({ last_result: { ...r, on_path: false, needs_restart: true, message: 'Restart.' } })))
      .toEqual({ text: 'Restart.', tone: 'warn' })
    expect(denoResultLine(deno({ last_result: { ...r, ok: false, message: 'Checksum mismatch.' } })))
      .toEqual({ text: 'Checksum mismatch.', tone: 'error' })
    expect(denoResultLine(deno({ last_result: r, job: running }))).toBeNull()
  })
})
