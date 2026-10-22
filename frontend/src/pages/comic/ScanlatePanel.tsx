/*
 * The Comic page's Translate panel: upload pages (useCanUpload), pick an engine,
 * "Translate all pages" (skips pages that already have text), "Redo this
 * page" (confirm), "Redo all…" (confirm), job progress and cancel, the current page's
 * run notes, and ZIP/PDF export. One Scanlate job runs per drama; when it
 * finishes, onChanged() lets the viewer reload its pages so the typeset
 * images show. Server notes are rendered as text only.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { scanlateApi, scanlateExportUrl } from '../../api/scanlate'
import { engineOptionLabel, engineShortName } from '../../api/translate'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { useJob, useJobRun } from '../../hooks/useJob'
import { useCanUpload } from '../../hooks/useHolds'
import { useReadiness } from '../../hooks/useReadiness'
import { useStageDraft } from '../../hooks/useStageDraft'
import type { JobRecord } from '../../types/jobs'
import type {
  ScanlateConfig,
  ScanlateDetectBackend,
  ScanlateExportFormat,
  ScanlatePageNotes,
  ScanlateRunMode,
} from '../../types/scanlate'
import { mlDetectorNeed, scanlateNeed } from '../readiness/needs'
import { NeedsInstall } from '../readiness/NeedsInstall'
import { JobPanel } from '../workspace/stages/JobPanel'
import {
  checkFiles,
  defaultEngine,
  progressSummary,
  runBlockedReason,
  toolsSummary,
  uploadResultText,
  usableEngines,
} from './scanlateLogic'
import { AI_ENGINE_LABEL } from '../../helpText'
import type { ScopeCounts } from './chapterLogic'

interface Props {
  dramaId: number
  // The page on screen (for "Redo this page"), or null when there is none.
  pageId: number | null
  // The reader's Typeset toggle: off exports the original pages.
  typeset: boolean
  // Opens a page by its 1-based position, for the list of pages with warnings.
  onGoToPage?: (ordinal: number) => void
  pageNumber: number | null
  // Page counts for "this chapter" and "all chapters" (hidden pages never count).
  scopes?: ScopeCounts
  onChanged: () => void
  // The job this panel follows (null once it has finished), for a parent that outlives the panel.
  onJob?: (jobId: string | null) => void
}

const DETECTORS: { value: ScanlateDetectBackend; label: string }[] = [
  { value: 'auto', label: 'Auto (ML model if downloaded)' },
  { value: 'cv', label: 'OpenCV heuristic' },
  { value: 'ml', label: 'ML model (downloads once)' },
]

// The form is remembered per title, so leaving the page does not reset the engine, detector and scope.
const SCANLATE_DRAFT = { engine: '', detector: 'auto' as string, slice: true, scope: 'all' as string }

// A page's first note line, short enough to scan in a list.
function firstLine(message: string): string {
  const line = message.split('\n')[0]
  return line.length > 140 ? `${line.slice(0, 139)}…` : line
}

export function ScanlatePanel({ dramaId, pageId, typeset, pageNumber, scopes, onChanged, onJob, onGoToPage }: Props) {
  const canUpload = useCanUpload()
  const { readiness } = useReadiness()
  const [config, setConfig] = useState<ScanlateConfig | null>(null)
  const [notes, setNotes] = useState<ScanlatePageNotes[]>([])
  const [error, setError] = useState<unknown>(null)
  const { draft, save: saveDraft } = useStageDraft(dramaId, 'scanlate', SCANLATE_DRAFT)
  const draftRef = useRef(draft)
  const [engine, setEngine] = useState(draft.engine ?? '')
  const [detector, setDetector] = useState<ScanlateDetectBackend>(
    DETECTORS.find((d) => d.value === draft.detector)?.value ?? 'auto',
  )
  const [files, setFiles] = useState<File[]>([])
  const [slice, setSlice] = useState(draft.slice ?? true)
  // Not remembered: it adds seconds per caption, so each visit starts with it off.
  const [artCaptions, setArtCaptions] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [scope, setScope] = useState<'chapter' | 'all'>(draft.scope === 'chapter' ? 'chapter' : 'all')
  const [exported, setExported] = useState<ScanlateExportFormat[]>([])
  const [jobKind, setJobKind] = useState<'run' | 'export' | null>(null)
  const [loadKey, setLoadKey] = useState(0)
  const fileInput = useRef<HTMLInputElement>(null)
  const [jobId, setJobId, runKey] = useJobRun()
  const pendingExport = useRef<ScanlateExportFormat[]>([])

  useEffect(() => saveDraft({ engine, detector, slice, scope }), [saveDraft, engine, detector, slice, scope])

  const reload = useCallback(() => setLoadKey((n) => n + 1), [])
  const onDone = useCallback(
    (job: JobRecord) => {
      if (job.status === 'done' && pendingExport.current.length) setExported(pendingExport.current)
      reload()
      onChanged()
      onJob?.(null)
    },
    [reload, onChanged, onJob],
  )
  const { job, done, error: pollError } = useJob(jobId, { runKey, onDone })
  useEffect(() => {
    if (jobId !== null) onJob?.(jobId)
  }, [jobId, runKey, onJob])
  const running = starting || (jobId !== null && !done && !pollError)

  useEffect(() => {
    let cancelled = false
    scanlateApi.config(dramaId).then(
      (c) => {
        if (cancelled) return
        setConfig(c)
        // A remembered engine that is no longer usable falls back to the default.
        setEngine((e) => (e && usableEngines(c).some((u) => u.name === e) ? e : defaultEngine(c)))
        setSlice((s) => (loadKey === 0 ? (draftRef.current.slice ?? c.upload_limits.slice_strips_default) : s))
      },
      (e) => !cancelled && setError(e),
    )
    scanlateApi.runNotes(dramaId).then(
      (n) => !cancelled && setNotes(n.pages),
      () => {},
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, loadKey])

  // A job already running for this drama (started elsewhere or before a reload): follow it.
  const attached = useRef(false)
  useEffect(() => {
    if (config?.job_running && !attached.current && jobId === null) {
      attached.current = true
      setJobKind('run')
      setJobId(config.job_id)
    }
  }, [config, jobId, setJobId])

  // A missing package or program blocks every run button; the server would refuse the run anyway.
  const need = config ? scanlateNeed(readiness, config.ocr_backend, config.ocr_backend_installed) : null
  const detectorNeed = config ? mlDetectorNeed(readiness, detector, config.ml_weights_cached) : null
  const blocked = runBlockedReason(config, engine) ?? need?.reason ?? null
  const usable = usableEngines(config)
  const selectedEngine = usable.find((e) => e.name === engine)

  // "This chapter" only when the title has more than one chapter.
  const chapterScope = scope === 'chapter' && scopes?.chapter ? scopes.chapter : null
  const target = chapterScope ?? scopes?.all ?? null
  const targetName = chapterScope ? 'this chapter' : scopes?.chapter ? 'all chapters' : 'all pages'
  const start = async (mode: ScanlateRunMode) => {
    setError(null)
    setStarting(true)
    pendingExport.current = []
    try {
      const r = await scanlateApi.run(dramaId, {
        mode,
        page_id: mode === 'page' && pageId ? pageId : undefined,
        chapter_id: mode !== 'page' && chapterScope ? chapterScope.id : undefined,
        confirm: mode === 'all' ? true : undefined,
        engine,
        detect_backend: detector,
        art_captions: artCaptions || undefined,
      })
      setJobKind('run')
      setJobId(r.job_id)
    } catch (e) {
      setError(e)
    } finally {
      setStarting(false)
    }
  }

  const startExport = async (formats: ScanlateExportFormat[]) => {
    setError(null)
    setStarting(true)
    setExported([])
    pendingExport.current = formats
    try {
      const r = await scanlateApi.export(dramaId, formats, undefined, typeset)
      setJobKind('export')
      setJobId(r.job_id)
    } catch (e) {
      setError(e)
    } finally {
      setStarting(false)
    }
  }

  const fileError = config && files.length ? checkFiles(files, config.upload_limits) : null
  const upload = async () => {
    if (!config || fileError || !files.length) return
    setError(null)
    setUploadMsg(null)
    setUploading(true)
    try {
      const r = await scanlateApi.upload(dramaId, files, slice)
      setUploadMsg(uploadResultText(r))
      setFiles([])
      if (fileInput.current) fileInput.current.value = ''
      reload()
      onChanged()
    } catch (e) {
      setError(e)
    } finally {
      setUploading(false)
    }
  }

  const pageNotes = notes.find((n) => n.page_id === pageId)?.notes ?? []
  const problemPages = notes.filter((n) => n.page_id !== pageId && n.notes.some((x) => x.level !== 'info'))
  const limits = config?.upload_limits
  const accept = limits?.pdf === false
    ? '.png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp'
    : '.png,.jpg,.jpeg,.webp,.pdf,image/png,image/jpeg,image/webp,application/pdf'

  return (
    <Card
      className="scanlate-panel"
      aria-label="Translate pages"
      title="Translate pages"
      meta={config ? progressSummary(config) : 'Loading…'}
    >
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {!canUpload ? null : (
        <div className="stack scanlate-upload">
          <div className="actions">
            <input
              ref={fileInput}
              type="file"
              multiple
              accept={accept}
              aria-label="Page images or PDFs"
              onChange={(e) => {
                setUploadMsg(null)
                setFiles(Array.from(e.target.files ?? []))
              }}
            />
            <button
              type="button"
              className={buttonClass('secondary')}
              disabled={!files.length || !!fileError || uploading || running}
              onClick={() => void upload()}
            >
              {uploading ? 'Uploading…' : 'Upload pages'}
            </button>
          </div>
          <div className="toggle-list">
          <Field
            label="Slice tall strips"
            help={`A page taller than ${limits?.strip_slice_ratio ?? 3}× its width is cut at the gaps between panels into page-sized slices.`}
          >
            <Toggle checked={slice} onChange={setSlice} />
          </Field>
          </div>
          {limits && (
            <p className="muted scanlate-limits">
              PNG, JPEG, WebP{limits.pdf ? ' or PDF' : ''} · up to {limits.max_image_mb} MB and {limits.max_image_megapixels} MP per image,{' '}
              {limits.pdf ? `${limits.max_pdf_mb} MB and ${limits.max_pdf_pages} pages per PDF, ` : 'PDF import needs pypdf (Diagnostics), '}{limits.max_files} files and{' '}
              {limits.max_total_mb >= 1024 ? `${limits.max_total_mb / 1024} GB` : `${limits.max_total_mb} MB`} at once.
            </p>
          )}
          {fileError && <p className="error" role="alert">{fileError}</p>}
          {uploadMsg && <p role="status">{uploadMsg}</p>}
        </div>
      )}

      <div className="scanlate-run-row">
        <Field
          label={AI_ENGINE_LABEL}
          help={`Keys stay on the PC; engines without a key are not listed.${selectedEngine ? ` ${engineShortName(selectedEngine)}: ${selectedEngine.label}` : ''}`}
        >
          <select value={engine} onChange={(e) => setEngine(e.target.value)} disabled={!usable.length}>
            {usable.length === 0 && <option value="">No engine has a key</option>}
            {usable.map((e) => (
              <option key={e.name} value={e.name}>
                {engineOptionLabel(e)}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {scopes?.chapter && (
        <Field label="Translate" help="Pages marked as not part of the story are always skipped.">
          <select value={chapterScope ? 'chapter' : 'all'} onChange={(e) => setScope(e.target.value as 'chapter' | 'all')}>
            <option value="chapter">
              This chapter: {scopes.chapter.label} ({scopes.chapter.pages} {scopes.chapter.pages === 1 ? 'page' : 'pages'})
            </option>
            <option value="all">All chapters ({scopes.all.pages} pages)</option>
          </select>
        </Field>
      )}

      <div className="toggle-list">
        <Field
          label="Translate captions on the art"
          help="Also finds large lettering drawn on the artwork, paints it out and sets the English in the same colour and glow. Needs PaddleOCR and the LaMa-manga model; takes about 5 seconds per caption."
        >
          <Toggle checked={artCaptions} onChange={setArtCaptions} />
        </Field>
      </div>

      <div className="actions">
        <button
          type="button"
          className={buttonClass('primary')}
          disabled={!config || !!blocked || running || target?.pages === 0}
          aria-describedby={blocked ? 'scanlate-blocked' : undefined}
          onClick={() => void start('missing')}
        >
          {running && jobKind === 'run' ? 'Translating…' : `Translate ${targetName}`}
        </button>
        <ConfirmButton
          name={pageNumber ? `page ${pageNumber}` : 'this page'}
          label={pageNumber ? `Redo page ${pageNumber}…` : 'Redo this page…'}
          verb="redo"
          ariaLabel={pageNumber ? `Redo page ${pageNumber}` : 'Redo this page'}
          confirmLabel={pageNumber ? `Replace text on page ${pageNumber}` : 'Replace text on this page'}
          busy={running}
          disabled={!config || !!blocked || !pageId}
          onConfirm={() => void start('page')}
        />
        {blocked && (
          <span id="scanlate-blocked" className={need ? 'visually-hidden' : 'muted'}>
            {blocked}
          </span>
        )}
      </div>
      {need && <NeedsInstall need={need} onInstalled={reload} />}
      <p className="muted scanlate-hint">
        {target ? `${target.todo} of ${target.pages} pages have no text yet. ` : ''}Translate skips pages that already have text. Redo replaces a page's text boxes, including edits.
      </p>

      {jobId && <JobPanel job={job} pollError={pollError} />}

      {pageNotes.length > 0 && (
        <div className="stack scanlate-notes" aria-label={`Page ${pageNumber ?? ''} notes`}>
          <h4>Page {pageNumber} notes</h4>
          <ul>
            {pageNotes.map((n, i) => (
              <li key={i} className={n.level === 'error' ? 'error' : n.level === 'warning' ? 'warn' : 'muted'}>
                {n.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {problemPages.length > 0 && (
        <details className="scanlate-problem-pages">
          <summary>
            {problemPages.length} other page{problemPages.length === 1 ? ' has' : 's have'} warnings
          </summary>
          <ul>
            {problemPages.map((p) => {
              const worst = p.notes.find((x) => x.level === 'error') ?? p.notes.find((x) => x.level !== 'info')
              return (
                <li key={p.page_id}>
                  <button type="button" className={buttonClass('secondary')} onClick={() => onGoToPage?.(p.ordinal)}>
                    Page {p.ordinal}
                  </button>{' '}
                  <span className={worst?.level === 'error' ? 'error' : 'warn'}>
                    {firstLine(worst?.message ?? '')}
                  </span>
                </li>
              )
            })}
          </ul>
        </details>
      )}

      <div className="actions scanlate-export">
        <button
          type="button"
          className={buttonClass('ghost')}
          disabled={!config?.page_count || running}
          onClick={() => void startExport(['zip'])}
        >
          Export ZIP
        </button>
        <button
          type="button"
          className={buttonClass('ghost')}
          disabled={!config?.page_count || running}
          onClick={() => void startExport(['pdf'])}
        >
          Export PDF
        </button>
        {exported.map((f) => (
          <a key={f} className={buttonClass('secondary')} href={scanlateExportUrl(dramaId, f)} download>
            Download {f.toUpperCase()}
          </a>
        ))}
      </div>

      <Section storageKey="comic.scanlate.advanced" title="Advanced" summary={config ? toolsSummary(config) : ''}>
        <div className="stack">
          {config && <p className="muted">{toolsSummary(config)}</p>}
          <Field label="Text detector" help="Auto uses the ML model once it is downloaded, else the OpenCV heuristic.">
            <select value={detector} onChange={(e) => setDetector(e.target.value as ScanlateDetectBackend)}>
              {DETECTORS.map((d) => (
                <option key={d.value} value={d.value}>
                  {d.label}
                </option>
              ))}
            </select>
          </Field>
          {detectorNeed && <NeedsInstall need={detectorNeed} />}
          <div className="actions">
            <ConfirmButton
              name={chapterScope ? 'every page of this chapter' : 'every page'}
              label={chapterScope ? 'Redo chapter…' : 'Redo all…'}
              verb="redo"
              confirmLabel="Replace text on every page"
              busy={running}
              disabled={!config || !!blocked}
              onConfirm={() => void start('all')}
            />
          </div>
        </div>
      </Section>
    </Card>
  )
}
