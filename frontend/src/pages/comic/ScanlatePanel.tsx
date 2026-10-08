/*
 * The Comic page's Translate panel: upload pages (PC only), pick an engine,
 * "Translate all pages" (skips pages that already have text), "Redo this
 * page", "Redo all…" (confirm), job progress and cancel, the current page's
 * run notes, and ZIP/PDF export. One Scanlate job runs per drama; when it
 * finishes, onChanged() lets the viewer reload its pages so the typeset
 * images show. Server notes are rendered as text only.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { scanlateApi, scanlateExportUrl } from '../../api/scanlate'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { useJob, useJobRun } from '../../hooks/useJob'
import { usePcOnly } from '../../hooks/usePcOnly'
import type { JobRecord } from '../../types/jobs'
import type {
  ScanlateConfig,
  ScanlateDetectBackend,
  ScanlateExportFormat,
  ScanlatePageNotes,
  ScanlateRunMode,
} from '../../types/scanlate'
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
  pageNumber: number | null
  // Page counts for "this chapter" and "all chapters" (hidden pages never count).
  scopes?: ScopeCounts
  onChanged: () => void
}

const DETECTORS: { value: ScanlateDetectBackend; label: string }[] = [
  { value: 'auto', label: 'Auto (ML model if downloaded)' },
  { value: 'cv', label: 'OpenCV heuristic' },
  { value: 'ml', label: 'ML model (downloads once)' },
]

export function ScanlatePanel({ dramaId, pageId, pageNumber, scopes, onChanged }: Props) {
  const pc = usePcOnly()
  const [config, setConfig] = useState<ScanlateConfig | null>(null)
  const [notes, setNotes] = useState<ScanlatePageNotes[]>([])
  const [error, setError] = useState<unknown>(null)
  const [engine, setEngine] = useState('')
  const [detector, setDetector] = useState<ScanlateDetectBackend>('auto')
  const [files, setFiles] = useState<File[]>([])
  const [slice, setSlice] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [scope, setScope] = useState<'chapter' | 'all'>('all')
  const [exported, setExported] = useState<ScanlateExportFormat[]>([])
  const [jobKind, setJobKind] = useState<'run' | 'export' | null>(null)
  const [loadKey, setLoadKey] = useState(0)
  const fileInput = useRef<HTMLInputElement>(null)
  const [jobId, setJobId, runKey] = useJobRun()
  const pendingExport = useRef<ScanlateExportFormat[]>([])

  const reload = useCallback(() => setLoadKey((n) => n + 1), [])
  const onDone = useCallback(
    (job: JobRecord) => {
      if (job.status === 'done' && pendingExport.current.length) setExported(pendingExport.current)
      reload()
      onChanged()
    },
    [reload, onChanged],
  )
  const { job, done, error: pollError } = useJob(jobId, { runKey, onDone })
  const running = starting || (jobId !== null && !done && !pollError)

  useEffect(() => {
    let cancelled = false
    scanlateApi.config(dramaId).then(
      (c) => {
        if (cancelled) return
        setConfig(c)
        setEngine((e) => e || defaultEngine(c))
        setSlice((s) => (loadKey === 0 ? c.upload_limits.slice_strips_default : s))
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

  const blocked = runBlockedReason(config, engine)
  const usable = usableEngines(config)

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
      const r = await scanlateApi.export(dramaId, formats)
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
  const otherNoteCount = notes.filter((n) => n.page_id !== pageId && n.notes.some((x) => x.level !== 'info')).length
  const limits = config?.upload_limits
  const accept = '.png,.jpg,.jpeg,.webp,.pdf,image/png,image/jpeg,image/webp,application/pdf'

  return (
    <Card
      className="scanlate-panel"
      aria-label="Translate pages"
      title="Translate pages"
      meta={config ? progressSummary(config) : 'Loading…'}
    >
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {pc === 'remote' ? (
        <p className="muted">Uploading pages is PC only.</p>
      ) : (
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
              PNG, JPEG, WebP or PDF · up to {limits.max_image_mb} MB and {limits.max_image_megapixels} MP per image,{' '}
              {limits.max_pdf_mb} MB and {limits.max_pdf_pages} pages per PDF, {limits.max_files} files and{' '}
              {limits.max_total_mb >= 1024 ? `${limits.max_total_mb / 1024} GB` : `${limits.max_total_mb} MB`} at once.
            </p>
          )}
          {fileError && <p className="error" role="alert">{fileError}</p>}
          {uploadMsg && <p role="status">{uploadMsg}</p>}
        </div>
      )}

      <div className="scanlate-run-row">
        <Field label={AI_ENGINE_LABEL} help="Keys stay on the PC; engines without a key are not listed.">
          <select value={engine} onChange={(e) => setEngine(e.target.value)} disabled={!usable.length}>
            {usable.length === 0 && <option value="">No engine has a key</option>}
            {usable.map((e) => (
              <option key={e.name} value={e.name}>
                {e.label}
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
        <button
          type="button"
          className={buttonClass('secondary')}
          disabled={!config || !!blocked || running || !pageId}
          onClick={() => void start('page')}
        >
          {pageNumber ? `Redo page ${pageNumber}` : 'Redo this page'}
        </button>
        {blocked && (
          <span id="scanlate-blocked" className="muted">
            {blocked}
          </span>
        )}
      </div>
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
      {otherNoteCount > 0 && (
        <p className="muted">
          {otherNoteCount} other page{otherNoteCount === 1 ? ' has' : 's have'} warnings; open that page to see them.
        </p>
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
