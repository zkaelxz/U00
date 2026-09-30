import { afterEach, describe, expect, it, vi } from 'vitest'

import { copyText } from './clipboard'

// No DOM in this test environment: stub just enough of navigator/document.
function fakeDocument(execResult: boolean | (() => boolean)) {
  const removed: unknown[] = []
  const ta = { value: '', style: {}, setAttribute: vi.fn(), select: vi.fn(), remove: vi.fn(() => removed.push(ta)) }
  const button = { focus: vi.fn() }
  const doc = {
    activeElement: button,
    createElement: vi.fn(() => ta),
    body: { appendChild: vi.fn() },
    execCommand: vi.fn(typeof execResult === 'function' ? execResult : () => execResult),
  }
  vi.stubGlobal('document', doc)
  return { doc, ta, removed, button }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('copyText', () => {
  it('uses the async clipboard API when it exists', async () => {
    const writeText = vi.fn(() => Promise.resolve())
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    const { doc } = fakeDocument(true)
    await expect(copyText('hello')).resolves.toBe(true)
    expect(writeText).toHaveBeenCalledWith('hello')
    expect(doc.execCommand).not.toHaveBeenCalled()
  })

  it('falls back to execCommand when navigator.clipboard is undefined (non-HTTPS page)', async () => {
    vi.stubGlobal('navigator', {})
    const { doc, ta, removed } = fakeDocument(true)
    await expect(copyText('hello')).resolves.toBe(true)
    expect(ta.value).toBe('hello')
    expect(doc.execCommand).toHaveBeenCalledWith('copy')
    expect(removed).toEqual([ta])
  })

  it('gives focus back to whatever had it (the Copy button) after the fallback', async () => {
    vi.stubGlobal('navigator', {})
    const { button } = fakeDocument(true)
    await copyText('hello')
    expect(button.focus).toHaveBeenCalled()
  })

  it('falls back when writeText rejects', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: () => Promise.reject(new Error('denied')) } })
    const { doc } = fakeDocument(true)
    await expect(copyText('hello')).resolves.toBe(true)
    expect(doc.execCommand).toHaveBeenCalledWith('copy')
  })

  it('returns false, without throwing, when nothing can copy', async () => {
    vi.stubGlobal('navigator', {})
    const { ta, removed } = fakeDocument(() => {
      throw new Error('execCommand unsupported')
    })
    await expect(copyText('hello')).resolves.toBe(false)
    expect(removed).toEqual([ta])
  })

  it('returns false when execCommand reports failure', async () => {
    vi.stubGlobal('navigator', {})
    fakeDocument(false)
    await expect(copyText('hello')).resolves.toBe(false)
  })
})
