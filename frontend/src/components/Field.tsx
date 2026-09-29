/*
 * Field: label + one control + optional unit, help and error line.
 *
 *   <Field label="Batch (lines)" unit="lines" help="Lines sent per request." error={err}>
 *     <input type="number" value={n} onChange={...} />
 *   </Field>
 *
 * Props
 *   label     short label (1-4 words); it is the control's accessible name
 *   unit      optional suffix shown after the control (e.g. "s", "$")
 *   help      optional longer text, behind a focusable (i) button; shown on
 *             hover, focus or click, hidden with Escape or blur
 *   error     optional message shown under the control (role="alert")
 *   children  exactly ONE element (input, select, textarea); Field gives it
 *             an id, aria-describedby (help + error) and aria-invalid.
 *
 * Do not also wrap the control in <label>; Field renders the <label>.
 */
import { fieldIds } from './fieldIds'
import { Children, cloneElement, isValidElement, useId, useState, type ReactElement } from 'react'

type FieldProps = {
  label: string
  unit?: string
  help?: string
  error?: string | null
  children: ReactElement
}

export function Field({ label, unit, help, error, children }: FieldProps) {
  const base = useId()
  const [helpOpen, setHelpOpen] = useState(false)
  const ids = fieldIds(base, { help: !!help, error: !!error })
  const child = Children.only(children)
  const control = isValidElement<Record<string, unknown>>(child)
    ? cloneElement(child, {
        id: ids.controlId,
        'aria-describedby': ids.describedBy,
        'aria-invalid': error ? true : undefined,
      })
    : child

  return (
    <div className="field-item">
      <div className="field-label-row">
        <label htmlFor={ids.controlId}>{label}</label>
        {help && (
          <span
            className="field-help"
            onMouseEnter={() => setHelpOpen(true)}
            onMouseLeave={() => setHelpOpen(false)}
          >
            <button
              type="button"
              className="field-help-btn"
              aria-label={`Help: ${label}`}
              aria-describedby={ids.helpId}
              aria-expanded={helpOpen}
              onClick={() => setHelpOpen((v) => !v)}
              onFocus={() => setHelpOpen(true)}
              onBlur={() => setHelpOpen(false)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setHelpOpen(false)
              }}
            >
              i
            </button>
            <span id={ids.helpId} role="tooltip" className="field-help-text" hidden={!helpOpen}>
              {help}
            </span>
          </span>
        )}
      </div>
      <div className="field-control">
        {control}
        {unit && <span className="field-unit">{unit}</span>}
      </div>
      {error && (
        <p id={ids.errorId} className="field-error error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
