import { isValidElement, type ReactElement, type ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import type { ReviewLine } from '../../types/review'
import { NowCard, type NowCardProps } from './NowCard'
import { CONFLICT_TEXT, currentLine, openFix, saveFix, watchKey, type LineFix } from './watchLogic'

const line = (id: number, start: number, over: Partial<ReviewLine> = {}): ReviewLine => ({
  id, idx: id - 1, start, end: start + 1.5, zh: `第${id}句`, en: `Line ${id}`, speaker: null, speaker_manual: false, sfx: false,
  flag: null, flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null, ...over,
})
const lines = [line(1, 1), line(2, 4), line(3, 8)]

const props = (over: Partial<NowCardProps> = {}): NowCardProps => ({
  line: lines[1], sourceLanguage: 'zh', canEdit: true, fix: null, busy: false, status: '',
  onOpen: vi.fn(), onDraft: vi.fn(), onSave: vi.fn(), onCancel: vi.fn(), onAgain: vi.fn(), ...over,
})
const html = (p: NowCardProps) => renderToStaticMarkup(<NowCard {...p} />)

type Props = Record<string, unknown> & { children?: ReactNode }
// The card has no hooks, so its element tree can be searched and its handlers called without a browser.
function find(node: ReactNode, match: (p: Props, type: unknown) => boolean): ReactElement<Props> | null {
  if (Array.isArray(node)) {
    for (const n of node) {
      const hit = find(n, match)
      if (hit) return hit
    }
    return null
  }
  if (!isValidElement<Props>(node)) return null
  if (match(node.props, node.type)) return node
  return find(node.props.children, match)
}
const byTestId = (tree: ReactNode, id: string) => find(tree, (p) => p['data-testid'] === id)
const byText = (tree: ReactNode, text: string) => find(tree, (p, type) => type === 'button' && p.children === text)

// The page's own wiring, reduced to what the card asks of it: opening takes the line under the playhead at that
// moment, and saving goes through saveFix.
function page(time: () => number) {
  let fix: LineFix | null = null
  const pause = vi.fn()
  return {
    pause,
    get fix() { return fix },
    open: () => {
      pause()
      const l = currentLine(lines, time())
      if (l) fix = openFix(l)
    },
  }
}

describe('NowCard', () => {
  it('asks for play before the first line, with no box', () => {
    const out = html(props({ line: null }))
    expect(out).toContain('Press play. The line being spoken shows here.')
    expect(out).not.toContain('watch-now-box')
    expect(out).not.toContain('Fix this line')
  })

  it('shows the line number and start, the original in its language and the English in a field-like box', () => {
    const out = html(props())
    expect(out).toContain('Line 2 · 0:04.0')
    expect(out).toContain('<p class="watch-now-zh" lang="zh">第2句</p>')
    expect(out).toContain('>English<')
    expect(out).toMatch(/<button[^>]*class="watch-now-box"[^>]*>Line 2<\/button>/)
    expect(out).toContain('Fix this line')
    expect(out).toContain('Hear it again')
    expect(html(props({ line: line(2, 4, { lang: 'ja' }) }))).toContain('lang="ja"')
  })

  it('says No English yet, muted, for a line without English, and still offers the fix', () => {
    const out = html(props({ line: line(2, 4, { en: '' }) }))
    expect(out).toMatch(/class="watch-now-box is-empty"[^>]*>No English yet</)
    expect(out).toContain('Fix this line')
  })

  it('E, a click on the box and Fix this line open the field on the line current at that moment', () => {
    expect(watchKey('e', { closest: () => null })).toBe('fix')
    let t = 4.5
    const p = page(() => t)
    const tree = NowCard(props({ onOpen: p.open }))
    t = 8.2 // the sound moved on after the card was drawn
    ;(byTestId(tree, 'watch-now-box')!.props.onClick as () => void)()
    expect(p.pause).toHaveBeenCalled()
    expect(p.fix).toEqual({ lineId: 3, seen: 'Line 3', draft: 'Line 3', conflict: false, error: null })
    const onOpen = vi.fn()
    ;(byText(NowCard(props({ onOpen })), 'Fix this line')!.props.onClick as () => void)()
    expect(onOpen).toHaveBeenCalledOnce()
  })

  it('turns the box into a text field holding the draft, with Save and play and Cancel', () => {
    const out = html(props({ fix: { ...openFix(lines[1]), draft: 'Typed' } }))
    expect(out).toMatch(/<textarea[^>]*id="watch-now-field"[^>]*>Typed<\/textarea>/)
    expect(out).toContain('<label class="watch-now-label" for="watch-now-field">English</label>')
    expect(out).toContain('Save and play')
    expect(out).toContain('>Cancel<')
    expect(out).not.toContain('watch-now-box')
  })

  it('Ctrl+Enter saves and Esc cancels from the field', () => {
    const onSave = vi.fn()
    const onCancel = vi.fn()
    const tree = NowCard(props({ fix: openFix(lines[1]), onSave, onCancel }))
    const keyDown = byTestId(tree, 'watch-now-field')!.props.onKeyDown as (e: object) => void
    const press = (e: object) => keyDown({ key: '', ctrlKey: false, metaKey: false, altKey: false, preventDefault: vi.fn(), ...e })
    press({ key: 'Enter' })
    expect(onSave).not.toHaveBeenCalled()
    press({ key: 'Enter', ctrlKey: true })
    expect(onSave).toHaveBeenCalledOnce()
    press({ key: 'Escape' })
    expect(onCancel).toHaveBeenCalledOnce()
  })

  it('Save sends { en, expected: { en } }, and an unchanged text sends nothing', async () => {
    const patch = vi.fn(async (_id: number, body: { en?: string }) => ({ ...lines[1], en: body.en ?? '' }))
    await saveFix({ ...openFix(lines[1]), draft: 'Fixed' }, { patch, reload: vi.fn() })
    expect(patch).toHaveBeenCalledWith(2, { en: 'Fixed', expected: { en: 'Line 2' } })
    patch.mockClear()
    expect(await saveFix(openFix(lines[1]), { patch, reload: vi.fn() })).toEqual({ kind: 'unchanged' })
    expect(patch).not.toHaveBeenCalled()
  })

  it('a 409 reloads the lines, keeps the draft in the field and says so as an alert', async () => {
    const reload = vi.fn(async () => [lines[0], line(2, 4, { en: 'Theirs' }), lines[2]])
    const patch = vi.fn(async () => { throw new ApiError(409, { code: 'conflict', message: 'Changed.' }, 'POST') })
    const r = await saveFix({ ...openFix(lines[1]), draft: 'Mine' }, { patch, reload })
    expect(reload).toHaveBeenCalledOnce()
    if (r.kind !== 'conflict') throw new Error(r.kind)
    const out = html(props({ line: line(2, 4, { en: 'Theirs' }), fix: r.fix }))
    expect(out).toContain('>Mine</textarea>')
    expect(out).toContain(`<p class="error" role="alert">${CONFLICT_TEXT}</p>`)
  })

  it('any other failure shows the usual error text and keeps the draft', () => {
    const error = new ApiError(500, { code: 'internal_error', message: 'Something went wrong.' }, 'POST')
    const out = html(props({ fix: { ...openFix(lines[1]), draft: 'Mine', error } }))
    expect(out).toContain('role="alert"')
    expect(out).toContain('>Mine</textarea>')
  })

  it('announces Saved. in a status region that is always there', () => {
    expect(html(props())).toContain('<p class="watch-now-status" role="status"></p>')
    expect(html(props({ status: 'Saved.' }))).toContain('role="status">Saved.</p>')
  })

  it('a viewer without lines.edit gets the English as plain large text and no fix', () => {
    const out = html(props({ canEdit: false }))
    expect(out).toContain('<p class="watch-now-en" data-testid="watch-now-en" lang="en">Line 2</p>')
    expect(out).not.toContain('watch-now-box')
    expect(out).not.toContain('Fix this line')
    expect(out).not.toContain('<textarea')
    expect(out).toContain('Hear it again')
  })
})
