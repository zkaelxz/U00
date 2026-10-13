import { describe, expect, it, vi } from 'vitest'

import type { JobRecord } from '../types/jobs'
import {
  upsertJob, isFinished, orderJobs,
  describeGpu, formatDuration, formatSeconds, hasActiveJobs, jobBaseName, jobDetail, jobStatusLabel, jobTitledName,
  plainJobText, pollWhenSettled, shouldRefreshGpu, splitDependencies,
} from './diagnosticsFormat'

const job = (o: Partial<JobRecord>): JobRecord => ({
  job_id: 'j', status: 'done', progress: null, message: '', error: null, description: null,
  gpu_touching: false, started_at: 100, finished_at: null, updated_at: 0, ...o,
})

const gpu = { name: null, vram_used_gb: null, vram_total_gb: null, torch_cuda_version: null, message: null }

describe('diagnosticsFormat', () => {
  it('splits and sorts dependencies', () => {
    const r = splitDependencies({
      b: { installed: false, powers: 'x', tier: 't' },
      a: { installed: true, powers: 'y', tier: 't' },
    })
    expect(r.installed.map((d) => d.name)).toEqual(['a'])
    expect(r.missing.map((d) => d.name)).toEqual(['b'])
  })
  it('detects active jobs', () => {
    expect(hasActiveJobs([job({ status: 'done' })])).toBe(false)
    expect(hasActiveJobs([job({ status: 'queued' })])).toBe(true)
  })
  it('formats durations', () => {
    expect(formatDuration(job({ started_at: null }), 0)).toBe('not started')
    expect(formatDuration(job({ finished_at: 142 }), 999)).toBe('42s')
    expect(formatDuration(job({}), 100 + 185)).toBe('3m 05s')
    expect(formatDuration(job({}), 100 + 3720)).toBe('1h 02m')
  })
  it('formats plain seconds', () => {
    expect(formatSeconds(-3)).toBe('0s')
    expect(formatSeconds(65.7)).toBe('1m 05s')
  })
  it('shows progress text only while a job runs, and the error when it failed', () => {
    expect(jobDetail(job({ status: 'running', message: 'Transcribing... 40%' }))).toBe('Transcribing... 40%')
    expect(jobDetail(job({ status: 'queued', message: 'Waiting' }))).toBe('Waiting')
    expect(jobDetail(job({ status: 'running', message: '' }))).toBeNull()
    expect(jobDetail(job({ status: 'done', message: 'Transcribing... 99%' }))).toBeNull()
    expect(jobDetail(job({ status: 'cancelled', message: 'Batch 2 of 5' }))).toBeNull()
    expect(jobDetail(job({ status: 'cancelled', message: 'Starting...', error: 'Interrupted: Baihe restarted while this was running.' }))).toBe('Interrupted: Baihe restarted while this was running.')
    expect(jobDetail(job({ status: 'error', message: 'Batch 2 of 5', error: 'Timed out' }))).toBe('Timed out')
    expect(jobDetail(job({ status: 'error', message: 'Batch 2 of 5', error: null }))).toBe('Batch 2 of 5')
  })
  it('describes the GPU', () => {
    expect(describeGpu({ ...gpu, available: false })).toBe('No GPU detected.')
    expect(describeGpu({ ...gpu, available: true, name: 'X', vram_used_gb: 1, vram_total_gb: 8 })).toBe('X (GPU memory in use (all programs): 1.0 / 8.0 GB)')
    expect(describeGpu({ ...gpu, available: true, name: 'X', vram_used_gb: null, vram_total_gb: 8, message: 'GPU memory in use: not available (nvidia-smi could not be read).' }))
      .toBe('X (GPU memory in use: not available (nvidia-smi could not be read).)')
  })
  it('refreshes the GPU reading only while visible with a GPU job active', () => {
    const jobs = (status: string, gpu_touching: boolean) => [job({ job_id: 'a', status, gpu_touching })]
    expect(shouldRefreshGpu(jobs('running', true), false)).toBe(true)
    expect(shouldRefreshGpu(jobs('queued', true), false)).toBe(true)
    expect(shouldRefreshGpu(jobs('running', true), true)).toBe(false)
    expect(shouldRefreshGpu(jobs('running', false), false)).toBe(false)
    expect(shouldRefreshGpu(jobs('done', true), false)).toBe(false)
    expect(shouldRefreshGpu(null, false)).toBe(false)
  })
})

describe('upsertJob', () => {
  const j = (job_id: string, status = 'running') => ({
    job_id, status, progress: null, message: '', error: null, description: null,
    gpu_touching: false, started_at: null, finished_at: null, updated_at: 0,
  }) as JobRecord
  it('replaces a known job in place and puts a new one first', () => {
    const list = [j('a'), j('b')]
    expect(upsertJob(list, j('b', 'done')).map((x) => `${x.job_id}:${x.status}`)).toEqual(['a:running', 'b:done'])
    expect(upsertJob(list, j('c')).map((x) => x.job_id)).toEqual(['c', 'a', 'b'])
    expect(list.map((x) => x.status)).toEqual(['running', 'running'])
  })
  it('orders active jobs first, keeping the order within each group', () => {
    const jobs = [job({ job_id: 'a' }), job({ job_id: 'b', status: 'running' }), job({ job_id: 'c', status: 'error' }), job({ job_id: 'd', status: 'queued' })]
    expect(orderJobs(jobs).map((j) => j.job_id)).toEqual(['b', 'd', 'a', 'c'])
  })
  it('knows finished statuses', () => {
    expect(['done', 'error', 'cancelled'].every(isFinished)).toBe(true)
    expect(isFinished('running') || isFinished('queued')).toBe(false)
  })
})

