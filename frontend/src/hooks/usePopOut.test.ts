import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { copyStyles, createPopOut, popOutSupported } from './usePopOut'

// The vitest environment is node: a tiny fake document stands in for the DOM.
const fakeDoc = () => {
  const children: unknown[] = []
  const attrs: Record<string, string> = {}
  return {
    children,
    attrs,
    handlers: {} as Record<string, (e: unknown) => void>,
    styleSheets: [] as unknown[],
    head: { appendChild: (n: unknown) => children.push(n) },
    body: { style: {} as Record<string, string>, appendChild: vi.fn() },
    createElement: (tag: string) => ({ tag }) as Record<string, unknown>,
    documentElement: {
      className: '',
      classList: { add: vi.fn() },
      attributes: [] as { name: string; value: string }[],
      setAttribute: (k: string, v: string) => void (attrs[k] = v),
    },
    addEventListener(n: string, h: (e: unknown) => void) {
      this.handlers[n] = h
    },
  }
}

function fakeWindow() {
  const document = fakeDoc()
  const handlers: Record<string, () => void> = {}
  const win = {
    document,
    addEventListener: (n: string, h: () => void) => void (handlers[n] = h),
    close: vi.fn(() => handlers.pagehide?.()),
  }
  return { win, handlers, document }
}

beforeEach(() => {
  vi.stubGlobal('document', fakeDoc())
})
afterEach(() => vi.unstubAllGlobals())

const box = {} as HTMLElement

describe('createPopOut', () => {
  it('is unsupported and inert without the API', async () => {
    vi.stubGlobal('window', {})
    expect(popOutSupported()).toBe(false)
    const restore = vi.fn()
    const onActive = vi.fn()
    const p = createPopOut(box, restore, onActive)
    await p.open()
    p.close()
    p.dispose()
    expect(onActive).not.toHaveBeenCalled()
    expect(restore).not.toHaveBeenCalled()
  })

  it('detects the API', () => {
    vi.stubGlobal('window', { documentPictureInPicture: { requestWindow: vi.fn() } })
    expect(popOutSupported()).toBe(true)
  })

  it('moves the same element into the window and restores it on close', async () => {
    const { win, document: pipDoc } = fakeWindow()
    const requestWindow = vi.fn().mockResolvedValue(win)
    const restore = vi.fn()
    const onActive = vi.fn()
    const p = createPopOut(box, restore, onActive, () => ({ requestWindow }))
    await p.open()
    expect(pipDoc.body.appendChild).toHaveBeenCalledWith(box)
    expect(onActive).toHaveBeenLastCalledWith(true)
    await p.open()
    expect(requestWindow).toHaveBeenCalledTimes(1)
    p.close()
    expect(win.close).toHaveBeenCalled()
    expect(onActive).toHaveBeenLastCalledWith(false)
    expect(restore).toHaveBeenCalledTimes(1)
  })

  it('restores when the window closes by itself, and on dispose', async () => {
    const a = fakeWindow()
    const restore = vi.fn()
    const p = createPopOut(box, restore, vi.fn(), () => ({ requestWindow: vi.fn().mockResolvedValue(a.win) }))
    await p.open()
    a.handlers.pagehide()
    expect(restore).toHaveBeenCalledTimes(1)
    await p.open()
    p.dispose()
    expect(restore).toHaveBeenCalledTimes(2)
    p.dispose()
    expect(restore).toHaveBeenCalledTimes(2)
  })

  it('stays put when the browser refuses the window', async () => {
    const restore = vi.fn()
    const onActive = vi.fn()
    const p = createPopOut(box, restore, onActive, () => ({ requestWindow: vi.fn().mockRejectedValue(new Error('no gesture')) }))
    await p.open()
    expect(onActive).not.toHaveBeenCalled()
    expect(restore).not.toHaveBeenCalled()
  })

  it('forwards keys from the window to the page, except while typing', async () => {
    const { win, document: pipDoc } = fakeWindow()
    const dispatched: unknown[] = []
    class FakeKey {
      defaultPrevented = false
      type: string
      init: Record<string, unknown>
      constructor(type: string, init: Record<string, unknown>) {
        this.type = type
        this.init = init
      }
    }
    vi.stubGlobal('KeyboardEvent', FakeKey)
    vi.stubGlobal('document', { ...fakeDoc(), dispatchEvent: (e: unknown) => dispatched.push(e) })
    const p = createPopOut(box, vi.fn(), vi.fn(), () => ({ requestWindow: vi.fn().mockResolvedValue(win) }))
    await p.open()
    pipDoc.handlers.keydown({ type: 'keydown', key: ' ', altKey: true, target: { tagName: 'DIV' }, preventDefault: vi.fn() })
    pipDoc.handlers.keydown({ type: 'keydown', key: 'l', altKey: false, target: { tagName: 'INPUT', type: 'text' }, preventDefault: vi.fn() })
    expect(dispatched).toHaveLength(1)
  })
})

describe('copyStyles', () => {
  it('copies readable sheets, links unreadable ones, and the theme attributes', () => {
    const from = fakeDoc()
    const to = fakeDoc()
    from.styleSheets = [
      { cssRules: [{ cssText: '.x { color: red }' }, { cssText: '.y { top: 0 }' }] },
      { get cssRules(): never { throw new Error('cross-origin') }, href: 'https://cdn/x.css' },
    ]
    from.documentElement.className = 'dark'
    from.documentElement.attributes = [{ name: 'data-theme', value: 'dark' }, { name: 'lang', value: 'en' }]
    copyStyles(from as unknown as Document, to as unknown as Document)
    expect((to.children[0] as { textContent: string }).textContent).toBe('.x { color: red }\n.y { top: 0 }')
    expect((to.children[1] as { href: string }).href).toBe('https://cdn/x.css')
    expect(to.documentElement.className).toBe('dark')
    expect(to.attrs).toEqual({ 'data-theme': 'dark' })
  })
})
