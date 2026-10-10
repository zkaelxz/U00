/*
 * The Reader's "Aa" panel: theme, font, text size, spacing, width (desktop
 * only), lines per page and spoiler-free. A popover under the Aa button on
 * desktop, a bottom Sheet on phones (where it also holds "Go to" and the
 * reading metrics, passed in as children).
 */
import { useCallback, useRef, useState, type ReactNode } from 'react'

import { Field } from '../../components/Field'
import { Sheet } from '../../components/Sheet'
import { usePopoverDismiss } from '../../hooks/usePopoverDismiss'
import {
  CHAPTER_SIZE,
  FONT_OPTIONS,
  FONT_SIZE,
  SPACING_OPTIONS,
  THEME_OPTIONS,
  WIDTH_OPTIONS,
  roundChapterSize,
  type ReaderFont,
  type ReaderPrefs,
  type ReaderTheme,
} from './readerPrefsStore'

type Props = {
  prefs: ReaderPrefs
  onChange: (next: ReaderPrefs) => void
  phone: boolean
  children?: ReactNode
}

function PrefsForm({ prefs, onChange, phone }: Omit<Props, 'children'>) {
  const set = <K extends keyof ReaderPrefs>(k: K, v: ReaderPrefs[K]) => onChange({ ...prefs, [k]: v })
  const size = (n: number) => set('fontSize', Math.min(FONT_SIZE.max, Math.max(FONT_SIZE.min, n)))
  return (
    <div className="reader-prefs-form">
      <Field label="Theme">
        <select value={prefs.theme} onChange={(e) => set('theme', e.target.value as ReaderTheme)}>
          {THEME_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </Field>
      <Field label="Font">
        <select value={prefs.font} onChange={(e) => set('font', e.target.value as ReaderFont)}>
          {FONT_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </Field>
      <div className="reader-size-row">
        <Field label="Text size" unit="px">
          <input
            type="number"
            min={FONT_SIZE.min}
            max={FONT_SIZE.max}
            defaultValue={prefs.fontSize}
            key={prefs.fontSize}
            onBlur={(e) => {
              const n = Math.round(Number(e.target.value))
              if (Number.isFinite(n) && n !== prefs.fontSize) size(n)
              else e.target.value = String(prefs.fontSize)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') e.currentTarget.blur()
            }}
          />
        </Field>
        <button type="button" aria-label="Smaller text" onClick={() => size(prefs.fontSize - 1)} disabled={prefs.fontSize <= FONT_SIZE.min}>
          −
        </button>
        <button type="button" aria-label="Larger text" onClick={() => size(prefs.fontSize + 1)} disabled={prefs.fontSize >= FONT_SIZE.max}>
          +
        </button>
      </div>
      <Field label="Spacing">
        <select value={prefs.lineHeight} onChange={(e) => set('lineHeight', Number(e.target.value))}>
          {SPACING_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </Field>
      {!phone && (
        <Field label="Width" help="How wide the text column gets on a large screen.">
          <select value={prefs.maxWidth} onChange={(e) => set('maxWidth', Number(e.target.value))}>
            {WIDTH_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Field>
      )}
      <Field label="Lines per page" help="10 to 200, in steps of 10. Changing it keeps your place.">
        <input
          type="number"
          min={CHAPTER_SIZE.min}
          max={CHAPTER_SIZE.max}
          step={CHAPTER_SIZE.step}
          defaultValue={prefs.chapterSize}
          key={prefs.chapterSize}
          onBlur={(e) => {
            const n = roundChapterSize(Number(e.target.value))
            if (n !== prefs.chapterSize) set('chapterSize', n)
            else e.target.value = String(n)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') e.currentTarget.blur()
          }}
        />
      </Field>
      <div className="toggle-list">
        <Field
          label="Spoiler-free"
          help="Who is, Explain, relationships and the wiki only use lines up to the end of this page. Recap covers the lines before it. Q&A always uses the whole title."
        >
          <input type="checkbox" checked={prefs.spoilerFree} onChange={(e) => set('spoilerFree', e.target.checked)} />
        </Field>
      </div>
    </div>
  )
}

export function ReaderPrefsControl({ prefs, onChange, phone, children }: Props) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)

  const close = useCallback(() => setOpen(false), [])
  usePopoverDismiss(open && !phone, wrap, buttonRef, close, true)

  const button = (
    <button
      ref={buttonRef}
      type="button"
      className="reader-aa"
      aria-label="Reading settings"
      aria-expanded={open}
      onClick={() => setOpen((v) => !v)}
    >
      Aa
    </button>
  )

  if (phone) {
    return (
      <>
        {button}
        <Sheet open={open} title="Reading settings" onClose={() => setOpen(false)}>
          {children}
          <PrefsForm prefs={prefs} onChange={onChange} phone />
        </Sheet>
      </>
    )
  }
  return (
    <div className="reader-aa-wrap" ref={wrap}>
      {button}
      {open && (
        <div className="reader-popover" role="dialog" aria-label="Reading settings">
          <PrefsForm prefs={prefs} onChange={onChange} phone={false} />
        </div>
      )}
    </div>
  )
}
