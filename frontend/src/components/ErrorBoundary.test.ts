import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { installBootFallback, renderBootFallback } from '../bootFallback'
import { ErrorBoundary } from './ErrorBoundary'
import { errorText } from './errorFallbackText'

function Boom(): never {
  throw new TypeError("Cannot read properties of undefined (reading 'toFixed')")
}

// No DOM in this test environment, so drive the boundary the way React does:
// getDerivedStateFromError on a throw, getDerivedStateFromProps on each render.
function boundary(resetKey: string) {
  const props = { resetKey, children: createElement(Boom) }
  const b = new ErrorBoundary(props)
  const apply = (patch: object | null) => {
    if (patch) b.state = { ...b.state, ...patch }
  }
  const setProps = (next: string) => {
    ;(b as { props: typeof props }).props = { ...props, resetKey: next }
    apply(ErrorBoundary.getDerivedStateFromProps(b.props, b.state))
  }
  return { b, apply, setProps }
}

describe('ErrorBoundary', () => {
  it('shows the fallback with the message, Copy, Reload and Diagnostics after a child throws', () => {
    const { b, apply } = boundary('#/library')
    let thrown: unknown
    try {
      renderToStaticMarkup(createElement(Boom))
    } catch (e) {
      thrown = e
    }
    apply(ErrorBoundary.getDerivedStateFromError(thrown))
    const html = renderToStaticMarkup(b.render() as never)
    expect(html).toContain('This page hit an error.')
    expect(html).toContain('reading &#x27;toFixed&#x27;')
    expect(html).toContain('Copy error')
    expect(html).toContain('Reload')
    expect(html).toContain('href="#/diagnostics"')
  })

  it('resets when the route changes, and not before', () => {
    const { b, apply, setProps } = boundary('#/library')
    apply(ErrorBoundary.getDerivedStateFromError(new Error('x')))
    setProps('#/library')
    expect(b.state.hasError).toBe(true)
    setProps('#/settings')
    expect(b.state.hasError).toBe(false)
    expect(b.render()).toBe(b.props.children)
  })

  it('renders its children when nothing threw', () => {
    const b = new ErrorBoundary({ resetKey: 'k', children: createElement('p', null, 'fine') })
    expect(renderToStaticMarkup(b.render() as never)).toBe('<p>fine</p>')
  })

  it('shows only the message, capped', () => {
    expect(errorText(new Error('bad thing'))).toBe('bad thing')
    expect(errorText('plain')).toBe('plain')
    expect(errorText(new Error('x'.repeat(5000))).length).toBe(2001)
  })
})

// A tiny stand-in DOM, enough for the boot fallback's createElement/append.
type FakeEl = {
  tag: string; id?: string; className?: string; textContent?: string; type?: string
  children: FakeEl[]; attrs: Record<string, string>
  setAttribute(k: string, v: string): void
  addEventListener(): void
  append(...c: FakeEl[]): void
  appendChild(c: FakeEl): void
  replaceChildren(...c: FakeEl[]): void
}
function fakeRoot() {
  const make = (tag: string): FakeEl => ({
    tag, children: [], attrs: {},
    setAttribute(k, v) { this.attrs[k] = v },
    addEventListener() {},
    append(...c) { this.children.push(...c) },
    appendChild(c) { this.children.push(c) },
    replaceChildren(...c) { this.children = c },
  })
  const root = make('div') as FakeEl & { ownerDocument: unknown }
  root.ownerDocument = { createElement: make, defaultView: null }
  return root
}
const text = (el: FakeEl): string => (el.textContent ?? '') + el.children.map(text).join(' ')

describe('boot fallback', () => {
  it('writes a plain message into an empty #root', () => {
    const root = fakeRoot()
    expect(renderBootFallback(root as unknown as HTMLElement, new Error('first render failed'))).toBe(true)
    expect(text(root)).toContain('could not start')
    expect(text(root)).toContain('first render failed')
    expect(text(root)).toContain('Reload')
  })

  it('replaces the static index.html note', () => {
    const root = fakeRoot()
    const note = fakeRoot()
    note.id = 'boot-static'
    root.appendChild(note)
    expect(renderBootFallback(root as unknown as HTMLElement, new Error('boom'))).toBe(true)
    expect(root.children).toHaveLength(1)
    expect(text(root)).toContain('boom')
  })

  it('leaves a mounted app alone', () => {
    const root = fakeRoot()
    root.appendChild(fakeRoot())
    expect(renderBootFallback(root as unknown as HTMLElement, new Error('x'))).toBe(false)
    expect(root.children).toHaveLength(1)
  })

  it('listens for window errors and unhandled rejections', () => {
    const listeners: Record<string, (e: unknown) => void> = {}
    const win = {
      addEventListener: (t: string, f: (e: unknown) => void) => { listeners[t] = f },
      setTimeout: (f: () => void) => f(),
    }
    const root = fakeRoot()
    installBootFallback(root as unknown as HTMLElement, win as unknown as Window)
    listeners.unhandledrejection({ reason: new Error('import failed') })
    expect(text(root)).toContain('import failed')
    expect(Object.keys(listeners).sort()).toEqual(['error', 'unhandledrejection'])
  })
})
