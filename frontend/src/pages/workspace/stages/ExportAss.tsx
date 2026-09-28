import { useState } from 'react'

import { getAssText } from '../../../api/export'
import { ErrorBanner } from '../../../components/ErrorBanner'
import type { AssStyleOptions, SubtitleField } from '../../../types/export'
import { buildAssRequest, MAX_SPEAKER_COLORS, type AssForm } from '../exportForm'
import { useStage } from '../StageContext'
import { ExportTextResult } from './ExportTextResult'

interface Props {
  form: AssForm
  setForm: (f: AssForm) => void
  options: AssStyleOptions | null
  optionsError: unknown
}

// The style form is owned by ExportStage so the burned-in video job reuses it.
export function ExportAss({ form, setForm, options, optionsError }: Props) {
  const { dramaId } = useStage()
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [result, setResult] = useState<{ text: string; filename: string } | null>(null)
  const set = <K extends keyof AssForm>(k: K, v: AssForm[K]) => setForm({ ...form, [k]: v })

  const generate = () => {
    if (!options) return
    const built = buildAssRequest(form, options)
    setProblem(built.error ?? null)
    if (!built.request) return
    getAssText(dramaId, built.request).then(
      (text) => {
        setError(null)
        setResult({ text, filename: `drama_${dramaId}_${form.field}.ass` })
      },
      (e: unknown) => {
        setResult(null)
        setError(e)
      },
    )
  }

  const align = (key: 'alignment' | 'sfxAlignment' | 'notesAlignment', label: string) => (
    <label>
      {label}
      <select value={form[key]} onChange={(e) => set(key, e.target.value)}>
        <option value="">Preset default</option>
        {Object.keys(options?.alignments ?? {}).map((a) => (
          <option key={a} value={a}>{a}</option>
        ))}
      </select>
    </label>
  )
  const num = (key: 'size' | 'outlineWidth' | 'shadow', label: string, range: number[] | undefined) => (
    <label>
      {label}{range ? ` (${range[0]} to ${range[1]})` : ''}
      <input inputMode="numeric" value={form[key]} placeholder="Preset default" onChange={(e) => set(key, e.target.value)} />
    </label>
  )
  const tri = (key: 'bold' | 'italic', label: string) => (
    <label>
      {label}
      <select value={form[key]} onChange={(e) => set(key, e.target.value as AssForm['bold'])}>
        <option value="">Preset default</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    </label>
  )

  return (
    <section className="panel" aria-label="ASS subtitle file">
      <h3>ASS subtitle file (styled)</h3>
      <p className="muted">These style settings are also used for the burned-in video below.</p>
      <ErrorBanner error={optionsError} />
      {options && (
        <>
          <div className="export-form">
            <label>
              Text
              <select value={form.field} onChange={(e) => set('field', e.target.value as SubtitleField)}>
                <option value="en">English</option>
                <option value="zh">Source language</option>
                <option value="bilingual">Both</option>
              </select>
            </label>
            <label>
              Preset
              <select value={form.preset} onChange={(e) => set('preset', e.target.value)}>
                {Object.keys(options.presets).map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </label>
            <label>
              Font
              <input list="ass-fonts" value={form.font} placeholder="Preset default" onChange={(e) => set('font', e.target.value)} />
              <datalist id="ass-fonts">
                {options.fonts.map((f) => <option key={f} value={f} />)}
              </datalist>
            </label>
            {num('size', 'Font size', options.size_range)}
            {num('outlineWidth', 'Outline width', options.outline_width_range)}
            {num('shadow', 'Shadow', options.shadow_range)}
            <label>
              Text colour
              <input value={form.primary} placeholder="#RRGGBB (preset default)" onChange={(e) => set('primary', e.target.value)} />
            </label>
            <label>
              Outline colour
              <input value={form.outline} placeholder="#RRGGBB (preset default)" onChange={(e) => set('outline', e.target.value)} />
            </label>
            {tri('bold', 'Bold')}
            {tri('italic', 'Italic')}
            {align('alignment', 'Position')}
            {align('sfxAlignment', 'Sound-effect position')}
            {align('notesAlignment', 'Notes position')}
            <label>
              Wrap English at (characters)
              <input inputMode="numeric" value={form.wrapEn} placeholder="no wrapping" onChange={(e) => set('wrapEn', e.target.value)} />
            </label>
            <label>
              Wrap source at (characters)
              <input inputMode="numeric" value={form.wrapSource} placeholder="no wrapping" onChange={(e) => set('wrapSource', e.target.value)} />
            </label>
            <label className="export-check">
              <input type="checkbox" checked={form.perSpeakerColors} onChange={(e) => set('perSpeakerColors', e.target.checked)} />
              Colour each speaker differently
            </label>
            <label className="export-check">
              <input type="checkbox" checked={form.includeNotes} onChange={(e) => set('includeNotes', e.target.checked)} />
              Include translation notes
            </label>
            <label className="export-check">
              <input
                type="checkbox"
                checked={form.notesAsSeparateLine}
                disabled={!form.includeNotes}
                onChange={(e) => set('notesAsSeparateLine', e.target.checked)}
              />
              Show notes on their own line
            </label>
          </div>
          <label>
            Speaker colours (one &quot;Name = #RRGGBB&quot; per line, at most {MAX_SPEAKER_COLORS})
            <textarea rows={3} value={form.speakerColors} onChange={(e) => set('speakerColors', e.target.value)} />
          </label>
          <button type="button" onClick={generate}>Generate ASS</button>
        </>
      )}
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {result && <ExportTextResult text={result.text} filename={result.filename} mime="text/x-ssa" />}
    </section>
  )
}
