/*
 * The Reader's glossary tools, shown above the text in Edit mode: the term
 * editor a tap on a highlighted name opens, and the "replace everywhere"
 * panel a text selection opens. Both write through the same glossary and
 * find-and-replace routes Translate and Review use.
 */
import { useEffect, useState, type RefObject } from 'react'

import { applyFindReplace, previewFindReplace } from '../../api/review'
import { getGlossaryCatalogues, getGlossaryTerms, saveGlossaryTerm, deleteGlossaryTerms } from '../../api/translateStage'
import { Field } from '../../components/Field'
import { Sheet } from '../../components/Sheet'
import { useLatest } from '../../hooks/useLatest'
import { buttonClass } from '../../components/uiClasses'
import type { ReviewMatch } from '../../types/review'
import type { GlossaryTerm } from '../../types/translateStage'
import { ActionError } from './ReaderAction'
import { useAction } from './useReaderAction'

const MAX_NOTES = 1000

/** The term a tap on highlighted text opens, or a new one made from a selection. */
export type TermDraft = Pick<GlossaryTerm, 'term_original' | 'term_translation' | 'category' | 'notes'> & { id?: number }

export function TermEditor({ dramaId, draft, onClose, onChanged }: {
  dramaId: number
  draft: TermDraft
  onClose: () => void
  /** A term was saved or deleted: the page and the term list need reloading. */
  onChanged: () => void
}) {
  const isNew = draft.id === undefined
  const [original, setOriginal] = useState(draft.term_original)
  const [translation, setTranslation] = useState(draft.term_translation)
  const [category, setCategory] = useState(draft.category ?? 'other')
  const [notes, setNotes] = useState(draft.notes ?? '')
  const [categories, setCategories] = useState<{ key: string; label: string }[]>([])
  useEffect(() => {
    let live = true
    getGlossaryCatalogues().then((c) => live && setCategories(c.term_categories), () => {})
    return () => {
      live = false
    }
  }, [])
  const save = useAction(async () => {
    await saveGlossaryTerm(dramaId, {
      ...(draft.id !== undefined ? { id: draft.id } : {}),
      term_original: original.trim(),
      term_translation: translation.trim(),
      category,
      notes: notes.trim(),
    })
    onChanged()
    onClose()
  })
  const remove = useAction(async () => {
    if (draft.id === undefined) return
    await deleteGlossaryTerms(dramaId, [draft.id])
    onChanged()
    onClose()
  })
  const busy = save.busy || remove.busy
  return (
    <form
      className="reader-edit-form reader-term-form"
      aria-label="Edit term"
      onSubmit={(e) => {
        e.preventDefault()
        void save.run()
      }}
    >
      <p className="muted">Customize how this term is translated in future chapters.</p>
      {isNew ? (
        <Field label="Original" help="The name or term as written in the source text.">
          <input value={original} lang="zh" maxLength={200} onChange={(e) => setOriginal(e.target.value)} />
        </Field>
      ) : (
        <p>
          Raw name: <strong lang="zh">{draft.term_original}</strong>
          <button type="button" className="link" aria-label="Copy raw name"
            onClick={() => void navigator.clipboard?.writeText(draft.term_original)}>
            Copy
          </button>
        </p>
      )}
      <Field label="Translated name">
        <input value={translation} maxLength={200} onChange={(e) => setTranslation(e.target.value)} />
      </Field>
      <Field label="Category">
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {categories.length === 0 && <option value={category}>{category}</option>}
          {categories.map((c) => (
            <option key={c.key} value={c.key}>{c.label}</option>
          ))}
        </select>
      </Field>
      <Field label="Notes" help="For you and the translator, such as a pinyin spelling. Not shown in the English text.">
        <textarea value={notes} rows={2} maxLength={MAX_NOTES} placeholder="Optional" onChange={(e) => setNotes(e.target.value)} />
      </Field>
      <p className="muted reader-term-count" aria-live="off">{notes.length}/{MAX_NOTES}</p>
      <button type="submit" className={buttonClass()} disabled={busy || !translation.trim() || !original.trim()}>
        Save changes
      </button>
      {!isNew && (
        <button type="button" className="link reader-term-delete" disabled={busy} onClick={() => void remove.run()}>
          Delete term
        </button>
      )}
      <ActionError action={save} />
      <ActionError action={remove} />
    </form>
  )
}

