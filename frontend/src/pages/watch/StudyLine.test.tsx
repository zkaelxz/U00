import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { StudyLine, StudyWords } from './StudyLine'

const study = { line_id: 7, language: 'zh', original: '你好', needs_install: null }

describe('StudyWords', () => {
  it('shows each word with its reading and meanings', () => {
    const out = renderToStaticMarkup(
      <StudyWords study={{ ...study, words: [{ word: '你好', reading: 'nǐ hǎo', meanings: ['hello', 'hi'] }] }} language="zh" />,
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
