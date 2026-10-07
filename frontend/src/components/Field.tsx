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
 *             hover, focus or click, hidden with Escape, blur, a second
 *             click or a press outside it
 *   error     optional message shown under the control (role="alert")
 *   children  exactly ONE element (input, select, textarea); Field gives it
 *             an id, aria-describedby (help + error) and aria-invalid.
 *
 * Do not also wrap the control in <label>; Field renders the <label>.
 */
import { fieldIds, helpOpenAfterClick } from './fieldIds'
import { Children, cloneElement, isValidElement, useEffect, useId, useRef, useState, type ReactElement } from 'react'

type FieldProps = {
  label: string
  unit?: string
  help?: string
  error?: string | null
  children: ReactElement
}

export function Field({ label, unit, help, error, children }: FieldProps) {
  const base = useId()
  const [helpOpen, setHelpOpenState] = useState(false)
  // Hover events render at a lower priority than clicks, so the state captured
  // by a handler can lag the DOM; the ref is always current.
  const openNow = useRef(false)
  const setHelpOpen = (open: boolean) => {
    openNow.current = open
    setHelpOpenState(open)
  }
  const helpRef = useRef<HTMLSpanElement>(null)
  const openAtPress = useRef<boolean | null>(null)

  // Hover uses pointer events and skips touch: a tap's emulated mouseenter and
  // mouseleave bracket the click and would open then close it. iOS Safari also
  // neither focuses a tapped button nor leaves it for a tap on empty space, so
  // blur and leave cannot be relied on to close it.
  useEffect(() => {
    if (!helpOpen) return
    const closeOutside = (e: PointerEvent) => {
      if (!helpRef.current?.contains(e.target as Node)) setHelpOpen(false)
    }
    document.addEventListener('pointerdown', closeOutside)
    return () => document.removeEventListener('pointerdown', closeOutside)
  }, [helpOpen])
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
            ref={helpRef}
            className="field-help"
            onPointerEnter={(e) => e.pointerType !== 'touch' && setHelpOpen(true)}
            onPointerLeave={(e) => e.pointerType !== 'touch' && setHelpOpen(false)}
          >
            <button
              type="button"
              className="field-help-btn"
              aria-label={`Help: ${label}`}
              aria-describedby={ids.helpId}
              aria-expanded={helpOpen}
              onPointerDown={() => {
                openAtPress.current = openNow.current
              }}
              onClick={() => {
                setHelpOpen(helpOpenAfterClick(openAtPress.current, openNow.current))
                openAtPress.current = null
              }}
              onFocus={() => setHelpOpen(true)}
              onBlur={() => {
                openAtPress.current = null
                setHelpOpen(false)
              }}
              onKeyDown={(e) => {
                openAtPress.current = null
                if (e.key === 'Escape') setHelpOpen(false)
              }}
            >
              i
            </button>
            <span
              id={ids.helpId}
              role="tooltip"
              className="field-help-text"
              hidden={!helpOpen}
              onMouseDown={(e) => e.preventDefault()}
            >
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