/** Replace `selected` in every English line. Previewed first; a line edited since is skipped, not overwritten. */
export function ReplaceEverywhere({ dramaId, selected, onClose, onApplied, onAddTerm }: {
  dramaId: number
  selected: string
  onClose: () => void
  onApplied: () => void
  onAddTerm: (text: string) => void
}) {
  const [replacement, setReplacement] = useState('')
  const [matches, setMatches] = useState<ReviewMatch[] | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const preview = useAction(async () => {
    setDone(null)
    setMatches(await previewFindReplace(dramaId, { find: selected, replace: replacement, case_sensitive: true, use_regex: false }))
  })
  const apply = useAction(async () => {
    if (!matches?.length) return
    const r = await applyFindReplace(dramaId, matches)
    setMatches(null)
    setDone(r.stale > 0 ? `Replaced in ${r.applied} lines; ${r.stale} changed since and were skipped.` : `Replaced in ${r.applied} lines.`)
    if (r.applied > 0) onApplied()
  })
  const busy = preview.busy || apply.busy
  return (
    <form
      className="reader-edit-form reader-replace-form"
      aria-label="Replace everywhere"
      onSubmit={(e) => {
        e.preventDefault()
        void preview.run()
      }}
    >
      <p>Replace <strong>{selected}</strong> everywhere</p>
      <Field label="With">
        <input value={replacement} maxLength={200} onChange={(e) => { setReplacement(e.target.value); setMatches(null) }} />
      </Field>
      <div className="actions">
        <button type="submit" className={buttonClass()} disabled={busy || replacement === selected}>Preview</button>
        <button type="button" className={buttonClass()} disabled={busy} onClick={() => onAddTerm(selected)}>Add as term</button>
        <button type="button" className={buttonClass()} disabled={busy} onClick={onClose}>Close</button>
      </div>
      {matches && (
        <p role="status">
          {matches.length === 0 ? 'No lines contain it.' : `${matches.length} lines will change.`}{' '}
          {matches.length > 0 && (
            <button type="button" className={buttonClass()} disabled={busy} onClick={() => void apply.run()}>
              Replace in {matches.length} lines
            </button>
          )}
        </p>
      )}
      {done && <p role="status">{done}</p>}
      <ActionError action={preview} />
      <ActionError action={apply} />
    </form>
  )
}

/** Listens for taps and selections reported by the Reader's frame and shows the matching tool above the text. */
export function ReaderTermPanels({ dramaId, editMode, frameRef, terms, onTermsLoaded, onPageChanged }: {
  dramaId: number
  editMode: boolean
  frameRef: RefObject<HTMLIFrameElement | null>
  terms: GlossaryTerm[]
  onTermsLoaded: (terms: GlossaryTerm[]) => void
  onPageChanged: () => void
}) {
  const [draft, setDraft] = useState<TermDraft | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const termsRef = useLatest(terms)
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      // The frame is sandboxed to an opaque origin, so its window is the only way to know it sent this.
      if (!editMode || e.source !== frameRef.current?.contentWindow) return
      const m = e.data as { baiheReader?: string; termId?: unknown; text?: unknown }
      if (m?.baiheReader === 'term' && typeof m.termId === 'number') {
        const t = termsRef.current.find((x) => x.id === m.termId)
        if (t) setDraft({ id: t.id, term_original: t.term_original, term_translation: t.term_translation, category: t.category, notes: t.notes })
      } else if (m?.baiheReader === 'select' && typeof m.text === 'string' && m.text.length <= 200) {
        setSelected(m.text)
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [editMode, frameRef, termsRef])
  if (!editMode) return null
  return (
    <>
      <Sheet open={draft !== null} title="Edit term" onClose={() => setDraft(null)}>
        {draft && (
          <TermEditor key={draft.id ?? `new-${draft.term_translation}`} dramaId={dramaId} draft={draft}
            onClose={() => setDraft(null)}
            onChanged={() => {
              getGlossaryTerms(dramaId).then(onTermsLoaded, () => {})
              onPageChanged()
            }} />
        )}
      </Sheet>
      {selected && !draft && (
        <ReplaceEverywhere key={selected} dramaId={dramaId} selected={selected} onClose={() => setSelected(null)}
          onApplied={onPageChanged}
          onAddTerm={(text) => {
            setSelected(null)
            setDraft({ term_original: '', term_translation: text, category: 'other', notes: '' })
          }} />
      )}
      {!draft && !selected && (
        <p className="muted">Tap a highlighted name or term to edit it. Select text to replace it everywhere.</p>
      )}
    </>
  )
}
