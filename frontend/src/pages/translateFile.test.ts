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
import { MAX_TRANSLATE_TEXT_CHARS } from '../api/translate'
import { DownloadResultButton, OpenFileField } from './TranslateFileControls'
import { MAX_EPUB_BYTES } from './translateEpub'
import { buildEpub, stripTags } from './translateEpubFixture'

const bytes = (s: string) => new TextEncoder().encode(s).buffer as ArrayBuffer
const file = (name: string, content = 'hi', size?: number) => ({
  name,
  size: size ?? content.length,
  content,
})
const readBytes = async (f: { content: string }) => bytes(f.content)

describe('checkTranslateFile', () => {
  it('accepts .txt, .md and .epub like the Streamlit tab', () => {
    expect(ACCEPT_ATTR).toBe('.txt,.md,.epub')
    expect(checkTranslateFile({ name: 'a.TXT', size: 1 })).toBeNull()
    expect(checkTranslateFile({ name: 'b.md', size: 1 })).toBeNull()
    expect(checkTranslateFile({ name: 'c.EPUB', size: 1 })).toBeNull()
  })
  it('refuses other types and oversize files with a message', () => {
    expect(checkTranslateFile({ name: 'x.srt', size: 1 })).toMatch(/not a .txt, .md or .epub/)
    expect(checkTranslateFile({ name: 'book.epub', size: MAX_EPUB_BYTES + 1 })).toMatch(/200 MB limit for EPUB/)
    expect(checkTranslateFile({ name: 'book.epub', size: MAX_EPUB_BYTES })).toBeNull()
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
  it('refuses text longer than the API accepts', async () => {
    const atCap = 'a'.repeat(MAX_TRANSLATE_TEXT_CHARS)
    expect((await readTranslateFile(file('a.txt', atCap), readBytes)).ok).toBe(true)
    const over = await readTranslateFile(file('a.txt', `${atCap}a`), readBytes)
    expect(over).toEqual({ ok: false, error: expect.stringMatching(/2,000,000 characters/) })
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
    await loadChosenFile(file('clip.srt'), t, readBytes)
    expect(t.setText).not.toHaveBeenCalled()
    expect(t.setFileMessage).toHaveBeenCalledWith(expect.stringMatching(/not a .txt/))
  })

  it('an .epub fills the text box with its chapter text', async () => {
    const t = target()
    const epub = buildEpub({ 'OEBPS/c1.xhtml': '<body><p>第一章</p><p>你好</p></body>' })
    const f = { name: 'book.epub', size: epub.length }
    await loadChosenFile(f, t, async () => epub.slice().buffer as ArrayBuffer, stripTags)
    expect(t.setText).toHaveBeenCalledWith('第一章\n你好')
    expect(t.setSourceName).toHaveBeenCalledWith('book.epub')
  })

  it('an unreadable .epub shows a plain message and leaves the text alone', async () => {
    const t = target()
    await loadChosenFile(file('book.epub', 'not a zip'), t, readBytes, stripTags)
    expect(t.setText).not.toHaveBeenCalled()
    expect(t.setFileMessage).toHaveBeenCalledWith('"book.epub" is not a readable EPUB (it could not be unzipped).')
  })

  it('renders a labelled file input limited to .txt/.md/.epub', () => {
    const html = renderToStaticMarkup(
      createElement(OpenFileField, { sourceName: 'ep1.txt', message: null, target: target() }),
    )
    const id = /<input[^>]*id="([^"]+)"/.exec(html)?.[1]
    expect(id).toBeTruthy()
    expect(html).toContain(`for="${id}"`)
    expect(html).toContain('Open a file')
    expect(html).toContain('accept=".txt,.md,.epub"')
    expect(html).toContain('Loaded ep1.txt')
  })

  it('shows the accepted types as visible text, and a file error in place of it', () => {
    const idle = renderToStaticMarkup(
      createElement(OpenFileField, { sourceName: null, message: null, target: target() }),
    )
    expect(idle).toContain('.txt, .md or .epub; replaces the text below')
    const failed = renderToStaticMarkup(
      createElement(OpenFileField, { sourceName: 'ep1.txt', message: 'Not a text file.', target: target() }),
    )
    expect(failed).toContain('role="alert"')
    expect(failed).toContain('Not a text file.')
    expect(failed).not.toContain('Loaded ep1.txt')
  })

  it('download button names the file after the source and language', () => {
    const html = renderToStaticMarkup(
      createElement(DownloadResultButton, {
        result: 'x',
        sourceName: 'ep1.txt',
        targetLanguage: 'en',
      }),
    )
    expect(html).toContain('aria-label="Download result (ep1.en.txt)"')
    expect(html).toContain('>Download</button>')
    expect(html).toContain('type="button"')
  })
})
