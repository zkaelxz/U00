import { isValidElement, type ReactElement, type ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import type { ReviewLine } from '../../types/review'
import { NowCard, type NowCardProps } from './NowCard'
import { CONFLICT_TEXT, CONFLICT_UNREAD_TEXT, openFix, type LineFix } from './watchLogic'

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
const byType = (tree: ReactNode, tag: string) => find(tree, (_p, type) => type === tag)

// A fix refused because the line changed, as saveFix leaves it (its own tests are in watchLogic.test.ts).
const refusedFix = (now: string | null): LineFix => ({ ...openFix(lines[1]), seen: now ?? 'Line 2', draft: 'Mine', conflict: true, now })

describe('NowCard', () => {
  it('asks for play before the first line, with no box', () => {
    const out = html(props({ line: null }))
    expect(out).toContain('Press play. The line being spoken shows here.')
    expect(out).not.toContain('watch-now-box')
    expect(out).not.toContain('Fix this line')
  })

  it('shows the line number and start, the original in its language and the English in a field-like box', () => {
    const out = html(props())
    expect(out).toContain('Line #2 · 0:04.0')
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

  it('a click on the box and Fix this line both ask the page to open the fix', () => {
    const onOpen = vi.fn()
    const tree = NowCard(props({ onOpen }))
    ;(byTestId(tree, 'watch-now-box')!.props.onClick as () => void)()
    ;(byText(tree, 'Fix this line')!.props.onClick as () => void)()
    expect(onOpen).toHaveBeenCalledTimes(2)
  })

  it('can take focus as a whole, so Space after Save and play goes to the player', () => {
    expect(html(props())).toMatch(/^<section class="card watch-now" aria-label="Line being spoken" tabindex="-1">/)
  })

  it('turns the box into a text field holding the draft, with Save and play and Cancel', () => {
    const out = html(props({ fix: { ...openFix(lines[1]), draft: 'Typed' } }))
    expect(out).toMatch(/<textarea[^>]*id="watch-now-field"[^>]*>Typed<\/textarea>/)
    expect(out).toContain('<label class="watch-now-label" for="watch-now-field">English</label>')
    expect(out).toContain('Save and play')
    expect(out).toContain('>Cancel<')
    expect(out).not.toContain('watch-now-box')
  })

  // Pressed on the form, so they work from Save and play, Cancel and Hear it again as well as from the box.
  it('Ctrl+Enter saves and Esc cancels anywhere in the fix, not only in the box', () => {
    const onSave = vi.fn()
    const onCancel = vi.fn()
    const tree = NowCard(props({ fix: openFix(lines[1]), onSave, onCancel }))
    expect(byTestId(tree, 'watch-now-field')!.props.onKeyDown).toBeUndefined()
    const keyDown = byType(tree, 'form')!.props.onKeyDown as (e: object) => void
    const preventDefault = vi.fn()
    const press = (e: object) => keyDown({ key: '', ctrlKey: false, metaKey: false, altKey: false, preventDefault, ...e })
    press({ key: 'Enter' })
    expect(onSave).not.toHaveBeenCalled()
    expect(preventDefault).not.toHaveBeenCalled()
    press({ key: 'Enter', ctrlKey: true })
    expect(onSave).toHaveBeenCalledOnce()
    press({ key: 'Escape' })
    expect(onCancel).toHaveBeenCalledOnce()
    expect(preventDefault).toHaveBeenCalledTimes(2)
  })

  it('while saving, the keys do nothing and the box is read-only, not disabled, so it keeps focus and nothing typed is lost', () => {
    const onSave = vi.fn()
    const onCancel = vi.fn()
    const p = props({ fix: openFix(lines[1]), busy: true, onSave, onCancel })
    const keyDown = byType(NowCard(p), 'form')!.props.onKeyDown as (e: object) => void
    keyDown({ key: 'Escape', ctrlKey: false, metaKey: false, altKey: false, preventDefault: vi.fn() })
    keyDown({ key: 'Enter', ctrlKey: true, metaKey: false, altKey: false, preventDefault: vi.fn() })
    expect(onSave).not.toHaveBeenCalled()
    expect(onCancel).not.toHaveBeenCalled()
    const field = html(p).match(/<textarea[^>]*>/)![0]
    expect(field).toContain('readOnly=""')
    expect(field).not.toContain('disabled')
    expect(html(props({ fix: openFix(lines[1]) })).match(/<textarea[^>]*>/)![0]).not.toContain('readOnly')
  })

  it('a refused save keeps the draft in the field and shows under the alert what the line says now', () => {
    const out = html(props({ line: line(2, 4, { en: 'Theirs' }), fix: refusedFix('Theirs') }))
    expect(out).toContain('>Mine</textarea>')
    expect(CONFLICT_TEXT).toBe('This line was changed while you were fixing it. What it says now is shown below, and your text is still in the box. Save again if it still needs your fix.')
    expect(out).toContain(`<p class="error" role="alert">${CONFLICT_TEXT}</p><div class="watch-now-theirs"><span class="watch-now-label">It now says</span><p class="watch-now-theirs-en" lang="en">Theirs</p></div>`)
  })

  it('a refused save whose line now has no English says No English yet under It now says', () => {
    expect(html(props({ fix: refusedFix('') }))).toContain('<span class="watch-now-label">It now says</span><p class="watch-now-theirs-en is-empty">No English yet</p>')
  })

  it('a refused save whose new text could not be read says so and shows nothing below', () => {
    const out = html(props({ fix: refusedFix(null) }))
    expect(out).toContain('>Mine</textarea>')
    expect(CONFLICT_UNREAD_TEXT).toBe('This line was changed while you were fixing it, and its new text could not be loaded. Your text is still in the box. Try Save again in a moment.')
    expect(out).toContain(`<p class="error" role="alert">${CONFLICT_UNREAD_TEXT}</p>`)
    expect(out).not.toContain('It now says')
    expect(out).not.toContain(CONFLICT_TEXT)
  })

  it('any other failure shows the usual error text and keeps the draft', () => {
    const error = new ApiError(500, { code: 'internal_error', message: 'Something went wrong.' }, 'POST')
    const out = html(props({ fix: { ...openFix(lines[1]), draft: 'Mine', error } }))
    expect(out).toContain('<div class="banner error-banner" role="alert"><div><strong>Something went wrong inside Baihe. Details are in the app log.</strong></div></div>')
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
