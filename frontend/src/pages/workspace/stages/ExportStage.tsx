import { useEffect, useState } from 'react'

import { getAssStyleOptions, getReadiness } from '../../../api/export'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Section } from '../../../components/Section'
import type { AssStyleOptions, ExportReadiness } from '../../../types/export'
import { buildAssRequest, emptyAssForm, type AssForm } from '../exportForm'
import { useStage } from '../StageContext'
import { ExportAss } from './ExportAss'
import { ExportFlags } from './ExportFlags'
import { ExportEpub, ExportMediaJobs } from './ExportMedia'
import { ExportSubtitles, type ExportFormat } from './ExportSubtitles'
import './export.css'

const FMT_KEY = 'baihe.export.format'
const FIELD_KEY = 'baihe.export.language'

// Last-used choices are a per-viewer convenience; storage may be missing or throw.
function readChoice<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = window.localStorage.getItem(key)
    return allowed.find((a) => a === v) ?? fallback
  } catch {
    return fallback
  }
}

function writeChoice(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // ignore
  }
}

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`

export default function ExportStage() {
  const { dramaId, drama } = useStage()
  const [readiness, setReadiness] = useState<ExportReadiness | null>(null)
  const [readinessError, setReadinessError] = useState<unknown>(null)
  const [reloads, setReloads] = useState(0)
  const [options, setOptions] = useState<AssStyleOptions | null>(null)
  const [optionsError, setOptionsError] = useState<unknown>(null)
  const [form, setFormState] = useState<AssForm>(() => ({
    ...emptyAssForm(''),
    field: readChoice(FIELD_KEY, ['en', 'zh', 'bilingual'], 'en'),
  }))
  const [fmt, setFmtState] = useState<ExportFormat>(() => readChoice(FMT_KEY, ['srt', 'vtt', 'ass'], 'srt'))

  const setForm = (f: AssForm) => {
    setFormState(f)
    writeChoice(FIELD_KEY, f.field)
  }
  const setFmt = (f: ExportFormat) => {
    setFmtState(f)
    writeChoice(FMT_KEY, f)
  }

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
        setFormState((f) => ({ ...f, preset: o.default_preset }))
      },
      (e: unknown) => !cancelled && setOptionsError(e),
    )
    return () => {
      cancelled = true
    }
  }, [])

  const r = readiness
  const review: string[] = []
  if (r) {
    if (r.overlap_count > 0) review.push(plural(r.overlap_count, 'overlapping line'))
    if (r.auto_qc_issue_count > 0) review.push(plural(r.auto_qc_issue_count, 'auto-QC issue'))
    if (r.dense_line_count > 0) review.push(plural(r.dense_line_count, 'dense line'))
  }
  const untranslated = r !== null && r.total_lines > 0 && !r.fully_translated
  return (
    <div className="stage-export">
      <section className="panel" aria-label="Export">
        <h3>Export</h3>
        <ErrorBanner error={readinessError} />
        {r && (
          <>
            <p className="export-line" data-testid="readiness">
              {plural(r.total_lines, 'line')} · {r.en_filled} translated
            </p>
            {(untranslated || review.length > 0 || r.test_mode_output) && (
              <ul className="export-warnings" data-testid="readiness-warnings">
                {untranslated && <li className="muted">Some lines are not translated yet, so English exports will have gaps.</li>}
                {review.length > 0 && (
                  <li className="muted">{review.join(', ')}. You can flag them under More export.</li>
                )}
                {r.test_mode_output && (
                  <li className="error" role="alert">The translations look like test-mode output, not real translations.</li>
                )}
              </ul>
            )}
          </>
        )}
        <ExportSubtitles fmt={fmt} setFmt={setFmt} form={form} setForm={setForm} options={options} />
      </section>
      {options ? <ExportAss form={form} setForm={setForm} options={options} /> : <ErrorBanner error={optionsError} />}
      <Section title="More export" summary="flags, EPUB, audiobook, burned-in video">
        <ExportFlags onDone={() => setReloads((n) => n + 1)} />
        {drama.content_mode === 'novel_narration' && <ExportEpub />}
        <ExportMediaJobs
          request={() =>
            options ? buildAssRequest(form, options) : { error: 'The style options have not loaded yet.' }
          }
        />
      </Section>
    </div>
  )
}
