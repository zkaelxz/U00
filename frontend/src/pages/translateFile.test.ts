import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import {
  ACCEPT_ATTR,
  MAX_FILE_BYTES,
  checkTranslateFile,
  decodeText,
  downloadName,
  downloadText,
  extensionOf,
  loadChosenFile,
  readTranslateFile,
} from './translateFile'
import { DownloadResultButton, OpenFileField } from './TranslateFileControls'

const bytes = (s: string) => new TextEncoder().encode(s).buffer as ArrayBuffer
const file = (name: string, content = 'hi', size?: number) => ({
  name,
  size: size ?? content.length,
  content,
})
const readBytes = async (f: { content: string }) => bytes(f.content)

describe('checkTranslateFile', () => {
  it('accepts .txt and .md like the Streamlit tab', () => {
    expect(ACCEPT_ATTR).toBe('.txt,.md')
    expect(checkTranslateFile({ name: 'a.TXT', size: 1 })).toBeNull()
    expect(checkTranslateFile({ name: 'b.md', size: 1 })).toBeNull()
  })
  it('refuses epub, other types and oversize files with a message', () => {
    expect(checkTranslateFile({ name: 'book.epub', size: 1 })).toMatch(/EPUB/)
    expect(checkTranslateFile({ name: 'x.srt', size: 1 })).toMatch(/not a .txt or .md/)
    expect(checkTranslateFile({ name: 'noext', size: 1 })).toMatch(/not a .txt/)
    expect(checkTranslateFile({ name: 'a.txt', size: MAX_FILE_BYTES + 1 })).toMatch(/2 GB/)
    expect(checkTranslateFile({ name: 'a.txt', size: MAX_FILE_BYTES })).toBeNull()
  })
  it('extensionOf ignores dotfiles', () => {
    expect(extensionOf('.txt')).toBe('')
    expect(extensionOf('a.b.Md')).toBe('.md')
  })
})

describe('decodeText / readTranslateFile', () => {
  it('drops invalid UTF-8 bytes like errors="ignore"', () => {
    const buf = new Uint8Array([0x61, 0xff, 0x62]).buffer
    expect(decodeText(buf)).toBe('ab')
  })
  it('reads a valid file', async () => {
    expect(await readTranslateFile(file('a.txt', '你好'), readBytes)).toEqual({
      ok: true,
      text: '你好',
      name: 'a.txt',
    })
  })
  it('reports a read failure', async () => {
    const r = await readTranslateFile(file('a.txt'), () => Promise.reject(new Error('x')))
    expect(r).toEqual({ ok: false, error: 'Could not read "a.txt".' })
  })
})

describe('downloadName', () => {
  it('uses the source stem plus the language', () => {
    expect(downloadName('chapter1.md', 'en')).toBe('chapter1.en.txt')
    expect(downloadName('my.notes.txt', 'zh')).toBe('my.notes.zh.txt')
  })
  it('falls back to translation', () => {
    expect(downloadName(null, 'en')).toBe('translation.en.txt')
    expect(downloadName('', 'ja')).toBe('translation.ja.txt')
  })
  it('strips path separators and odd language chars', () => {
    expect(downloadName('a/b.txt', 'e n!')).toBe('a_b.en.txt')
  })
})

describe('downloadText', () => {
  it('saves a UTF-8 blob under the name and revokes the URL', async () => {
    vi.useFakeTimers()
    const deps = {
      createObjectURL: vi.fn((_b: Blob) => 'blob:1'),
      revokeObjectURL: vi.fn(),
      click: vi.fn(),
    }
    downloadText('你好', 'a.en.txt', deps)
    const blob = deps.createObjectURL.mock.calls[0][0]
    expect(blob.type).toBe('text/plain;charset=utf-8')
    expect(await blob.text()).toBe('你好')
    expect(deps.click).toHaveBeenCalledWith('blob:1', 'a.en.txt')
    vi.runAllTimers()
    expect(deps.revokeObjectURL).toHaveBeenCalledWith('blob:1')
    vi.useRealTimers()
  })
})

describe('Translate file controls', () => {
  const target = () => ({ setText: vi.fn(), setSourceName: vi.fn(), setFileMessage: vi.fn() })

  it('loading a file fills the text box and remembers the name', async () => {
    const t = target()
    await loadChosenFile(file('ep1.txt', 'hello'), t, readBytes)
    expect(t.setText).toHaveBeenCalledWith('hello')
    expect(t.setSourceName).toHaveBeenCalledWith('ep1.txt')
    expect(t.setFileMessage).toHaveBeenCalledWith(null)
  })

  it('a refused file shows a message and leaves the text alone', async () => {
    const t = target()
    await loadChosenFile(file('book.epub'), t, readBytes)
    expect(t.setText).not.toHaveBeenCalled()
    expect(t.setFileMessage).toHaveBeenCalledWith(expect.stringMatching(/EPUB/))
  })

  it('renders a labelled file input limited to .txt/.md', () => {
    const html = renderToStaticMarkup(
      createElement(OpenFileField, { sourceName: 'ep1.txt', message: null, target: target() }),
    )
    const id = /<input[^>]*id="([^"]+)"/.exec(html)?.[1]
    expect(id).toBeTruthy()
    expect(html).toContain(`for="${id}"`)
    expect(html).toContain('Open a file')
    expect(html).toContain('accept=".txt,.md"')
    expect(html).toContain('Loaded ep1.txt')
  })

  it('download button names the file after the source and language', () => {
    const html = renderToStaticMarkup(
      createElement(DownloadResultButton, {
        result: 'x',
        sourceName: 'ep1.txt',
        targetLanguage: 'en',
      }),
    )
    expect(html).toContain('Download result (ep1.en.txt)')
    expect(html).toContain('type="button"')
  })
})
