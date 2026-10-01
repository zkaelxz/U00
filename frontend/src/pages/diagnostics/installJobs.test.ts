import { describe, expect, it } from 'vitest'

import type { DiagnosticsPackageUpdates } from '../../types/diagnostics'
import type { DiagnosticsDenoStatus, DiagnosticsUpgradeCheckState } from '../../types/diagnosticsInstalls'
import { canOfferDeno, denoNote, denoResultLine, showDeno } from './denoInstallText'
import { jobProgressLine, jobRunning } from './jobPoll'
import { strandedTest, testDetails, testLine } from './upgradeTestText'

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

const check = (o: Partial<DiagnosticsUpgradeCheckState> = {}): DiagnosticsUpgradeCheckState => ({
  package: 'edge_tts', target: '2.0.0', output_tail: [], result: null, job_id: 'upgrade_check', job: null, ...o,
})
const result = { ok: true, verdict: 'safe', reason: 'every test passed', version: '2.0.0',
  new_failures: [], preexisting_failures: [], conflicts: [] }

describe('Test first', () => {
  it('only for the same package and target', () => {
    expect(testLine(check({ job: running }), 'edge_tts', '2.0.0')).toEqual({ text: 'Testing edge_tts 2.0.0…', tone: 'muted' })
    expect(testLine(check({ job: running }), 'edge_tts', '2.1.0')).toBeNull()
    expect(testLine(check({ job: running }), 'jieba', '2.0.0')).toBeNull()
    expect(testLine(null, 'edge_tts', '2.0.0')).toBeNull()
  })

  it('verdict lines', () => {
    expect(testLine(check({ job: done, result }), 'edge_tts', '2.0.0'))
      .toEqual({ text: 'Safe to update: edge_tts 2.0.0: every test passed.', tone: 'ok' })
    expect(testLine(check({ job: done, result: { ...result, ok: false, verdict: 'broken', reason: '2 new failures' } }), 'edge_tts', '2.0.0')?.tone)
      .toBe('error')
    expect(testLine(check({ job: done, result: { ...result, ok: false, verdict: 'conflict' } }), 'edge_tts', '2.0.0')?.tone)
      .toBe('warn')
    expect(testLine(check({ job: { ...done, status: 'cancelled' } }), 'edge_tts', '2.0.0')?.text).toMatch(/cancelled/)
    expect(testLine(check({ job: { ...done, status: 'error' } }), 'edge_tts', '2.0.0')?.tone).toBe('error')
  })

  it('details keep only the non-empty lists', () => {
    const d = testDetails(check({
      result: { ...result, verdict: 'broken', new_failures: ['tests/a.py::t'] }, output_tail: ['last'],
    }))
    expect(d.map((x) => x.title)).toEqual(['Fail with the update, pass today', 'Last lines of output'])
  })
})

describe('Test first after leaving the page', () => {
  const updates = (target: string) => ({
    checked_at: 1, packages: { edge_tts: { target } },
  }) as unknown as DiagnosticsPackageUpdates

  it('a running or finished server test with no matching update row is shown on its own', () => {
    expect(strandedTest(check({ job: running }), null)).toEqual({ name: 'edge_tts', target: '2.0.0' })
    expect(strandedTest(check({ job: done, result }), null)).toEqual({ name: 'edge_tts', target: '2.0.0' })
    expect(strandedTest(check({ job: running }), updates('2.1.0'))).toEqual({ name: 'edge_tts', target: '2.0.0' })
  })

  it('stays out of the way when the package row already shows it, or there is no test', () => {
    expect(strandedTest(check({ job: running }), updates('2.0.0'))).toBeNull()
    expect(strandedTest(check({ package: null, target: null }), null)).toBeNull()
    expect(strandedTest(check(), null)).toBeNull()
    expect(strandedTest(null, null)).toBeNull()
  })
})
