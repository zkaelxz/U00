import { useState } from 'react'

import { getSubtitleText } from '../../../api/export'
import { ErrorBanner } from '../../../components/ErrorBanner'
import type { SubtitleField, SubtitleFormat } from '../../../types/export'
import { parseWrap } from '../exportForm'
import { useStage } from '../StageContext'
import { ExportTextResult } from './ExportTextResult'

export function ExportSubtitles() {
  const { dramaId } = useStage()
  const [fmt, setFmt] = useState<SubtitleFormat>('srt')
  const [field, setField] = useState<SubtitleField>('en')
  const [notes, setNotes] = useState(false)
  const [wrapEn, setWrapEn] = useState('')
  const [wrapSource, setWrapSource] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [result, setResult] = useState<{ text: string; filename: string; fmt: SubtitleFormat } | null>(null)

  const generate = () => {
    const en = parseWrap(wrapEn)
    const src = parseWrap(wrapSource)
    setProblem(en.error ?? src.error ?? null)
    if (en.error || src.error) return
    getSubtitleText(dramaId, { fmt, field, includeNotes: notes, wrapEn: en.value, wrapSource: src.value }).then(
      (text) => {
        setError(null)
        setResult({ text, filename: `drama_${dramaId}_${field}.${fmt}`, fmt })
      },
      (e: unknown) => {
        setResult(null)
        setError(e)
      },
    )
  }

  return (
    <section className="panel" aria-label="Subtitle file">
      <h3>Subtitle file (SRT / VTT)</h3>
      <div className="export-form">
        <label>
          Format
          <select value={fmt} onChange={(e) => setFmt(e.target.value as SubtitleFormat)}>
            <option value="srt">SRT</option>
            <option value="vtt">VTT</option>
          </select>
        </label>
        <label>
          Text
          <select value={field} onChange={(e) => setField(e.target.value as SubtitleField)}>
            <option value="en">English</option>
            <option value="zh">Source language</option>
            <option value="bilingual">Both</option>
          </select>
        </label>
        <label>
          Wrap English at (characters)
          <input inputMode="numeric" value={wrapEn} placeholder="no wrapping" onChange={(e) => setWrapEn(e.target.value)} />
        </label>
        <label>
          Wrap source at (characters)
          <input inputMode="numeric" value={wrapSource} placeholder="no wrapping" onChange={(e) => setWrapSource(e.target.value)} />
        </label>
        <label className="export-check">
          <input type="checkbox" checked={notes} onChange={(e) => setNotes(e.target.checked)} />
          Include translation notes inline
        </label>
      </div>
      <button type="button" onClick={generate}>Generate {fmt.toUpperCase()}</button>
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {result && <ExportTextResult text={result.text} filename={result.filename} mime={result.fmt === 'vtt' ? 'text/vtt' : 'application/x-subrip'} />}
    </section>
  )
}
