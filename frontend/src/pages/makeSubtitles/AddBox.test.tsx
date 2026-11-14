import type { ReactElement } from 'react'
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { FileRunRequest } from './addBoxModel'

// Mounts the Add box and its run with the real React DOM client. The suite runs in Node with no DOM
// library, so a small DOM stand-in (only what React DOM touches here) is installed before React DOM loads.

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
  value = ''
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

const stored = new Map<string, string>()
const localStorage = {
  getItem: (k: string) => stored.get(k) ?? null,
  setItem: (k: string, v: string) => { stored.set(k, v) },
  removeItem: (k: string) => { stored.delete(k) },
}
const doc = new FakeDocument()
const win = { document: doc, localStorage, HTMLIFrameElement: class {}, addEventListener() {}, removeEventListener() {} }
doc.defaultView = win
vi.stubGlobal('window', win)
vi.stubGlobal('document', doc)
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)

const api = vi.hoisted(() => ({
  createDrama: vi.fn(async () => ({ id: 9 })),
  uploadAndTranscribe: vi.fn(async () => ({ job_id: 'transcribe_9' })),
  startTranslateRun: vi.fn(async () => ({ job_id: 'translate_7' })),
  probeJob: vi.fn((_id: string): Promise<null> => Promise.resolve(null)),
}))
vi.mock('../../api/library', async (orig) => ({ ...(await orig<typeof import('../../api/library')>()), createDrama: api.createDrama }))
vi.mock('../../api/workspace', async (orig) => ({ ...(await orig<typeof import('../../api/workspace')>()), uploadAndTranscribe: api.uploadAndTranscribe }))
vi.mock('../../api/translateStage', async (orig) => ({
  ...(await orig<typeof import('../../api/translateStage')>()),
  getTranslateConfig: vi.fn(async () => ({})),
  startTranslateRun: api.startTranslateRun,
}))
vi.mock('../../api/jobs', async (orig) => ({ ...(await orig<typeof import('../../api/jobs')>()), probeJob: api.probeJob, cancelJob: vi.fn(async () => undefined) }))
vi.mock('../../api/translate', async (orig) => {
  const real = await orig<typeof import('../../api/translate')>()
  return {
    ...real,
    translateApi: { ...real.translateApi, engineList: vi.fn(async () => ({ items: [{ name: 'deepseek' }, { name: 'claude' }], default_engine: 'deepseek' })) },
  }
})
vi.mock('../../api/settings', async (orig) => ({
  ...(await orig<typeof import('../../api/settings')>()),
  getSettings: vi.fn(async () => ({ preferences: { default_locale: 'en-US' } })),
}))
// Job progress arrives over the event stream, which these tests have no use for.
vi.mock('../../hooks/useJob', async (orig) => ({ ...(await orig<typeof import('../../hooks/useJob')>()), useJob: () => ({ job: null }) }))

// The box's own plan views are stood in for, so a test reaches their buttons' handlers directly.
const shown = vi.hoisted(() => ({ linkPlan: null as null | { onOnlyAdd: () => void }, filePlan: null as null | { onOnlyAdd: () => void } }))
vi.mock('./QuickVideoLink', () => ({
  QuickVideoLinkPlan: (p: { onOnlyAdd: () => void }) => { shown.linkPlan = p; return <i data-stub="link-plan" /> },
  QuickVideoRuns: () => null,
}))
vi.mock('./MakeSubtitles', async (orig) => ({
  ...(await orig<typeof import('./MakeSubtitles')>()),
  FilePlan: (p: { onOnlyAdd: () => void }) => { shown.filePlan = p; return <i data-stub="file-plan" /> },
}))

const { act, createElement, useEffect } = await import('react')
const { createRoot } = await import('react-dom/client')
const { renderToStaticMarkup } = await import('react-dom/server')
const { AddBox, ADD_LINK_ID } = await import('./AddBox')
const { FileRunStatus } = await import('./MakeSubtitles')
const { useFileRun } = await import('./useFileRun')

afterAll(() => vi.unstubAllGlobals())

type Root = ReturnType<typeof createRoot>
let root: Root | null = null
let container: FakeElement
const mount = async (el: ReactElement) => {
  container = doc.body.appendChild(doc.createElement('div')) as FakeElement
  root = createRoot(container as unknown as Element)
  await act(async () => root!.render(el))
}
const propsOf = (el: FakeElement | null) => {
  const key = Object.keys(el ?? {}).find((k) => k.startsWith('__reactProps$'))
  if (!el || !key) throw new Error('not rendered by React')
  return (el as unknown as Record<string, Record<string, (...a: unknown[]) => void> & { value?: string }>)[key]
}
const byStub = (name: string) => container.find((e) => e.getAttribute('data-stub') === name)

beforeEach(() => {
  stored.clear()
  vi.clearAllMocks()
  // Never answers unless a test says so: the saved run's job is neither found nor lost.
  api.probeJob.mockImplementation(() => new Promise(() => undefined))
  shown.linkPlan = null
  shown.filePlan = null
})
afterEach(async () => {
  await act(async () => root?.unmount())
  root = null
})