describe('job wording', () => {
  it('reads a done job whose outcome failed as Failed', () => {
    expect(jobStatusLabel(job({ status: 'done', outcome: 'failed' }))).toBe('Failed')
    expect(jobStatusLabel(job({ status: 'done', outcome: 'cancelled' }))).toBe('Cancelled')
    expect(jobStatusLabel(job({ status: 'done', outcome: 'ok' }))).toBe('Done')
    expect(jobStatusLabel(job({ status: 'running' }))).toBe('Running')
  })

  it('words a pending cancel by the job kind', () => {
    const cancelling = 'Cancelling... (finishes the current step first)'
    expect(jobDetail({ job_id: 'dub_2', status: 'running', message: cancelling, error: null })).toBe('Cancelling... Stops at once.')
    expect(jobDetail({ job_id: 'translate_2', status: 'running', message: cancelling, error: null })).toContain('AI request')
    expect(jobDetail({ status: 'running', message: cancelling, error: null })).toBe(cancelling)
  })

  it('names a title job by its title and points pip installs at Diagnostics', () => {
    expect(jobBaseName({ job_id: 'transcribe_1', description: 'Transcription (drama #1)' })).toBe('Transcription')
    expect(jobTitledName({ job_id: 'transcribe_1', description: 'Transcription (drama #1)', drama_id: 1 }, new Map([[1, 'Moonlit']]))).toBe('Transcription - Moonlit')
    expect(plainJobText('Run pip install faster-whisper to use it')).not.toContain('pip install')
  })

  it('reads a job closed by a restart as Interrupted, not Cancelled', () => {
    expect(jobStatusLabel(job({ status: 'cancelled', outcome: 'interrupted' }))).toBe('Interrupted')
    expect(jobStatusLabel(job({ status: 'cancelled', outcome: 'cancelled' }))).toBe('Cancelled')
  })

  it('points a pip instruction at Diagnostics > Packages and installs', () => {
    expect(plainJobText('Run `pip install ebooklib` to export EPUB.')).toBe(
      'Open Diagnostics > Packages and installs (ebooklib) to export EPUB.')
    expect(plainJobText('Audio emotion tags need: pip install funasr')).toBe(
      'Audio emotion tags need: Diagnostics > Packages and installs (funasr)')
    expect(plainJobText('Needs `pip install genanki` for .apkg export')).toBe(
      'Needs Diagnostics > Packages and installs (genanki) for .apkg export')
    expect(plainJobText('pip install "transformers>=5.15" torch')).toBe('Diagnostics > Packages and installs (transformers)')
    expect(plainJobText('Nothing to install here.')).toBe('Nothing to install here.')
  })

  it('says title, not drama', () => {
    expect(plainJobText('Transcription (drama #1)')).toBe('Transcription (title #1)')
    expect(plainJobText('No lines in this drama. Dramas differ.')).toBe('No lines in this title. Titles differ.')
  })

  it('names a job without its title reference', () => {
    expect(jobBaseName({ job_id: 'j', description: 'Transcription (title #1)' })).toBe('Transcription')
    expect(jobBaseName({ job_id: 'j', description: 'Transcription (drama #12)' })).toBe('Transcription')
    expect(jobBaseName({ job_id: 'j', description: 'Re-transcribing 3 lines (title #4)' })).toBe('Re-transcribing 3 lines')
    expect(jobBaseName({ job_id: 'j', description: null })).toBe('j')
    expect(jobBaseName({ job_id: 'j', description: '(title #4)' })).toBe('(title #4)')
  })

  it('adds the title name where the menu has room for one label', () => {
    const titles = new Map([[1, 'Signal']])
    expect(jobTitledName({ job_id: 'j', description: 'Transcription (title #1)', drama_id: 1 }, titles)).toBe('Transcription - Signal')
    expect(jobTitledName({ job_id: 'j', description: 'Transcription (title #9)', drama_id: 9 }, titles)).toBe('Transcription')
    expect(jobTitledName({ job_id: 'j', description: 'URL download', drama_id: null }, titles)).toBe('URL download')
    expect(jobTitledName({ job_id: 'j', description: 'Translate Signal', drama_id: 1 }, titles)).toBe('Translate Signal')
  })
})

describe('pollWhenSettled', () => {
  it('starts the next wait only after the previous request settles, so requests never overlap', async () => {
    vi.useFakeTimers()
    let release: () => void = () => {}
    const tick = vi.fn(() => new Promise<void>((resolve) => { release = resolve }))
    const stop = pollWhenSettled(tick, 3000)
    await vi.advanceTimersByTimeAsync(3000)
    expect(tick).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(30000)      // still pending: no second request
    expect(tick).toHaveBeenCalledTimes(1)
    release()
    await vi.advanceTimersByTimeAsync(3000)
    expect(tick).toHaveBeenCalledTimes(2)
    stop()
    release()
    await vi.advanceTimersByTimeAsync(30000)
    expect(tick).toHaveBeenCalledTimes(2)
    vi.useRealTimers()
  })
  it('keeps going after a failed request', async () => {
    vi.useFakeTimers()
    const tick = vi.fn(() => Promise.reject(new Error('down')))
    const stop = pollWhenSettled(tick, 1000)
    await vi.advanceTimersByTimeAsync(3500)
    expect(tick).toHaveBeenCalledTimes(3)
    stop()
    vi.useRealTimers()
  })
})
