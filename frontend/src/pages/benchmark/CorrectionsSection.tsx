import { useEffect, useState } from 'react'

import { addTitleCorrections, getTitleCorrections, type BenchmarkSet, type TitleCorrections } from '../../api/benchmark'
import { api } from '../../api/client'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, type PcMode } from '../../hooks/usePcOnly'
import { usePersistedState } from '../../hooks/usePersistedState'
import { plainError } from './benchmarkForm'
import { CORRECTIONS_HELP } from './benchmarkHelp'
import {
  MAX_SET_NAME, NEW_SET, chosenSetName, correctionProblems, describeAdd, describeStats, transcriptionSetNames,
} from './correctionsForm'

type Title = { id: number; name: string }

/** "Add a title's corrections to a test set": its corrected lines, with their audio, become transcription cases. PC only. */
export function CorrectionsSection({ sets, pc, onDone }: { sets: BenchmarkSet[]; pc: PcMode; onDone: () => void }) {
  const [titles, setTitles] = useState<Title[] | null>(null)
  const [lastTitle, setLastTitle] = usePersistedState<number | null>('benchmark.correctionsTitle', null)
  const [dramaId, setDramaId] = useState<number | null>(null)
  const [choice, setChoice] = useState<string>(NEW_SET)
  const [typed, setTyped] = useState('')
  const [loaded, setLoaded] = useState<{ id: number; stats: TitleCorrections } | null>(null)
  const [busy, setBusy] = useState<'preview' | 'add' | null>(null)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (pc === 'remote') return
    api.listDramas().then(
      (r) => setTitles(r.items.map((d) => ({ id: d.id, name: d.title_en || d.title_zh || `#${d.id}` }))),
      () => setTitles([]),
    )
  }, [pc])

  const known = titles?.some((t) => t.id === lastTitle) ? lastTitle : null
  const title = dramaId ?? known

  useEffect(() => {
    if (pc === 'remote' || title == null) return
    let live = true
    getTitleCorrections(title).then(
      (s) => live && setLoaded({ id: title, stats: s }),
      (e) => live && setError(plainError(e)),
    )
    return () => {
      live = false
    }
  }, [pc, title])

  const stats = loaded?.id === title ? loaded.stats : null

  if (pc === 'remote') {
    return (
      <Section title="Add a title's corrections to a test set" summary={PC_ONLY_SUMMARY} storageKey="benchmark.corrections" defaultOpen>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Section>
    )
  }

  const names = transcriptionSetNames(sets)
  const setName = chosenSetName(names.includes(choice) ? choice : NEW_SET, typed)
  const problems = correctionProblems(title, setName, stats)

  const run = async (dryRun: boolean) => {
    if (title == null) return
    setBusy(dryRun ? 'preview' : 'add')
    setError(null)
    setResult(null)
    try {
      const r = await addTitleCorrections(title, { set_name: setName, dry_run: dryRun })
      setResult(describeAdd(r))
      if (!dryRun) onDone()
    } catch (e) {
      setError(plainError(e, { pcOnly: true }))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Section title="Add a title's corrections to a test set" summary="Your edited lines, with their audio" storageKey="benchmark.corrections" defaultOpen>
      <p className="muted">{CORRECTIONS_HELP}</p>
      <div className="field-row">
        <Field label="Title">
          <select
            value={title ?? ''}
            onChange={(e) => {
              const id = e.target.value ? Number(e.target.value) : null
              setLastTitle(id)
              setDramaId(id)
              setResult(null)
              setError(null)
            }}
          >
            <option value="">{titles ? 'Pick a title' : 'Loading…'}</option>
            {titles?.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </Field>
        <Field label="Test set">
          <select value={names.includes(choice) ? choice : NEW_SET} onChange={(e) => { setChoice(e.target.value); setResult(null) }}>
            {names.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
            <option value={NEW_SET}>New set…</option>
          </select>
        </Field>
        {!names.includes(choice) && (
          <Field label="New set name">
            <input value={typed} maxLength={MAX_SET_NAME} onChange={(e) => { setTyped(e.target.value); setResult(null) }} placeholder="e.g. moon-corrections" />
          </Field>
        )}
      </div>
      {stats && <p data-testid="corrections-stats">{describeStats(stats)}</p>}
      <div className="actions">
        <button type="button" className={buttonClass('secondary')} disabled={problems.length > 0 || busy !== null} onClick={() => void run(true)}>
          {busy === 'preview' ? 'Counting…' : 'Preview'}
        </button>
        <button type="button" className={buttonClass('primary')} disabled={problems.length > 0 || busy !== null} onClick={() => void run(false)}>
          {busy === 'add' ? 'Adding…' : 'Add lines'}
        </button>
        {problems[0] && <span className="muted" data-testid="corrections-reason">{problems[0]}</span>}
      </div>
      {result && <p role="status" data-testid="corrections-result">{result}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Section>
  )
}
