import { describe, expect, it } from 'vitest'

import type { TranscribeConfig } from '../../types/workspace'
import { mergeRunChoices, runChoicesFromConfig, runChoicesUnsaved, runChoicesUpdate, type RunChoices } from './sourceForm'
import { runSettingsLine } from './stages/transcribeRun'

const saved: RunChoices = {
  language: 'zh', runDiarize: false, speakers: '', minSpeakers: '', maxSpeakers: '', extraNames: '', override: '',
}
const config = (extra: Partial<TranscribeConfig> = {}) => ({ source_language: 'zh', chinese_script: 'simplified', ...extra }) as TranscribeConfig

describe('runChoicesFromConfig', () => {
  it('reads the language and run choices the title saved, blank counts as auto', () => {
    const c = config({
      source_language: 'ja',
      run_start: { run_diarize: true, expected_speakers: 3, min_speakers: null, max_speakers: null, extra_names: '白鹤', initial_prompt: '' },
    })
    expect(runChoicesFromConfig(c, 'zh')).toEqual({ ...saved, language: 'ja', runDiarize: true, speakers: '3', extraNames: '白鹤' })
  })

  it('falls back to the title language when an older server sends none', () => {
    expect(runChoicesFromConfig({} as TranscribeConfig, 'ko')).toEqual({ ...saved, language: 'ko' })
  })
})

describe('mergeRunChoices', () => {
  const server = { ...saved, language: 'ja', extraNames: 'from the title' }

  it('uses the title\'s values when there is no draft', () => {
    expect(mergeRunChoices(server, {}, undefined)).toEqual(server)
  })

  it('keeps a value this browser edited and not saved', () => {
    const base = { ...saved, language: 'zh' }
    expect(mergeRunChoices(server, { language: 'ko' }, base).language).toBe('ko')
  })

  it('takes the title\'s value over a draft that only repeats an older saved one', () => {
    // The draft held 'zh' beside a base of 'zh'; the title has since been saved as 'ja' elsewhere.
    const merged = mergeRunChoices(server, { language: 'zh', extraNames: '' }, { ...saved })
    expect(merged.language).toBe('ja')
    expect(merged.extraNames).toBe('from the title')
  })

  it('treats a draft with no base as edited', () => {
    expect(mergeRunChoices(server, { extraNames: 'mine' }, undefined).extraNames).toBe('mine')
  })
})

describe('runChoicesUpdate', () => {
  it('sends the language, a chosen script and the whole run_start', () => {
    const update = runChoicesUpdate({ ...saved, runDiarize: true, speakers: '2', extraNames: ' 白鹤 ', override: '' }, 'traditional')
    expect(update).toEqual({
      source_language: 'zh', chinese_script: 'traditional',
      run_start: { run_diarize: true, expected_speakers: 2, min_speakers: null, max_speakers: null, extra_names: '白鹤', initial_prompt: '' },
    })
  })

  it('sends no script for another language or when the title\'s is kept', () => {
    expect(runChoicesUpdate({ ...saved, language: 'ja' }, 'traditional').chinese_script).toBeUndefined()
    expect(runChoicesUpdate(saved, '').chinese_script).toBeUndefined()
  })

  it('leaves out counts that do not parse and keeps a range', () => {
    expect(runChoicesUpdate({ ...saved, speakers: 'x' }, '').run_start?.expected_speakers).toBeNull()
    const range = runChoicesUpdate({ ...saved, minSpeakers: '2', maxSpeakers: '4' }, '').run_start
    expect([range?.min_speakers, range?.max_speakers]).toEqual([2, 4])
  })
})

describe('runChoicesUnsaved', () => {
  const c = config({ run_start: { run_diarize: false, expected_speakers: null, min_speakers: null, max_speakers: null, extra_names: '', initial_prompt: '' } })

  it('is false for a form that matches the title', () => {
    expect(runChoicesUnsaved(saved, '', c, 'zh')).toBe(false)
  })

  it('is false when the speaker box says 0, the same as auto', () => {
    expect(runChoicesUnsaved({ ...saved, speakers: '0' }, '', c, 'zh')).toBe(false)
  })

  it.each([
    ['language', { language: 'ko' }, ''],
    ['speaker detection', { runDiarize: true }, ''],
    ['extra names', { extraNames: 'x' }, ''],
    ['prompt', { override: 'x' }, ''],
    ['a new script', {}, 'traditional'],
  ])('is true after %s changes', (_name, change, script) => {
    expect(runChoicesUnsaved({ ...saved, ...change }, script, c, 'zh')).toBe(true)
  })
})

describe('runSettingsLine', () => {
  const base = {
    language: 'ja', script: '', voiceDetection: 'normal', beamSize: 5, separateVocals: false, useGroq: false,
    runDiarize: false, speakers: '', extraNames: '', promptOverride: '',
  }

  it('lists the choices that change a long run', () => {
    expect(runSettingsLine(base)).toBe(
      'Settings this run will use: Japanese · voice detection normal · beam size 5 · vocals separated first: no · speaker detection off')
  })

  it('names the script on Chinese, the speaker count and the extras', () => {
    const line = runSettingsLine({
      ...base, language: 'zh', titleScript: 'traditional', separateVocals: true, runDiarize: true, speakers: '3',
      extraNames: '白鹤', useGroq: true,
    })
    expect(line).toContain('Chinese (traditional script)')
    expect(line).toContain('vocals separated first: yes')
    expect(line).toContain('speaker detection on, 3 speakers')
    expect(line).toContain('extra names added')
    expect(line).toContain('Groq cloud (paid)')
  })
})
