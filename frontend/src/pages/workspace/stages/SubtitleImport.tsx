/*
 * SubtitleImport: "Import subtitle file" in the Source stage. Pick an SRT, VTT, ASS/SSA or
 * LRC file; the server parses and checks it (POST /api/subtitle-import/dramas/{id}/preview)
 * and this shows the encoding it read, the cue count and any problems before anything is
 * written. Import as source lines (the file's times are kept) or as translation text put on
 * the existing lines by time overlap. Re-aligning with the audio is the existing Re-time job,
 * started afterwards and off by default.
 *
 *   <SubtitleImport dramaId={3} busy={busy} onImported={reload} onRealignStarted={setJobId} />
 */
import { useEffect, useId, useState } from 'react'

import { applySubtitle, previewSubtitle } from '../../../api/subtitleImport'
import { startRetime } from '../../../api/workspace'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Toggle } from '../../../components/Toggle'
import type { SubtitleImportMode, SubtitleImportOptions, SubtitleImportPreview } from '../../../types/subtitleImport'
import {
  checkSubtitleFile,
  confirmLabel,
  ENCODING_CHOICES,
  fileSummary,
  impactLine,
  needsConfirm,
  SUBTITLE_EXTENSIONS,
} from './subtitleImportModel'
import './subtitleImport.css'

type Props = {
  dramaId: number
  busy?: boolean
  onImported: () => void
  onRealignStarted: (jobId: string) => void
}

const DEFAULTS: SubtitleImportOptions = { mode: 'source', encoding: '', splitBilingual: false, translationFirst: false }