describe('a file handed to the Add page while a saved run is still going', () => {
  const request: FileRunRequest = { file: new File(['abc'], 'Second.mp3'), engine: 'claude', language: 'ja', title: 'Second', variant: 'en-GB' }
  const seen: { run: ReturnType<typeof useFileRun> | null } = { run: null }
  function Harness() {
    const r = useFileRun({ runs: true, request })
    useEffect(() => { seen.run = r })
    return null
  }
  const run = () => seen.run!
  const saveRun = () => stored.set('baihe.pref.makeSubtitles.run', JSON.stringify({ dramaId: 7, step: 'transcribe' }))

  it('waits, takes no part in that run, then is in the box after Make another, unstarted', async () => {
    saveRun()
    await mount(<Harness />)

    expect(run().showingRun).toBe(true)
    expect(run().flow).toMatchObject({ phase: 'running', dramaId: 7 })
    expect(api.createDrama).not.toHaveBeenCalled()
    expect(api.uploadAndTranscribe).not.toHaveBeenCalled()
    // The saved run's later steps read these, so none may be the other file's.
    expect(run().engine).toBe('deepseek')
    expect(run().variant).toBe('')
    expect(run().title).toBe('')
    expect(run().language).toBe('zh')
    expect(run().file).toBeNull()
    expect(renderToStaticMarkup(<FileRunStatus run={run()} />)).toContain(
      '“Second.mp3” has not been started, because this run was already under way. It will be here to start when this one is finished.',
    )

    await act(async () => run().reset())
    expect(run().flow.phase).toBe('idle')
    expect(run().file).toBe(request.file)
    expect(run()).toMatchObject({ engine: 'claude', language: 'ja', title: 'Second', variant: 'en-GB', waiting: null })
    expect(api.createDrama).not.toHaveBeenCalled()
    expect(api.uploadAndTranscribe).not.toHaveBeenCalled()
    expect(api.startTranslateRun).not.toHaveBeenCalled()
  })

  it('is in the box, unstarted, when the saved run turns out to be lost', async () => {
    saveRun()
    api.probeJob.mockImplementation(async () => null)
    await mount(<Harness />)

    expect(run().flow.phase).toBe('error')
    expect(run().file).toBe(request.file)
    expect(run()).toMatchObject({ engine: 'claude', language: 'ja', title: 'Second', variant: 'en-GB', waiting: null })
    expect(api.createDrama).not.toHaveBeenCalled()
    expect(api.uploadAndTranscribe).not.toHaveBeenCalled()
  })

  it('starts at once when there is no saved run', async () => {
    await mount(<Harness />)
    expect(api.createDrama).toHaveBeenCalledWith(expect.objectContaining({ title_en: 'Second', source_language: 'ja' }))
    expect(api.uploadAndTranscribe).toHaveBeenCalledTimes(1)
    expect(run().waiting).toBeNull()
  })
})

describe('the Library box after Only add it', () => {
  it('is empty again after handing a link to New title', async () => {
    const onNewTitle = vi.fn()
    await mount(createElement(AddBox, { place: 'library', onNewTitle }))
    const field = () => container.find((e) => e.getAttribute('id') === ADD_LINK_ID)
    const link = 'https://www.youtube.com/watch?v=abc'
    await act(async () => {
      propsOf(field()).onPaste()
      propsOf(field()).onChange({ target: { value: link } })
    })
    expect(byStub('link-plan')).not.toBeNull()

    await act(async () => shown.linkPlan!.onOnlyAdd())
    expect(onNewTitle).toHaveBeenCalledWith({ link })
    expect(propsOf(field()).value).toBe('')
    expect(byStub('link-plan')).toBeNull()
  })

  it('is empty again after handing a file to New title, and the same file can be chosen again', async () => {
    const onNewTitle = vi.fn()
    await mount(createElement(AddBox, { place: 'library', onNewTitle }))
    const input = container.find((e) => e.getAttribute('aria-label') === 'Audio or video file')!
    input.value = 'C:\\fakepath\\ep1.mp4'
    await act(async () => propsOf(input).onChange({ target: { files: [new File(['abc'], 'ep1.mp4')] } }))
    expect(byStub('file-plan')).not.toBeNull()

    await act(async () => shown.filePlan!.onOnlyAdd())
    expect(onNewTitle).toHaveBeenCalledWith({ fileName: 'ep1.mp4' })
    expect(byStub('file-plan')).toBeNull()
    expect(input.value).toBe('')
  })

  it('does not take a link on Enter while an input method is composing', async () => {
    await mount(createElement(AddBox, { place: 'library', onNewTitle: vi.fn() }))
    const field = () => container.find((e) => e.getAttribute('id') === ADD_LINK_ID)
    const enter = (isComposing: boolean) => propsOf(field()).onKeyDown({ key: 'Enter', nativeEvent: { isComposing }, preventDefault() {} })
    await act(async () => propsOf(field()).onChange({ target: { value: 'https://a.example/1' } }))
    await act(async () => enter(true))
    expect(byStub('link-plan')).toBeNull()
    await act(async () => enter(false))
    expect(byStub('link-plan')).not.toBeNull()
  })
})
