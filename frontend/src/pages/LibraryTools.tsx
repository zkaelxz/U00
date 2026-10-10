import { useState } from 'react'
import type { FormEvent, ReactNode } from 'react'

import {
  clearReadingHistory, getCosts, getHistory, getPresets, getSeries, getStats, getVoiceBank, renamePreset,
  renameVoiceBankEntry,
} from '../api/library'
import { deletePreset, deleteVoiceBankEntry } from '../api/libraryAdmin'
import { Badge } from '../components/Badge'
import { ButtonLink } from '../components/Button'
import { ConfirmButton } from '../components/ConfirmButton'
import { ErrorBanner } from '../components/ErrorBanner'
import { Field } from '../components/Field'
import { Section } from '../components/Section'
import { SharingControl } from '../components/SharingControl'
import { VoiceBankPlayButton } from '../components/VoiceBankPlayButton'
import { countDramas, dramaName, parseTime, readHref, workspaceHref } from '../components/libraryView'
import { buttonClass } from '../components/uiClasses'
import { useLoad } from '../hooks/useLoad'
import { PC_ONLY_DELETE_NOTE, usePcOnly, type PcMode } from '../hooks/usePcOnly'
import { engineLabel, languageLabel } from '../labels'
import { ADMIN_JOB_IDS } from '../types/libraryAdmin'
import { DiskUsageSection } from './diskUsage/DiskUsageSection'
import { AdminSection } from './libraryAdmin/AdminSection'
import { exportableCount } from './libraryAdmin/libraryAdmin'
import { useAdminJob } from './libraryAdmin/useAdminJob'
import { RENAME_MAX, groupHistory, showFold, validateRename } from './libraryForm'
import { costLabel, costMeta, countsLine, sharedLine, sharedSeries } from './libraryParity/libraryParity'
import './libraryParity/libraryParity.css'
import { listSavedSeries } from '../api/savedComics'
import { SavedSeriesRows } from './SavedManga'
import './manga/manga.css'
import { SERIES_HELP } from '../helpText'
import { Breadcrumbs } from '../nav/BreadcrumbNav'
import { routeCrumbs } from '../nav/breadcrumbs'

const readTime = (iso: string) => new Date(parseTime(iso)).toLocaleString()

// A "Library tools" fold: rare lists stay collapsed (spec rule 16); one with
// nothing in it renders nothing, but a failed load stays visible.
function ToolSection({ title, count, summary, error, defaultOpen, children }: {
  title: string; count?: number; summary?: string; error: unknown; defaultOpen?: boolean; children: ReactNode
}) {
  if (!showFold(count, error)) return null
  return (
    <Section title={title} count={count} summary={summary} defaultOpen={defaultOpen} storageKey={`library.tools.${title.toLowerCase().replace(/\W+/g, '-')}`}>
      <section aria-label={title} className="tool-body">
        <ErrorBanner error={error} />
        {children}
      </section>
    </Section>
  )
}

// Parity L18/L19: an inline rename for one row of a Library list.
function RenameForm({ current, onSave, onCancel }: {
  current: string
  onSave: (name: string) => Promise<unknown>
  onCancel: () => void
}) {
  const [value, setValue] = useState(current)
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const bad = validateRename(value, current)
    if (bad) {
      setProblem(bad)
      return
    }
    setProblem(null)
    setPending(true)
    onSave(value.trim()).then(
      () => setPending(false),
      (err: unknown) => {
        setPending(false)
        setError(err)
      },
    )
  }
  return (
    <form className="rename-form" onSubmit={submit}>
      <Field label={`New name for ${current}`}>
        <input value={value} maxLength={RENAME_MAX} onChange={(e) => setValue(e.target.value)} autoFocus />
      </Field>
      <div className="actions">
        <button type="submit" className={buttonClass('secondary', 'sm')} disabled={pending}>Save name</button>
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={pending} onClick={onCancel}>Cancel</button>
      </div>
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
    </form>
  )
}

