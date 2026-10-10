import { describe, expect, it } from 'vitest'

import type { PreflightRow } from '../preflight/preflightModel'
import {
  IDLE, STEPS, blockerText, flowReducer, mediaTypeFor, parseSaved, percentText, reattachIds, savedFor, stepForJob,
  titleFor, type Flow,
} from './makeSubtitlesFlow'

const row = (need: PreflightRow['need'], blocking = true): PreflightRow =>
  ({ need, label: need, state: '', tone: 'warn', badge: '', blocking, noFix: null })

const running = (step: (typeof STEPS)[number], dramaId: number | null = 7): Flow => ({ phase: 'running', dramaId, step })

describe('flowReducer', () => {
  it('goes upload, transcribe, translate, export, then done', () => {
    let f = flowReducer(IDLE, { type: 'start' })
    expect(f).toEqual({ phase: 'running', dramaId: null, step: 'upload' })
    f = flowReducer(f, { type: 'created', dramaId: 7 })
    const seen: string[] = []
    while (f.phase === 'running') { seen.push(f.step); f = flowReducer(f, { type: 'advance' }) }
    expect(seen).toEqual([...STEPS])
    expect(f).toEqual({ phase: 'done', dramaId: 7 })
  })

  it('names the failing step and keeps the title for the Fix link', () => {
    const err = new Error('x')
    expect(flowReducer(running('translate'), { type: 'fail', error: err }))
      .toEqual({ phase: 'error', dramaId: 7, step: 'translate', error: err })
    expect(flowReducer(running('upload', null), { type: 'fail', error: err }))
      .toMatchObject({ phase: 'error', dramaId: null, step: 'upload' })
  })

  it('ignores steps and failures when nothing is running', () => {
    expect(flowReducer(IDLE, { type: 'advance' })).toBe(IDLE)
    expect(flowReducer(IDLE, { type: 'fail', error: 1 })).toBe(IDLE)
  })

  it('resumes only from idle, then moves to the step a found job belongs to', () => {
    const saved = { dramaId: 7, step: 'transcribe' as const }
    const f = flowReducer(IDLE, { type: 'resume', saved })
    expect(f).toEqual(running('transcribe'))
    expect(flowReducer(running('export'), { type: 'resume', saved })).toEqual(running('export'))
    expect(flowReducer(f, { type: 'at', step: 'translate' })).toEqual(running('translate'))
    expect(flowReducer(IDLE, { type: 'resume', saved: null })).toBe(IDLE)
  })

  it('resets to idle', () => {
    expect(flowReducer(running('translate'), { type: 'reset' })).toBe(IDLE)
  })
})

describe('resume storage', () => {
  it('stores only a run in progress that has a title', () => {
    expect(savedFor(running('translate'))).toEqual({ dramaId: 7, step: 'translate' })
    expect(savedFor(running('upload', null))).toBeNull()
    expect(savedFor({ phase: 'done', dramaId: 7 })).toBeNull()
    expect(savedFor(IDLE)).toBeNull()
  })
  it('reads back only well-formed values', () => {
    expect(parseSaved({ dramaId: 3, step: 'export' })).toEqual({ dramaId: 3, step: 'export' })
    for (const bad of [null, 'x', { dramaId: 0, step: 'export' }, { dramaId: 3, step: 'dub' }, { dramaId: '3', step: 'export' }])
      expect(parseSaved(bad)).toBeNull()
  })
  it('maps each job id to its step and lists the ids to look for', () => {
    expect(stepForJob('extract_audio_7')).toBe('transcribe')
    expect(stepForJob('transcribe_7')).toBe('transcribe')
    expect(stepForJob('translate_7')).toBe('translate')
    expect(stepForJob('dub_7')).toBeNull()
    expect(reattachIds(7)).toEqual(['extract_audio_7', 'transcribe_7', 'translate_7'])
  })
})

describe('what is created', () => {
  it('chooses video_drama for video files and audio_drama otherwise', () => {
    expect(mediaTypeFor('Episode 1.MP4')).toBe('video_drama')
    expect(mediaTypeFor('clip.mkv')).toBe('video_drama')
    expect(mediaTypeFor('talk.mp3')).toBe('audio_drama')
  })
  it('names the title after the file stem unless one is typed', () => {
    expect(titleFor('My Show.ep1.mp4', '')).toBe('My Show.ep1')
    expect(titleFor('a.mp3', '  Typed ')).toBe('Typed')
  })
})

describe('blockerText', () => {
  it('asks for a file first', () => {
    expect(blockerText(false, [row('whisper')], 'claude')).toBe('Still needed: an audio or video file.')
  })
  it('names the first preflight blocker', () => {
    expect(blockerText(true, [row('whisper')], 'claude')).toBe('Still needed: install Whisper.')
    expect(blockerText(true, [row('key')], 'claude')).toBe('Still needed: add a Claude key.')
  })
  it('is null when nothing blocks', () => {
    expect(blockerText(true, [], 'claude')).toBeNull()
    expect(blockerText(true, [row('gpu', false)], 'claude')).toBeNull()
  })
})

describe('percentText', () => {
  it('rounds a 0..1 progress and stays blank before one is reported', () => {
    expect(percentText(0.426)).toBe('43%')
    expect(percentText(null)).toBeNull()
    expect(percentText(2)).toBe('100%')
  })
})
