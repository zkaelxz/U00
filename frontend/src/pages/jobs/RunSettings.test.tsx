import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { adviceRequestFromRun, engineThatRan, RunSettings, runSettingRows } from './RunSettings'

describe('RunSettings', () => {
  it('lists each setting with toggles as on/off', () => {
    const html = renderToStaticMarkup(
      <RunSettings result={{ run_settings: { whisper_size: 'large-v3', beam_size: 5, use_gpu: true, glossary: false } }} />,
    )
    expect(html).toContain('Run settings')
    expect(html).toContain('whisper size')
    expect(html).toContain('large-v3')
    expect(html).toContain('<dd>5</dd>')
    expect(html).toContain('<dd>on</dd>')
    expect(html).toContain('<dd>off</dd>')
  })

  it('renders nothing for an older job with no run settings', () => {
    expect(renderToStaticMarkup(<RunSettings result={{ line_count: 3 }} />)).toBe('')
    expect(renderToStaticMarkup(<RunSettings result={null} />)).toBe('')
  })

  it('ignores a value that is not a flat object of scalars', () => {
    expect(runSettingRows({ run_settings: 'x' })).toEqual([])
    expect(runSettingRows({ run_settings: ['a'] })).toEqual([])
    expect(runSettingRows({ run_settings: { a: { b: 1 }, c: null, d: 2 } })).toEqual([['d', '2']])
  })

  it('names the engine that ran when the backend skipped Whisper', () => {
    const result = { run_settings: { asr_backend: 'qwen3_asr_long', whisper_size: 'large-v3', transcript_mode: 'whisper' } }
    expect(engineThatRan(result)).toBe('Qwen3-ASR (Whisper model setting not used)')
    expect(renderToStaticMarkup(<RunSettings result={result} />)).toContain('Engine that ran: Qwen3-ASR (Whisper model setting not used)')
    expect(engineThatRan({ run_settings: { asr_backend: 'anime_whisper' } })).toBe('Anime-whisper (Whisper model setting not used)')
  })

  it('says nothing extra for a Whisper run, plain Qwen3 ASR (Whisper still cuts the lines) or an aligned transcript', () => {
    expect(engineThatRan({ run_settings: { asr_backend: 'whisper', whisper_size: 'large-v3' } })).toBeNull()
    expect(engineThatRan({ run_settings: { asr_backend: 'qwen3_asr', whisper_size: 'large-v3' } })).toBeNull()
    expect(engineThatRan({ run_settings: { asr_backend: 'qwen3_asr_long', transcript_mode: 'have_transcript' } })).toBeNull()
    expect(engineThatRan({ line_count: 3 })).toBeNull()
  })
})

describe('adviceRequestFromRun', () => {
  it('asks about the combination a transcription run recorded, without checking this PC', () => {
    const req = adviceRequestFromRun({ run_settings: { asr_backend: 'qwen3_asr_long', whisper_size: 'large-v3', source_language: 'ja', separate_vocals_first: true, use_gpu: false, transcript_mode: 'whisper' } })
    expect(req).toMatchObject({ asr_backend: 'qwen3_asr_long', language: 'ja', separate_vocals_first: true, use_gpu: false, has_transcript: false, check_environment: false })
  })

  it('is null for a job that is not a transcription', () => {
    expect(adviceRequestFromRun({ run_settings: { engine: 'claude' } })).toBeNull()
    expect(adviceRequestFromRun(null)).toBeNull()
  })
})
