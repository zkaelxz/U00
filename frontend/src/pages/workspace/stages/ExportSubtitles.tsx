import { useState } from 'react'

import { getAssText, getSubtitleText } from '../../../api/export'
import { ButtonLink } from '../../../components/Button'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { Toggle } from '../../../components/Toggle'
import { routeHref } from '../../../router'
import type { AssStyleOptions, SubtitleField } from '../../../types/export'
import { buildAssRequest, exportFilename, MAX_BASE_NAME, parseWrap, type AssForm } from '../exportForm'
import { useStage } from '../StageContext'
import { ExportTextResult } from './ExportTextResult'
import { exportBlocked } from './stageBlockers'

export type ExportFormat = 'srt' | 'vtt' | 'ass'

// "no wrap", "wrap 42", or "wrap 42/30" (English/source), for the Advanced summary.
function wrapSummary(en: string, src: string): string {
  const e = en.trim()
  const s = src.trim()
  if (!e && !s) return 'no wrap'
  if (e === s) return `wrap ${e}`
  return `wrap ${e || 'off'}/${s || 'off'}`
}

const MIME: Record<ExportFormat, string> = { srt: 'application/x-subrip', vtt: 'text/vtt', ass: 'text/x-ssa' }

interface Props {
  fmt: ExportFormat
  setFmt: (f: ExportFormat) => void
  form: AssForm
  setForm: (f: AssForm) => void
  options: AssStyleOptions | null
  // Lines in the drama from the readiness check; null while it loads or if it failed.
  totalLines: number | null
}

// The one primary panel: Format, Language and a single Export (download) button.
// Notes and line wrapping live in Advanced; the shared form also feeds ASS and burned-in video.
export function ExportSubtitles({ fmt, setFmt, form, setForm, options, totalLines }: Props) {
  const { dramaId } = useStage()
  const blocked = exportBlocked(totalLines)
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [result, setResult] = useState<{ text: string; filename: string; fmt: ExportFormat } | null>(null)
  const set = <K extends keyof AssForm>(k: K, v: AssForm[K]) => setForm({ ...form, [k]: v })

  const fetchText = (): Promise<string> | null => {
    if (fmt === 'ass') {
      if (!options) {
        setProblem('The style options have not loaded yet.')
        return null
      }
      const built = buildAssRequest(form, options)
      setProblem(built.error ?? null)
      return built.request ? getAssText(dramaId, built.request) : null
    }
    const en = parseWrap(form.wrapEn)
    const src = parseWrap(form.wrapSource)
    setProblem(en.error ?? src.error ?? null)
    if (en.error || src.error) return null
    return getSubtitleText(dramaId, {
      fmt, field: form.field, includeNotes: form.includeNotes, wrapEn: en.value, wrapSource: src.value,
    })
  }

  const exportFile = () => {
    const p = fetchText()
    if (!p) return
    p.then(
      (text) => {
        const filename = exportFilename(form.baseName, dramaId, form.field, fmt)
        setError(null)
        setResult({ text, filename, fmt })
        if (!text.trim()) return
        const url = URL.createObjectURL(new Blob([text], { type: `${MIME[fmt]};charset=utf-8` }))
        const a = document.createElement('a')
        a.href = url
        a.download = filename
        a.click()
        URL.revokeObjectURL(url)
      },
      (e: unknown) => {
        setResult(null)
        setError(e)
      },
    )
  }

  return (
    <>
      <div className="export-primary">
        <Field label="Format">
          <select value={fmt} onChange={(e) => setFmt(e.target.value as ExportFormat)}>
            <option value="srt">SRT</option>
            <option value="vtt">VTT</option>
            <option value="ass">ASS</option>
          </select>
        </Field>
        <Field label="Language">
          <select value={form.field} onChange={(e) => set('field', e.target.value as SubtitleField)}>
            <option value="en">English</option>
            <option value="zh">Source language</option>
            <option value="bilingual">Both</option>
          </select>
        </Field>
        <button
          type="button"
          className="primary"
          disabled={blocked}
          aria-describedby={blocked ? 'export-blocker' : undefined}
          onClick={exportFile}
        >
          Export
        </button>
      </div>
      {blocked && (
        <p className="stage-blocker" id="export-blocker" data-testid="export-blocker">
          <span>No lines to export yet.</span>
          <ButtonLink variant="ghost" size="sm" href={routeHref({ name: 'drama', id: dramaId, stage: 'source' })}>
            Go to Source
          </ButtonLink>
        </p>
      )}
      <Section
        title="Advanced"
        summary={`${form.includeNotes ? 'with notes' : 'no notes'} · ${wrapSummary(form.wrapEn, form.wrapSource)} · ${exportFilename(form.baseName, dramaId, form.field, fmt)}`}
      >
        <div className="export-form">
          <Field label="Wrap English" unit="chars" help="Break English lines longer than this. Blank means no wrapping.">
            <input inputMode="numeric" value={form.wrapEn} placeholder="Off" onChange={(e) => set('wrapEn', e.target.value)} />
          </Field>
          <Field label="Wrap source" unit="chars" help="Break source-language lines longer than this. Blank means no wrapping.">
            <input inputMode="numeric" value={form.wrapSource} placeholder="Off" onChange={(e) => set('wrapSource', e.target.value)} />
          </Field>
          <Field label="File name" help="Name of the downloaded file, without the extension. Blank uses drama_<id>_<language>.">
            <input
              value={form.baseName}
              maxLength={MAX_BASE_NAME}
              placeholder={`drama_${dramaId}_${form.field}`}
              onChange={(e) => set('baseName', e.target.value)}
            />
          </Field>
        </div>
        <div className="setting-list">
          <Field label="Include notes" help="Add translation notes inline in the exported text.">
            <Toggle checked={form.includeNotes} onChange={(v) => set('includeNotes', v)} />
          </Field>
        </div>
      </Section>
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {result && <ExportTextResult text={result.text} filename={result.filename} mime={MIME[result.fmt]} />}
    </>
  )
}
