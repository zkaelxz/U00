import { useCallback, useEffect, useState } from 'react'
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
import { Tabs } from '../components/Tabs'
import { tabButtonId, tabPanelId } from '../components/tabIds'
import { SharingControl } from '../components/SharingControl'
import { VoiceBankPlayButton } from '../components/VoiceBankPlayButton'
import { countDramas, dramaName, parseTime, readHref, workspaceHref } from '../components/libraryView'
import { buttonClass } from '../components/uiClasses'
import { useHolds } from '../hooks/useHolds'
import { useLoad } from '../hooks/useLoad'
import { usePersistedState } from '../hooks/usePersistedState'
import { PC_ONLY_BODY, PC_ONLY_DELETE_NOTE, usePcOnly, usePcPendingNote, type PcMode } from '../hooks/usePcOnly'
import { engineLabel, languageLabel } from '../labels'
import { ADMIN_JOB_IDS } from '../types/libraryAdmin'
import { DataFolderPanel } from './diskUsage/DiskUsageSection'
import { useDiskUsage } from './diskUsage/useDiskUsage'
import { exportableCount } from './libraryAdmin/libraryAdmin'
import { ExportBlock } from './libraryAdmin/AdminBlocks'
import { useAdminJob } from './libraryAdmin/useAdminJob'
import { RENAME_MAX, groupHistory, showFold, validateRename } from './libraryForm'
import { costLabel, costMeta, countsLine, sharedLine, sharedSeries } from './libraryParity/libraryParity'
import './libraryParity/libraryParity.css'
import { listSavedSeries } from '../api/savedComics'
import { SavedSeriesRows } from './SavedManga'
import './manga/manga.css'
import { SERIES_HELP } from '../helpText'
import { PageShell } from '../components/PageShell'
import { Breadcrumbs } from '../nav/BreadcrumbNav'
import { routeCrumbs } from '../nav/breadcrumbs'
import { useRoute } from '../router'
import { seedPageTabFolds } from '../components/sectionStorage'
import { BackupTab } from './libraryTools/BackupTab'
import { StorageTab } from './libraryTools/StorageTab'
import { TabCard } from './libraryTools/TabCard'
import { CARDS, LIBRARY_TOOLS_TABS, cardStorageKey, cardsOf, isLibraryToolsTab, type LibraryToolsCard, type LibraryToolsTab } from './libraryTools/libraryToolsTabs'

const readTime = (iso: string) => new Date(parseTime(iso)).toLocaleString()

const ORGANIZE_EMPTY =
  'No shared series, presets, voice-bank entries or saved manga yet. Make a series from a title\'s Details, '
  + 'save a preset from the Translate stage, save a voice to the voice bank from a character\'s voice, '
  + 'and save manga chapters as CBZ from Sources.'
const ACTIVITY_EMPTY =
  'No translation costs or reading history yet. Costs appear once a title is translated with a paid engine; '
  + 'reading history appears as you read a title.'

// What a tab shows when every one of its sections is empty (and none failed to load).
function EmptyTab({ children }: { children: ReactNode }) {
  return <p className="muted" data-testid="tools-empty">{children}</p>
}

