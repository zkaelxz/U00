/** Redo for the comic panel: which pages (the one on screen, a typed set such as "5-20", or
 *  every page / the chapter) and how much to redo (everything, only the AI and typeset on the
 *  text already read, or only the typeset). Sends nothing itself: the panel starts the job. */
import { useState } from 'react'

import { ConfirmButton } from '../../components/ConfirmButton'
import { Field } from '../../components/Field'
import { parsePageRanges } from './scanlateLogic'

export type RedoScope = 'page' | 'chosen' | 'all'
export type RedoHow = 'full' | 'keep_text' | 'typeset'

export interface RedoRequest {
  scope: RedoScope
  how: RedoHow
  // Positions (1-based) typed for "chosen".
  ordinals: number[]
}

const HOWS: { value: RedoHow; label: string; hint: string }[] = [
  { value: 'full', label: 'Read again and translate', hint: 'Finds the text boxes, reads them and translates again. Slowest.' },
  { value: 'keep_text', label: 'Keep the text already read', hint: 'Skips finding and reading the text. Translates it again with the AI and typesets. Much faster.' },
  { value: 'typeset', label: 'Typeset only', hint: 'Keeps the translation as it is and only redraws the pages. No AI cost.' },
]

interface Props {
  pageNumber: number | null
  pageCount: number
  // "this chapter" or "all pages", for the third scope.
  allName: string
  disabled: boolean
  busy: boolean
  onRedo: (req: RedoRequest) => void
}

export function RedoControls({ pageNumber, pageCount, allName, disabled, busy, onRedo }: Props) {
  const [scope, setScope] = useState<RedoScope>('page')
  const [how, setHow] = useState<RedoHow>('keep_text')
  const [text, setText] = useState('')
  const ordinals = scope === 'chosen' ? parsePageRanges(text, pageCount) : null
  const needsPages = scope === 'chosen' && !ordinals
  const hint = HOWS.find((h) => h.value === how)?.hint
  const target =
    scope === 'page' ? (pageNumber ? `page ${pageNumber}` : 'this page') : scope === 'chosen' ? 'the chosen pages' : allName

  return (
    <div className="stack redo-controls">
      <h3>Redo</h3>
      <Field label="Pages">
        <select value={scope} onChange={(e) => setScope(e.target.value as RedoScope)}>
          <option value="page">{pageNumber ? `This page (${pageNumber})` : 'This page'}</option>
          <option value="chosen">Pages I pick…</option>
          <option value="all">{allName.charAt(0).toUpperCase() + allName.slice(1)}</option>
        </select>
      </Field>
      {scope === 'chosen' && (
        <Field
          label="Which pages"
          help={`Page numbers or ranges, like 5-20 or 3, 8, 10-12. This title has ${pageCount} pages.`}
          error={text.trim() && !ordinals ? `Use numbers from 1 to ${pageCount}, like 5-20.` : undefined}
        >
          <input type="text" value={text} onChange={(e) => setText(e.target.value)} inputMode="numeric" />
        </Field>
      )}
      <Field label="How" help={hint}>
        <select value={how} onChange={(e) => setHow(e.target.value as RedoHow)}>
          {HOWS.map((h) => (
            <option key={h.value} value={h.value}>
              {h.label}
            </option>
          ))}
        </select>
      </Field>
      <div className="actions">
        <ConfirmButton
          name={target}
          label="Redo…"
          ariaLabel="Redo pages"
          verb="redo"
          confirmLabel={`Redo ${target}`}
          busy={busy}
          disabled={disabled || needsPages || (scope === 'page' && !pageNumber)}
          onConfirm={() => onRedo({ scope, how, ordinals: ordinals ?? [] })}
        />
      </div>
    </div>
  )
}
