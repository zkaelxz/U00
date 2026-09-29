import { afterAll, describe, expect, it, vi } from 'vitest'

// Renders the "Why this?" panel inside <StrictMode> with the real React DOM
// client. The suite runs in Node with no DOM library, so a tiny DOM stand-in
// (just what React DOM touches for this panel) is installed before React DOM
// loads. StrictMode's dev-only unmount/remount is the behaviour under test.

class FakeNode {
  childNodes: FakeNode[] = []
  parentNode: FakeNode | null = null
  nodeValue: string | null = null
  nodeType: number
  nodeName: string
  ownerDocument: FakeDocument | null
  constructor(nodeType: number, nodeName: string, ownerDocument: FakeDocument | null) {
    this.nodeType = nodeType
    this.nodeName = nodeName
    this.ownerDocument = ownerDocument
  }
  get firstChild() { return this.childNodes[0] ?? null }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] ?? null }
  get nextSibling(): FakeNode | null {
    const sibs = this.parentNode?.childNodes ?? []
    return sibs[sibs.indexOf(this) + 1] ?? null
  }
  get textContent(): string {
    return this.nodeType === 3 ? (this.nodeValue ?? '') : this.childNodes.map((c) => c.textContent).join('')
  }
  set textContent(v: string) {
    for (const c of this.childNodes) c.parentNode = null
    this.childNodes = []
    if (v) this.appendChild(this.ownerDocument!.createTextNode(v))
  }
  appendChild(c: FakeNode) { return this.insertBefore(c, null) }
  insertBefore(c: FakeNode, ref: FakeNode | null) {
    c.parentNode?.removeChild(c)
    const i = ref ? this.childNodes.indexOf(ref) : -1
    if (i < 0) this.childNodes.push(c)
    else this.childNodes.splice(i, 0, c)
    c.parentNode = this
    return c
  }
  removeChild(c: FakeNode) {
    this.childNodes = this.childNodes.filter((n) => n !== c)
    c.parentNode = null
    return c
  }
  addEventListener() {}
  removeEventListener() {}
}

class FakeElement extends FakeNode {
  attributes = new Map<string, string>()
  style: Record<string, string> = {}
  namespaceURI = 'http://www.w3.org/1999/xhtml'
  constructor(tag: string, doc: FakeDocument | null) { super(1, tag.toUpperCase(), doc) }
  get tagName() { return this.nodeName }
  setAttribute(k: string, v: unknown) { this.attributes.set(k, String(v)) }
  getAttribute(k: string) { return this.attributes.get(k) ?? null }
  hasAttribute(k: string) { return this.attributes.has(k) }
  removeAttribute(k: string) { this.attributes.delete(k) }
  find(test: (e: FakeElement) => boolean): FakeElement | null {
    for (const c of this.childNodes) {
      if (!(c instanceof FakeElement)) continue
      if (test(c)) return c
      const hit = c.find(test)
      if (hit) return hit
    }
    return null
  }
}

class FakeDocument extends FakeNode {
  body: FakeElement
  documentElement: FakeElement
  defaultView: unknown = null
  constructor() {
    super(9, '#document', null)
    this.documentElement = this.appendChild(new FakeElement('html', this)) as FakeElement
    this.body = this.documentElement.appendChild(new FakeElement('body', this)) as FakeElement
  }
  get activeElement() { return this.body }
  createElement(tag: string) { return new FakeElement(tag, this) }
  createElementNS(_ns: string, tag: string) { return new FakeElement(tag, this) }
  createTextNode(text: string) {
    const t = new FakeNode(3, '#text', this)
    t.nodeValue = text
    return t
  }
}

const doc = new FakeDocument()
const win = { document: doc, HTMLIFrameElement: class {}, addEventListener() {}, removeEventListener() {} }
doc.defaultView = win
vi.stubGlobal('window', win)
vi.stubGlobal('document', doc)
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)

const { act, createElement, StrictMode } = await import('react')
const { createRoot } = await import('react-dom/client')
const { LineAi } = await import('./LineAi')

const line = { id: 7, en: 'Hello', zh: '你好' } as unknown as Parameters<typeof LineAi>[0]['line']

afterAll(() => vi.unstubAllGlobals())

describe('LineAi under StrictMode', () => {
  it('re-asks "Why this?" after the dev remount and clears Working…', async () => {
    const calls: AbortSignal[] = []
    const answers: ((r: Response) => void)[] = []
    vi.stubGlobal('fetch', (_url: string, init: RequestInit) => {
      calls.push(init.signal!)
      return new Promise<Response>((resolve, reject) => {
        answers.push(resolve)
        init.signal!.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      })
    })

    const container = doc.body.appendChild(doc.createElement('div')) as FakeElement
    const root = createRoot(container as unknown as Element)
    const props = { dramaId: 3, line, mode: 'explain' as const, onClose: () => {}, onUse: async () => true }
    await act(async () => root.render(createElement(StrictMode, null, createElement(LineAi, props))))

    // The first request was aborted by the simulated unmount; the remount asked again.
    expect(calls).toHaveLength(2)
    expect(calls[0].aborted).toBe(true)
    expect(calls[1].aborted).toBe(false)
    expect(container.textContent).toContain('Working…')

    await act(async () => {
      answers[1](new Response(JSON.stringify({ line_id: 7, explanation: 'Because.' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    })
    expect(container.textContent).not.toContain('Working…')
    const shown = container.find((e) => e.getAttribute('data-testid') === 'line-ai-explanation')
    expect(shown?.textContent).toBe('Because.')

    await act(async () => root.unmount())
    expect(calls[1].aborted).toBe(true)
  })
})
