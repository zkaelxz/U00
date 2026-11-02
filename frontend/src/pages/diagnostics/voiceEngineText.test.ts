import { describe, expect, it } from 'vitest'

import type { VoiceEngineInstallRow } from '../../types/voiceEngines'
import { canOfferVoiceEngine, canRemoveVoiceEngine, voiceEngineResultLine, voiceEngineStatusLine } from './voiceEngineText'

const row = (o: Partial<VoiceEngineInstallRow> = {}): VoiceEngineInstallRow => ({
  engine: 'qwen3_tts', label: 'Qwen3-TTS', installed: false, folder_present: false, required_mb: 6000, refusal: null,
  job_id: 'voice_engine_install_qwen3_tts', job: null, last_result: null, ...o,
})
const running = { status: 'running', progress: 0.4, message: 'Installing PyTorch…', error: null }

describe('Voice cloning: Qwen3-TTS row', () => {
  it('offers the install only when the server has no refusal and nothing runs', () => {
    expect(canOfferVoiceEngine(row())).toBe(true)
    expect(canOfferVoiceEngine(row({ refusal: 'Not enough free disk space (about 6 GB needed).' }))).toBe(false)
    expect(canOfferVoiceEngine(row({ job: running }))).toBe(false)
  })

  it('says what is installed or why not, and nothing while a job runs', () => {
    expect(voiceEngineStatusLine(row())).toBeNull()
    expect(voiceEngineStatusLine(row({ installed: true, refusal: 'Qwen3-TTS is already installed.' })))
      .toBe('Qwen3-TTS is installed.')
    expect(voiceEngineStatusLine(row({ refusal: 'Not enough free disk space (about 6 GB needed).' })))
      .toBe('Not enough free disk space (about 6 GB needed).')
    expect(voiceEngineStatusLine(row({ installed: true, job: running }))).toBeNull()
  })

  it('offers Remove only for a folder that exists, and calls a half-installed one damaged', () => {
    expect(canRemoveVoiceEngine(row())).toBe(false)
    expect(canRemoveVoiceEngine(row({ installed: true, folder_present: true }))).toBe(true)
    expect(canRemoveVoiceEngine(row({ folder_present: true, job: running }))).toBe(false)
    expect(voiceEngineStatusLine(row({ folder_present: true })))
      .toBe('The Qwen3-TTS folder is damaged. Remove it in Diagnostics and install it again.')
  })

  it('shows the last result with its tone, but not while a job runs', () => {
    const failed = { ok: false, message: 'The Qwen3-TTS install failed; see the output.', output_tail: [] }
    expect(voiceEngineResultLine(row({ last_result: failed })))
      .toEqual({ text: 'The Qwen3-TTS install failed; see the output.', tone: 'error' })
    expect(voiceEngineResultLine(row({ last_result: { ...failed, ok: true, message: 'Qwen3-TTS installed.' } })))
      .toEqual({ text: 'Qwen3-TTS installed.', tone: 'ok' })
    expect(voiceEngineResultLine(row({ last_result: failed, job: running }))).toBeNull()
  })
})
