import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { DiagnosticsDependencyInstallState } from '../../types/diagnosticsInstalls'

const getDependencyInstall = vi.fn()
vi.mock('../../api/diagnosticsInstalls', () => ({
  getDependencyInstall: (...a: unknown[]) => getDependencyInstall(...a),
}))

import { cancelledText, followInstallJob, runInstallJob } from './installJob'

const job = (status: string, progress = 0.5, message = 'Downloading') => ({ status, progress, message, error: null })
const result = (o: Record<string, unknown> = {}) => ({
  package: 'jieba', ok: true, output_tail: ['Successfully installed'], hint: null, cancelled: false,
  variant: null, verify: null, ...o,
})
const state = (o: Partial<DiagnosticsDependencyInstallState>): DiagnosticsDependencyInstallState => ({
  kind: 'package', package: 'jieba', result: null, job_id: 'dependency_install', job: null, ...o,
})
const noWait = () => Promise.resolve()

beforeEach(() => {
  getDependencyInstall.mockReset()
})

describe('followInstallJob', () => {
  it('reports progress while the job runs, then returns its result', async () => {
    getDependencyInstall
      .mockResolvedValueOnce(state({ job: job('running', 0.1, 'Collecting jieba') }))
      .mockResolvedValueOnce(state({ job: job('running', 0.6, 'Downloading') }))
      .mockResolvedValueOnce(state({ job: job('done', 1), result: result() }))
    const seen: string[] = []
    const out = await followInstallJob((j) => seen.push(j.message), 'jieba', 1, noWait)
    expect(seen).toEqual(['Collecting jieba', 'Downloading'])
    expect(out.ok).toBe(true)
  })

  it('returns the cancelled result when Cancel ended it', async () => {
    getDependencyInstall
      .mockResolvedValueOnce(state({ job: job('running') }))
      .mockResolvedValueOnce(state({ job: job('cancelled'), result: result({ ok: false, cancelled: true }) }))
    const out = await followInstallJob(undefined, 'jieba', 1, noWait)
    expect(out).toMatchObject({ ok: false, cancelled: true })
  })

  it('makes a failed result when the job ended with none stored', async () => {
    getDependencyInstall.mockResolvedValueOnce(state({ job: { ...job('error'), error: 'The install did not finish' } }))
    expect(await followInstallJob(undefined, 'jieba', 1, noWait)).toMatchObject({
      ok: false, cancelled: false, package: 'jieba', hint: 'The install did not finish',
    })
    getDependencyInstall.mockResolvedValueOnce(state({ job: job('cancelled'), package: null }))
    expect(await followInstallJob(undefined, 'torch', 1, noWait)).toMatchObject({ cancelled: true, package: 'torch' })
    getDependencyInstall.mockResolvedValueOnce(state({ job: null }))
    expect((await followInstallJob(undefined, 'x', 1, noWait)).ok).toBe(false)
  })

  it('rides out a few dropped polls', async () => {
    getDependencyInstall
      .mockImplementationOnce(async () => { throw new Error('net') })
      .mockImplementationOnce(async () => { throw new Error('net') })
      .mockResolvedValueOnce(state({ job: job('done'), result: result() }))
    expect((await followInstallJob(undefined, 'jieba', 1, noWait)).ok).toBe(true)
  })

  it('gives up after five dropped polls in a row', async () => {
    getDependencyInstall.mockImplementation(async () => { throw new Error('gone') })
    await expect(followInstallJob(undefined, 'jieba', 1, noWait)).rejects.toThrow('gone')
    expect(getDependencyInstall).toHaveBeenCalledTimes(5)
  })

  it('waits the poll interval between polls', async () => {
    getDependencyInstall
      .mockResolvedValueOnce(state({ job: job('running') }))
      .mockResolvedValueOnce(state({ job: job('done'), result: result() }))
    const wait = vi.fn(noWait)
    await followInstallJob(undefined, 'jieba', 1234, wait)
    expect(wait).toHaveBeenCalledExactlyOnceWith(1234)
  })
})

describe('runInstallJob', () => {
  it('starts the job, then follows it', async () => {
    const order: string[] = []
    const start = vi.fn(async () => {
      order.push('start')
      return { job_id: 'dependency_install', started: true }
    })
    getDependencyInstall.mockImplementation(async () => {
      order.push('poll')
      return state({ job: job('done'), result: result() })
    })
    expect((await runInstallJob(start, undefined, 'jieba', 1, noWait)).ok).toBe(true)
    expect(order).toEqual(['start', 'poll'])
  })

  it('a refused start throws and polls nothing', async () => {
    const refused = Object.assign(new Error('busy'), { status: 409 })
    await expect(runInstallJob(() => Promise.reject(refused), undefined, 'jieba', 1, noWait)).rejects.toBe(refused)
    expect(getDependencyInstall).not.toHaveBeenCalled()
  })
})

it('words a cancelled install', () => {
  expect(cancelledText('torch')).toBe('Cancelled installing torch.')
})
