import { describe, expect, it } from 'vitest'

import {
  addFiles, applyManifest, applyPaste, importBlocker, importSummary, notesFor, parseManifest, splitPaste, toPayload, type ClipRow,
} from './clipImportForm'

const file = (name: string) => new File(['x'], name)
const defaults = { language: 'zh' as const, title: 'Show' }

function rowsFor(...names: string[]): ClipRow[] {
  return addFiles([], names.map(file), defaults, 1).rows
}

describe('addFiles', () => {
  it('makes a row per audio file with the defaults, and keeps keys unique', () => {
    const { rows, skipped } = addFiles([], [file('a.wav'), file('b.MP3')], { language: 'ja', title: 'T' }, 5)
    expect(rows.map((r) => [r.key, r.file.name, r.language, r.title])).toEqual([[5, 'a.wav', 'ja', 'T'], [6, 'b.MP3', 'ja', 'T']])
    expect(skipped).toEqual([])
  })

  it('leaves out other files and ones already in the table', () => {
    const first = rowsFor('a.wav')
    const { rows, skipped } = addFiles(first, [file('a.wav'), file('notes.txt'), file('c.flac')], defaults, 10)
    expect(rows.map((r) => r.file.name)).toEqual(['c.flac'])
    expect(skipped).toEqual(['a.wav', 'notes.txt'])
  })
})

describe('paste', () => {
  it('splits a transcript from its translation at the Tab and skips blank lines', () => {
    expect(splitPaste('你好\tHello\n\n谢谢  \n')).toEqual([
      { transcript: '你好', translation: 'Hello' }, { transcript: '谢谢', translation: '' }])
  })

  it('fills rows in order and counts the lines that have no row', () => {
    const { rows, unused } = applyPaste(rowsFor('a.wav', 'b.wav'), '一\tOne\n二\n三')
    expect(rows.map((r) => [r.transcript, r.translation])).toEqual([['一', 'One'], ['二', '']])
    expect(unused).toBe(1)
  })

  it('leaves later rows alone when there are fewer lines', () => {
    const base = rowsFor('a.wav', 'b.wav')
    base[1].transcript = 'kept'
    expect(applyPaste(base, '只有一行').rows[1].transcript).toBe('kept')
  })
})

describe('manifest', () => {
  const text = '# my clips\n{"audio": "clips/a.wav", "language": "ja", "transcript": "はい", "tags": ["asmr", "whisper"]}\n{"audio": "gone.wav"}'

  it('reads JSON lines and reports the first bad line', () => {
    expect(parseManifest(text).lines).toHaveLength(2)
    expect(parseManifest('{"audio": "a.wav"}\nnot json')).toEqual({ lines: [], badLine: 2 })
    expect(parseManifest('{"language": "zh"}').badLine).toBe(1)
  })

  it('fills the rows it names by file name and lists the ones with no file', () => {
    const { rows, unmatched } = applyManifest(rowsFor('a.wav', 'b.wav'), parseManifest(text).lines)
    expect(rows[0]).toMatchObject({ language: 'ja', transcript: 'はい', tags: 'asmr, whisper' })
    expect(rows[1].transcript).toBe('')
    expect(unmatched).toEqual(['gone.wav'])
  })
})

describe('payload and blockers', () => {
  it('trims, drops empty optional fields and splits tags', () => {
    const [row] = rowsFor('a.wav')
    Object.assign(row, { transcript: ' 你好 ', tags: 'music, whisper；crosstalk', title: '' })
    expect(toPayload(row)).toEqual({ filename: 'a.wav', language: 'zh', transcript: '你好', tags: ['music', 'whisper', 'crosstalk'] })
  })

  it('says what is missing before the table can be saved', () => {
    const rows = rowsFor('a.wav', 'b.wav')
    expect(importBlocker('x', [])).toBe('Add some audio files.')
    expect(importBlocker(' ', rows)).toBe('Give the set a name.')
    expect(importBlocker('x', rows)).toBe('2 clips still need a transcript.')
    rows[0].transcript = 'a'
    expect(importBlocker('x', rows)).toBe('b.wav still needs a transcript.')
    rows[1].transcript = 'b'
    expect(importBlocker('x', rows)).toBeNull()
  })

  it('picks a clip\'s own notes and words the result', () => {
    const notes = [{ clip: 'a.wav', message: 'm1' }, { clip: 'b.wav', message: 'm2' }]
    expect(notesFor(notes, 'a.wav')).toEqual(['m1'])
    expect(importSummary({ set_name: 's', clips: 1, transcription_cases: 1, translation_cases: 0 })).toBe(
      'Added 1 clip to “s” (1 case: 1 transcription, 0 translation).')
  })
})
