import { describe, expect, it } from 'vitest'

import type { FfmpegInstallStatus } from '../../types/ffmpegInstall'
import { canOfferFfmpeg, ffmpegResultLine, ffmpegStatusLine, showFfmpeg } from './ffmpegInstallText'

const status = (o: Partial<FfmpegInstallStatus> = {}): FfmpegInstallStatus => ({
  found: false, libass: null, app_ffmpeg_installed: false, version: '9.0.2', download_mb: 109, free_mb: 5000,
  required_mb: 500, refusal: null, job_id: 'ffmpeg_install', job: null, last_result: null, ...o,
})
const running = { status: 'running', progress: 0.4, message: 'Downloading ffmpeg…', error: null }

describe('Install ffmpeg card', () => {
  it('shows only when ffmpeg is missing, or there is a job or result', () => {
    expect(showFfmpeg(null)).toBe(false)
    expect(showFfmpeg(status())).toBe(true)
    expect(showFfmpeg(status({ found: true }))).toBe(false)
    expect(showFfmpeg(status({ found: true, job: running }))).toBe(true)
    expect(showFfmpeg(status({ found: true, last_result: { ok: true, message: 'x' } }))).toBe(true)
  })

  it('offers the install only when the server has no refusal and nothing runs', () => {
    expect(canOfferFfmpeg(status())).toBe(true)
    expect(canOfferFfmpeg(status({ refusal: 'This download is for Windows.' }))).toBe(false)
    expect(canOfferFfmpeg(status({ job: running }))).toBe(false)
  })

  it('says what is installed or why not, and nothing while a job runs', () => {
    expect(ffmpegStatusLine(status())).toBeNull()
    expect(ffmpegStatusLine(status({ app_ffmpeg_installed: true }))).toMatch(/own folder/)
    expect(ffmpegStatusLine(status({ refusal: 'Not enough free disk space (about 500 MB needed).' })))
      .toBe('Not enough free disk space (about 500 MB needed).')
    expect(ffmpegStatusLine(status({ job: running }))).toBeNull()
  })

  it('reports the last result, hidden while a job runs', () => {
    expect(ffmpegResultLine(status({ last_result: { ok: true, message: 'ffmpeg installed.' } })))
      .toEqual({ text: 'ffmpeg installed.', tone: 'ok' })
    expect(ffmpegResultLine(status({ last_result: { ok: false, message: 'It failed.' } }))?.tone).toBe('error')
    expect(ffmpegResultLine(status({ job: running, last_result: { ok: false, message: 'x' } }))).toBeNull()
  })
})
