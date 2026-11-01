/* The side panel for the selected text box: its fields and the Delete box and Apply actions. */
import { buttonClass } from '../../components/uiClasses'
import { MAX_REGION_TEXT_CHARS } from '../../types/scanlate'
import { draftValid, type Draft } from './editorState'

// Values the server's font_category accepts (scanlate_render.FONT_CATEGORIES); shown as stored.
const FONTS = ['regular', 'bold', 'handwritten']
// Values the server's kind accepts (scanlate_regions.REGION_KINDS); shown as stored.
const KINDS = ['bubble', 'thought', 'narration', 'caption', 'sign', 'sfx']
const SWATCHES = ['#111111', '#ffffff', '#b3261e']

interface Props {
  number: number
  source: string
  draft: Draft
  busy: boolean
  setDraft: (patch: Partial<Draft>) => void
  onDelete: () => void
  onApply: () => void
}

export function EditorPanel({ number, source, draft, busy, setDraft, onDelete, onApply }: Props) {
  return (
    <aside className="ce-panel" aria-label={`Text box ${number}`}>
      <h2 className="ce-panel-title">{`Text box ${number}`}</h2>
      <label className="ce-field">
        <span>English</span>
        <textarea
          rows={3}
          maxLength={MAX_REGION_TEXT_CHARS}
          value={draft.text}
          onChange={(e) => setDraft({ text: e.target.value })}
        />
      </label>
      <label className="ce-field">
        <span>Original</span>
        <textarea rows={2} readOnly value={source} />
      </label>
      <div className="ce-row">
        <label className="ce-field">
          <span>Font</span>
          <select value={draft.fontCategory} onChange={(e) => setDraft({ fontCategory: e.target.value })}>
            {(FONTS.includes(draft.fontCategory) ? FONTS : [draft.fontCategory, ...FONTS]).map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>
        <label className="ce-field">
          <span>Kind</span>
          <select value={draft.kind} onChange={(e) => setDraft({ kind: e.target.value })}>
            {(KINDS.includes(draft.kind) ? KINDS : [draft.kind, ...KINDS]).map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <label className="ce-field ce-size">
          <span>Size</span>
          <input type="number" inputMode="numeric" min={6} max={200} value={draft.size} onChange={(e) => setDraft({ size: e.target.value })} />
        </label>
      </div>
      <div className="ce-field">
        <span id="ce-colour">Colour</span>
        <div className="ce-swatches" role="group" aria-labelledby="ce-colour">
          {SWATCHES.map((c) => (
            <button
              key={c}
              type="button"
              className="ce-swatch"
              style={{ background: c }}
              aria-label={c}
              aria-pressed={draft.color?.toLowerCase() === c}
              onClick={() => setDraft({ color: c })}
            />
          ))}
        </div>
      </div>
      <div className="ce-row ce-apply">
        <button type="button" className={buttonClass()} disabled={busy} onClick={() => onDelete()}>
          Delete box
        </button>
        <button
          type="button"
          className={buttonClass('primary')}
          disabled={busy || !draftValid(draft)}
          onClick={onApply}
        >
          Apply
        </button>
      </div>
    </aside>
  )
}