// A Library list whose rows have a rename and a PC-only two-step delete (presets, voice bank).
function DeletableList({ pc, help, items, remove, rename, onDeleted, extra }: {
  pc: PcMode
  help: string
  items: { id: number; name: string; meta: string | null }[] | undefined
  remove: (id: number) => Promise<unknown>
  rename: (id: number, name: string) => Promise<unknown>
  onDeleted: () => void
  // Row controls before Rename (the voice bank's Play).
  extra?: (id: number, name: string) => ReactNode
}) {
  const [busyId, setBusyId] = useState<number | null>(null)
  const [renamingId, setRenamingId] = useState<number | null>(null)
  const [error, setError] = useState<unknown>(null)
  const run = (id: number) => {
    setBusyId(id)
    setError(null)
    remove(id).then(
      () => { setBusyId(null); onDeleted() },
      (e: unknown) => { setBusyId(null); setError(e) },
    )
  }
  return (
    <>
      <ul className="deletable-list">
        {items?.map((x) => (
          <li key={x.id}>
            {renamingId === x.id ? (
              <RenameForm
                current={x.name}
                onSave={(n) => rename(x.id, n).then(() => { setRenamingId(null); onDeleted() })}
                onCancel={() => setRenamingId(null)}
              />
            ) : (
              <>
                <span>{x.name} <span className="muted">{x.meta}</span></span>
                <span className="row-actions">
                  {extra?.(x.id, x.name)}
                  <button type="button" className={buttonClass('ghost', 'sm')} aria-label={`Rename ${x.name}`} onClick={() => setRenamingId(x.id)}>Rename</button>
                  {pc !== 'remote' && <ConfirmButton name={x.name} busy={busyId === x.id} onConfirm={() => run(x.id)} />}
                </span>
              </>
            )}
          </li>
        ))}
      </ul>
      <p className="muted">{pc === 'remote' ? PC_ONLY_DELETE_NOTE : help}</p>
      <ErrorBanner error={error} describe={{ pcOnly: true }} />
    </>
  )
}

