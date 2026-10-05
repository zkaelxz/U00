import { describe, expect, it } from 'vitest'

import {
  EMPTY_SELECTION_FORM,
  buildSelection,
  capProblem,
  compareOutcome,
  isSameText,
  textDiff,
} from './compareTranscriptionLogic'

const form = (over: Partial<typeof EMPTY_SELECTION_FORM>) => ({ ...EMPTY_SELECTION_FORM, ...over })

describe('buildSelection', () => {
  it('flagged needs nothing', () => {
    expect(buildSelection(form({ mode: 'flagged' }))).toEqual({ selection: { kind: 'flagged' } })
  })
  it('one line is a range of one', () => {
    expect(buildSelection(form({ mode: 'line', lineNumber: '7' }))).toEqual({
      selection: { kind: 'range', from_number: 7, to_number: 7 },
    })
    expect(buildSelection(form({ mode: 'line', lineNumber: '0' }))).toHaveProperty('problem')
  })
  it('range must be ordered whole numbers', () => {
    expect(buildSelection(form({ mode: 'range', from: '2', to: '9' }))).toEqual({
      selection: { kind: 'range', from_number: 2, to_number: 9 },
    })
    expect(buildSelection(form({ mode: 'range', from: '9', to: '2' }))).toHaveProperty('problem')
    expect(buildSelection(form({ mode: 'range', from: '1.5', to: '3' }))).toHaveProperty('problem')
    expect(buildSelection(form({ mode: 'range', from: '', to: '3' }))).toHaveProperty('problem')
  })
  it('speaker is trimmed and required', () => {
    expect(buildSelection(form({ mode: 'speaker', speaker: ' A ' }))).toEqual({ selection: { kind: 'speaker', speaker: 'A' } })
    expect(buildSelection(form({ mode: 'speaker', speaker: ' ' }))).toHaveProperty('problem')
  })
  it('time needs start before end', () => {
    expect(buildSelection(form({ mode: 'time', startSeconds: '1.5', endSeconds: '30' }))).toEqual({
      selection: { kind: 'time', start_seconds: 1.5, end_seconds: 30 },
    })
    expect(buildSelection(form({ mode: 'time', startSeconds: '5', endSeconds: '5' }))).toHaveProperty('problem')
    expect(buildSelection(form({ mode: 'time', startSeconds: '-1', endSeconds: '5' }))).toHaveProperty('problem')
  })
})

describe('capProblem', () => {
  it('names the count and the limit only when over', () => {
    expect(capProblem(200, 200)).toBeNull()
    expect(capProblem(201, 200)).toContain('201 lines')
    expect(capProblem(201, 200)).toContain('200')
  })
})

describe('textDiff', () => {
  it('marks differing CJK characters on both sides', () => {
    const d = textDiff('魏婴来了', '魏婴来啦')
    expect(d.a.filter((p) => p.changed).map((p) => p.text)).toEqual(['了'])
    expect(d.b.filter((p) => p.changed).map((p) => p.text)).toEqual(['啦'])
    expect(d.a.map((p) => p.text).join('')).toBe('魏婴来了')
  })
  it('marks whole English words', () => {
    const d = textDiff('He is coming now', 'He was coming now')
    expect(d.a.filter((p) => p.changed).map((p) => p.text)).toEqual(['is'])
    expect(d.b.filter((p) => p.changed).map((p) => p.text)).toEqual(['was'])
  })
  it('joins neighbouring changed words across the space between them', () => {
    const d = textDiff('Wei is here', 'Wei has arrived')
    expect(d.a.filter((p) => p.changed).map((p) => p.text)).toEqual(['is here'])
    expect(d.b.filter((p) => p.changed).map((p) => p.text)).toEqual(['has arrived'])
  })
  it('identical text has no change; empty side marks everything', () => {
    expect(textDiff('same', 'same').a.every((p) => !p.changed)).toBe(true)
    expect(textDiff('', 'new').b).toEqual([{ text: 'new', changed: true }])
    expect(textDiff('old', '').a).toEqual([{ text: 'old', changed: true }])
  })
  it('isSameText ignores outer whitespace', () => {
    expect(isSameText(' a ', 'a')).toBe(true)
    expect(isSameText('a', 'b')).toBe(false)
  })
})

describe('compareOutcome', () => {
  it('ready, cancelled and failed', () => {
    expect(compareOutcome({ status: 'done', result: {} })).toEqual({ kind: 'ready' })
    expect(compareOutcome({ status: 'done', result: { failed_reason: 'cancelled' } })).toMatchObject({ kind: 'none' })
    expect(compareOutcome({ status: 'done', result: { failed_reason: 'model_download' } })).toMatchObject({
      text: expect.stringContaining('downloaded'),
    })
    expect(compareOutcome({ status: 'error' })).toMatchObject({ kind: 'none' })
  })
})
