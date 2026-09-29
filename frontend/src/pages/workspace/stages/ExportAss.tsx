import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import type { AssStyleOptions } from '../../../types/export'
import { MAX_SPEAKER_COLORS, resolveAssStyle, type AssForm } from '../exportForm'

interface Props {
  form: AssForm
  setForm: (f: AssForm) => void
  options: AssStyleOptions
}

// The style form is owned by ExportStage so the ASS file and the burned-in video share it.
export function ExportAss({ form, setForm, options }: Props) {
  const set = <K extends keyof AssForm>(k: K, v: AssForm[K]) => setForm({ ...form, [k]: v })

  const align = (key: 'alignment' | 'sfxAlignment' | 'notesAlignment', label: string) => (
    <Field label={label}>
      <select value={form[key]} onChange={(e) => set(key, e.target.value)}>
        <option value="">Preset default</option>
        {Object.keys(options.alignments).map((a) => (
          <option key={a} value={a}>{a}</option>
        ))}
      </select>
    </Field>
  )
  const num = (key: 'size' | 'outlineWidth' | 'shadow', label: string, range: number[] | undefined) => (
    <Field label={label} help={range ? `From ${range[0]} to ${range[1]}. Blank uses the preset.` : undefined}>
      <input
        inputMode="numeric"
        value={form[key]}
        placeholder={range ? `${range[0]}-${range[1]}` : 'preset'}
        onChange={(e) => set(key, e.target.value)}
      />
    </Field>
  )
  const tri = (key: 'bold' | 'italic', label: string) => (
    <Field label={label}>
      <select value={form[key]} onChange={(e) => set(key, e.target.value as AssForm['bold'])}>
        <option value="">Preset default</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    </Field>
  )

  const look = resolveAssStyle(form, options)
  const summary = `${form.preset || 'default'} · ${form.font || 'preset font'} · ${form.size ? `${form.size} pt` : 'preset size'}`
  return (
    <Section title="ASS style" summary={summary}>
      <p className="muted">Used for the ASS file and the burned-in video.</p>
      <div
        className={`ass-preview ass-preview-${look.alignment.split('-')[1] ?? 'center'}`}
        data-testid="ass-preview"
        role="img"
        aria-label="Approximate style preview"
        title="Approximate preview; the exported file may render slightly differently."
      >
        <span
          style={{
            fontFamily: `"${look.font}", sans-serif`,
            fontSize: `${look.size}px`,
            fontWeight: look.bold ? 700 : 400,
            fontStyle: look.italic ? 'italic' : 'normal',
            color: look.primary,
            WebkitTextStroke: look.outlineWidth ? `${look.outlineWidth / 2}px ${look.outline}` : undefined,
            textShadow: look.shadow ? `${look.shadow}px ${look.shadow}px 0 ${look.outline}` : undefined,
          }}
        >
          Sample subtitle line
        </span>
      </div>
      <div className="export-form">
        <Field label="Preset">
          <select value={form.preset} onChange={(e) => set('preset', e.target.value)}>
            {Object.keys(options.presets).map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </Field>
        <div>
          <Field label="Font">
            <input list="ass-fonts" value={form.font} placeholder="Preset default" onChange={(e) => set('font', e.target.value)} />
          </Field>
          <datalist id="ass-fonts">
            {options.fonts.map((f) => <option key={f} value={f} />)}
          </datalist>
        </div>
        {num('size', 'Font size', options.size_range)}
        {num('outlineWidth', 'Outline width', options.outline_width_range)}
        {num('shadow', 'Shadow', options.shadow_range)}
        <Field label="Text colour" help="#RRGGBB. Blank uses the preset.">
          <input value={form.primary} placeholder="#RRGGBB" onChange={(e) => set('primary', e.target.value)} />
        </Field>
        <Field label="Outline colour" help="#RRGGBB. Blank uses the preset.">
          <input value={form.outline} placeholder="#RRGGBB" onChange={(e) => set('outline', e.target.value)} />
        </Field>
        {tri('bold', 'Bold')}
        {tri('italic', 'Italic')}
        {align('alignment', 'Position')}
        {align('sfxAlignment', 'Sound-effect position')}
        {align('notesAlignment', 'Notes position')}
        <Field label="Colour each speaker">
          <input type="checkbox" checked={form.perSpeakerColors} onChange={(e) => set('perSpeakerColors', e.target.checked)} />
        </Field>
        <Field label="Notes on own line">
          <input
            type="checkbox"
            checked={form.notesAsSeparateLine}
            disabled={!form.includeNotes}
            onChange={(e) => set('notesAsSeparateLine', e.target.checked)}
          />
        </Field>
      </div>
      <Field label="Speaker colours" help={`One "Name = #RRGGBB" per line, at most ${MAX_SPEAKER_COLORS}.`}>
        <textarea rows={3} value={form.speakerColors} onChange={(e) => set('speakerColors', e.target.value)} />
      </Field>
    </Section>
  )
}
