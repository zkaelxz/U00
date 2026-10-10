/*
 * Comic viewer controls: the "Aa" view settings (a popover on desktop, a
 * bottom Sheet on phones, like the Reader's) and the pager with its page
 * scrubber. Right-to-left paging mirrors the pager so "forward" is on the left.
 */
import { useState, type FormEvent, type ReactNode } from 'react'

import { Field } from '../../components/Field'
import { AaControl } from '../../components/AaControl'
import { clampPage, FIT_OPTIONS, MODE_OPTIONS, type ComicFit, type ComicMode, type ComicPrefs } from './comicLogic'

type PrefsProps = {
  prefs: ComicPrefs
  onChange: (next: ComicPrefs) => void
  canTypeset: boolean
  // Phone: Typeset and Text live here too (desktop has them in the bar).
  phone: boolean
}

function ViewForm({ prefs, onChange, canTypeset, phone }: PrefsProps) {
  const set = <K extends keyof ComicPrefs>(k: K, v: ComicPrefs[K]) => onChange({ ...prefs, [k]: v })
  return (
    <div className="reader-prefs-form">
      <Field label="Reading mode" help="Vertical scroll stacks the pages. One page at a time turns pages with taps, arrows or the pager.">
        <select
          value={prefs.mode}
          onChange={(e) => {
            const mode = e.target.value as ComicMode
            onChange({ ...prefs, mode, fit: mode === 'paged' ? 'height' : 'width' })
          }}
        >
          {MODE_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </Field>
      <Field label="Fit">
        <select value={prefs.fit} onChange={(e) => set('fit', e.target.value as ComicFit)}>
          {FIT_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </Field>
      <div className="toggle-list">
        {prefs.mode === 'paged' && (
          <Field label="Right to left" help="For manga: the left side of the page and the left arrow go forward.">
            <input type="checkbox" checked={prefs.rtl} onChange={(e) => set('rtl', e.target.checked)} />
          </Field>
        )}
        {phone && (
          <Field label="Typeset pages" help={canTypeset ? 'Show the typeset (translated) image where a page has one.' : 'No page has been typeset yet.'}>
            <input type="checkbox" checked={prefs.typeset && canTypeset} disabled={!canTypeset} onChange={(e) => set('typeset', e.target.checked)} />
          </Field>
        )}
        {phone && (
          <Field label="Text boxes" help="Number the text boxes on each page. Tap Text at the bottom to read the lines.">
            <input type="checkbox" checked={prefs.text} onChange={(e) => set('text', e.target.checked)} />
          </Field>
        )}
      </div>
    </div>
  )
}

export function ComicViewControl({ children, ...props }: PrefsProps & { children?: ReactNode }) {
  return (
    <AaControl label="View settings" phone={props.phone} sheetExtra={children}>
      <ViewForm {...props} />
    </AaControl>
  )
}

export function ComicPager({ page, count, rtl, onGo, compact = false, loading = false }: {
  page: number
  count: number
  // Pages not loaded yet: a disabled pager that takes the loaded pager's space.
  loading?: boolean
  // Mirror the pager (forward on the left), for right-to-left paging.
  rtl: boolean
  onGo: (n: number) => void
  // Phone bottom bar: no text label (the top bar shows n / N).
  compact?: boolean
}) {
  const prev = (
    <button key="prev" type="button" aria-label="Previous page" disabled={loading || page <= 1} onClick={() => onGo(page - 1)}>
      {rtl ? '›' : '‹'}
    </button>
  )
  const next = (
    <button key="next" type="button" aria-label="Next page" disabled={loading || page >= count} onClick={() => onGo(page + 1)}>
      {rtl ? '‹' : '›'}
    </button>
  )
  return (
    <nav className="comic-pager" aria-label="Pages">
      {rtl ? next : prev}
      <input
        className="comic-scrubber"
        type="range"
        min={1}
        max={Math.max(1, count)}
        step={1}
        value={page}
        dir={rtl ? 'rtl' : 'ltr'}
        aria-label="Page"
        aria-valuetext={`Page ${page} of ${count}`}
        disabled={loading || count <= 1}
        onChange={(e) => onGo(Number(e.target.value))}
      />
      {rtl ? prev : next}
      {!compact && (
        <span className="comic-page-label" data-testid="comic-page-label" aria-live="polite">
          {loading ? 'Page - of -' : `Page ${page} of ${count}`}
        </span>
      )}
    </nav>
  )
}

export function ComicGoTo({ count, onGo }: { count: number; onGo: (n: number) => void }) {
  const [value, setValue] = useState('')
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const n = Number(value)
    if (Number.isFinite(n) && n >= 1) onGo(clampPage(n, count))
    setValue('')
  }
  return (
    <form className="reader-goto" onSubmit={submit}>
      <Field label="Go to page">
        <input
          type="number"
          min={1}
          max={count}
          inputMode="numeric"
          enterKeyHint="go"
          autoComplete="off"
          placeholder="Page"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
      </Field>
      <button type="submit" disabled={!value}>Go</button>
    </form>
  )
}