export function SubtitleImport({ dramaId, busy, onImported, onRealignStarted }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [fileProblem, setFileProblem] = useState<string | null>(null)
  const [opts, setOpts] = useState<SubtitleImportOptions>(DEFAULTS)
  const [preview, setPreview] = useState<SubtitleImportPreview | null>(null)
  const [checking, setChecking] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [realign, setRealign] = useState(false)
  const [importing, setImporting] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  // A new key empties the file input after an import, so it doesn't keep showing the old name.
  const [inputKey, setInputKey] = useState(0)
  const inputId = useId()

  // Re-checks whenever the file or an option changes, so the counts always match what Import will do.
  useEffect(() => {
    if (!file) return
    let cancelled = false
    setChecking(true)
    previewSubtitle(dramaId, file, opts).then(
      (p) => {
        if (cancelled) return
        setPreview(p)
        setError(null)
        setChecking(false)
      },
      (e: unknown) => {
        if (cancelled) return
        setPreview(null)
        setError(e)
        setChecking(false)
      },
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, file, opts])

  const pick = (f: File | null) => {
    setDone(null)
    setPreview(null)
    setError(null)
    setConfirmed(false)
    const problem = f ? checkSubtitleFile(f.name, f.size) : null
    setFileProblem(problem)
    setFile(f && !problem ? f : null)
    if (f && !problem) setOpts(DEFAULTS)
  }

  const set = <K extends keyof SubtitleImportOptions>(k: K, v: SubtitleImportOptions[K]) => {
    setConfirmed(false)
    setOpts({ ...opts, [k]: v })
  }

  const confirmKind = preview ? needsConfirm(preview) : null
  const blocked = !preview || preview.blocking || !!preview.blocked_reason || checking
  const canImport = !!file && !blocked && !busy && !importing && (!confirmKind || confirmed)

  const runImport = () => {
    if (!file || !canImport) return
    setImporting(true)
    setDone(null)
    applySubtitle(dramaId, file, opts, { replaceLines: confirmKind === 'replace' && confirmed, overwrite: confirmKind === 'overwrite' && confirmed }).then(
      (r) => {
        setImporting(false)
        setError(null)
        setFile(null)
        setInputKey((n) => n + 1)
        setPreview(null)
        setConfirmed(false)
        setDone(
          r.mode === 'source'
            ? `Imported ${r.lines_written} line${r.lines_written === 1 ? '' : 's'}. The previous text is saved to history.`
            : `Put text on ${r.lines_written} line${r.lines_written === 1 ? '' : 's'}${r.unmatched_cues ? `; ${r.unmatched_cues} cue${r.unmatched_cues === 1 ? '' : 's'} matched no line` : ''}. The previous text is saved to history.`,
        )
        onImported()
        if (realign && r.line_ids.length > 0) {
          startRetime(dramaId, { line_ids: r.line_ids }).then(
            (job) => {
              setDone((d) => `${d ?? ''} Re-aligning with the audio; review the new times in Review > Re-time.`.trim())
              onRealignStarted(job.job_id)
            },
            (e: unknown) => setError(e),
          )
        }
      },
      (e: unknown) => {
        setImporting(false)
        setError(e)
      },
    )
  }

  return (
    <div className="subtitle-import" data-testid="subtitle-import">
      <p className="muted">
        Timed lines from an SRT, VTT, ASS/SSA or LRC file. The file's times are kept as they are.
      </p>
      <div className="subtitle-import-pick">
        <input
          key={inputKey}
          type="file"
          id={inputId}
          aria-label="Subtitle file"
          accept={SUBTITLE_EXTENSIONS.join(',')}
          onChange={(e) => pick(e.target.files?.[0] ?? null)}
        />
      </div>
      {fileProblem && (
        <p className="error" role="alert">
          {fileProblem}
        </p>
      )}
      {file && checking && !preview && <p className="muted" role="status">Checking the file…</p>}
      {preview && (
        <div className="subtitle-import-report" data-testid="subtitle-import-report">
          <p data-testid="subtitle-import-summary">{fileSummary(preview)}</p>
          {preview.problems.length > 0 && (
            <ul className="subtitle-import-problems" aria-label="Problems found">
              {preview.problems.map((p) => (
                <li key={p.code} className={p.severity === 'error' ? 'error' : 'muted'}>
                  {p.message}
                </li>
              ))}
            </ul>
          )}
          {preview.sample.length > 0 && (
            <ol className="subtitle-import-sample muted" aria-label="First cues">
              {preview.sample.slice(0, 3).map((c, i) => (
                <li key={i}>{c.text.replace(/\n/g, ' / ')}</li>
              ))}
            </ol>
          )}
          <div className="segmented subtitle-import-mode" role="radiogroup" aria-label="Import as">
            {(['source', 'translation'] as SubtitleImportMode[]).map((m) => (
              <label key={m} className={opts.mode === m ? 'segmented-on' : undefined}>
                <input
                  type="radio"
                  name={`subtitle-import-mode-${dramaId}`}
                  checked={opts.mode === m}
                  onChange={() => set('mode', m)}
                />
                {m === 'source' ? 'Source lines' : 'Translation'}
              </label>
            ))}
          </div>
          <div className="setting-list">
            <Field
              label="Two lines per cue"
              help="The first line becomes the source and the second the translation (or the reverse)."
            >
              <Toggle checked={opts.splitBilingual} onChange={(v) => set('splitBilingual', v)} />
            </Field>
            {opts.splitBilingual && (
              <Field label="First line is">
                <select
                  value={opts.translationFirst ? 'translation' : 'source'}
                  onChange={(e) => set('translationFirst', e.target.value === 'translation')}
                >
                  <option value="source">The source</option>
                  <option value="translation">The translation</option>
                </select>
              </Field>
            )}
            <Field label="Text encoding" help="Change this if the characters above look wrong.">
              <select value={opts.encoding} onChange={(e) => set('encoding', e.target.value)}>
                {ENCODING_CHOICES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {preview.bilingual_suspected && !opts.splitBilingual && (
            <p className="muted">Most cues have two lines. Turn on "Two lines per cue" to split them.</p>
          )}
          <p data-testid="subtitle-import-impact">{impactLine(preview)}</p>
          {opts.splitBilingual && preview.unsplit_cues > 0 && (
            <p className="muted">{preview.unsplit_cues} cue(s) don't have exactly two lines and stay whole.</p>
          )}
          {confirmKind && (
            <label className="subtitle-import-confirm">
              <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
              {confirmLabel(preview)}
            </label>
          )}
          <div className="setting-list">
            <Field label="Re-align with the audio afterwards" help="Starts the Re-time job on the imported lines. You review the new times in Review before they are applied.">
              <Toggle checked={realign} onChange={setRealign} />
            </Field>
          </div>
        </div>
      )}
      <div className="actions">
        <button type="button" className="primary" disabled={!canImport} onClick={runImport}>
          {importing ? 'Importing…' : 'Import'}
        </button>
        {busy && <span className="muted">Wait for the running job to finish.</span>}
      </div>
      {done && <p role="status">{done}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
    </div>
  )
}
