import { describe, expect, it } from 'vitest'

import type { DiagnosticsCudaRuntimeStatus } from '../../types/diagnosticsInstalls'
import { cudaRuntimeResultLine } from './cudaRuntimeText'

const base: DiagnosticsCudaRuntimeStatus = { missing: true, show: true, install: null, job: null }
const install = (ok: boolean, hint: string | null = null): DiagnosticsCudaRuntimeStatus['install'] => ({
  kind: 'cuda_runtime', package: 'nvidia-cublas-cu12', job_id: 'dependency_install', job: null,
  result: { package: 'nvidia-cublas-cu12', ok, output_tail: [], hint, cancelled: false, variant: null, verify: null },
})

describe('cudaRuntimeResultLine', () => {
  it('says nothing before any install', () => {
    expect(cudaRuntimeResultLine(base)).toBeNull()
  })
  it('reports a failed install with its hint', () => {
    expect(cudaRuntimeResultLine({ ...base, install: install(false, 'pip failed') })).toEqual({ text: 'pip failed', tone: 'error' })
  })
  it('reports success once the libraries are found', () => {
    expect(cudaRuntimeResultLine({ ...base, missing: false, install: install(true) })?.tone).toBe('ok')
  })
})
