import { describe, expect, it } from 'vitest'

import { chineseLang, hanScript, nameLang, titleTextLang } from './hanScript'

describe('hanScript', () => {
  it('tells Traditional from Simplified by the characters each writes differently', () => {
    expect(hanScript('臺灣的夜晚，為了一句話直接掛我電話！')).toBe('zh-Hant')
    expect(hanScript('台湾的夜晚，为了一句话直接挂我电话！')).toBe('zh-Hans')
  })

  it('says nothing for text both scripts write the same, or no Chinese at all', () => {
    expect(hanScript('你好！')).toBeNull()
    expect(hanScript('Alex Example')).toBeNull()
  })
})

describe('chineseLang', () => {
  it('narrows plain zh to the line’s script, else the title’s', () => {
    expect(chineseLang('zh', '這是我的', 'zh-Hans')).toBe('zh-Hant')
    expect(chineseLang('zh', '你好！', 'zh-Hant')).toBe('zh-Hant')
    expect(chineseLang('zh', '你好！')).toBe('zh')
  })

  it('keeps a script the lang already names when the text shows none, and leaves other languages alone', () => {
    expect(chineseLang('zh-Hant', '你好！')).toBe('zh-Hant')
    expect(chineseLang('ja', '這是')).toBe('ja')
  })
})

describe('titleTextLang', () => {
  it('reads the script from the name, author and summary of a Chinese title', () => {
    expect(titleTextLang({ source_language: 'zh', title_en: 'Night Call', summary: '一個關於電話的故事' })).toBe('zh-Hant')
    expect(titleTextLang({ source_language: 'zh', title_zh: '夜晚' })).toBe('zh')
    expect(titleTextLang({ source_language: 'ko', title_zh: '這個' })).toBe('ko')
  })
})

describe('nameLang', () => {
  it('gives a CJK name its language and a Latin name none', () => {
    expect(nameLang('Night Call', { source_language: 'zh' })).toBeUndefined()
    expect(nameLang('夜晚的電話', { source_language: 'zh' })).toBe('zh-Hant')
    expect(nameLang('夜の電話', { source_language: 'zh' })).toBe('ja')
    expect(nameLang('밤의 전화', {})).toBe('ko')
    expect(nameLang('夜晚', { source_language: 'zh', summary: '这是一个关于电话的故事' })).toBe('zh-Hans')
  })
})
