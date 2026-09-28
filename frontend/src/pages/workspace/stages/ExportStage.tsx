import { useEffect, useState } from 'react'

import { getAssStyleOptions, getReadiness } from '../../../api/export'
import { ErrorBanner } from '../../../components/ErrorBanner'
import type { AssStyleOptions, ExportReadiness } from '../../../types/export'
import { buildAssRequest, emptyAssForm, type AssForm } from '../exportForm'
import { useStage } from '../StageContext'
import { ExportAss } from './ExportAss'
import { ExportFlags } from './ExportFlags'
import { ExportEpub, ExportMediaJobs } from './ExportMedia'
import { ExportSubtitles } from './ExportSubtitles'
import './exportStage.css'

export default function ExportStage() {
  const { dramaId, drama } = useStage()
  const [readiness, setReadiness] = useState<ExportReadiness | null>(null)
  const [readinessError, setReadinessError] = useState<unknown>(null)
  const [reloads, setReloads] = useState(0)
  const [options, setOptions] = useState<AssStyleOptions | null>(null)
  const [optionsError, setOptionsError] = useState<unknown>(null)
  const [form, setForm] = useState<AssForm | null>(null)

  useEffect(() => {
    let cancelled = false
    getReadiness(dramaId).then(
      (r) => !cancelled && (setReadiness(r), setReadinessError(null)),
      (e: unknown) => !cancelled && setReadinessError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads])

  useEffect(() => {
    let cancelled = false
    getAssStyleOptions().then(
      (o) => {
        if (cancelled) return
        setOptions(o)
        setForm(emptyAssForm(o.default_preset))
      },
      (e: unknown) => !cancelled && setOptionsError(e),
    )
    return () => {
      cancelled = true
    }
  }, [])

  const r = readiness
  return (
    <div className="stage-export">
      <section className="panel" aria-label="Export readiness">
        <h3>Readiness</h3>
        <ErrorBanner error={readinessError} />
        {r && (
          <>
            <ul className="export-counts" data-testid="readiness">
              <li>Lines: <strong>{r.total_lines}</strong></li>
              <li>Source text filled: <strong>{r.zh_filled}</strong></li>
              <li>Translated: <strong>{r.en_filled}</strong></li>
              <li>Overlapping lines: <strong>{r.overlap_count}</strong></li>
              <li>Auto-QC issues: <strong>{r.auto_qc_issue_count}</strong></li>
              <li>Dense lines: <strong>{r.dense_line_count}</strong></li>
            </ul>
            {r.total_lines > 0 && !r.fully_translated && (
              <p className="muted">Some lines are not translated yet, so English exports will have gaps.</p>
            )}
            {r.test_mode_output && (
              <p className="error" role="alert">
                The translations look like test-mode output, not real translations.
              </p>
            )}
          </>
        )}
      </section>
      <ExportSubtitles />
      {form && (
        <ExportAss form={form} setForm={setForm} options={options} optionsError={optionsError} />
      )}
      {!form && optionsError !== null && <ErrorBanner error={optionsError} />}
      <ExportFlags onDone={() => setReloads((n) => n + 1)} />
      {drama.content_mode === 'novel_narration' && <ExportEpub />}
      <ExportMediaJobs
        request={() =>
          options && form
            ? buildAssRequest(form, options)
            : { error: 'The style options have not loaded yet.' }
        }
      />
    </div>
  )
}
