import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { OriginalLine, ReadingWords, StudyLine, StudyWords } from './StudyLine'
import { heardUpTo } from './watchLogic'

const study = { line_id: 7, language: 'zh', original: '你好', needs_install: null }

describe('StudyWords', () => {
  it('shows each word with its reading and meanings', () => {
    const out = renderToStaticMarkup(
      <StudyWords study={{ ...study, words: [{ word: '你好', reading: 'nǐ hǎo', meanings: ['hello', 'hi'], start: null }] }} language="zh" />,
    )
    expect(out).toContain('nǐ hǎo')
    expect(out).toContain('hello; hi')
  })

  it('names a missing word splitter', () => {
    const out = renderToStaticMarkup(<StudyWords study={{ ...study, needs_install: 'sudachipy', words: [] }} language="ja" />)
    expect(out).toContain('needs sudachipy')
  })
})

describe('StudyLine', () => {
  it('stays closed and asks for nothing until Study is pressed', () => {
    const out = renderToStaticMarkup(<StudyLine dramaId={3} lineId={7} language="zh" />)
    expect(out).toContain('>Study<')
    expect(out).not.toContain('data-testid="watch-study"')
  })
})

describe('the original line on the now card', () => {
  it('shows the text and a pinyin switch, off until turned on', () => {
    const out = renderToStaticMarkup(<OriginalLine dramaId={3} lineId={7} text="你好" language="zh" />)
    expect(out).toContain('<p class="watch-now-zh" lang="zh">你好</p>')
    expect(out).toContain('Pinyin')
    expect(out).toContain('aria-checked="false"')
  })

  it('has no switch for Korean, which Hangul already spells', () => {
    expect(renderToStaticMarkup(<OriginalLine dramaId={3} lineId={7} text="안녕" language="ko" />)).not.toContain('role="switch"')
  })

  it('lights every word heard so far, skipping words without a time', () => {
    const words = [{ start: 1 }, { start: null }, { start: 2 }]
    expect(heardUpTo(words, 0.5)).toBe(-1)
    expect(heardUpTo(words, 1.5)).toBe(0)
    expect(heardUpTo(words, 2)).toBe(2)
  })
})

describe('the original line with readings on', () => {
  const words = [
    { word: '我', reading: 'wǒ', meanings: [], start: 1 },
    { word: 'New', reading: null, meanings: [], start: 2 },
  ]
  it('shows ruby over each word and keeps the spaces between them', () => {
    const out = renderToStaticMarkup(<ReadingWords text="我 New" words={words} lang="zh" heard={-1} />)
    expect(out).toBe('<ruby class="watch-word">我<rt>wǒ</rt></ruby> <span class="watch-word">New</span>')
  })
  it('marks the words heard so far', () => {
    const out = renderToStaticMarkup(<ReadingWords text="我 New" words={words} lang="zh" heard={0} />)
    expect(out).toContain('<ruby class="watch-word watch-word-heard">我')
    expect(out).toContain('<span class="watch-word">New</span>')
  })
  it('puts no furigana over kana', () => {
    const ja = [{ word: 'テスト', reading: 'てすと', meanings: [], start: null }]
    expect(renderToStaticMarkup(<ReadingWords text="テスト" words={ja} lang="ja" heard={-1} />)).not.toContain('<rt>')
  })
})
