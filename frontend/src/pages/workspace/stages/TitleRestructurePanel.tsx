import { useEffect, useState } from 'react'

import { applyRestructure, getRestructure, previewRestructure } from '../../../api/titleRestructure'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { buttonClass } from '../../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../../hooks/usePcOnly'
import type {
  TitleRestructureInfo,
  TitleRestructureKind,
  TitleRestructurePreview,
  TitleRestructureRequest,
} from '../../../types/titleRestructure'
import { useStage } from '../StageContext'
import { chapterSize, confirmLabel, previewBlocker, previewSummary, resultText } from './titleRestructureModel'
import './titleRestructure.css'

// Merge this title into another, or move some of its chapters there. Always
// preview first: the confirm button sends the preview's fingerprint, so a
// change made in between is refused instead of applied blind.
export function TitleRestructurePanel() {
  const pc = usePcOnly()
  const [open, setOpen] = useState(false)
  return (
    <section className="panel" aria-label="Merge or move chapters">
      <Section
        title="Merge or move chapters"
        summary={pc === 'remote' ? PC_ONLY_SUMMARY : 'Move chapters to another title, or merge this title into one'}
        onToggle={setOpen}
      >
        {pc === 'remote' ? <p className="muted">{PC_ONLY_BODY}</p> : open && <RestructureBody />}
      </Section>
    </section>
  )
}

function RestructureBody() {
  const { dramaId, refetchDrama } = useStage()
  const [info, setInfo] = useState<TitleRestructureInfo | null>(null)
  const [reloads, setReloads] = useState(0)
  const [kind, setKind] = useState<TitleRestructureKind>('move')
  const [targetId, setTargetId] = useState('')
  const [picked, setPicked] = useState<string[]>([])
  const [moveDuplicates, setMoveDuplicates] = useState(false)
  const [preview, setPreview] = useState<TitleRestructurePreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    getRestructure(dramaId).then(
      (r) => {
        if (cancelled) return
        setInfo(r)
        setPicked((p) => p.filter((id) => r.chapters.some((c) => c.id === id)))
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads])

  if (!info) return error ? <ErrorBanner error={error} onDismiss={() => setError(null)} /> : <p className="muted">Loading chapters…</p>
  if (!info.kind) return <p className="muted">Only comic and novel titles have chapters to move.</p>

  const changed = () => {
    setPreview(null)
    setDone(null)
    setWarnings([])
  }
  const request = (): TitleRestructureRequest => ({
    target_id: Number(targetId),
    kind,
    ...(kind === 'move' ? { chapter_ids: picked } : {}),
    move_duplicates: moveDuplicates,
  })
  const blocker = previewBlocker(kind, targetId, picked.length)
  const targetTitle = info.targets.find((t) => String(t.id) === targetId)?.title ?? ''

  const runPreview = () => {
    setBusy(true)
    setError(null)
    previewRestructure(dramaId, request()).then(
      (p) => {
        setBusy(false)
        setPreview(p)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }
  const runApply = () => {
    if (!preview) return
    setBusy(true)
    setError(null)
    applyRestructure(dramaId, request(), preview.fingerprint).then(
      (r) => {
        setBusy(false)
        setPreview(null)
        setPicked([])
        setDone(resultText(r, targetTitle))
        setWarnings(r.warnings)
        setReloads((n) => n + 1)
        refetchDrama()
      },
      (e: unknown) => {
        setBusy(false)
        setPreview(null) // a refused apply means the preview is stale
        setError(e)
      },
    )
  }

  return (
    <div className="title-restructure" data-testid="title-restructure">
      <fieldset className="title-restructure-kind">
        <legend>What to do</legend>
        <label>
          <input type="radio" name="restructure-kind" checked={kind === 'move'} onChange={() => { setKind('move'); changed() }} />
          Move some chapters to another title
        </label>
        <label>
          <input type="radio" name="restructure-kind" checked={kind === 'merge'} onChange={() => { setKind('merge'); changed() }} />
          Merge this whole title into another
        </label>
      </fieldset>
      <Field
        label="Move to"
        help={info.targets.length ? 'Titles of the same kind and original language.' : 'No other title of the same kind and original language yet.'}
      >
        <select value={targetId} disabled={busy} onChange={(e) => { setTargetId(e.target.value); changed() }}>
          <option value="">Pick a title…</option>
          {info.targets.map((t) => <option key={t.id} value={String(t.id)}>{t.title}</option>)}
        </select>
      </Field>
      {kind === 'move' && (
        <fieldset className="title-restructure-chapters">
          <legend>Chapters ({picked.length} picked)</legend>
          {info.chapters.length === 0 && <p className="muted">This title has no chapters yet.</p>}
          <ul>
            {info.chapters.map((c) => (
              <li key={c.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={picked.includes(c.id)}
                    disabled={busy}
                    onChange={(e) => {
                      const on = e.target.checked
                      setPicked((p) => (on ? [...p, c.id] : p.filter((x) => x !== c.id)))
                      changed()
                    }}
                  />
                  <span className="title-restructure-name">{c.title}</span>
                  <span className="muted">{chapterSize(c)}</span>
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
      )}
      <label className="title-restructure-option">
        <input type="checkbox" checked={moveDuplicates} disabled={busy} onChange={(e) => { setMoveDuplicates(e.target.checked); changed() }} />
        Also move chapters the other title already has (both copies are kept)
      </label>
      <div className="actions">
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy || !!blocker} onClick={runPreview}>
          Preview
        </button>
        {blocker && <span className="muted">{blocker}</span>}
      </div>
      {preview && (
        <div className="title-restructure-preview" data-testid="title-restructure-preview" aria-live="polite">
          {preview.blocked.length > 0 ? (
            <ul className="error" role="alert">
              {preview.blocked.map((b) => <li key={b}>{b}</li>)}
            </ul>
          ) : (
            <p><strong>{previewSummary(preview)}</strong></p>
          )}
          {preview.chapters.some((c) => c.duplicate) && (
            <ul className="title-restructure-dups">
              {preview.chapters.filter((c) => c.duplicate).map((c) => (
                <li key={c.id}>
                  “{c.title}” is already in “{preview.target_title}”{c.moves ? '; both copies will be kept.' : ' and stays here.'}
                </li>
              ))}
            </ul>
          )}
          {[...preview.stays, ...preview.notes].map((s) => <p key={s} className="muted">{s}</p>)}
          <p className="muted">A safety copy of the library database and the changed files is saved before anything moves. Nothing is deleted.</p>
          <div className="actions">
            <button type="button" className={buttonClass('primary', 'sm')} disabled={busy || preview.blocked.length > 0} onClick={runApply}>
              {confirmLabel(preview)}
            </button>
            <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} onClick={() => setPreview(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {done && <p role="status">{done}</p>}
      {warnings.map((w) => <p key={w} className="warn">{w}</p>)}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}