// A "Library tools" fold: rare lists stay collapsed (spec rule 16); one with nothing in it renders
// nothing while another section on the tab has content, but a failed load stays visible.
// `first` marks the first section on screen, which starts open; it is only known once every list
// on the tab has loaded, so nothing mounts before then (a fold reads its default when it mounts).
function ToolSection({ card, count, summary, error, first, ready, children }: {
  card: LibraryToolsCard; count?: number; summary?: string; error: unknown; first: boolean; ready: boolean; children: ReactNode
}) {
  if (!ready || !showFold(count, error)) return null
  return (
    <TabCard card={card} count={count} summary={summary} defaultOpen={first}>
      <section aria-label={CARDS[card].title} className="tool-body">
        <ErrorBanner error={error} />
        {children}
      </section>
    </TabCard>
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
  // Renaming needs admin.library; deleting is PC only (pc).
  const canRename = useHolds('admin.library')
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
                  {canRename && (
                    <button type="button" className={buttonClass('ghost', 'sm')} aria-label={`Rename ${x.name}`} onClick={() => setRenamingId(x.id)}>Rename</button>
                  )}
                  {pc !== 'remote' && <ConfirmButton name={x.name} busy={busyId === x.id} onConfirm={() => run(x.id)} />}
                </span>
              </>
            )}
          </li>
        ))}
      </ul>
      <p className="muted">{pc === 'remote' ? PC_ONLY_DELETE_NOTE : help}</p>
      {!canRename && !!items?.length && <p className="muted" data-testid="no-rename-note">Renaming is for the library admin.</p>}
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
  useState(() => seedPageTabFolds('library-tools',
    Object.fromEntries(LIBRARY_TOOLS_TABS.map((t) => [t.id, cardsOf(t.id).map(cardStorageKey)]))))
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

  const route = useRoute()
  const [storedTab, setStoredTab] = usePersistedState<LibraryToolsTab>('library-tools.tab', 'organize')
  // A link's tab wins over the remembered one from the first render, so the tab it names is what shows.
  const [tab, setTabState] = useState<LibraryToolsTab>(() =>
    (route.name === 'library-tools' ? route.tab : undefined) ?? (isLibraryToolsTab(storedTab) ? storedTab : 'organize'))
  const selectTab = useCallback((t: LibraryToolsTab) => {
    setTabState(t)
    setStoredTab(t)
  }, [setStoredTab])
  useEffect(() => {
    if (route.name === 'library-tools' && route.tab) selectTab(route.tab)
  }, [route, selectTab])
  // The disk scan walks the data folder, so it waits until a tab that shows it is opened, on the PC:
  // away from it (or before /api/meta says which) nothing is asked.
  const usage = useDiskUsage(pc === 'local' && (tab === 'storage' || tab === 'data'))
  const pcPending = usePcPendingNote(pc)
  const panel = (id: LibraryToolsTab, children: ReactNode) => (
    <div key={id} id={tabPanelId('library-tools', id)} role="tabpanel" aria-labelledby={tabButtonId('library-tools', id)} className="tools-panel" hidden={tab !== id}>
      {children}
    </div>
  )
  const grouped = history.data ? groupHistory(history.data.items) : undefined
  const shared = series.data ? sharedSeries(series.data.items) : undefined
  // Which sections of the two list tabs are on screen, in order: only the first starts open, and when
  // none are the tab shows one empty note instead of nothing.
  const organizeShown = [
    showFold(shared?.length, series.error), showFold(presets.data?.items.length, presets.error),
    showFold(voices.data?.items.length, voices.error), showFold(saved.data?.length, saved.error),
  ]
  const activityShown = [showFold(costs.data?.items.length, costs.error), showFold(grouped?.length, history.error)]
  const firstOf = (shown: boolean[]) => shown.indexOf(true)
  const settled = (...loads: { data: unknown; error: unknown }[]) => loads.every((l) => l.data !== null || !!l.error)
  const organizeReady = settled(series, presets, voices, saved)
  const activityReady = settled(costs, history)
  const totalCost = costs.data?.items.reduce((sum, c) => sum + c.estimated_cost_usd, 0)
  return (
    <PageShell
      width="standard"
      title="Library tools"
      className="library-page library-tools-page"
      back={<Breadcrumbs crumbs={routeCrumbs({ name: 'library-tools' })} />}
      meta="Series, saved manga, costs, reading history, presets, backups, storage and the data folder. Titles and Continue stay on the Library page."
      actions={<ButtonLink variant="secondary" href="#/library">Back to Library</ButtonLink>}
      tabs={<Tabs label="Library tools" idPrefix="library-tools" tabs={LIBRARY_TOOLS_TABS} active={tab} onSelect={selectTab} />}
    >
      <ErrorBanner error={stats.error} />
      <section className="tools-stack" aria-label="Library tools">
        {panel('organize', (
          <>
        <ToolSection card="series" ready={organizeReady} first={firstOf(organizeShown) === 0} count={shared?.length} summary={SERIES_HELP} error={series.error}>
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
        <ToolSection card="presets" ready={organizeReady} first={firstOf(organizeShown) === 1} count={presets.data?.items.length} error={presets.error}>
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
        <ToolSection card="voice-bank" ready={organizeReady} first={firstOf(organizeShown) === 2} count={voices.data?.items.length} error={voices.error}>
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
        <ToolSection card="saved-manga" ready={organizeReady} first={firstOf(organizeShown) === 3} count={saved.data?.length} summary="Chapters saved as CBZ files" error={saved.error}>
          {saved.data && <SavedSeriesRows items={saved.data} />}
        </ToolSection>
        {!organizeReady && <p className="muted">Loading…</p>}
        {organizeReady && !organizeShown.some(Boolean) && <EmptyTab>{ORGANIZE_EMPTY}</EmptyTab>}
        {pc !== 'remote' && (
          <div className="tools-export">
            <h3 className="tools-group-title">Export</h3>
            <ExportBlock exportable={exportableCount(stats.data?.by_status)} job={exporter} />
          </div>
        )}
          </>
        ))}
        {panel('activity', (
          <>
        <ToolSection
          card="cost"
          ready={activityReady}
          first={firstOf(activityShown) === 0}
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
        <ToolSection card="history" ready={activityReady} first={firstOf(activityShown) === 1} count={grouped?.length} error={history.error}>
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
        {!activityReady && <p className="muted">Loading…</p>}
        {activityReady && !activityShown.some(Boolean) && <EmptyTab>{ACTIVITY_EMPTY}</EmptyTab>}
          </>
        ))}
        {panel('backup', <BackupTab pc={pc} />)}
        {panel('storage', <StorageTab pc={pc} usage={usage} />)}
        {panel('data', pc === 'local' ? <DataFolderPanel usage={usage} /> : <p className="muted">{pcPending ?? PC_ONLY_BODY}</p>)}
      </section>
    </PageShell>
  )
}
