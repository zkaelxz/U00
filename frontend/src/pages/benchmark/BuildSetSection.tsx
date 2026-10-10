import { useEffect, useState } from 'react'

import { buildBenchmarkSet } from '../../api/benchmark'
import { api } from '../../api/client'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, type PcMode } from '../../hooks/usePcOnly'
import { usePersistedState } from '../../hooks/usePersistedState'
import { plainError } from './benchmarkForm'
import { MAX_SET_NAME, buildProblems, buildRequest, describeBuild, emptyBuildForm, type BuildForm } from './buildSetForm'
import { BUILD_SET_HELP } from './benchmarkHelp'

type Title = { id: number; name: string }

/** "Build a set from a reviewed title": the owner's corrected lines become golden-set cases. PC only. */
export function BuildSetSection({ pc, onDone }: { pc: PcMode; onDone: () => void }) {
  const [form, setForm] = useState<BuildForm>(emptyBuildForm)
  const [titles, setTitles] = useState<Title[] | null>(null)
  const [lastTitle, setLastTitle] = usePersistedState<number | null>('benchmark.buildTitle', null)
  const [busy, setBusy] = useState<'preview' | 'build' | null>(null)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (pc === 'remote') return
    api.listDramas().then(
      (r) => setTitles(r.items.map((d) => ({ id: d.id, name: d.title_en || d.title_zh || `#${d.id}` }))),
      () => setTitles([]),
    )
  }, [pc])

  if (pc === 'remote') {
    return (
      <Section title="Build a set from a reviewed title" summary={PC_ONLY_SUMMARY} storageKey="benchmark.build" defaultOpen>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Section>
    )
  }

  const known = titles?.some((t) => t.id === lastTitle) ? lastTitle : null
  const dramaId = form.dramaId ?? known
  const current: BuildForm = { ...form, dramaId }
  const problems = buildProblems(current)
  const set = (patch: Partial<BuildForm>) => {
    setForm({ ...current, ...patch })
    setResult(null)
  }

  const run = async (dryRun: boolean) => {
    setBusy(dryRun ? 'preview' : 'build')
    setError(null)
    setResult(null)
    try {
      const r = await buildBenchmarkSet(buildRequest(current, dryRun))
      setResult(describeBuild(r))
      if (!dryRun) onDone()
    } catch (e) {
      setError(plainError(e, { pcOnly: true }))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Section title="Build a set from a reviewed title" summary="Your corrections as references" storageKey="benchmark.build" defaultOpen>
      <p className="muted">{BUILD_SET_HELP}</p>
      <div className="field-row">
        <Field label="Title">
          <select
            value={dramaId ?? ''}
            onChange={(e) => {
              const id = e.target.value ? Number(e.target.value) : null
              setLastTitle(id)
              set({ dramaId: id })
            }}
          >
            <option value="">{titles ? 'Pick a title' : 'Loading…'}</option>
            {titles?.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </Field>
        <Field label="New set name">
          <input value={form.setName} maxLength={MAX_SET_NAME} onChange={(e) => set({ setName: e.target.value })} placeholder="e.g. moon-reviewed" />
        </Field>
        <Field label="Lines to use" help="Reviewed: only lines you edited and saved, or approved into translation memory. All lines: every line with a source and a translation, including ones you read and left as they were.">
          <select value={form.include} onChange={(e) => set({ include: e.target.value === 'all' ? 'all' : 'reviewed' })}>
            <option value="reviewed">Reviewed lines only</option>
            <option value="all">All lines</option>
          </select>
        </Field>
      </div>
      <div className="field-row">
        <Field label="First line" help="A line number from the Review page. Blank = from the start.">
          <input inputMode="numeric" value={form.lineStart} onChange={(e) => set({ lineStart: e.target.value })} placeholder="Start" />
        </Field>
        <Field label="Last line" help="Blank = to the end.">
          <input inputMode="numeric" value={form.lineEnd} onChange={(e) => set({ lineEnd: e.target.value })} placeholder="End" />
        </Field>
        <Field label="Scenes" help="Keep this many evenly spaced scenes from the range. Blank = every scene.">
          <input inputMode="numeric" value={form.sceneCount} onChange={(e) => set({ sceneCount: e.target.value })} placeholder="All" />
        </Field>
        <Field label="Lines per case" help="A case is a scene of up to this many consecutive lines (1 to 10). A pause or a skipped line starts a new one.">
          <input inputMode="numeric" value={form.linesPerCase} onChange={(e) => set({ linesPerCase: e.target.value })} />
        </Field>
      </div>
      <div className="actions">
        <button type="button" className={buttonClass('secondary')} disabled={problems.length > 0 || busy !== null} onClick={() => void run(true)}>
          {busy === 'preview' ? 'Counting…' : 'Preview'}
        </button>
        <button type="button" className={buttonClass('primary')} disabled={problems.length > 0 || busy !== null} onClick={() => void run(false)}>
          {busy === 'build' ? 'Building…' : 'Build set'}
        </button>
        {problems[0] && <span className="muted" data-testid="build-reason">{problems[0]}</span>}
      </div>
      {result && <p role="status" data-testid="build-result">{result}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Section>
  )
}