// Clear the reading history (PC only). Reading progress, and so the Continue
// shelf, is kept.
function ClearHistory({ pc, onCleared }: { pc: PcMode; onCleared: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  if (pc === 'remote') return <p className="muted">Clearing history is PC only.</p>
  const run = () => {
    setBusy(true)
    setError(null)
    clearReadingHistory().then(
      () => { setBusy(false); onCleared() },
      (e: unknown) => { setBusy(false); setError(e) },
    )
  }
  return (
    <>
      <div className="actions">
        <ConfirmButton
          name="reading history"
          label="Clear history…"
          ariaLabel="Clear reading history"
          verb="clear"
          busy={busy}
          onConfirm={run}
        />
      </div>
      <p className="muted">Where you left off in each title is kept.</p>
      <ErrorBanner error={error} describe={{ pcOnly: true }} onDismiss={() => setError(null)} />
    </>
  )
}

// Series, costs, reading history, presets, voice bank and backups: the Library's
// rarely used controls, each folded so people open them on purpose.
export default function LibraryToolsPage() {
  const [reloadKey, setReloadKey] = useState(0)
  const pc = usePcOnly()
  const stats = useLoad(getStats, reloadKey)
  const series = useLoad(getSeries, reloadKey)
  const costs = useLoad(getCosts, reloadKey)
  const history = useLoad(getHistory, reloadKey)
  const presets = useLoad(getPresets, reloadKey)
  const voices = useLoad(getVoiceBank, reloadKey)
  const saved = useLoad(listSavedSeries, reloadKey)
  const exporter = useAdminJob(ADMIN_JOB_IDS.export, 'export')
  const onChanged = () => setReloadKey((k) => k + 1)
  const grouped = history.data ? groupHistory(history.data.items) : undefined
  const shared = series.data ? sharedSeries(series.data.items) : undefined
  const totalCost = costs.data?.items.reduce((sum, c) => sum + c.estimated_cost_usd, 0)
  return (
    <main className="library-page library-tools-page">
      <Breadcrumbs crumbs={routeCrumbs({ name: 'library-tools' })} />
      <header className="page-head">
        <div className="page-head-text">
          <h2 className="page-title">Library tools</h2>
          <p className="page-meta">Series, saved manga, costs, reading history, presets, backups and disk usage. Titles and Continue stay on the Library page.</p>
        </div>
        <ButtonLink variant="secondary" href="#/library">Back to Library</ButtonLink>
      </header>
      <ErrorBanner error={stats.error} />
      <section className="tools-stack" aria-label="Library tools">
        <h3 className="tools-group-title">Organize</h3>
        <ToolSection title="Series" defaultOpen count={shared?.length} summary={SERIES_HELP} error={series.error}>
          <ul className="tool-list series-list">
            {shared?.map((x) => (
              <li key={x.id} className="series-item">
                <span className="tool-row"><strong>{x.name}</strong> <span className="muted">{countDramas(x.dramas.length)}</span></span>
                <span className="muted series-meta">{countsLine(x.types, 'mediaType')}</span>
                <span className="muted series-meta">{sharedLine(x)}</span>
                <SharingControl kind="series" id={x.id} title={x.name} isPrivate={x.is_private} ownedByMe={x.owned_by_me} onChanged={onChanged} />
                <ul className="series-drama-list" aria-label={`Titles in ${x.name}`}>
                  {x.dramas.map((d) => (
                    <li key={d.id} className="series-drama">
                      <span className="series-drama-text">
                        <span>{dramaName(d)}</span>
                        <span className="series-drama-meta">
                          <Badge kind="mediaType" value={d.media_type || 'audio_drama'} />
                          {d.status && <Badge kind="status" value={d.status} />}
                        </span>
                      </span>
                      <ButtonLink size="sm" href={workspaceHref(d.id)} aria-label={`Open ${dramaName(d)}`}>
                        Open
                      </ButtonLink>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </ToolSection>
        <ToolSection title="Presets" count={presets.data?.items.length} error={presets.error}>
          <DeletableList
            pc={pc}
            help="Titles that used it keep their settings."
            items={presets.data?.items.map((p) => ({
              id: p.id, name: p.name, meta: p.translation_engine ? engineLabel(p.translation_engine) : null,
            }))}
            remove={deletePreset}
            rename={renamePreset}
            onDeleted={onChanged}
          />
        </ToolSection>
        <ToolSection title="Voice bank" count={voices.data?.items.length} error={voices.error}>
          <DeletableList
            pc={pc}
            help="Characters that used it keep their own copy."
            items={voices.data?.items.map((v) => ({ id: v.id, name: v.name, meta: v.language ? languageLabel(v.language) : null }))}
            remove={deleteVoiceBankEntry}
            rename={renameVoiceBankEntry}
            onDeleted={onChanged}
            extra={(id, name) => voices.data?.items.find((v) => v.id === id)?.clip_available
              ? <VoiceBankPlayButton entryId={id} name={name} /> : null}
          />
        </ToolSection>
        <ToolSection title="Saved manga" count={saved.data?.length} summary="Chapters saved as CBZ files" error={saved.error}>
          {saved.data && <SavedSeriesRows items={saved.data} />}
        </ToolSection>
        <h3 className="tools-group-title">Activity</h3>
        <ToolSection
          title="Cost by title"
          count={costs.data?.items.length}
          summary={totalCost !== undefined ? `$${totalCost.toFixed(2)} in all` : undefined}
          error={costs.error}
        >
          <ul className="tool-list">
            {costs.data?.items.map((c) => (
              <li key={c.id}>
                <span className="tool-row">
                  <a className="cost-link" href={workspaceHref(c.id)}>{dramaName(c)}</a>
                  <span className="num">{costLabel(c.estimated_cost_usd)}</span>
                </span>
                <span className="muted num cost-meta">
                  {c.translation_engine && `${engineLabel(c.translation_engine)} · `}{costMeta(c)}
                </span>
              </li>
            ))}
          </ul>
        </ToolSection>
        <ToolSection title="Reading history" count={grouped?.length} error={history.error}>
          <ul className="tool-list">
            {grouped?.map(({ entry: h, count }) => (
              <li key={`${h.drama_id}-${h.accessed_at}`}>
                <span className="tool-row">
                  <a className="history-read" href={readHref({ id: h.drama_id, media_type: null })}>
                    {dramaName({ ...h, id: h.drama_id })}
                  </a>
                  {count > 1 && <Badge>×{count}</Badge>}
                </span>
                <span className="muted">
                  {h.percent_complete != null && `${Math.round(h.percent_complete)}%`}
                  {h.percent_complete != null && h.accessed_at && ' · '}
                  {h.accessed_at && `last read ${readTime(h.accessed_at)}`}
                </span>
              </li>
            ))}
          </ul>
          <ClearHistory pc={pc} onCleared={onChanged} />
        </ToolSection>
        <h3 className="tools-group-title">Data</h3>
        <AdminSection pc={pc} exportable={exportableCount(stats.data?.by_status)} exporter={exporter} />
        <DiskUsageSection pc={pc} />
      </section>
    </main>
  )
}
